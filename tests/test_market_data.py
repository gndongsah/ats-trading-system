from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from src.market_data import (
    JsonMarketDataAdapter,
    MarketDataStore,
    MarketEvent,
    ReplayRequirement,
    ReplayUnavailable,
    RevisionConflict,
)
from src.persistence.models import MarketDataRecord

T = datetime(2026, 10, 19, 13, 30, 2, tzinfo=UTC)
EVENTS = JsonMarketDataAdapter().parse(
    (Path(__file__).parent / "fixtures/market_data_v1.json").read_text()
)


def requirements(age=timedelta(minutes=1)):
    return tuple(
        ReplayRequirement(
            source="synthetic-json-v1", symbol="ABC", stream=s, max_age=age
        )
        for s in ("bar:1m", "reference")
    )


@pytest.fixture
def session():
    engine = create_engine("sqlite://")
    MarketDataRecord.__table__.create(engine)
    with Session(engine) as session, session.begin():
        yield session
    engine.dispose()


def ingest(session, event, at=T):
    return MarketDataStore(session, clock=lambda: at).ingest(event)


def test_yesterday_never_reveals_correction_learned_today(session):
    for event in EVENTS[:2]:
        ingest(session, event)
    store = MarketDataStore(session)
    before = store.replay(as_of=T, requirements=requirements())
    ingest(session, EVENTS[2], T + timedelta(days=1))
    after = store.replay(as_of=T, requirements=requirements())
    assert before == after and before.digest == after.digest
    assert after.observations[0].event.revision == 1
    today = store.replay(
        as_of=T + timedelta(days=1), requirements=requirements(timedelta(days=2))
    )
    assert today.observations[0].event.revision == 2
    assert session.scalar(select(func.count()).select_from(MarketDataRecord)) == 3


@pytest.mark.parametrize("case", ["event", "availability", "ingestion"])
def test_each_future_clock_is_excluded(session, case):
    ingest(session, EVENTS[0])
    event = EVENTS[1].model_dump()
    at = T
    if case == "event":
        event["event_time"] = T + timedelta(seconds=1)
        event["available_at"] = at = T + timedelta(seconds=1)
    elif case == "availability":
        event["available_at"] = at = T + timedelta(seconds=1)
    else:
        at = T + timedelta(seconds=1)
    ingest(session, MarketEvent.model_validate(event), at)
    with pytest.raises(ReplayUnavailable, match="MISSING"):
        MarketDataStore(session).replay(as_of=T, requirements=requirements())


def test_duplicate_preserves_first_ingestion_and_conflict_never_overwrites(session):
    original = ingest(session, EVENTS[1])
    duplicate = ingest(session, EVENTS[1], T + timedelta(days=1))
    assert duplicate == original
    changed = EVENTS[1].model_dump()
    changed["payload"]["close"] = "101"
    with pytest.raises(RevisionConflict):
        ingest(session, MarketEvent.model_validate(changed), T + timedelta(days=1))
    assert session.scalar(select(func.count()).select_from(MarketDataRecord)) == 1


def test_missing_reference_and_stale_data_deny(session):
    ingest(session, EVENTS[1])
    with pytest.raises(ReplayUnavailable, match="MISSING:ABC:reference"):
        MarketDataStore(session).replay(as_of=T, requirements=requirements())
    ingest(session, EVENTS[0])
    with pytest.raises(ReplayUnavailable, match="STALE"):
        MarketDataStore(session).replay(
            as_of=T + timedelta(seconds=61), requirements=requirements()
        )


def test_correction_does_not_refresh_stale_event(session):
    for event in EVENTS:
        ingest(session, event, T + timedelta(days=1))
    with pytest.raises(ReplayUnavailable, match="STALE"):
        MarketDataStore(session).replay(
            as_of=T + timedelta(days=1), requirements=requirements()
        )


def test_out_of_order_revision_and_source_isolation(session):
    for event in (EVENTS[2], EVENTS[1], EVENTS[0]):
        ingest(session, event, T + timedelta(days=1))
    event = EVENTS[1].model_dump()
    event.update(source="other", revision=99)
    ingest(session, MarketEvent.model_validate(event), T + timedelta(days=1))
    replay = MarketDataStore(session).replay(
        as_of=T + timedelta(days=1), requirements=requirements(timedelta(days=2))
    )
    assert replay.observations[0].event.revision == 2


def test_boundaries_and_invalid_inputs(session):
    for event in EVENTS[:2]:
        ingest(session, event)
    store = MarketDataStore(session)
    assert store.replay(as_of=T + timedelta(seconds=58), requirements=requirements())
    with pytest.raises(ReplayUnavailable, match="STALE"):
        store.replay(
            as_of=T + timedelta(seconds=58, microseconds=1), requirements=requirements()
        )
    with pytest.raises(ValueError, match="UTC"):
        store.replay(as_of=T.replace(tzinfo=None), requirements=requirements())
    for reqs in ((), requirements() * 2):
        with pytest.raises(ValueError):
            store.replay(as_of=T, requirements=reqs)
    with pytest.raises(ValidationError, match="ingestion"):
        ingest(session, EVENTS[2], T)
    with pytest.raises(ValidationError):
        JsonMarketDataAdapter().parse('[{"symbol":"ABC"}]')


def test_persistence_survives_session_reopen(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'market.db'}")
    MarketDataRecord.__table__.create(engine)
    with Session(engine) as session, session.begin():
        for event in EVENTS[:2]:
            ingest(session, event)
    with Session(engine) as session:
        assert MarketDataStore(session).replay(as_of=T, requirements=requirements())
    engine.dispose()
