"""ATS-036B durable Webull market-data collection for PAPER forward testing.

The runtime owns the collection/persistence transaction boundary. Freshness is
validated before an event is persisted or exposed to the forward-test
orchestrator. Webull remains market-data-only; execution stays in the internal
PAPER adapter.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Callable

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session, sessionmaker

from src.market_data import MarketDataStore, Observation
from src.webull_market_data import WebullMarketDataAdapter


class MarketFreshnessPolicy(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    max_event_age: timedelta = Field(
        default=timedelta(minutes=2), gt=timedelta(0)
    )
    max_collection_delay: timedelta = Field(
        default=timedelta(seconds=30), ge=timedelta(0)
    )


class MarketFreshnessViolation(PermissionError):
    """The provider event is not safe to use for a current PAPER decision."""


class DurableWebullPaperMarketRuntime:
    """Fetch, freshness-check, commit and restore one completed Webull bar."""

    def __init__(
        self,
        *,
        adapter: WebullMarketDataAdapter,
        session_factory: sessionmaker[Session],
        policy: MarketFreshnessPolicy = MarketFreshnessPolicy(),
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._adapter = adapter
        self._session_factory = session_factory
        self._policy = policy
        self._clock = clock

    def collect(
        self,
        *,
        symbol: str,
        category: str,
        as_of: datetime | None = None,
    ) -> Observation:
        evaluated_at = self._utc(as_of or self._clock())
        event = self._adapter.fetch_completed_1m_bar(symbol, category=category)
        self._validate_freshness(event_time=event.event_time, available_at=event.available_at, evaluated_at=evaluated_at)

        with self._session_factory.begin() as session:
            observation = MarketDataStore(
                session,
                clock=lambda: self._utc(self._clock()),
            ).ingest(event)

        # Re-read through a new Session so a successful return proves the
        # observation survived the commit boundary rather than only the ORM map.
        with self._session_factory() as session:
            row = session.get(
                __import__("src.persistence.models", fromlist=["MarketDataRecord"]).MarketDataRecord,
                (
                    event.source,
                    event.symbol,
                    event.stream,
                    event.event_time,
                    event.revision,
                ),
            )
            if row is None:
                raise RuntimeError("persisted market observation was not durable")
            restored = MarketDataStore._decode(row)

        if restored != observation:
            raise RuntimeError("persisted market observation changed after commit")
        return restored

    def _validate_freshness(
        self,
        *,
        event_time: datetime,
        available_at: datetime,
        evaluated_at: datetime,
    ) -> None:
        event_time = self._utc(event_time)
        available_at = self._utc(available_at)

        if event_time > evaluated_at:
            raise MarketFreshnessViolation("market event is in the future")
        if available_at > evaluated_at:
            raise MarketFreshnessViolation("market data is not yet available")
        if available_at < event_time:
            raise MarketFreshnessViolation("market availability predates event")

        event_age = evaluated_at - event_time
        if event_age > self._policy.max_event_age:
            raise MarketFreshnessViolation("market event is stale")

        collection_delay = evaluated_at - available_at
        if collection_delay > self._policy.max_collection_delay:
            raise MarketFreshnessViolation("market collection is stale")

    @staticmethod
    def _utc(value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("runtime clock must be timezone-aware")
        return value.astimezone(UTC)
