"""F12 actual capture, accounting and Layer 3 against disposable PostgreSQL."""

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from src.domain.models import Regime, RiskIntent
from src.persistence.models import PortfolioSnapshotRecord, RiskReservationEventRecord
from src.portfolio import PositionExposure, RiskDecision
from src.simulation_ledger import EntryKind, SimulationEntry, SimulationLedger
from src.simulation_opening import report_correction
from src.simulation_risk import SimulationRiskService
from tests import test_postgres_persistence as pg
from tests import test_simulation_ledger_loss_locks_postgres as f6
from tests import test_simulation_opening as f11

migrated_postgres_database = pg.migrated_postgres_database
migrated_postgres_engine = pg.migrated_postgres_engine


def setup(
    engine,
    *,
    pnl="0",
    buying_power="100000",
    mismatch=False,
    capture=True,
    quantity=50,
    regime=Regime.RISK_ON,
    positions=(),
):
    factory, _, account, _entries = pg._postgres_layer3_setup(engine, plan_count=0)
    book = SimulationLedger(account_id=account, opened_at=f11.T, opening_nlv="100000")
    openings = f11.capture_both(factory, book) if capture else ()
    at = f11.T + timedelta(seconds=2)
    if Decimal(pnl):
        book = book.append(
            SimulationEntry(
                event_id="pnl", occurred_at=at, amount=pnl, kind=EntryKind.PNL
            )
        )
    f6.store(factory, book, at)
    with factory.begin() as session:
        row = session.scalar(select(PortfolioSnapshotRecord))
        from src.portfolio import PortfolioSnapshot

        snapshot = PortfolioSnapshot.model_validate(row.snapshot_data).model_copy(
            update={
                "version": 2,
                "as_of": at,
                "equity": book.valuation(at).net_liquidation + Decimal(int(mismatch)),
                "positions": positions,
            }
        )
        entry = pg.make_complete_trade()
        plan = entry.plan.model_copy(
            update={
                "account_id": account,
                "quantity": Decimal(quantity),
                "risk_intent": RiskIntent(
                    stop_risk_budget=Decimal(quantity) * Decimal("2.5")
                ),
                "score_snapshot": entry.plan.score_snapshot.model_copy(
                    update={"regime": regime, "id": uuid4()}
                ),
            }
        )
        pg.JournalRepository(session).save(entry.model_copy(update={"plan": plan}))
    now = [at]
    service = SimulationRiskService(
        snapshot=snapshot,
        buying_power=Decimal(buying_power),
        calendar=f11.calendar(),
        taxonomy=pg.POSTGRES_RISK_TAXONOMY,
        principal_provider=pg._PostgresPrincipalProvider(account),
        clock=lambda: now[0],
    )
    with factory.begin() as session:
        service.publish_snapshot(session, snapshot)
    return factory, service, plan.id, now, openings


def reserve(context, key="f12"):
    factory, service, plan_id, *_ = context
    return service.authorize_with_retry(factory, plan_id=plan_id, idempotency_key=key)


def count(factory):
    with factory() as session:
        return session.scalar(
            select(func.count()).select_from(RiskReservationEventRecord)
        )


@pytest.mark.parametrize("bp,quantity", [("100000", 50), ("500", 4), ("0", 0)])
def test_receipted_pipeline_sizes_and_reserves(migrated_postgres_engine, bp, quantity):
    ctx = setup(migrated_postgres_engine, buying_power=bp)
    result = reserve(ctx)
    # Fixture entry is 100.5, stop 98; buying power 500 caps to four shares.
    assert result.quantity == quantity
    assert not result.execution_enabled
    assert count(ctx[0]) == int(quantity > 0)
    if quantity:
        replay = reserve(ctx)
        assert replay.replayed and replay.reservation_id == result.reservation_id
        with ctx[0]() as session:
            event = session.scalar(select(RiskReservationEventRecord))
            assert event.event_data["simulation_basis"]["nlv"] == "100000"


@pytest.mark.parametrize(
    "case", ["missing", "mismatch", "stale", "future", "correction", "loss"]
)
def test_untrusted_or_locked_pipeline_denies_without_reservation(
    migrated_postgres_engine, case
):
    ctx = setup(
        migrated_postgres_engine,
        capture=case != "missing",
        mismatch=case == "mismatch",
        pnl="-1500" if case == "loss" else "0",
    )
    if case == "stale":
        ctx[3][0] += timedelta(seconds=30, microseconds=1)
    if case == "future":
        ctx[3][0] -= timedelta(microseconds=1)
    if case == "correction":
        with ctx[0].begin() as session:
            from uuid import uuid4

            report_correction(
                session,
                ctx[4][0],
                correction_id=uuid4(),
                reason="restated",
                candidate_nlv="110000",
                discovered_at=ctx[3][0],
            )
    assert reserve(ctx).decision is RiskDecision.DENY
    assert count(ctx[0]) == 0


def test_concurrent_replay_and_stale_replay_fail_closed(migrated_postgres_engine):
    ctx = setup(migrated_postgres_engine)
    with ThreadPoolExecutor(max_workers=3) as executor:
        results = list(executor.map(lambda _: reserve(ctx), range(3)))
    assert len({r.reservation_id for r in results}) == 1
    assert sum(r.replayed for r in results) == 2
    assert count(ctx[0]) == 1
    ctx[3][0] += timedelta(seconds=31)
    assert reserve(ctx).decision is RiskDecision.DENY
    assert count(ctx[0]) == 1


def test_failure_after_reservation_rolls_back(migrated_postgres_engine, monkeypatch):
    ctx = setup(migrated_postgres_engine)
    original = ctx[1]._append_event

    def fail(session, **kwargs):
        original(session, **kwargs)
        raise ValueError("after reservation")

    monkeypatch.setattr(ctx[1], "_append_event", fail)
    with pytest.raises(ValueError, match="after reservation"):
        reserve(ctx)
    assert count(ctx[0]) == 0


@pytest.mark.parametrize(
    "regime,pnl,quantity",
    [
        (Regime.RISK_ON, "0", 149),
        (Regime.NEUTRAL_CHOP, "0", 100),
        (Regime.RISK_ON, "10000", 164),
    ],
)
def test_same_current_nlv_drives_sizing_and_theme(
    migrated_postgres_engine, regime, pnl, quantity
):
    ctx = setup(migrated_postgres_engine, quantity=500, regime=regime, pnl=pnl)
    result = reserve(ctx)
    assert result.quantity == quantity
    assert result.decision is RiskDecision.RESIZE
    with ctx[0]() as session:
        event = session.scalar(select(RiskReservationEventRecord))
        assert Decimal(event.event_data["simulation_basis"]["nlv"]) == Decimal(
            100000
        ) + Decimal(pnl)


@pytest.mark.parametrize("ticker,stop", [("XYZ", "81"), ("ABC", "91")])
def test_total_and_correlated_capacity_use_current_nlv(
    migrated_postgres_engine, ticker, stop
):
    # 1900 total risk leaves 100; 900 correlated risk leaves 100. Both allow 40 shares.
    position = PositionExposure(
        ticker=ticker,
        quantity=100,
        average_entry=Decimal(100),
        active_stop=Decimal(stop),
        stop_confirmed=True,
    )
    result = reserve(
        setup(migrated_postgres_engine, quantity=500, positions=(position,))
    )
    assert result.quantity == 40
    assert result.decision is RiskDecision.RESIZE
