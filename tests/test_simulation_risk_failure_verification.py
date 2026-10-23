"""F14 author-executed failure probes; not an independent review verdict."""

from datetime import timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import select, text

from src.ats009_accounting import PeriodKind
from src.domain.models import Regime
from src.persistence.models import RiskReservationEventRecord
from src.persistence.transaction_retry import TransactionRetryExhausted
from src.portfolio import RiskDecision
from src.simulation_opening import report_correction
from src.simulation_risk import SimulationRiskService
from tests import test_simulation_risk as f12

migrated_postgres_database = f12.migrated_postgres_database
migrated_postgres_engine = f12.migrated_postgres_engine


def adapter(original, snapshot):
    return SimulationRiskService(
        snapshot=snapshot,
        buying_power=original.buying_power,
        calendar=original._loss_locks.calendar,
        taxonomy=original._taxonomy,
        principal_provider=original._principal_provider,
        clock=original._clock,
    )


@pytest.mark.parametrize("sqlstate", ["40001", "40P01", "55P03"])
@pytest.mark.parametrize("exhaust", [False, True])
def test_f14_retry_after_reservation_preserves_basis_and_atomicity(
    migrated_postgres_engine, monkeypatch, sqlstate, exhaust
):
    ctx = f12.setup(migrated_postgres_engine, buying_power="500")
    factory, service, plan, *_ = ctx
    original = service._append_event
    transactions = []
    keys = []
    bases = []

    def fault(session, **kwargs):
        original(session, **kwargs)
        transactions.append(session.scalar(text("SELECT txid_current()")))
        keys.append(kwargs["idempotency_key"])
        row = session.scalar(select(RiskReservationEventRecord))
        bases.append(row.event_data["simulation_basis"])
        with factory() as observer:
            assert observer.scalar(select(RiskReservationEventRecord)) is None
        if exhaust or len(transactions) == 1:
            # PostgreSQL raises after actual writes; not a genuine conflict.
            session.execute(
                text(
                    f"DO $$ BEGIN RAISE EXCEPTION 'F14 fault' USING ERRCODE = '{sqlstate}'; END $$"
                )
            )

    monkeypatch.setattr(service, "_append_event", fault)
    if exhaust:
        with pytest.raises(TransactionRetryExhausted) as info:
            service.authorize_with_retry(
                factory, plan_id=plan, idempotency_key="f14", max_attempts=2
            )
        assert info.value.sqlstate == sqlstate
        assert f12.count(factory) == 0
    else:
        result = service.authorize_with_retry(
            factory, plan_id=plan, idempotency_key="f14", max_attempts=2
        )
        assert result.quantity == 4 and not result.execution_enabled
        replay = service.authorize_with_retry(
            factory, plan_id=plan, idempotency_key="f14"
        )
        assert replay.replayed and replay.reservation_id == result.reservation_id
        assert not replay.execution_enabled
        assert f12.count(factory) == 1
    assert len(set(transactions)) == 2
    assert keys == ["reserve:f14"] * 2
    assert (
        bases[0]
        == bases[1]
        == {
            "source": "SIMULATION-ONLY",
            "snapshot_version": 2,
            "as_of": service.snapshot.as_of.isoformat(),
            "nlv": "100000",
            "buying_power": "500",
            "risk_fraction": "0.005",
        }
    )


@pytest.mark.parametrize("kind", [PeriodKind.DAILY, PeriodKind.WEEKLY])
def test_f14_correction_after_reservation_denies_same_key_replay(
    migrated_postgres_engine, kind
):
    ctx = f12.setup(migrated_postgres_engine)
    initial = f12.reserve(ctx)
    assert initial.quantity > 0
    opening = next(o for o in ctx[4] if o.period_kind == kind)
    with ctx[0].begin() as session:
        report_correction(
            session,
            opening,
            correction_id=uuid4(),
            candidate_nlv="110000",
            reason="F14 correction",
            discovered_at=ctx[3][0],
        )
    replay = f12.reserve(ctx)
    assert replay.decision is RiskDecision.DENY
    assert not replay.execution_enabled and not replay.replayed
    assert f12.count(ctx[0]) == 1


@pytest.mark.parametrize(
    "regime,quantity",
    [
        (Regime.RISK_ON_NARROW, 149),
        (Regime.RISK_OFF, 0),
        (Regime.CAPITULATION_REVERSAL, 0),
        (None, 0),
    ],
)
def test_f14_final_adapter_regime_policy(migrated_postgres_engine, regime, quantity):
    ctx = f12.setup(migrated_postgres_engine, quantity=500, regime=regime)
    result = f12.reserve(ctx)
    assert result.quantity == quantity
    assert not result.execution_enabled
    assert f12.count(ctx[0]) == int(quantity > 0)
    if quantity == 0:
        assert result.decision is RiskDecision.DENY


@pytest.mark.parametrize("offset", [-1, 1])
def test_f14_equal_nlv_different_timestamp_denies(migrated_postgres_engine, offset):
    ctx = f12.setup(migrated_postgres_engine)
    original = ctx[1]
    changed = original.snapshot.model_copy(
        update={
            "version": 3,
            "as_of": original.snapshot.as_of + timedelta(microseconds=offset),
        }
    )
    service = adapter(original, changed)
    with ctx[0].begin() as session:
        service.publish_snapshot(session, changed)
    result = service.authorize_with_retry(
        ctx[0], plan_id=ctx[2], idempotency_key="timestamp"
    )
    assert result.decision is RiskDecision.DENY
    assert f12.count(ctx[0]) == 0


@pytest.mark.parametrize("prior_reservation", [False, True])
def test_f14_snapshot_replacement_invalidates_old_adapter(
    migrated_postgres_engine, prior_reservation
):
    ctx = f12.setup(migrated_postgres_engine)
    if prior_reservation:
        assert f12.reserve(ctx).quantity > 0
    service = adapter(ctx[1], ctx[1].snapshot.model_copy(update={"version": 3}))
    with ctx[0].begin() as session:
        service.publish_snapshot(session, service.snapshot)
    old = f12.reserve(ctx)
    assert old.decision is RiskDecision.DENY
    assert f12.count(ctx[0]) == int(prior_reservation)
    if not prior_reservation:
        current = service.authorize_with_retry(
            ctx[0], plan_id=ctx[2], idempotency_key="new-version"
        )
        assert current.quantity > 0 and not current.execution_enabled


@pytest.mark.parametrize("buying_power", [Decimal("NaN"), Decimal(-1), 500.0])
def test_f14_invalid_buying_power_rejected_before_database(
    migrated_postgres_engine, buying_power
):
    ctx = f12.setup(migrated_postgres_engine)
    original = ctx[1]
    with pytest.raises(ValueError, match="buying power"):
        SimulationRiskService(
            snapshot=original.snapshot,
            buying_power=buying_power,
            calendar=original._loss_locks.calendar,
            taxonomy=original._taxonomy,
            principal_provider=original._principal_provider,
            clock=original._clock,
        )
    assert f12.count(ctx[0]) == 0
