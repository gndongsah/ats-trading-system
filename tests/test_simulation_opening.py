"""F11 offline timing and disposable-PostgreSQL capture contracts."""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

from src.ats009_accounting import PeriodKind
from src.ats009_calendar import ScheduleAttestation, XNYSCalendar
from src.persistence.loss_state_repository import LossStateConflict, LossStateRepository
from src.persistence.models import LossPeriodOpeningRecord, SourceDocumentRecord
from src.persistence.repository import PersistenceConflict
from src.simulation_ledger import EntryKind, SimulationEntry, SimulationLedger
from src.simulation_opening import (
    CaptureUnavailable,
    SealedLedger,
    SimulationCaptureLossProvider,
    SimulationOpeningCapture,
    report_correction,
    seal,
)
from tests import test_postgres_persistence as pg
from tests import test_simulation_ledger_loss_locks_postgres as f6

migrated_postgres_database = pg.migrated_postgres_database
migrated_postgres_engine = pg.migrated_postgres_engine
T = datetime(2026, 10, 19, 13, 30, tzinfo=UTC)


class Authority:
    def verified_status(self, day, version):
        midnight = datetime(day.year, day.month, day.day, tzinfo=UTC)
        return ScheduleAttestation(
            day,
            version,
            "SIMULATION-ONLY-F11",
            midnight,
            midnight + timedelta(days=1),
            False,
        )


class Clock:
    def __init__(self, observed=T, completed=None, elapsed=0):
        self.times = iter([observed, completed if completed is not None else observed])
        self.ticks = iter([0, elapsed])

    def now(self):
        return next(self.times)

    def monotonic(self):
        return next(self.ticks)


def calendar():
    return XNYSCalendar(Authority())


def ledger():
    return SimulationLedger(account_id=uuid4(), opened_at=T, opening_nlv="100000")


def service(observed=T, completed=None, elapsed=0):
    return SimulationOpeningCapture(calendar(), Clock(observed, completed, elapsed))


@pytest.fixture
def factory(migrated_postgres_engine):
    return sessionmaker(bind=migrated_postgres_engine, expire_on_commit=False)


def rows(factory, model):
    with factory() as session:
        return session.scalar(select(func.count()).select_from(model))


def capture_both(factory, book):
    return tuple(service().capture(factory, seal(book, T), kind) for kind in PeriodKind)


def state(factory, book, at):
    with factory.begin() as session:
        return SimulationCaptureLossProvider(calendar()).evaluate_in_transaction(
            session, book.account_id, at
        )


def test_seal_prefix_includes_all_same_cutoff_entries_and_excludes_later_events():
    book = SimulationLedger(
        account_id=uuid4(), opened_at=T - timedelta(seconds=1), opening_nlv="100000"
    )
    for identity, time, amount in [
        ("a", T, "1"),
        ("b", T, "2"),
        ("later", T + timedelta(seconds=1), "4"),
    ]:
        book = book.append(
            SimulationEntry(
                event_id=identity, occurred_at=time, amount=amount, kind=EntryKind.PNL
            )
        )
    sealed = seal(book, T)
    assert sealed.ledger.valuation(T).net_liquidation == Decimal(100003)
    assert [e.event_id for e in sealed.ledger.entries] == ["a", "b"]
    assert seal(book, T).digest == sealed.digest
    with pytest.raises(ValueError):
        SealedLedger(ledger=book, cutoff=T, complete=True)


@pytest.mark.parametrize(
    "day, hour, week",
    [
        (date(2026, 10, 30), 13, "2026-10-26"),
        (date(2026, 11, 2), 14, "2026-11-02"),
        (date(2026, 9, 8), 13, "2026-09-08"),
    ],
)
def test_calendar_dst_holiday_week_ids(day, hour, week):
    resolved = calendar().resolve(
        datetime(day.year, day.month, day.day, 15, tzinfo=UTC)
    )
    assert resolved.open_utc.hour == hour
    assert resolved.week_id.endswith(week)
    assert calendar().resolve(datetime(2026, 9, 7, 15, tzinfo=UTC)) is None


@pytest.mark.parametrize("lag", [0, 10])
def test_observation_boundary_inclusive(factory, lag):
    opening = service(T + timedelta(seconds=lag)).capture(
        factory, seal(ledger(), T), PeriodKind.DAILY
    )
    assert opening.opened_at == T
    assert rows(factory, SourceDocumentRecord) == 2


@pytest.mark.parametrize("lag", [-0.000001, 10.000001])
def test_observation_outside_window_no_writes(factory, lag):
    with pytest.raises(CaptureUnavailable):
        service(T + timedelta(seconds=lag)).capture(
            factory, seal(ledger(), T), PeriodKind.DAILY
        )
    assert (
        rows(factory, LossPeriodOpeningRecord)
        == rows(factory, SourceDocumentRecord)
        == 0
    )


@pytest.mark.parametrize("elapsed", [0, 60])
def test_completion_sixty_seconds_inclusive(factory, elapsed):
    service(T, T + timedelta(seconds=60), elapsed).capture(
        factory, seal(ledger(), T), PeriodKind.DAILY
    )
    assert rows(factory, SourceDocumentRecord) == 2


@pytest.mark.parametrize(
    "completed,elapsed",
    [
        (T + timedelta(seconds=60, microseconds=1), 60),
        (T, 60.000001),
        (T - timedelta(microseconds=1), 0),
        (T, 1),
    ],
)
def test_late_or_clock_rollback_quarantines_durable_opening(
    factory, completed, elapsed
):
    book = ledger()
    with pytest.raises(CaptureUnavailable):
        service(T, completed, elapsed).capture(factory, seal(book, T), PeriodKind.DAILY)
    assert (
        rows(factory, LossPeriodOpeningRecord) == 1
    )  # committed candidate, not usable
    assert state(factory, book, T + timedelta(seconds=1)) is None
    with pytest.raises(CaptureUnavailable):
        service().capture(factory, seal(book, T), PeriodKind.DAILY)


def test_replay_conflict_and_separate_daily_weekly_identity(factory):
    book = ledger()
    day, week = capture_both(factory, book)
    assert day.period_kind != week.period_kind
    assert day.opening_nlv == week.opening_nlv == Decimal(100000)
    assert rows(factory, LossPeriodOpeningRecord) == 2
    assert (
        service(T + timedelta(hours=1)).capture(
            factory, seal(book, T), PeriodKind.DAILY
        )
        == day
    )
    assert rows(factory, SourceDocumentRecord) == 4
    changed = SimulationLedger(
        account_id=book.account_id, opened_at=T, opening_nlv="99999"
    )
    with pytest.raises(LossStateConflict):
        service().capture(factory, seal(changed, T), PeriodKind.DAILY)
    assert rows(factory, LossPeriodOpeningRecord) == 2


def test_historical_replay_incomplete_and_wrong_cutoff_never_capture(factory):
    book = ledger()
    for sealed in [seal(book, T, replay=True), seal(book, T + timedelta(seconds=1))]:
        with pytest.raises(CaptureUnavailable):
            service().capture(factory, sealed, PeriodKind.DAILY)
    with pytest.raises(ValueError):
        SealedLedger(ledger=book, cutoff=T, complete=False)
    assert rows(factory, SourceDocumentRecord) == 0


def test_missing_schedule_authority_denied(factory):
    with pytest.raises(CaptureUnavailable):
        SimulationOpeningCapture(XNYSCalendar(), Clock()).capture(
            factory, seal(ledger(), T), PeriodKind.DAILY
        )
    assert rows(factory, LossPeriodOpeningRecord) == 0


def test_concurrent_capture_serializes_and_deduplicates(factory):
    book = ledger()
    with ThreadPoolExecutor(max_workers=3) as pool:
        results = tuple(
            pool.map(
                lambda _: service().capture(factory, seal(book, T), PeriodKind.DAILY),
                range(3),
            )
        )
    assert all(r == results[0] for r in results)
    assert rows(factory, LossPeriodOpeningRecord) == 1
    assert rows(factory, SourceDocumentRecord) == 2


def test_metadata_failure_rolls_back_opening(factory, monkeypatch):
    import src.simulation_opening as module

    def fail(*args, **kwargs):
        raise ValueError("metadata failed")

    monkeypatch.setattr(module, "_write", fail)
    with pytest.raises(ValueError, match="metadata failed"):
        service().capture(factory, seal(ledger(), T), PeriodKind.DAILY)
    assert (
        rows(factory, LossPeriodOpeningRecord)
        == rows(factory, SourceDocumentRecord)
        == 0
    )


def test_receipt_failure_leaves_candidate_quarantined(factory, monkeypatch):
    import src.simulation_opening as module

    original = module._write

    def fail(session, key, kind, payload, at):
        if kind == "SIM_OPENING_RECEIPT":
            raise ValueError("receipt failed")
        return original(session, key, kind, payload, at)

    book = ledger()
    monkeypatch.setattr(module, "_write", fail)
    with pytest.raises(ValueError, match="receipt failed"):
        service().capture(factory, seal(book, T), PeriodKind.DAILY)
    assert (
        rows(factory, LossPeriodOpeningRecord)
        == rows(factory, SourceDocumentRecord)
        == 1
    )
    assert state(factory, book, T + timedelta(seconds=1)) is None
    monkeypatch.setattr(module, "_write", original)
    service(T + timedelta(seconds=5)).capture(factory, seal(book, T), PeriodKind.DAILY)
    assert rows(factory, SourceDocumentRecord) == 2


@pytest.mark.parametrize(
    "age,allowed", [(0, True), (30, True), (30.000001, False), (-0.000001, False)]
)
def test_current_valuation_age_and_missing_receipt_gate(factory, age, allowed):
    book = ledger()
    capture_both(factory, book)
    f6.store(factory, book, T + timedelta(seconds=1))
    result = state(factory, book, T + timedelta(seconds=1 + age))
    assert (result is not None) == allowed


def test_correction_preserves_denominators_sticky_locks_and_denies_new_risk(factory):
    book = ledger()
    day, week = capture_both(factory, book)
    book = book.append(
        SimulationEntry(
            event_id="loss",
            occurred_at=T + timedelta(seconds=1),
            kind=EntryKind.PNL,
            amount="-1500",
        )
    )
    f6.store(factory, book, T + timedelta(seconds=2))
    assert state(factory, book, T + timedelta(seconds=3)).daily_locked
    identity = uuid4()
    for _ in range(2):
        with factory.begin() as session:
            report_correction(
                session,
                day,
                correction_id=identity,
                reason="restatement",
                candidate_nlv="110000",
                discovered_at=T + timedelta(seconds=4),
            )
    assert rows(factory, SourceDocumentRecord) == 5
    assert state(factory, book, T + timedelta(seconds=5)) is None
    with factory.begin() as session:
        original = LossStateRepository().rebuild_projection(
            session,
            account_id=book.account_id,
            active_daily_period_id=day.period_id,
            active_weekly_period_id=week.period_id,
        )
        assert original.daily_locked
        assert all(
            o.opening_equity == Decimal(100000)
            for o in session.scalars(select(LossPeriodOpeningRecord))
        )
        with pytest.raises(PersistenceConflict):
            report_correction(
                session,
                day,
                correction_id=identity,
                reason="changed",
                candidate_nlv="120000",
                discovered_at=T + timedelta(seconds=4),
            )


def test_daily_rollover_preserves_weekly_capture_and_new_week_requires_new_facts(
    factory,
):
    book = ledger()
    day, _week = capture_both(factory, book)
    tomorrow = T + timedelta(days=1)
    nextday = service(tomorrow).capture(factory, seal(book, tomorrow), PeriodKind.DAILY)
    assert nextday.period_id != day.period_id
    assert rows(factory, LossPeriodOpeningRecord) == 3
    f6.store(factory, book, tomorrow + timedelta(seconds=1))
    assert state(factory, book, tomorrow + timedelta(seconds=2)) is not None
    assert state(factory, book, T + timedelta(days=7, seconds=2)) is None


def test_changed_same_time_order_is_a_seal_conflict_even_if_nlv_is_equal(factory):
    original = SimulationLedger(
        account_id=uuid4(), opened_at=T - timedelta(seconds=1), opening_nlv="100000"
    )
    first = SimulationEntry(event_id="a", occurred_at=T, kind=EntryKind.PNL, amount="1")
    second = SimulationEntry(
        event_id="b", occurred_at=T, kind=EntryKind.PNL, amount="2"
    )
    book = original.append(first).append(second)
    opening = service().capture(factory, seal(book, T), PeriodKind.DAILY)
    assert opening.opening_nlv == Decimal(100003)
    with pytest.raises(LossStateConflict):
        service().capture(
            factory, seal(original.append(second).append(first), T), PeriodKind.DAILY
        )


def test_explicit_simulation_authority_required(factory):
    class OtherAuthority(Authority):
        def verified_status(self, day, version):
            from dataclasses import replace

            return replace(
                super().verified_status(day, version),
                authority_revision="BROKER-LOOKING-LABEL",
            )

    with pytest.raises(CaptureUnavailable, match="simulation schedule"):
        SimulationOpeningCapture(XNYSCalendar(OtherAuthority()), Clock()).capture(
            factory, seal(ledger(), T), PeriodKind.DAILY
        )
    assert rows(factory, LossPeriodOpeningRecord) == 0


def test_missing_receipt_denies_even_with_openings_and_current_loss_state(
    factory, monkeypatch
):
    import src.simulation_opening as module

    book = ledger()
    service().capture(factory, seal(book, T), PeriodKind.WEEKLY)
    original = module._write

    def fail(session, key, kind, payload, at):
        if kind == "SIM_OPENING_RECEIPT":
            raise ValueError("receipt unavailable")
        return original(session, key, kind, payload, at)

    monkeypatch.setattr(module, "_write", fail)
    with pytest.raises(ValueError):
        service().capture(factory, seal(book, T), PeriodKind.DAILY)
    f6.store(factory, book, T + timedelta(seconds=1))
    assert (
        f6.provider_state(factory, book.account_id, T + timedelta(seconds=2))
        is not None
    )
    assert state(factory, book, T + timedelta(seconds=2)) is None


@pytest.mark.parametrize(
    "kind,denied", [(PeriodKind.DAILY, False), (PeriodKind.WEEKLY, True)]
)
def test_correction_denial_scope_at_daily_rollover(factory, kind, denied):
    book = ledger()
    day, week = capture_both(factory, book)
    target = day if kind is PeriodKind.DAILY else week
    with factory.begin() as session:
        report_correction(
            session,
            target,
            correction_id=uuid4(),
            reason="cutoff restatement",
            candidate_nlv="90000",
            discovered_at=T + timedelta(seconds=1),
        )
    tomorrow = T + timedelta(days=1)
    service(tomorrow).capture(factory, seal(book, tomorrow), PeriodKind.DAILY)
    f6.store(factory, book, tomorrow + timedelta(seconds=1))
    assert (state(factory, book, tomorrow + timedelta(seconds=2)) is None) is denied


def test_new_week_separate_captures_follow_the_new_frozen_nlv(factory):
    book = ledger()
    capture_both(factory, book)
    book = book.append(
        SimulationEntry(
            event_id="loss",
            occurred_at=T + timedelta(seconds=1),
            kind=EntryKind.PNL,
            amount="-3000",
        )
    )
    f6.store(factory, book, T + timedelta(seconds=2))
    assert state(factory, book, T + timedelta(seconds=3)).weekly_locked
    next_week = T + timedelta(days=7)
    for kind in PeriodKind:
        opening = service(next_week).capture(factory, seal(book, next_week), kind)
        assert opening.opening_equity == Decimal(97000)
    book = book.append(
        SimulationEntry(
            event_id="next-week",
            occurred_at=next_week + timedelta(seconds=1),
            kind=EntryKind.PNL,
            amount="1",
        )
    )
    f6.store(factory, book, next_week + timedelta(seconds=2))
    current = state(factory, book, next_week + timedelta(seconds=3))
    assert (
        current is not None and not current.daily_locked and not current.weekly_locked
    )
    assert rows(factory, LossPeriodOpeningRecord) == 4
