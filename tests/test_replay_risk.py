from datetime import timedelta

import pytest
from sqlalchemy import select, text

from src.market_data import MarketDataStore
from src.persistence.models import RiskReservationEventRecord
from src.portfolio import RiskDecision
from src.replay_risk import ReplaySimulationRiskService
from tests import test_simulation_risk as f12
from tests.test_market_data import EVENTS, requirements

migrated_postgres_database = f12.migrated_postgres_database
engine = f12.migrated_postgres_engine


def setup(engine, *, missing=False, stale=False, correction=False):
    ctx = f12.setup(engine)
    factory, original, plan, now, _ = ctx
    at = now[0]
    with factory.begin() as session:
        for event in EVENTS[: 1 if missing else 2]:
            data = event.model_dump()
            data["event_time"] = at - timedelta(minutes=2) if stale else at
            data["available_at"] = data["event_time"]
            from src.market_data import MarketEvent

            MarketDataStore(session, clock=lambda: at).ingest(
                MarketEvent.model_validate(data)
            )
        if correction:
            data = EVENTS[2].model_dump()
            data["event_time"] = at
            data["available_at"] = at + timedelta(days=1)
            MarketDataStore(session, clock=lambda: at + timedelta(days=1)).ingest(
                MarketEvent.model_validate(data)
            )
        try:
            replay = MarketDataStore(session).replay(
                as_of=at, requirements=requirements()
            )
        except ValueError:
            from src.market_data import ReplaySnapshot

            replay = ReplaySnapshot(as_of=at, observations=())
    service = ReplaySimulationRiskService(
        market_snapshot=replay,
        requirements=requirements(),
        snapshot=original.snapshot,
        buying_power=original.buying_power,
        calendar=original._loss_locks.calendar,
        taxonomy=original._taxonomy,
        principal_provider=original._principal_provider,
        clock=original._clock,
    )
    return factory, service, plan, now, ctx[4]


def test_replay_feeds_real_simulation_risk_and_records_provenance(engine):
    ctx = setup(engine, correction=True)
    result = f12.reserve(ctx)
    assert result.quantity == 50 and not result.execution_enabled
    with ctx[0]() as session:
        event = session.scalar(select(RiskReservationEventRecord))
        basis = event.event_data["market_replay_basis"]
        assert basis["digest"] == ctx[1].market_snapshot.digest
        assert basis["snapshot"]["observations"][0]["event"]["revision"] == 1
    assert f12.reserve(ctx).replayed


@pytest.mark.parametrize("case", ["missing", "stale", "clock", "price"])
def test_replay_denial_creates_no_risk_reservation(engine, case):
    ctx = setup(engine, missing=case == "missing", stale=case == "stale")
    if case == "clock":
        ctx[3][0] += timedelta(seconds=1)
    if case == "price":
        with ctx[0].begin() as session:
            from src.market_data import MarketEvent

            data = EVENTS[1].model_dump()
            data.update(event_time=ctx[3][0], available_at=ctx[3][0], revision=2)
            data["payload"]["close"] = "101"
            MarketDataStore(session, clock=lambda: ctx[3][0]).ingest(
                MarketEvent.model_validate(data)
            )
    result = f12.reserve(ctx)
    assert result.decision is RiskDecision.DENY and not result.execution_enabled
    assert f12.count(ctx[0]) == 0


def test_risk_provenance_rolls_back_with_reservation(engine, monkeypatch):
    ctx = setup(engine)
    original = ctx[1]._append_event

    def fail(session, **kwargs):
        original(session, **kwargs)
        session.execute(text("SELECT 1"))
        raise ValueError("after replay reservation")

    monkeypatch.setattr(ctx[1], "_append_event", fail)
    with pytest.raises(ValueError, match="after replay reservation"):
        f12.reserve(ctx)
    assert f12.count(ctx[0]) == 0
