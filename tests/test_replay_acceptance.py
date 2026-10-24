"""Multi-day SIMULATION-ONLY acceptance against isolated PostgreSQL schemas."""

import json
from datetime import timedelta
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import func, select

from src.domain.models import Regime, RiskIntent
from src.market_data import (
    MarketDataStore,
    MarketEvent,
    ReplaySnapshot,
    ReplayUnavailable,
)
from src.persistence.models import MarketDataRecord, RiskReservationEventRecord
from src.persistence.repository import JournalRepository
from src.portfolio import RiskDecision
from src.replay_risk import ReplaySimulationRiskService
from src.simulation_ledger import EntryKind, SimulationEntry, SimulationLedger
from src.simulation_opening import PeriodKind, seal
from tests import test_simulation_ledger_loss_locks_postgres as ledger_fixture
from tests import test_simulation_opening as opening_fixture
from tests import test_simulation_risk as risk_fixture
from tests.persistence_fixtures import make_complete_trade
from tests.test_market_data import EVENTS, T, requirements

migrated_postgres_database = risk_fixture.migrated_postgres_database
migrated_postgres_engine = risk_fixture.migrated_postgres_engine


def event(at, *, kind="bar", revision=1, available_at=None, close="100.5"):
    data = EVENTS[1 if kind == "bar" else 0].model_dump()
    data.update(event_time=at, available_at=available_at or at, revision=revision)
    if kind == "bar":
        data["payload"]["close"] = close
    return MarketEvent.model_validate(data)


class Harness:
    """Existing capture/accounting/risk engines; no policy arithmetic duplicated."""

    def __init__(self, engine):
        ctx = risk_fixture.setup(engine, quantity=10)
        self.factory, original, _, _, _ = ctx
        # setup already published its snapshot; each acceptance tick publishes
        # a new version even when its initial as-of timestamp is unchanged.
        self.snapshot = original.snapshot.model_copy(
            update={"version": original.snapshot.version + 1}
        )
        self.calendar = original._loss_locks.calendar
        self.taxonomy = original._taxonomy
        self.principal = original._principal_provider
        self.book = SimulationLedger(
            account_id=self.snapshot.account_id,
            opened_at=opening_fixture.T,
            opening_nlv="100000",
        )
        self.last = T
        self.steps = []

    def ingest(self, at, *events):
        with self.factory.begin() as session:
            store = MarketDataStore(session, clock=lambda: at)
            return tuple(store.ingest(item) for item in events)

    def replay(self, at):
        with self.factory() as session:
            return MarketDataStore(session).replay(
                as_of=at, requirements=requirements()
            )

    def count(self):
        return risk_fixture.count(self.factory)

    def advance(self, at):
        if at == self.last:
            return
        if at < self.last:
            raise ValueError("risk harness advances chronologically")
        if at.date() != self.last.date():
            resolved = self.calendar.resolve(at)
            opening_fixture.service(resolved.open_utc).capture(
                self.factory, seal(self.book, resolved.open_utc), PeriodKind.DAILY
            )
        # A new source sequence and valuation for each tick; constant tiny P&L
        # makes state advance without inferring profit from synthetic bars.
        self.book = self.book.append(
            SimulationEntry(
                event_id=f"valuation-{at.isoformat()}",
                occurred_at=at,
                amount="1",
                kind=EntryKind.PNL,
            )
        )
        ledger_fixture.store(self.factory, self.book, at)
        self.snapshot = self.snapshot.model_copy(
            update={
                "version": self.snapshot.version + 1,
                "as_of": at,
                "equity": self.book.valuation(at).net_liquidation,
            }
        )
        self.last = at

    def attempt(self, at, *, expected, label):
        self.advance(at)
        try:
            market = self.replay(at)
            unavailable = None
        except ReplayUnavailable as exc:
            market = ReplaySnapshot(as_of=at, observations=())
            unavailable = str(exc)
        service = ReplaySimulationRiskService(
            market_snapshot=market,
            requirements=requirements(),
            snapshot=self.snapshot,
            buying_power=Decimal(100000),
            calendar=self.calendar,
            taxonomy=self.taxonomy,
            principal_provider=self.principal,
            clock=lambda: at,
        )
        entry = make_complete_trade()
        plan = entry.plan.model_copy(
            update={
                "account_id": self.snapshot.account_id,
                "quantity": Decimal(10),
                "risk_intent": RiskIntent(stop_risk_budget=Decimal(25)),
                "score_snapshot": entry.plan.score_snapshot.model_copy(
                    update={
                        "regime": Regime.RISK_ON,
                        "id": uuid4(),
                    }
                ),
            }
        )
        with self.factory.begin() as session:
            service.publish_snapshot(session, self.snapshot)
            JournalRepository(session).save(entry.model_copy(update={"plan": plan}))
        before = self.count()
        result = service.authorize_with_retry(
            self.factory, plan_id=plan.id, idempotency_key=label
        )
        assert not result.execution_enabled
        if expected == "RESERVE":
            assert result.quantity == 10 and result.decision is not RiskDecision.DENY
            assert self.count() == before + 1
            with self.factory() as session:
                row = session.scalar(
                    select(RiskReservationEventRecord).where(
                        RiskReservationEventRecord.reservation_id
                        == result.reservation_id
                    )
                )
                assert row.event_data["market_replay_basis"] == {
                    "digest": market.digest,
                    "snapshot": market.model_dump(mode="json"),
                }
                assert row.event_data["simulation_basis"]["source"] == "SIMULATION-ONLY"
            again = service.authorize_with_retry(
                self.factory, plan_id=plan.id, idempotency_key=label
            )
            assert again.replayed and again.reservation_id == result.reservation_id
            assert not again.execution_enabled and self.count() == before + 1
        else:
            assert result.decision is RiskDecision.DENY and result.quantity == 0
            assert result.reason == "MARKET_DATA_UNAVAILABLE"
            assert unavailable and unavailable.startswith(expected)
            assert self.count() == before
        self.steps.append(
            {
                "label": label,
                "as_of": at.isoformat(),
                "expected": expected,
                "decision": result.decision.value,
                "quantity": result.quantity,
                "execution_enabled": result.execution_enabled,
                "replay_digest": market.digest if market.observations else None,
                "unavailable": unavailable,
                "reservation_count": self.count(),
                "provenance_verified": expected == "RESERVE",
            }
        )
        return market


def evidence(record_property, harness, **extra):
    record_property(
        "evidence",
        json.dumps(
            {
                "source": "SIMULATION-ONLY",
                "steps": harness.steps,
                **extra,
            },
            sort_keys=True,
        ),
    )


def test_three_days_preserve_yesterday_after_today_correction(
    migrated_postgres_engine, record_property
):
    h = Harness(migrated_postgres_engine)
    h.ingest(T, event(T), event(T, kind="reference"))
    yesterday = h.attempt(T, expected="RESERVE", label="monday")
    for day in (1, 2):
        at = T + timedelta(days=day)
        h.ingest(at, event(at), event(at, kind="reference"))
        if day == 1:
            h.ingest(at, event(T, revision=2, available_at=at, close="101"))
        h.attempt(at, expected="RESERVE", label=f"day-{day + 1}")
        assert h.replay(T) == yesterday
        assert h.replay(T).digest == yesterday.digest
    with h.factory() as session:
        revisions = session.scalar(
            select(func.count())
            .select_from(MarketDataRecord)
            .where(
                MarketDataRecord.event_time == T, MarketDataRecord.stream == "bar:1m"
            )
        )
        assert revisions == 2
    assert h.count() == 3
    evidence(
        record_property, h, yesterday_digest=yesterday.digest, preserved_revisions=2
    )


def test_delayed_arrival_denies_then_recovers_without_backdating(
    migrated_postgres_engine, record_property
):
    h = Harness(migrated_postgres_engine)
    h.ingest(T, event(T, kind="reference"))
    h.attempt(T, expected="MISSING", label="before-arrival")
    later = T + timedelta(seconds=8)
    original = h.ingest(later, event(T))[0]
    try:
        h.replay(T)
    except ReplayUnavailable as exc:
        assert str(exc).startswith("MISSING")
    else:
        raise AssertionError("late ingestion leaked into the earlier replay")
    market = h.attempt(later, expected="RESERVE", label="after-arrival")
    duplicate = h.ingest(later + timedelta(seconds=1), event(T))[0]
    assert duplicate == original and duplicate.ingested_at == later
    assert h.replay(later) == market
    evidence(
        record_property,
        h,
        first_ingested_at=later.isoformat(),
        duplicate_preserved=True,
    )


def test_stale_tick_denies_then_fresh_tick_recovers(
    migrated_postgres_engine, record_property
):
    h = Harness(migrated_postgres_engine)
    h.ingest(T, event(T), event(T, kind="reference"))
    h.attempt(T, expected="RESERVE", label="fresh")
    stale = T + timedelta(seconds=61)
    h.attempt(stale, expected="STALE", label="stale")
    fresh = stale + timedelta(seconds=1)
    h.ingest(fresh, event(fresh), event(fresh, kind="reference"))
    h.attempt(fresh, expected="RESERVE", label="recovered")
    # A newly ingested correction does not refresh the event's age.
    after = fresh + timedelta(seconds=61)
    h.ingest(after, event(fresh, revision=2, available_at=after, close="101"))
    h.attempt(after, expected="STALE", label="corrected-but-stale")
    evidence(record_property, h)


def test_next_day_missing_update_denies_until_new_bars_arrive(
    migrated_postgres_engine, record_property
):
    h = Harness(migrated_postgres_engine)
    h.ingest(T, event(T), event(T, kind="reference"))
    h.attempt(T, expected="RESERVE", label="day-one")
    tomorrow = T + timedelta(days=1)
    h.ingest(tomorrow, event(tomorrow, kind="reference"))
    h.attempt(tomorrow, expected="STALE", label="missing-next-day-bar")
    recovered = tomorrow + timedelta(seconds=1)
    h.ingest(recovered, event(recovered))
    h.attempt(recovered, expected="RESERVE", label="next-day-recovery")
    evidence(record_property, h)
