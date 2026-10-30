from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.database import Base
from src.forward_test_runtime import (
    DurableWebullPaperMarketRuntime,
    MarketFreshnessPolicy,
    MarketFreshnessViolation,
)
from src.market_data import Bar, MarketDataStore, MarketEvent, ReplayRequirement
from src.persistence.models import MarketDataRecord

AT = datetime(2026, 10, 30, 14, 31, tzinfo=UTC)


class Adapter:
    def __init__(self, event):
        self.event = event
        self.calls = []

    def fetch_completed_1m_bar(self, symbol: str, *, category: str):
        self.calls.append((symbol, category))
        return self.event


def event(*, event_time=AT - timedelta(minutes=1), available_at=AT):
    return MarketEvent(
        source="webull-openapi-sandbox-v1",
        symbol="SPY",
        event_time=event_time,
        available_at=available_at,
        revision=1,
        payload=Bar(
            interval="1m",
            open=Decimal("769.20"),
            high=Decimal("769.50"),
            low=Decimal("769.10"),
            close=Decimal("769.36"),
            volume=1181369,
        ),
    )


@pytest.fixture
def runtime(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'ats-runtime.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    try:
        yield factory
    finally:
        engine.dispose()


def test_collect_commits_and_restores_observation(runtime):
    adapter = Adapter(event())
    runner = DurableWebullPaperMarketRuntime(
        adapter=adapter,
        session_factory=runtime,
        clock=lambda: AT,
    )

    observation = runner.collect(symbol="SPY", category="US_ETF", as_of=AT)

    assert adapter.calls == [("SPY", "US_ETF")]
    assert observation.event == event()
    assert observation.ingested_at == AT

    with runtime() as session:
        replay = MarketDataStore(session).replay(
            as_of=AT,
            requirements=(
                ReplayRequirement(
                    source=observation.event.source,
                    symbol="SPY",
                    stream="bar:1m",
                    max_age=timedelta(minutes=2),
                ),
            ),
        )
    assert replay.observations == (observation,)


def test_prior_session_or_old_bar_is_rejected_before_persistence(runtime):
    stale = event(
        event_time=AT - timedelta(hours=18),
        available_at=AT - timedelta(hours=18),
    )
    runner = DurableWebullPaperMarketRuntime(
        adapter=Adapter(stale),
        session_factory=runtime,
        clock=lambda: AT,
    )

    with pytest.raises(MarketFreshnessViolation, match="event is stale"):
        runner.collect(symbol="SPY", category="US_ETF", as_of=AT)

    with runtime() as session:
        assert session.query(MarketDataRecord).count() == 0


def test_delayed_collection_is_rejected(runtime):
    delayed = event(
        event_time=AT - timedelta(minutes=1),
        available_at=AT - timedelta(minutes=1),
    )
    runner = DurableWebullPaperMarketRuntime(
        adapter=Adapter(delayed),
        session_factory=runtime,
        policy=MarketFreshnessPolicy(
            max_event_age=timedelta(minutes=2),
            max_collection_delay=timedelta(seconds=30),
        ),
        clock=lambda: AT,
    )

    with pytest.raises(MarketFreshnessViolation, match="collection is stale"):
        runner.collect(symbol="SPY", category="US_ETF", as_of=AT)


def test_future_event_and_future_availability_fail_closed(runtime):
    for candidate, message in (
        (
            event(
                event_time=AT + timedelta(seconds=1),
                available_at=AT + timedelta(seconds=1),
            ),
            "event is in the future",
        ),
        (
            event(
                event_time=AT - timedelta(minutes=1),
                available_at=AT + timedelta(seconds=1),
            ),
            "not yet available",
        ),
    ):
        runner = DurableWebullPaperMarketRuntime(
            adapter=Adapter(candidate),
            session_factory=runtime,
            clock=lambda: AT,
        )
        with pytest.raises(MarketFreshnessViolation, match=message):
            runner.collect(symbol="SPY", category="US_ETF", as_of=AT)
