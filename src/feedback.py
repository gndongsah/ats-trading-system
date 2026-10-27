"""ATS-024 automatic PAPER fill capture and trade-outcome feedback."""

from datetime import datetime
from decimal import Decimal
from fractions import Fraction

from src.domain.models import (
    Fill,
    JournalEntry,
    OrderAction,
    OrderIntent,
    PositionSnapshot,
    TradeResult,
    TradeSide,
    _require_utc,
)
from src.execution import BrokerSubmissionReceipt, SubmissionStatus
from src.persistence.repository import JournalRepositoryProtocol
from src.run_type import RunType


def _weighted_average(fills: tuple[Fill, ...]) -> Decimal:
    total_quantity = sum((fill.quantity for fill in fills), Decimal(0))
    if total_quantity <= 0:
        raise ValueError("weighted average requires positive filled quantity")
    total_notional = sum(
        (fill.quantity * fill.price for fill in fills),
        Decimal(0),
    )
    return total_notional / total_quantity


def fill_from_paper_receipt(
    *,
    receipt: BrokerSubmissionReceipt,
    intent: OrderIntent,
) -> Fill:
    """Convert one filled PAPER broker receipt into the canonical Fill model."""

    if receipt.run_type is not RunType.PAPER:
        raise ValueError("ATS-024 accepts PAPER receipts only")
    if receipt.status is not SubmissionStatus.FILLED or receipt.fill is None:
        raise ValueError("only filled broker receipts can create journal fills")
    if receipt.intent_id != intent.id:
        raise ValueError("receipt intent does not match supplied order intent")

    return Fill(
        id=receipt.submission_id,
        order_intent_id=intent.id,
        filled_at=receipt.fill.filled_at,
        quantity=receipt.fill.quantity,
        price=receipt.fill.price,
        fees=receipt.fill.fees,
    )


def _split_entry_exit_fills(
    entry: JournalEntry,
) -> tuple[tuple[Fill, ...], tuple[Fill, ...]]:
    orders = {order.id: order for order in entry.orders}
    entry_action = (
        OrderAction.BUY if entry.plan.side is TradeSide.LONG else OrderAction.SELL
    )
    entry_fills: list[Fill] = []
    exit_fills: list[Fill] = []
    for fill in entry.fills:
        order = orders[fill.order_intent_id]
        if order.action is entry_action:
            entry_fills.append(fill)
        else:
            exit_fills.append(fill)
    return tuple(entry_fills), tuple(exit_fills)


def _initial_risk(entry: JournalEntry, entry_fills: tuple[Fill, ...]) -> Decimal:
    quantity = sum((fill.quantity for fill in entry_fills), Decimal(0))
    if quantity <= 0:
        raise ValueError("trade outcome requires at least one entry fill")
    per_share_risk = abs(entry.plan.entry - entry.plan.stop)
    risk = per_share_risk * quantity
    if risk <= 0:
        raise ValueError("trade outcome requires positive initial risk")
    return risk


def _excursions_in_r(
    *,
    snapshots: tuple[PositionSnapshot, ...],
    initial_risk: Decimal,
) -> tuple[Decimal, Decimal]:
    if not snapshots:
        return Decimal(0), Decimal(0)
    pnl_values = tuple(snapshot.unrealized_pnl for snapshot in snapshots)
    adverse = min(min(pnl_values), Decimal(0))
    favorable = max(max(pnl_values), Decimal(0))
    return abs(adverse) / initial_risk, favorable / initial_risk


def _execution_adherence(
    entry: JournalEntry,
    entry_fills: tuple[Fill, ...],
) -> bool:
    """Deterministic execution adherence available from captured fill facts.

    ATS-024 can verify planned size and entry-price discipline. It does not infer
    discretionary exit quality or the trader's intent from price action.
    """

    quantity = sum((fill.quantity for fill in entry_fills), Decimal(0))
    if quantity > entry.plan.quantity:
        return False
    average_entry = _weighted_average(entry_fills)
    if entry.plan.side is TradeSide.LONG:
        return average_entry <= entry.plan.entry
    return average_entry >= entry.plan.entry


def compute_closed_trade_result(entry: JournalEntry) -> TradeResult | None:
    """Return a PAPER TradeResult when captured fills fully close the position."""

    entry_fills, exit_fills = _split_entry_exit_fills(entry)
    if not entry_fills or not exit_fills:
        return None

    entry_quantity = sum((fill.quantity for fill in entry_fills), Decimal(0))
    exit_quantity = sum((fill.quantity for fill in exit_fills), Decimal(0))
    if exit_quantity > entry_quantity:
        raise ValueError("exit fills exceed captured entry quantity")
    if exit_quantity != entry_quantity:
        return None

    average_entry = _weighted_average(entry_fills)
    average_exit = _weighted_average(exit_fills)
    gross = (
        (average_exit - average_entry) * entry_quantity
        if entry.plan.side is TradeSide.LONG
        else (average_entry - average_exit) * entry_quantity
    )
    fees = sum((fill.fees for fill in entry.fills), Decimal(0))
    realized = gross - fees
    initial_risk = _initial_risk(entry, entry_fills)
    ratio = Fraction(realized) / Fraction(initial_risk)
    r_multiple = Decimal(ratio.numerator) / Decimal(ratio.denominator)
    mae_r, mfe_r = _excursions_in_r(
        snapshots=entry.position_snapshots,
        initial_risk=initial_risk,
    )
    outcome = "win" if realized > 0 else "loss" if realized < 0 else "breakeven"

    return TradeResult(
        plan_id=entry.plan.id,
        run_type=RunType.PAPER,
        closed_at=max(fill.filled_at for fill in exit_fills),
        realized_pnl=realized,
        r_multiple=r_multiple,
        outcome=outcome,
        mae_r=mae_r,
        mfe_r=mfe_r,
        excursion_source="position_snapshots",
    )


def capture_paper_fill(
    *,
    entry: JournalEntry,
    intent: OrderIntent,
    receipt: BrokerSubmissionReceipt,
    captured_at: datetime,
    position_snapshots: tuple[PositionSnapshot, ...] = (),
) -> JournalEntry:
    """Append a PAPER fill and recompute outcome/adherence without manual re-entry."""

    captured_at = _require_utc(captured_at)
    if intent.plan_id != entry.plan.id:
        raise ValueError("order intent must reference the journal plan")
    fill = fill_from_paper_receipt(receipt=receipt, intent=intent)

    orders = entry.orders
    existing_order = next((order for order in orders if order.id == intent.id), None)
    if existing_order is not None and existing_order != intent:
        raise ValueError("order intent ID was reused with different data")
    if existing_order is None:
        orders = (*orders, intent)

    existing_fill = next((item for item in entry.fills if item.id == fill.id), None)
    if existing_fill is not None:
        if existing_fill != fill:
            raise ValueError("fill ID was reused with different data")
        return entry

    snapshots = (*entry.position_snapshots, *position_snapshots)
    provisional = JournalEntry(
        id=entry.id,
        plan=entry.plan,
        prior_plans=entry.prior_plans,
        orders=orders,
        fills=(*entry.fills, fill),
        position_snapshots=snapshots,
        result=None,
        plan_followed=None,
        mood=entry.mood,
        lesson=entry.lesson,
        thesis=entry.thesis,
        created_at=captured_at,
    )
    entry_fills, _ = _split_entry_exit_fills(provisional)
    plan_followed = (
        _execution_adherence(provisional, entry_fills) if entry_fills else None
    )
    result = compute_closed_trade_result(provisional)
    lifecycle_time = max(
        captured_at,
        fill.filled_at,
        *(snapshot.as_of for snapshot in position_snapshots),
        result.closed_at if result is not None else captured_at,
    )
    return JournalEntry(
        id=entry.id,
        plan=entry.plan,
        prior_plans=entry.prior_plans,
        orders=orders,
        fills=provisional.fills,
        position_snapshots=snapshots,
        result=result,
        plan_followed=plan_followed,
        mood=entry.mood,
        lesson=entry.lesson,
        thesis=entry.thesis,
        created_at=lifecycle_time,
    )


class PaperFillJournalService:
    """Persist append-only journal revisions produced from PAPER broker receipts."""

    def __init__(self, repository: JournalRepositoryProtocol) -> None:
        self._repository = repository

    def capture(
        self,
        *,
        entry: JournalEntry,
        intent: OrderIntent,
        receipt: BrokerSubmissionReceipt,
        captured_at: datetime,
        expected_revision: int | None = None,
        position_snapshots: tuple[PositionSnapshot, ...] = (),
    ) -> tuple[JournalEntry, int]:
        updated = capture_paper_fill(
            entry=entry,
            intent=intent,
            receipt=receipt,
            captured_at=captured_at,
            position_snapshots=position_snapshots,
        )
        if updated == entry:
            revision = expected_revision if expected_revision is not None else 0
            return entry, revision
        revision = self._repository.save(updated, expected_revision=expected_revision)
        return updated, revision
