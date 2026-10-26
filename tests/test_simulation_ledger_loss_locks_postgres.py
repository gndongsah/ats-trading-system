"""ATS-009F6: SIMULATION-ONLY ledger scenarios through the persisted loss pipeline.

Synthetic ledgers feed the existing ``evaluate_loss_accounting`` policy and the
PostgreSQL ``LossStateRepository``. No threshold arithmetic is reimplemented
here. Nothing in this module is broker-authenticated evidence; it uses no
credentials, API routes, or order paths.
"""

from __future__ import annotations

import ast
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from fractions import Fraction
from hashlib import sha256
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

from src.ats009_accounting import (
    PeriodKind,
    PeriodOpeningEquity,
    evaluate_loss_accounting,
)
from src.ats009_calendar import XNYSCalendar
from src.layer3 import Layer3AuthorizationService, Layer3Reason
from src.persistence.loss_state_provider import RepositoryLossLockProvider
from src.persistence.loss_state_repository import (
    LossStateConflict,
    LossStateRepository,
)
from src.persistence.models import (
    LossExternalFlowRecord,
    LossPeriodOpeningRecord,
    LossStateEventRecord,
    LossValuationObservationRecord,
    RiskReservationEventRecord,
)
from src.persistence.transaction_retry import (
    TransactionRetryExhausted,
    run_in_transaction,
)
from src.portfolio import RiskDecision
from src.simulation_ledger import (
    SOURCE,
    EntryKind,
    SimulationEntry,
    SimulationLedger,
)
from tests.test_postgres_persistence import (  # noqa: F401  (pytest fixtures)
    POSTGRES_RISK_POLICY,
    POSTGRES_RISK_TAXONOMY,
    _postgres_layer3_setup,
    _PostgresPrincipalProvider,
    _PostgresSnapshotVerifier,
    migrated_postgres_database,
    migrated_postgres_engine,
)

CALENDAR = XNYSCalendar()
MON, TUE, WED, NEXT_MON = (date(2026, 10, d) for d in (19, 20, 21, 26))
MAX_AGE = timedelta(minutes=5)


def session_open(day: date) -> datetime:
    resolved = CALENDAR.resolve(
        datetime(day.year, day.month, day.day, 14, 0, tzinfo=UTC)
    )
    assert resolved is not None
    return resolved.open_utc


def t(day: date, minutes: int) -> datetime:
    return session_open(day) + timedelta(minutes=minutes)


def entry(event_id: str, at: datetime, amount: str, kind=EntryKind.PNL):
    return SimulationEntry(event_id=event_id, occurred_at=at, kind=kind, amount=amount)


def new_ledger(account_id: UUID, nlv: str = "100000") -> SimulationLedger:
    return SimulationLedger(
        account_id=account_id, opened_at=session_open(MON), opening_nlv=nlv
    )


def extend(ledger: SimulationLedger, *entries: SimulationEntry) -> SimulationLedger:
    for item in entries:
        ledger = ledger.append(item)
    return ledger


@dataclass(frozen=True)
class Inputs:
    parts: dict
    session: object
    evaluation: object
    as_of: datetime
    digest: str


def build_inputs(
    ledger: SimulationLedger,
    as_of: datetime,
    *,
    evaluated_at: datetime | None = None,
    tamper: Callable[[dict], dict] | None = None,
) -> Inputs:
    resolved = CALENDAR.resolve(as_of)
    assert resolved is not None
    session = replace(
        resolved,
        schedule_verified=True,
        authority_revision="SIMULATION-ONLY-CALENDAR-ATTESTATION",
        eligible_new_risk=True,
    )
    monday = as_of.date() - timedelta(days=as_of.weekday())
    week_open = session_open(monday)

    def opening(kind: PeriodKind, period_id: str, opened_at: datetime):
        frozen = ledger.valuation(opened_at)
        return PeriodOpeningEquity(
            account_id=ledger.account_id,
            period_kind=kind,
            period_id=period_id,
            calendar_version=session.calendar_version,
            opened_at=opened_at,
            opening_equity=frozen.net_liquidation,
            opening_nlv=frozen.net_liquidation,
            base_currency="USD",
            source_id=SOURCE,
            source_epoch=SOURCE,
            source_sequence=frozen.source_sequence,
            immutable_version=1,
            complete=True,
            reconciled=True,
            provenance_ref=SOURCE,
        )

    daily_opening = opening(PeriodKind.DAILY, session.session_id, resolved.open_utc)
    weekly_opening = opening(PeriodKind.WEEKLY, session.week_id, week_open)
    parts = {
        "valuation": ledger.valuation(as_of),
        "daily_opening": daily_opening,
        "weekly_opening": weekly_opening,
        "daily_flows": ledger.flows(daily_opening, as_of),
        "weekly_flows": ledger.flows(weekly_opening, as_of),
    }
    if tamper is not None:
        parts = tamper(parts)
    evaluation = evaluate_loss_accounting(
        session=session,
        evaluated_at=evaluated_at or as_of,
        max_valuation_age=MAX_AGE,
        **parts,
    )
    digest = sha256(
        f"{SOURCE}:{ledger.account_id}:{as_of.isoformat()}:{len(ledger.entries)}".encode()
    ).hexdigest()
    return Inputs(parts, session, evaluation, as_of, digest)


def persist(session: Session, repository: LossStateRepository, inputs: Inputs):
    parts = inputs.parts
    valuation = repository.append_valuation(session, parts["valuation"])
    daily = repository.append_opening(session, parts["daily_opening"])
    weekly = repository.append_opening(session, parts["weekly_opening"])
    flow_ids = {
        flow.flow_id: repository.append_external_flow(session, flow).record_id
        for flow in parts["weekly_flows"].flows
    }
    return repository.append_evaluation(
        session,
        evaluation=inputs.evaluation,
        valuation_id=valuation.record_id,
        daily_opening_id=daily.record_id,
        weekly_opening_id=weekly.record_id,
        daily_flow_ids=tuple(flow_ids[f.flow_id] for f in parts["daily_flows"].flows),
        weekly_flow_ids=tuple(flow_ids.values()),
        daily_period_id=inputs.session.session_id,
        weekly_period_id=inputs.session.week_id,
        input_digest=inputs.digest,
        created_at=inputs.as_of,
    )


def store(factory, ledger, as_of, **kwargs):
    inputs = build_inputs(ledger, as_of, **kwargs)
    with factory.begin() as session:
        events = persist(session, LossStateRepository(), inputs)
    return inputs, events


def kinds(events) -> list[str]:
    return [event.event_type for event in events]


def counts(factory) -> dict[str, int]:
    with factory() as session:
        return {
            name: session.scalar(select(func.count()).select_from(model))
            for name, model in (
                ("valuations", LossValuationObservationRecord),
                ("openings", LossPeriodOpeningRecord),
                ("flows", LossExternalFlowRecord),
                ("events", LossStateEventRecord),
            )
        }


def provider_state(factory, account_id, at):
    with factory.begin() as session:
        return RepositoryLossLockProvider().evaluate_in_transaction(
            session, account_id, at
        )


def projection(factory, account_id, inputs: Inputs):
    with factory.begin() as session:
        return LossStateRepository().rebuild_projection(
            session,
            account_id=account_id,
            active_daily_period_id=inputs.session.session_id,
            active_weekly_period_id=inputs.session.week_id,
        )


@pytest.fixture
def factory(migrated_postgres_engine: Engine):  # noqa: F811
    return sessionmaker(bind=migrated_postgres_engine, expire_on_commit=False)


# 1. Boundary breaches persist the correct locks -------------------------------


def test_f6_daily_minus_3r_inclusive_boundary_locks_daily_only(factory):
    account = uuid4()
    ledger = extend(new_ledger(account), entry("loss-1", t(MON, 10), "-1499.99999999"))
    first, events = store(factory, ledger, t(MON, 30))
    assert kinds(events) == ["OBSERVED"]  # just inside the limit: no lock
    assert not projection(factory, account, first).daily_locked

    ledger = extend(ledger, entry("loss-2", t(MON, 40), "-0.00000001"))
    second, events = store(factory, ledger, t(MON, 60))
    assert kinds(events) == ["OBSERVED", "LOCKED"]
    assert (events[-1].daily_r_numerator, events[-1].daily_r_denominator) == ("-3", "1")
    state = projection(factory, account, second)
    assert state.daily_locked and not state.weekly_locked
    lock = provider_state(factory, account, t(MON, 61))
    assert lock.daily_limit_r == Decimal(-3) and lock.weekly_limit_r == Decimal(-6)
    assert lock.daily_loss_r == Decimal(-3) and lock.blocks_risk_increase()


def test_f6_weekly_minus_6r_inclusive_boundary_locks_weekly_only(factory):
    account = uuid4()
    ledger = new_ledger(account)
    event_rows = []
    for day, name in ((MON, "mon"), (TUE, "tue")):
        ledger = extend(ledger, entry(name, t(day, 10), "-1000"))
        inputs, events = store(factory, ledger, t(day, 30))
        event_rows.extend(events)
        assert kinds(events) == ["OBSERVED"]
        assert not projection(factory, account, inputs).weekly_locked

    ledger = extend(ledger, entry("wed", t(WED, 10), "-1000"))
    inputs, events = store(factory, ledger, t(WED, 30))
    event_rows.extend(events)
    assert kinds(events) == ["OBSERVED", "LOCKED"]
    assert (events[-1].weekly_r_numerator, events[-1].weekly_r_denominator) == (
        "-6",
        "1",
    )
    state = projection(factory, account, inputs)
    assert state.weekly_locked and not state.daily_locked

    # One frozen weekly opening serves the week; each day froze its own opening.
    assert len({e.weekly_opening_id for e in event_rows}) == 1
    assert len({e.daily_opening_id for e in event_rows}) == 3
    assert counts(factory)["openings"] == 4


def test_f6_weekly_just_inside_limit_does_not_lock(factory):
    account = uuid4()
    ledger = new_ledger(account)
    for day, name, amount in (
        (MON, "a", "-1000"),
        (TUE, "b", "-1000"),
        (WED, "c", "-999.99999999"),
    ):
        ledger = extend(ledger, entry(name, t(day, 10), amount))
        inputs, events = store(factory, ledger, t(day, 30))
    assert kinds(events) == ["OBSERVED"]
    assert not projection(factory, account, inputs).weekly_locked


# 2. Sticky after recovery and service/session recreation ----------------------


def test_f6_lock_survives_nlv_recovery_and_service_recreation(
    factory,
    migrated_postgres_engine,  # noqa: F811
):
    account = uuid4()
    ledger = extend(new_ledger(account), entry("loss", t(MON, 10), "-1500"))
    store(factory, ledger, t(MON, 30))
    ledger = extend(ledger, entry("recovery", t(MON, 40), "1500"))
    inputs, events = store(factory, ledger, t(MON, 60))
    assert kinds(events) == ["OBSERVED"]  # already locked: no second LOCKED event
    assert inputs.parts["valuation"].net_liquidation == Decimal(100000)

    migrated_postgres_engine.dispose()  # drop every pooled connection
    fresh_factory = sessionmaker(bind=migrated_postgres_engine, expire_on_commit=False)
    with fresh_factory.begin() as session:
        state = RepositoryLossLockProvider(
            LossStateRepository(), XNYSCalendar()
        ).evaluate_in_transaction(session, account, t(MON, 61))
    assert state.state_version == 3
    assert state.daily_locked and not state.weekly_locked
    assert state.daily_loss_r == 0 and state.blocks_risk_increase()


def test_f6_layer3_denies_ledger_locked_account_without_reservation_rows(
    migrated_postgres_engine: Engine,  # noqa: F811
):
    factory, _, account, entries = _postgres_layer3_setup(migrated_postgres_engine)
    ledger = extend(new_ledger(account), entry("loss", t(MON, 10), "-1500"))
    store(factory, ledger, t(MON, 30))
    service = Layer3AuthorizationService(
        policy=POSTGRES_RISK_POLICY,
        taxonomy=POSTGRES_RISK_TAXONOMY,
        loss_locks=RepositoryLossLockProvider(),
        principal_provider=_PostgresPrincipalProvider(account),
        snapshot_verifier=_PostgresSnapshotVerifier(),
        broker_event_verifier=None,
        ats009_policy_version="ATS-009-v0.1",
        clock=lambda: t(MON, 31),
    )
    with factory.begin() as session:
        decision = service.authorize(
            session, plan_id=entries[0].plan.id, idempotency_key="f6-locked"
        )
    assert decision.decision is RiskDecision.DENY
    assert decision.reason == Layer3Reason.ATS009_LOSS_LOCKED.value
    with factory() as session:
        assert (
            session.scalar(select(func.count()).select_from(RiskReservationEventRecord))
            == 0
        )


# 3 and 4. Rollovers ------------------------------------------------------------


def _week_one_double_lock(factory, account):
    ledger = extend(new_ledger(account), entry("big-loss", t(MON, 10), "-3000"))
    inputs, events = store(factory, ledger, t(MON, 30))
    assert kinds(events) == ["OBSERVED", "LOCKED"]
    return ledger, inputs


def test_f6_next_session_clears_daily_lock_but_weekly_lock_persists(factory):
    account = uuid4()
    ledger, monday = _week_one_double_lock(factory, account)
    assert provider_state(factory, account, t(TUE, 31)) is None  # no Tuesday evidence

    ledger = extend(ledger, entry("tiny-gain", t(TUE, 10), "1"))
    _, events = store(factory, ledger, t(TUE, 30))
    assert kinds(events) == ["OBSERVED"]
    state = provider_state(factory, account, t(TUE, 31))
    assert not state.daily_locked
    assert state.weekly_locked and state.blocks_risk_increase()
    assert state.weekly_loss_r < Decimal(-5)  # above -6R, yet still sticky
    # The Monday daily lock remains in append-only history.
    assert projection(factory, account, monday).daily_locked


def test_f6_new_week_uses_separate_frozen_openings_and_clears_prior_locks(factory):
    account = uuid4()
    ledger, week_one = _week_one_double_lock(factory, account)
    ledger = extend(ledger, entry("tiny-gain", t(TUE, 10), "1"))
    store(factory, ledger, t(TUE, 30))
    assert provider_state(factory, account, t(NEXT_MON, 31)) is None

    # 3 * 0.5% of the new frozen 97001 is exactly 1455.015; 100000 would not breach.
    ledger = extend(ledger, entry("new-week-loss", t(NEXT_MON, 10), "-1455.015"))
    inputs, events = store(factory, ledger, t(NEXT_MON, 30))
    assert kinds(events) == ["OBSERVED", "LOCKED"]
    daily_open = inputs.parts["daily_opening"]
    weekly_open = inputs.parts["weekly_opening"]
    assert daily_open.opening_equity == weekly_open.opening_equity == Decimal(97001)
    assert daily_open.period_kind is not weekly_open.period_kind
    state = projection(factory, account, inputs)
    assert state.daily_locked and not state.weekly_locked
    assert events[0].daily_opening_id != events[0].weekly_opening_id
    assert projection(factory, account, week_one).weekly_locked  # prior week intact
    with factory() as session:
        equities = {
            (row.period_kind, row.period_id): row.opening_equity
            for row in session.scalars(select(LossPeriodOpeningRecord))
        }
    assert equities[("WEEKLY", week_one.session.week_id)] == Decimal(100000)
    assert equities[("WEEKLY", inputs.session.week_id)] == Decimal(97001)


# 5. External flows are not trading P&L; fees are ------------------------------

FLOW = EntryKind.EXTERNAL_FLOW
PNL = EntryKind.PNL


@pytest.mark.parametrize(
    ("steps", "expected_pnl", "locked"),
    [
        pytest.param([("dep", FLOW, "100000")], 0, False, id="deposit-not-profit"),
        pytest.param([("wd", FLOW, "-50000")], 0, False, id="withdrawal-not-loss"),
        pytest.param(
            [("dep", FLOW, "10000"), ("loss", PNL, "-1500")],
            -1500,
            True,
            id="deposit-does-not-mask-loss",
        ),
        pytest.param(
            [("wd", FLOW, "-50000"), ("loss", PNL, "-1500")],
            -1500,
            True,
            id="withdrawal-keeps-frozen-1R-basis",
        ),
        pytest.param([("fee", PNL, "-1500")], -1500, True, id="fee-counts-as-loss"),
        pytest.param(
            [("dep", FLOW, "5000"), ("fee", PNL, "-1")],
            -1,
            False,
            id="fee-counts-deposit-does-not",
        ),
    ],
)
def test_f6_flows_excluded_from_pnl_and_fees_included(
    factory, steps, expected_pnl, locked
):
    account = uuid4()
    ledger = new_ledger(account)
    for index, (name, kind, amount) in enumerate(steps, start=1):
        ledger = extend(ledger, entry(name, t(MON, 10 * index), amount, kind))
    inputs, events = store(factory, ledger, t(MON, 60))
    observed = events[0]
    assert Fraction(
        int(observed.daily_pnl_numerator), int(observed.daily_pnl_denominator)
    ) == Fraction(expected_pnl)
    assert Fraction(
        int(observed.weekly_pnl_numerator), int(observed.weekly_pnl_denominator)
    ) == Fraction(expected_pnl)
    assert projection(factory, account, inputs).daily_locked is locked
    flow_steps = [s for s in steps if s[1] is FLOW]
    assert counts(factory)["flows"] == len(flow_steps)
    assert len(observed.flow_evidence["daily"]) == len(flow_steps)
    assert len(observed.flow_evidence["weekly"]) == len(flow_steps)


def test_f6_all_persisted_evidence_is_simulation_only(factory):
    account = uuid4()
    ledger = extend(
        new_ledger(account),
        entry("dep", t(MON, 10), "5000", FLOW),
        entry("loss", t(MON, 20), "-1500"),
    )
    store(factory, ledger, t(MON, 60))
    with factory() as session:
        for model in (
            LossValuationObservationRecord,
            LossPeriodOpeningRecord,
            LossExternalFlowRecord,
        ):
            rows = session.scalars(select(model)).all()
            assert rows
            for row in rows:
                assert row.source_id == SOURCE
                assert row.source_epoch == SOURCE
                assert row.provenance_ref == SOURCE


# 6. Replay and conflicting identities -----------------------------------------


def test_f6_duplicate_replay_creates_no_duplicate_state_or_evidence(factory):
    account = uuid4()
    ledger = extend(
        new_ledger(account),
        entry("dep", t(MON, 10), "2000", FLOW),
        entry("loss", t(MON, 20), "-1500"),
    )
    inputs, events = store(factory, ledger, t(MON, 30))
    assert kinds(events) == ["OBSERVED", "LOCKED"]
    baseline = counts(factory)

    ledger_replay = ledger.append(entry("loss", t(MON, 20), "-1500"))
    assert ledger_replay is ledger  # ledger replay is also idempotent
    for _ in range(2):
        _, replay = store(factory, ledger_replay, t(MON, 30))
        assert [e.id for e in replay] == [events[0].id]
        assert counts(factory) == baseline
    assert projection(factory, account, inputs).state_version == 2


def test_f6_conflicting_identities_fail_closed_and_roll_back(factory):
    account = uuid4()
    ledger = extend(
        new_ledger(account),
        entry("dep", t(MON, 10), "2000", FLOW),
        entry("loss", t(MON, 20), "-1000"),
    )
    original, _ = store(factory, ledger, t(MON, 30))
    baseline = counts(factory)

    # Same event IDs, different loss: conflicting valuation source identity.
    conflicting = extend(
        new_ledger(account),
        entry("dep", t(MON, 10), "2000", FLOW),
        entry("loss", t(MON, 20), "-1600"),
    )
    with pytest.raises(LossStateConflict, match="conflicting replay"):
        store(factory, conflicting, t(MON, 30))
    assert counts(factory) == baseline

    # Same valuation sequence observed at a different time.
    with pytest.raises(LossStateConflict, match="conflicting replay"):
        store(factory, ledger, t(MON, 31))
    assert counts(factory) == baseline

    # Same flow identity, different amount.
    altered_flow = (
        original.parts["weekly_flows"]
        .flows[0]
        .model_copy(update={"signed_amount": Decimal(2500)})
    )
    with (
        pytest.raises(LossStateConflict, match="conflicting replay"),
        factory.begin() as session,
    ):
        LossStateRepository().append_external_flow(session, altered_flow)
    assert counts(factory) == baseline

    # Same period, different frozen opening.
    altered_opening = original.parts["daily_opening"].model_copy(
        update={"opening_nlv": Decimal(99999)}
    )
    with (
        pytest.raises(LossStateConflict, match="conflicting replay"),
        factory.begin() as session,
    ):
        LossStateRepository().append_opening(session, altered_opening)
    assert counts(factory) == baseline


# 7. Rollback and bounded retries ----------------------------------------------

OTHER_LOCK = select(func.pg_advisory_xact_lock(func.hashtextextended("f6-other", 0)))


def _locked_ledger(account):
    return extend(
        new_ledger(account),
        entry("dep", t(MON, 10), "2000", FLOW),
        entry("loss", t(MON, 20), "-1500"),
    )


def test_f6_non_retryable_failure_rolls_back_all_partial_writes(factory):
    account = uuid4()
    inputs = build_inputs(_locked_ledger(account), t(MON, 30))
    attempts = []

    def work(session):
        attempts.append(1)
        persist(session, LossStateRepository(), inputs)
        session.execute(text("SELECT 1/0"))  # SQLSTATE 22012: never retried

    with pytest.raises(DBAPIError):
        run_in_transaction(factory, work, max_attempts=3)
    assert len(attempts) == 1
    assert counts(factory) == dict.fromkeys(counts(factory), 0)


def test_f6_transient_failure_after_writes_retries_with_same_request_identity(
    factory,
):
    account = uuid4()
    inputs = build_inputs(_locked_ledger(account), t(MON, 30))
    holder = factory()
    holder.begin()
    holder.execute(OTHER_LOCK)
    opened = []
    identities = []

    def tracking_factory():
        opened.append(1)
        if len(opened) == 2:
            holder.rollback()  # release the contended lock between attempts
            holder.close()
        return factory()

    def work(session):
        identities.append(
            (
                inputs.digest,
                inputs.parts["valuation"].source_sequence,
                inputs.as_of,
            )
        )
        session.execute(text("SET LOCAL lock_timeout = '150ms'"))
        events = persist(session, LossStateRepository(), inputs)
        session.execute(OTHER_LOCK)  # times out on attempt 1, after all writes
        return events

    try:
        events = run_in_transaction(tracking_factory, work, max_attempts=3)
    finally:
        if holder.in_transaction():
            holder.rollback()
        holder.close()
    assert len(opened) == 2 and len(identities) == 2
    assert len(set(identities)) == 1  # original request identity preserved
    assert kinds(events) == ["OBSERVED", "LOCKED"]
    assert counts(factory) == {
        "valuations": 1,
        "openings": 2,
        "flows": 1,
        "events": 2,
    }  # exactly one logical write set; attempt 1 left nothing behind


def test_f6_retry_exhaustion_fails_closed_without_partial_records(factory):
    account = uuid4()
    inputs = build_inputs(_locked_ledger(account), t(MON, 30))
    holder = factory()
    holder.begin()
    holder.execute(OTHER_LOCK)
    attempts = []

    def work(session):
        attempts.append(1)
        session.execute(text("SET LOCAL lock_timeout = '100ms'"))
        persist(session, LossStateRepository(), inputs)
        session.execute(OTHER_LOCK)

    try:
        with pytest.raises(TransactionRetryExhausted) as info:
            run_in_transaction(factory, work, max_attempts=2)
    finally:
        holder.rollback()
        holder.close()
    assert info.value.attempts == 2 and info.value.sqlstate == "55P03"
    assert len(attempts) == 2
    assert counts(factory) == dict.fromkeys(counts(factory), 0)
    assert provider_state(factory, account, t(MON, 31)) is None


# 8. Missing or stale evidence denies new risk ---------------------------------


def _unavailable_case(
    factory, ledger, account, as_of, reason, match="unavailable", **kwargs
):
    inputs = build_inputs(ledger, as_of, **kwargs)
    assert not inputs.evaluation.available
    assert reason in inputs.evaluation.unavailable_reasons
    with pytest.raises(LossStateConflict, match=match), factory.begin() as session:
        persist(session, LossStateRepository(), inputs)
    assert counts(factory) == dict.fromkeys(counts(factory), 0)
    assert provider_state(factory, account, as_of + timedelta(minutes=1)) is None


def test_f6_stale_valuation_is_unavailable_and_creates_no_state(factory):
    account = uuid4()
    ledger = extend(new_ledger(account), entry("loss", t(MON, 10), "-1500"))
    _unavailable_case(
        factory,
        ledger,
        account,
        t(MON, 30),
        "VALUATION_STALE_OR_FROM_FUTURE",
        evaluated_at=t(MON, 30) + timedelta(minutes=6),
    )


def test_f6_incomplete_flow_reconciliation_is_unavailable(factory):
    account = uuid4()
    ledger = extend(
        new_ledger(account),
        entry("dep", t(MON, 10), "1000", FLOW),
        entry("loss", t(MON, 20), "-1500"),
    )

    def tamper(parts):
        parts["daily_flows"] = parts["daily_flows"].model_copy(
            update={"complete": False}
        )
        return parts

    _unavailable_case(
        factory,
        ledger,
        account,
        t(MON, 30),
        "FLOW_RECONCILIATION_INCOMPLETE",
        tamper=tamper,
    )


def test_f6_stale_opening_period_is_unavailable(factory):
    account = uuid4()
    ledger = extend(new_ledger(account), entry("loss", t(TUE, 10), "-1500"))

    def tamper(parts):
        stale = f"XNYS:{MON.isoformat()}"
        parts["daily_opening"] = parts["daily_opening"].model_copy(
            update={"period_id": stale}
        )
        return parts

    _unavailable_case(
        factory,
        ledger,
        account,
        t(TUE, 30),
        "DAILY_PERIOD_MISMATCH",
        match="opening period ID does not match",
        tamper=tamper,
    )


def test_f6_unreconciled_valuation_is_unavailable(factory):
    account = uuid4()
    ledger = extend(new_ledger(account), entry("loss", t(MON, 10), "-1500"))

    def tamper(parts):
        parts["valuation"] = parts["valuation"].model_copy(update={"reconciled": False})
        return parts

    _unavailable_case(
        factory,
        ledger,
        account,
        t(MON, 30),
        "VALUATION_INCOMPLETE_OR_UNRECONCILED",
        tamper=tamper,
    )


def test_f6_missing_daily_opening_evidence_yields_no_lock_state(factory):
    account = uuid4()
    ledger = extend(new_ledger(account), entry("loss", t(MON, 10), "-1500"))
    inputs = build_inputs(ledger, t(MON, 30))
    with factory.begin() as session:
        repository = LossStateRepository()
        repository.append_valuation(session, inputs.parts["valuation"])
        repository.append_opening(session, inputs.parts["weekly_opening"])
    assert provider_state(factory, account, t(MON, 31)) is None
    with pytest.raises(LossStateConflict, match="opening evidence"):
        projection(factory, account, inputs)
    assert counts(factory)["events"] == 0


def test_f6_layer3_denies_when_no_ledger_loss_state_exists(
    migrated_postgres_engine: Engine,  # noqa: F811
):
    factory, _, account, entries = _postgres_layer3_setup(migrated_postgres_engine)
    service = Layer3AuthorizationService(
        policy=POSTGRES_RISK_POLICY,
        taxonomy=POSTGRES_RISK_TAXONOMY,
        loss_locks=RepositoryLossLockProvider(),
        principal_provider=_PostgresPrincipalProvider(account),
        snapshot_verifier=_PostgresSnapshotVerifier(),
        broker_event_verifier=None,
        ats009_policy_version="ATS-009-v0.1",
        clock=lambda: t(MON, 31),
    )
    with factory.begin() as session:
        decision = service.authorize(
            session, plan_id=entries[0].plan.id, idempotency_key="f6-missing"
        )
    assert decision.decision is RiskDecision.DENY
    with factory() as session:
        assert (
            session.scalar(select(func.count()).select_from(RiskReservationEventRecord))
            == 0
        )


# Isolation of scope ------------------------------------------------------------


def test_f6_module_has_no_broker_credential_or_order_dependencies():
    tree = ast.parse(Path(__file__).read_text(encoding="utf-8"))
    imported = {
        alias.name.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    } | {
        (node.module or "").split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
    }
    assert not imported & {"webull", "keyring", "requests", "httpx", "fastapi"}
    assert SOURCE == "SIMULATION-ONLY"
