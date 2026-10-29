from datetime import UTC, datetime
from decimal import Decimal

import pytest

from src.market_data import MarketDataStore, ReplayRequirement
from src.persistence.models import MarketDataRecord
from src.webull_market_data import WebullMarketDataAdapter
from src.webull_sandbox_readonly import MalformedResponse


AT = datetime(2026, 10, 29, 15, 31, tzinfo=UTC)


class Response:
    def __init__(self, body, status_code=200):
        self._body = body
        self.status_code = status_code

    def json(self):
        return self._body


class Gateway:
    def __init__(self, *, snapshot=None, bars=None):
        self.snapshot = snapshot or [
            {
                "symbol": "SPY",
                "price": "688.12",
                "last_trade_time": 1793287800000,
            }
        ]
        self.bars = bars or {
            "symbol": "SPY",
            "result": [
                {
                    "time": "2026-10-29T15:30:00+00:00",
                    "open": "687.90",
                    "high": "688.30",
                    "low": "687.80",
                    "close": "688.12",
                    "volume": "125000",
                    "trading_sessions": "RTH",
                }
            ],
        }
        self.calls = []

    def get_snapshot(self, **kwargs):
        self.calls.append(("snapshot", kwargs))
        return Response(self.snapshot)

    def get_history_bar(self, **kwargs):
        self.calls.append(("bar", kwargs))
        return Response(self.bars)


def test_probe_fetches_snapshot_and_completed_rth_bar():
    gateway = Gateway()
    result = WebullMarketDataAdapter(gateway, clock=lambda: AT).probe(
        "SPY", category="US_ETF"
    )

    assert result.snapshot.symbol == "SPY"
    assert result.snapshot.price == Decimal("688.12")
    assert result.event.symbol == "SPY"
    assert result.event.event_time == datetime(2026, 10, 29, 15, 30, tzinfo=UTC)
    assert result.event.available_at == AT
    assert result.event.payload.close == Decimal("688.12")
    assert result.event.payload.volume == 125000
    assert gateway.calls[1] == (
        "bar",
        {
            "symbol": "SPY",
            "category": "US_ETF",
            "timespan": "M1",
            "count": "1",
            "real_time_required": False,
            "trading_sessions": "RTH",
        },
    )


def test_bar_can_be_persisted_and_replayed(sqlite_session):
    event = WebullMarketDataAdapter(Gateway(), clock=lambda: AT).fetch_completed_1m_bar(
        "SPY", category="US_ETF"
    )
    with sqlite_session.begin():
        observation = MarketDataStore(sqlite_session, clock=lambda: AT).ingest(event)

    replay = MarketDataStore(sqlite_session).replay(
        as_of=AT,
        requirements=(
            ReplayRequirement(
                source=event.source,
                symbol="SPY",
                stream="bar:1m",
                max_age=AT - event.event_time,
            ),
        ),
    )
    assert replay.observations == (observation,)


def test_future_provider_timestamp_is_rejected():
    gateway = Gateway(
        bars={
            "symbol": "SPY",
            "result": [
                {
                    "time": "2026-10-29T15:32:00+00:00",
                    "open": "1",
                    "high": "1",
                    "low": "1",
                    "close": "1",
                    "volume": "1",
                }
            ],
        }
    )
    with pytest.raises(MalformedResponse, match="collector future"):
        WebullMarketDataAdapter(gateway, clock=lambda: AT).fetch_completed_1m_bar(
            "SPY", category="US_ETF"
        )


@pytest.mark.parametrize(
    "field,value",
    [
        ("open", "0"),
        ("high", "nan"),
        ("low", "-1"),
        ("close", ""),
        ("volume", "1.5"),
    ],
)
def test_malformed_bar_values_fail_closed(field, value):
    row = {
        "time": "2026-10-29T15:30:00+00:00",
        "open": "10",
        "high": "11",
        "low": "9",
        "close": "10.5",
        "volume": "100",
    }
    row[field] = value
    gateway = Gateway(bars={"symbol": "SPY", "result": [row]})
    with pytest.raises((MalformedResponse, ValueError)):
        WebullMarketDataAdapter(gateway, clock=lambda: AT).fetch_completed_1m_bar(
            "SPY", category="US_ETF"
        )


def test_snapshot_requires_exact_requested_symbol():
    gateway = Gateway(snapshot=[{"symbol": "AAPL", "price": "200"}])
    with pytest.raises(MalformedResponse, match="requested symbol"):
        WebullMarketDataAdapter(gateway, clock=lambda: AT).fetch_snapshot(
            "SPY", category="US_ETF"
        )


@pytest.fixture
def sqlite_session():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    engine = create_engine("sqlite://")
    MarketDataRecord.__table__.create(engine)
    session = Session(engine)
    try:
        yield session
    finally:
        session.close()
        engine.dispose()
