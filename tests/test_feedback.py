from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest

from src.domain.models import (
    JournalEntry,
    OrderAction,
    OrderIntent,
    OrderType,
    PositionSnapshot,
    RiskIntent,
    ScoreSnapshot,
    SecurityRef,
    SetupType,
    TradePlan,
    TradeSide,
    TradingStyle,
)
from src.execution import (
    BrokerFill,
    BrokerSubmissionReceipt,
    SubmissionStatus,
)
from src.feedback import (
    PaperFillJournalService,
    capture_paper_fill,
    compute_closed_trade_result,
    fill_from_paper_receipt,
)
from src.run_type import RunType

NOW = datetime(2026, 10, 27, 21, 0, tzinfo=UTC)


def make_plan() -> TradePlan:
    return TradePlan(
        security=SecurityRef(ticker="abc"),
        side=TradeSide.LONG,
        trading_style=TradingStyle.SWING,
        setup_type=SetupType.BREAKOUT,
        entry=Decimal(100),
        stop=Decimal(98),
        target=Decimal(106),
        quantity=Decimal(50),
        planned_at=NOW,
        score_snapshot=ScoreSnapshot(
            decided_at=NOW - timedelta(minutes=1),
            market_opportunity=80,
            discovery=75,
            setup=90,
            regime_score=2,
            inflection_stage=4,
        ),
        risk_intent=RiskIntent(stop_risk_budget=Decimal(100)),
    )


def make_journal(plan=None) -> JournalEntry:
    plan = plan or make_plan()
    return JournalEntry(
        plan=plan,
        created_at=NOW,
    )


def make_intent(
    plan: TradePlan,
    *,
    action: OrderAction,
    quantity=Decimal(50),
    created_at=None,
) -> OrderIntent:
    return OrderIntent(
        plan_id=plan.id,
        action=action,
        order_type=OrderType.MARKET,
        quantity=quantity,
        created_at=created_at or NOW + timedelta(minutes=1),
    )


def receipt(
    intent: OrderIntent,
    *,
    price,
    quantity=None,
    fees=Decimal(0),
    filled_at=None,
    submission_id=None,
) -> BrokerSubmissionReceipt:
    fill_time = filled_at or intent.created_at + timedelta(seconds=1)
    return BrokerSubmissionReceipt(
        submission_id=submission_id or uuid4(),
        idempotency_key=f"receipt-{uuid4()}",
        run_type=RunType.PAPER,
        symbol="ABC",
        intent_id=intent.id,
        adapter="paper",
        status=SubmissionStatus.FILLED,
        submitted_at=fill_time,
        fill=BrokerFill(
            quantity=quantity or intent.quantity,
            price=Decimal(price),
            fees=fees,
            filled_at=fill_time,
        ),
    )


def test_filled_receipt_converts_to_canonical_fill_without_manual_reentry():
    plan = make_plan()
    intent = make_intent(plan, action=OrderAction.BUY)
    broker_receipt = receipt(intent, price=100)

    fill = fill_from_paper_receipt(receipt=broker_receipt, intent=intent)

    assert fill.id == broker_receipt.submission_id
    assert fill.order_intent_id == intent.id
    assert fill.quantity == Decimal(50)
    assert fill.price == Decimal(100)


def test_nonfilled_receipt_cannot_create_journal_fill():
    plan = make_plan()
    intent = make_intent(plan, action=OrderAction.BUY)
    pending = BrokerSubmissionReceipt(
        submission_id=uuid4(),
        idempotency_key="pending",
        run_type=RunType.PAPER,
        symbol="ABC",
        intent_id=intent.id,
        adapter="paper",
        status=SubmissionStatus.ACCEPTED,
        submitted_at=intent.created_at + timedelta(seconds=1),
        fill=None,
    )

    with pytest.raises(ValueError, match="only filled"):
        fill_from_paper_receipt(receipt=pending, intent=intent)


def test_entry_fill_is_appended_and_adherence_is_computed():
    plan = make_plan()
    entry = make_journal(plan)
    intent = make_intent(plan, action=OrderAction.BUY)

    updated = capture_paper_fill(
        entry=entry,
        intent=intent,
        receipt=receipt(intent, price=100),
        captured_at=NOW + timedelta(minutes=2),
    )

    assert updated.orders == (intent,)
    assert len(updated.fills) == 1
    assert updated.result is None
    assert updated.plan_followed is True


def test_entry_fill_worse_than_planned_price_marks_execution_nonadherent():
    plan = make_plan()
    entry = make_journal(plan)
    intent = make_intent(plan, action=OrderAction.BUY)

    updated = capture_paper_fill(
        entry=entry,
        intent=intent,
        receipt=receipt(intent, price=101),
        captured_at=NOW + timedelta(minutes=2),
    )

    assert updated.plan_followed is False


def test_partial_exit_does_not_close_trade():
    plan = make_plan()
    journal = make_journal(plan)
    buy = make_intent(plan, action=OrderAction.BUY)
    journal = capture_paper_fill(
        entry=journal,
        intent=buy,
        receipt=receipt(buy, price=100),
        captured_at=NOW + timedelta(minutes=2),
    )
    sell = make_intent(
        plan,
        action=OrderAction.SELL,
        quantity=Decimal(25),
        created_at=NOW + timedelta(minutes=3),
    )

    journal = capture_paper_fill(
        entry=journal,
        intent=sell,
        receipt=receipt(sell, price=104),
        captured_at=NOW + timedelta(minutes=4),
    )

    assert journal.result is None


def test_full_close_computes_r_result_mae_mfe_and_outcome():
    plan = make_plan()
    journal = make_journal(plan)
    buy = make_intent(plan, action=OrderAction.BUY)
    journal = capture_paper_fill(
        entry=journal,
        intent=buy,
        receipt=receipt(buy, price=100),
        captured_at=NOW + timedelta(minutes=2),
    )

    snapshots = (
        PositionSnapshot(
            plan_id=plan.id,
            as_of=NOW + timedelta(minutes=3),
            side=TradeSide.LONG,
            quantity=Decimal(50),
            average_entry=Decimal(100),
            unrealized_pnl=Decimal(-25),
        ),
        PositionSnapshot(
            plan_id=plan.id,
            as_of=NOW + timedelta(minutes=4),
            side=TradeSide.LONG,
            quantity=Decimal(50),
            average_entry=Decimal(100),
            unrealized_pnl=Decimal(150),
        ),
    )
    sell = make_intent(
        plan,
        action=OrderAction.SELL,
        created_at=NOW + timedelta(minutes=5),
    )
    journal = capture_paper_fill(
        entry=journal,
        intent=sell,
        receipt=receipt(
            sell,
            price=104,
            fees=Decimal(0),
            filled_at=NOW + timedelta(minutes=6),
        ),
        captured_at=NOW + timedelta(minutes=7),
        position_snapshots=snapshots,
    )

    assert journal.result is not None
    assert journal.result.run_type is RunType.PAPER
    assert journal.result.realized_pnl == Decimal(200)
    assert journal.result.r_multiple == Decimal(2)
    assert journal.result.outcome == "win"
    assert journal.result.mae_r == Decimal("0.25")
    assert journal.result.mfe_r == Decimal("1.5")
    assert journal.result.excursion_source == "position_snapshots"


def test_fees_reduce_realized_pnl_and_r_multiple():
    plan = make_plan()
    journal = make_journal(plan)
    buy = make_intent(plan, action=OrderAction.BUY)
    journal = capture_paper_fill(
        entry=journal,
        intent=buy,
        receipt=receipt(buy, price=100, fees=Decimal(1)),
        captured_at=NOW + timedelta(minutes=2),
    )
    sell = make_intent(
        plan,
        action=OrderAction.SELL,
        created_at=NOW + timedelta(minutes=3),
    )
    journal = capture_paper_fill(
        entry=journal,
        intent=sell,
        receipt=receipt(sell, price=102, fees=Decimal(1)),
        captured_at=NOW + timedelta(minutes=4),
    )

    assert journal.result is not None
    assert journal.result.realized_pnl == Decimal(98)
    assert journal.result.r_multiple == Decimal("0.98")


def test_duplicate_receipt_is_idempotent():
    plan = make_plan()
    entry = make_journal(plan)
    intent = make_intent(plan, action=OrderAction.BUY)
    broker_receipt = receipt(intent, price=100)

    once = capture_paper_fill(
        entry=entry,
        intent=intent,
        receipt=broker_receipt,
        captured_at=NOW + timedelta(minutes=2),
    )
    twice = capture_paper_fill(
        entry=once,
        intent=intent,
        receipt=broker_receipt,
        captured_at=NOW + timedelta(minutes=3),
    )

    assert twice == once
    assert len(twice.fills) == 1


def test_exit_quantity_cannot_exceed_captured_entry_quantity():
    plan = make_plan()
    buy = make_intent(plan, action=OrderAction.BUY, quantity=Decimal(25))
    journal = capture_paper_fill(
        entry=make_journal(plan),
        intent=buy,
        receipt=receipt(buy, price=100),
        captured_at=NOW + timedelta(minutes=2),
    )
    sell = make_intent(
        plan,
        action=OrderAction.SELL,
        quantity=Decimal(50),
        created_at=NOW + timedelta(minutes=3),
    )

    with pytest.raises(ValueError, match="exit fills exceed"):
        capture_paper_fill(
            entry=journal,
            intent=sell,
            receipt=receipt(sell, price=102),
            captured_at=NOW + timedelta(minutes=4),
        )


class FakeRepository:
    def __init__(self):
        self.saved = []

    def save(self, entry, expected_revision=None):
        self.saved.append((entry, expected_revision))
        return 7

    def get(self, journal_id):
        return None

    def get_revision(self, journal_id, revision):
        return None

    def get_by_trade_id(self, trade_id):
        return None


def test_service_persists_append_only_journal_revision():
    plan = make_plan()
    entry = make_journal(plan)
    intent = make_intent(plan, action=OrderAction.BUY)
    repository = FakeRepository()
    service = PaperFillJournalService(repository)

    updated, revision = service.capture(
        entry=entry,
        intent=intent,
        receipt=receipt(intent, price=100),
        captured_at=NOW + timedelta(minutes=2),
        expected_revision=3,
    )

    assert revision == 7
    assert repository.saved == [(updated, 3)]


def test_compute_closed_result_returns_none_for_open_trade():
    plan = make_plan()
    intent = make_intent(plan, action=OrderAction.BUY)
    journal = capture_paper_fill(
        entry=make_journal(plan),
        intent=intent,
        receipt=receipt(intent, price=100),
        captured_at=NOW + timedelta(minutes=2),
    )

    assert compute_closed_trade_result(journal) is None
