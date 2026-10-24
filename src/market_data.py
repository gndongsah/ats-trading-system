"""ATS-010 offline adapter, immutable revisions and point-in-time replay.

The ingestion clock belongs to the collector, never the provider payload.
All replay predicates are applied before revision selection.
"""

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from hashlib import sha256
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictInt,
    field_validator,
    model_validator,
)
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from src.persistence.models import MarketDataRecord


def utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError("timestamp must be timezone-aware UTC")
    return value.astimezone(UTC)


class FrozenModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Bar(FrozenModel):
    kind: Literal["bar"] = "bar"
    interval: Literal["1m", "1d"]
    open: Decimal = Field(gt=0, allow_inf_nan=False)
    high: Decimal = Field(gt=0, allow_inf_nan=False)
    low: Decimal = Field(gt=0, allow_inf_nan=False)
    close: Decimal = Field(gt=0, allow_inf_nan=False)
    volume: StrictInt = Field(ge=0)

    @model_validator(mode="after")
    def valid_range(self):
        if (
            not self.low
            <= min(self.open, self.close)
            <= max(self.open, self.close)
            <= self.high
        ):
            raise ValueError("invalid OHLC range")
        return self


class Reference(FrozenModel):
    kind: Literal["reference"] = "reference"
    currency: Literal["USD"]
    exchange: str = Field(min_length=1, max_length=32)
    asset_class: Literal["equity"]
    active: bool


class MarketEvent(FrozenModel):
    source: str = Field(min_length=1, max_length=128)
    symbol: str = Field(pattern=r"^[A-Z][A-Z0-9.-]{0,31}$")
    event_time: datetime
    available_at: datetime
    revision: StrictInt = Field(ge=1)
    payload: Annotated[Bar | Reference, Field(discriminator="kind")]

    _timestamps = field_validator("event_time", "available_at")(utc)

    @property
    def stream(self):
        return (
            f"bar:{self.payload.interval}"
            if isinstance(self.payload, Bar)
            else "reference"
        )


class Observation(FrozenModel):
    event: MarketEvent
    ingested_at: datetime
    _timestamp = field_validator("ingested_at")(utc)

    @model_validator(mode="after")
    def valid_clock(self):
        if self.ingested_at < self.event.available_at:
            raise ValueError("ingestion cannot precede availability")
        if (
            isinstance(self.event.payload, Bar)
            and self.event.available_at < self.event.event_time
        ):
            raise ValueError("completed bar cannot be available before its end")
        return self


class ReplayRequirement(FrozenModel):
    source: str = Field(min_length=1, max_length=128)
    symbol: str = Field(pattern=r"^[A-Z][A-Z0-9.-]{0,31}$")
    stream: Literal["bar:1m", "bar:1d", "reference"]
    max_age: timedelta = Field(ge=timedelta(0))


class ReplayUnavailable(ValueError):
    pass


class RevisionConflict(ValueError):
    pass


class ReplaySnapshot(FrozenModel):
    as_of: datetime
    observations: tuple[Observation, ...]
    _timestamp = field_validator("as_of")(utc)

    @property
    def digest(self):
        return sha256(self.model_dump_json().encode()).hexdigest()


class JsonMarketDataAdapter:
    """Version 1 normalized JSON adapter for synthetic/research files.

    Bar event_time is the completed bar END, prices are unadjusted. Source is
    configured by the caller; input cannot supply source or ingestion time.
    Provider revision numbers must be monotonic for a logical event. Unknown
    publication times must be set conservatively to collection time upstream.
    """

    def __init__(self, source="synthetic-json-v1"):
        self.source = source

    def parse(self, document: str) -> tuple[MarketEvent, ...]:
        rows = json.loads(document)
        if not isinstance(rows, list):
            raise TypeError("adapter expects a JSON array")
        return tuple(MarketEvent(source=self.source, **row) for row in rows)


class MarketDataStore:
    def __init__(self, session, *, clock=lambda: datetime.now(UTC)):
        self.session = session
        self.clock = clock

    def ingest(self, event: MarketEvent) -> Observation:
        """Idempotent insert; conflicting same-revision data is never updated.

        Caller owns the transaction. Collector clock injection is for offline
        fixtures only; production callers must use the real clock.
        """
        if not self.session.in_transaction():
            raise RuntimeError("ingestion requires a caller-owned transaction")
        event = MarketEvent.model_validate(event.model_dump())
        observation = Observation(event=event, ingested_at=self.clock())
        values = {
            "source": event.source,
            "symbol": event.symbol,
            "stream": event.stream,
            "event_time": event.event_time,
            "revision": event.revision,
            "available_at": event.available_at,
            "ingested_at": observation.ingested_at,
            "event_data": event.model_dump(mode="json"),
        }
        dialect = self.session.get_bind().dialect.name
        insert = {"postgresql": pg_insert, "sqlite": sqlite_insert}.get(dialect)
        if insert is None:
            raise RuntimeError("market store supports PostgreSQL and SQLite")
        self.session.execute(
            insert(MarketDataRecord).values(**values).on_conflict_do_nothing()
        )
        row = self.session.get(
            MarketDataRecord,
            (
                event.source,
                event.symbol,
                event.stream,
                event.event_time,
                event.revision,
            ),
            populate_existing=True,
        )
        if row.event_data != values["event_data"]:
            raise RevisionConflict("same event revision has different content")
        return self._decode(row)

    @staticmethod
    def _decode(row):
        # SQLite strips tzinfo on roundtrip; UTC is the storage contract.
        at = row.ingested_at
        if at.tzinfo is None:
            at = at.replace(tzinfo=UTC)
        return Observation(
            event=MarketEvent.model_validate(row.event_data), ingested_at=at
        )

    def replay(
        self, *, as_of: datetime, requirements: tuple[ReplayRequirement, ...]
    ) -> ReplaySnapshot:
        as_of = utc(as_of)
        if not requirements:
            raise ValueError("explicit replay requirements are required")
        keys = [(r.source, r.symbol, r.stream) for r in requirements]
        if len(set(keys)) != len(keys):
            raise ValueError("duplicate replay requirement")
        observations = []
        for req in sorted(requirements, key=lambda r: (r.source, r.symbol, r.stream)):
            # Three independent clocks are bounded BEFORE choosing a revision.
            row = self.session.scalar(
                select(MarketDataRecord)
                .where(
                    MarketDataRecord.source == req.source,
                    MarketDataRecord.symbol == req.symbol,
                    MarketDataRecord.stream == req.stream,
                    MarketDataRecord.event_time <= as_of,
                    MarketDataRecord.available_at <= as_of,
                    MarketDataRecord.ingested_at <= as_of,
                )
                .order_by(
                    MarketDataRecord.event_time.desc(), MarketDataRecord.revision.desc()
                )
                .limit(1)
            )
            if row is None:
                raise ReplayUnavailable(f"MISSING:{req.symbol}:{req.stream}")
            observation = self._decode(row)
            if as_of - observation.event.event_time > req.max_age:
                raise ReplayUnavailable(f"STALE:{req.symbol}:{req.stream}")
            observations.append(observation)
        return ReplaySnapshot(as_of=as_of, observations=tuple(observations))
