import os
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from fractions import Fraction
from hashlib import sha256
from pathlib import Path
from threading import Barrier
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, delete, func, insert, select, text, update
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import sessionmaker

from src.ats009_accounting import (
    ExternalFlowKind,
    ExternalFlowReconciliation,
    PeriodKind,
    PeriodOpeningEquity,
    ReconciledExternalFlow,
    VerifiedAccountValuation,
    evaluate_loss_accounting,
)
from src.ats009_calendar import XNYSCalendar
from src.database import install_utc_session_timezone
from src.domain.evidence import EvidenceItem, SourceDocument
from src.domain.models import (
    EvidenceStatus,
    OrderAction,
    OrderIntent,
    OrderType,
    RiskIntent,
)
from src.evidence_verification import (
    evaluate_scoring_eligibility,
    score_contribution_from_eligibility,
)
from src.layer3 import (
    AuthenticatedAccountPrincipal,
    BrokerFillEvent,
    BrokerOrderStatus,
    BrokerOrderUpdate,
    BrokerStopConfirmation,
    Layer3AuthorizationService,
    Layer3Reason,
    LossLockState,
    VerifiedBrokerOrderUpdate,
    VerifiedBrokerStopConfirmation,
)
from src.persistence.evidence_repository import EvidenceRepository
from src.persistence.loss_state_provider import RepositoryLossLockProvider
from src.persistence.loss_state_repository import (
    LossStateConflict,
    LossStateRepository,
)
from src.persistence.models import (
    EvidenceItemRecord,
    FillRecord,
    JournalEntryRecord,
    LossPeriodOpeningRecord,
    LossStateEventRecord,
    LossValuationObservationRecord,
    OrderIntentRecord,
    PortfolioSnapshotRecord,
    PositionSnapshotRecord,
    RiskReservationEventRecord,
    ScoreSnapshotRecord,
    SourceDocumentRecord,
    TradePlanRecord,
    TradeRecord,
    TradeResultRecord,
)
from src.persistence.repository import (
    JournalRepository,
    PersistenceConflict,
    decode_score_snapshot,
)
from src.persistence.transaction_retry import (
    TransactionRetryExhausted,
    run_in_transaction,
)
from src.portfolio import (
    PortfolioRiskPolicy,
    PortfolioSnapshot,
    PositionExposure,
    RiskClassification,
    RiskDecision,
    RiskTaxonomy,
)
from tests.persistence_fixtures import make_complete_trade

POSTGRES_RISK_NOW = datetime.now(UTC)
POSTGRES_RISK_POLICY = PortfolioRiskPolicy(
    version="risk-policy-postgres-test-v1",
    snapshot_max_age=timedelta(minutes=5),
)
POSTGRES_RISK_TAXONOMY = RiskTaxonomy(
    version="taxonomy-postgres-test-v1",
    classifications={
        "ABC": RiskClassification(theme="AI_NETWORKING", factors=("AI_CAPEX",)),
        "XYZ": RiskClassification(theme="INDUSTRIALS", factors=("INDUSTRIAL",)),
    },
)


class _PostgresPassingLossLocks:
    def __init__(self, account_id: UUID):
        self._account_id = account_id

    def evaluate(self, account_id: UUID, at: datetime) -> LossLockState:
        assert account_id == self._account_id
        return LossLockState(
            account_id=account_id,
            as_of=at,
            policy_version="ats-009-postgres-test-v1",
            session_id="test-session",
            week_id="test-week",
            daily_loss_r=Decimal(0),
            weekly_loss_r=Decimal(0),
            daily_limit_r=Decimal(-3),
            weekly_limit_r=Decimal(-6),
        )


class _PostgresPrincipalProvider:
    def __init__(self, account_id: UUID):
        self._principal = AuthenticatedAccountPrincipal(
            account_id=account_id,
            subject="postgres-test",
            authorization_id="postgres-test-authz",
        )

    def current_principal(self) -> AuthenticatedAccountPrincipal:
        return self._principal


class _PostgresSnapshotVerifier:
    def __init__(self, authenticated: bool = True):
        self._authenticated = authenticated

    def verify_snapshot(self, snapshot, principal):
        if (
            not self._authenticated
            or snapshot.account_id != principal.account_id
            or not snapshot.positions_complete
            or not snapshot.orders_complete
            or not snapshot.ats_managed_exposure_excluded
        ):
            return None
        return snapshot


class _PostgresBrokerEventVerifier:
    def verify_order_update(self, event, principal):
        if event.account_id != principal.account_id:
            return None
        return VerifiedBrokerOrderUpdate(event=event, verification_id="test-only")

    def verify_stop_confirmation(self, event, principal):
        if event.account_id != principal.account_id:
            return None
        return VerifiedBrokerStopConfirmation(event=event, verification_id="test-only")


def _postgres_layer3_setup(
    engine: Engine,
    *,
    plan_count: int = 1,
    broker_event_verifier: _PostgresBrokerEventVerifier | None = None,
    snapshot_verifier: _PostgresSnapshotVerifier | None = None,
):
    account_id = uuid4()
    service = Layer3AuthorizationService(
        policy=POSTGRES_RISK_POLICY,
        taxonomy=POSTGRES_RISK_TAXONOMY,
        loss_locks=_PostgresPassingLossLocks(account_id),
        principal_provider=_PostgresPrincipalProvider(account_id),
        snapshot_verifier=snapshot_verifier or _PostgresSnapshotVerifier(),
        broker_event_verifier=broker_event_verifier,
        ats009_policy_version="ats-009-postgres-test-v1",
        clock=lambda: POSTGRES_RISK_NOW,
    )
    entries = []
    for _ in range(plan_count):
        entry = make_complete_trade()
        plan = entry.plan.model_copy(
            update={
                "account_id": account_id,
                "quantity": Decimal(100),
                "risk_intent": RiskIntent(stop_risk_budget=Decimal(250)),
            }
        )
        entries.append(entry.model_copy(update={"plan": plan}))

    snapshot = PortfolioSnapshot(
        account_id=account_id,
        version=1,
        as_of=POSTGRES_RISK_NOW,
        policy_version=POSTGRES_RISK_POLICY.version,
        taxonomy_version=POSTGRES_RISK_TAXONOMY.version,
        equity=Decimal(100000),
        positions=(
            PositionExposure(
                ticker="XYZ",
                quantity=950,
                average_entry=Decimal(10),
                active_stop=Decimal(8),
                stop_confirmed=True,
            ),
        ),
        pending_orders=(),
        positions_complete=True,
        orders_complete=True,
        ats_managed_exposure_excluded=True,
    )
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory.begin() as session:
        service.publish_snapshot(session, snapshot)
        for entry in entries:
            JournalRepository(session).save(entry)
    return factory, service, account_id, tuple(entries)


def _postgres_sqlstate(error: DBAPIError) -> str | None:
    return getattr(error.orig, "sqlstate", None)


@pytest.fixture
def migrated_postgres_database() -> Iterator[tuple[Engine, str]]:
    database_url = os.environ.get("ATS_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("set ATS_TEST_DATABASE_URL to a dedicated PostgreSQL test database")
    if make_url(database_url).database != "ats_test":
        raise RuntimeError(
            "ATS_TEST_DATABASE_URL must target the dedicated ats_test database"
        )

    schema = f"ats_test_{uuid4().hex}"
    runtime_role = f"ats_runtime_{uuid4().hex}"
    admin_engine = install_utc_session_timezone(
        create_engine(database_url, pool_pre_ping=True)
    )
    test_engine = None
    runtime_role_created = False
    previous_runtime_role = os.environ.get("ATS_RUNTIME_ROLE")
    try:
        with admin_engine.begin() as connection:
            connection.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
            connection.exec_driver_sql(f'CREATE ROLE "{runtime_role}" NOLOGIN')
            runtime_role_created = True
        scoped_url = make_url(database_url).update_query_dict(
            {"options": f"-csearch_path={schema}"}
        )
        test_engine = install_utc_session_timezone(
            create_engine(scoped_url, pool_pre_ping=True)
        )
        alembic_config = Config(
            str(Path(__file__).resolve().parents[1] / "alembic.ini")
        )
        alembic_config.set_main_option(
            "script_location",
            str(Path(__file__).resolve().parents[1] / "migrations"),
        )
        os.environ["ATS_RUNTIME_ROLE"] = runtime_role
        try:
            with test_engine.connect() as connection:
                alembic_config.attributes["connection"] = connection
                command.upgrade(alembic_config, "head")
                command.check(alembic_config)
        finally:
            if previous_runtime_role is None:
                os.environ.pop("ATS_RUNTIME_ROLE", None)
            else:
                os.environ["ATS_RUNTIME_ROLE"] = previous_runtime_role
        yield test_engine, runtime_role
    finally:
        if previous_runtime_role is None:
            os.environ.pop("ATS_RUNTIME_ROLE", None)
        else:
            os.environ["ATS_RUNTIME_ROLE"] = previous_runtime_role
        if test_engine is not None:
            test_engine.dispose()
        with admin_engine.begin() as connection:
            connection.exec_driver_sql(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
            if runtime_role_created:
                connection.exec_driver_sql(f'DROP OWNED BY "{runtime_role}"')
                connection.exec_driver_sql(f'DROP ROLE "{runtime_role}"')
        admin_engine.dispose()


@pytest.fixture
def migrated_postgres_engine(
    migrated_postgres_database: tuple[Engine, str],
) -> Engine:
    return migrated_postgres_database[0]


def _assert_no_trade_records(engine: Engine) -> None:
    record_types = (
        TradeRecord,
        ScoreSnapshotRecord,
        TradePlanRecord,
        OrderIntentRecord,
        FillRecord,
        PositionSnapshotRecord,
        TradeResultRecord,
        JournalEntryRecord,
        PortfolioSnapshotRecord,
        RiskReservationEventRecord,
    )
    with sessionmaker(bind=engine)() as session:
        for record_type in record_types:
            assert session.scalar(select(func.count()).select_from(record_type)) == 0


def test_postgres_persists_and_reloads_closed_trade_in_fresh_session(
    migrated_postgres_engine: Engine,
):
    entry = make_complete_trade()
    factory = sessionmaker(bind=migrated_postgres_engine)
    with factory.begin() as session:
        assert JournalRepository(session).save(entry) == 1

    with factory() as session:
        restored = JournalRepository(session).get_by_trade_id(entry.plan.trade_id)

    assert restored == entry
    assert restored is not None
    assert restored.plan.trade_id == entry.plan.trade_id
    assert restored.plan.score_snapshot == entry.prior_plans[0].score_snapshot
    assert restored.prior_plans[0].risk_intent == entry.prior_plans[0].risk_intent
    assert restored.result == entry.result
    assert restored.result is not None
    assert restored.result.mae_r == entry.result.mae_r
    assert restored.result.mfe_r == entry.result.mfe_r
    assert restored.plan_followed is True
    assert restored.mood == entry.mood
    assert restored.thesis == entry.thesis
    assert restored.lesson == entry.lesson


def test_postgres_rolls_back_complete_aggregate_after_application_exception(
    migrated_postgres_engine: Engine,
):
    entry = make_complete_trade()
    factory = sessionmaker(bind=migrated_postgres_engine)

    with (
        pytest.raises(RuntimeError, match="abort aggregate"),
        factory.begin() as session,
    ):
        JournalRepository(session).save(entry)
        raise RuntimeError("abort aggregate")

    _assert_no_trade_records(migrated_postgres_engine)


def test_postgres_rolls_back_complete_aggregate_after_integrity_violation(
    migrated_postgres_engine: Engine,
):
    entry = make_complete_trade()
    factory = sessionmaker(bind=migrated_postgres_engine)

    with pytest.raises(IntegrityError), factory.begin() as session:
        JournalRepository(session).save(entry)
        session.execute(
            insert(JournalEntryRecord).values(
                id=entry.id,
                revision=1,
                trade_id=entry.plan.trade_id,
                plan_id=entry.plan.id,
                created_at=entry.created_at,
                plan_followed=entry.plan_followed,
                mood=entry.mood.value,
                lesson=entry.lesson,
                thesis=entry.thesis,
                entry_data={},
            )
        )

    _assert_no_trade_records(migrated_postgres_engine)


def test_postgres_concurrent_stale_revision_is_rejected_without_partial_rows(
    migrated_postgres_engine: Engine,
):
    entry = make_complete_trade()
    factory = sessionmaker(bind=migrated_postgres_engine)
    with factory.begin() as session:
        assert JournalRepository(session).save(entry) == 1

    barrier = Barrier(2)
    order_ids = (uuid4(), uuid4())

    def append_revision(index: int) -> int | str:
        order = OrderIntent(
            id=order_ids[index],
            plan_id=entry.plan.id,
            action=OrderAction.BUY,
            order_type=OrderType.LIMIT,
            quantity=entry.plan.quantity,
            limit_price=entry.plan.entry,
            created_at=entry.plan.planned_at + timedelta(minutes=10),
        )
        revised_entry = entry.model_copy(
            update={"orders": (*entry.orders, order), "lesson": f"concurrent {index}"}
        )
        try:
            with factory.begin() as session:
                barrier.wait(timeout=10)
                return JournalRepository(session).save(
                    revised_entry, expected_revision=1
                )
        except PersistenceConflict:
            return "stale"

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(append_revision, range(2)))

    assert outcomes.count(2) == 1
    assert outcomes.count("stale") == 1
    with factory() as session:
        revisions = session.scalars(
            select(JournalEntryRecord.revision)
            .where(JournalEntryRecord.trade_id == entry.plan.trade_id)
            .order_by(JournalEntryRecord.revision)
        ).all()
        persisted_order_ids = set(session.scalars(select(OrderIntentRecord.id)).all())
    assert revisions == [1, 2]
    assert persisted_order_ids == {
        entry.orders[0].id,
        order_ids[outcomes.index(2)],
    }


def test_postgres_rejects_updates_and_deletes_to_append_only_records(
    migrated_postgres_engine: Engine,
):
    entry = make_complete_trade()
    factory = sessionmaker(bind=migrated_postgres_engine)
    with factory.begin() as session:
        JournalRepository(session).save(entry)

    for statement in (
        update(TradePlanRecord)
        .where(TradePlanRecord.id == entry.plan.id)
        .values(plan_data={}),
        delete(TradePlanRecord).where(TradePlanRecord.id == entry.plan.id),
        text("TRUNCATE TABLE ats_trade_plans CASCADE"),
    ):
        with pytest.raises(DBAPIError, match="append-only"), factory.begin() as session:
            session.execute(statement)


def test_postgres_concurrent_layer3_reservations_are_account_serialized(
    migrated_postgres_engine: Engine,
):
    factory, service, account_id, entries = _postgres_layer3_setup(
        migrated_postgres_engine, plan_count=3
    )

    repeatable_read_factory = sessionmaker(
        bind=migrated_postgres_engine.execution_options(
            isolation_level="REPEATABLE READ"
        )
    )
    with (
        repeatable_read_factory() as session,
        session.begin(),
        pytest.raises(RuntimeError, match="READ COMMITTED"),
    ):
        service.authorize(
            session,
            plan_id=entries[0].plan.id,
            idempotency_key="repeatable-read-must-fail",
        )

    serializable_factory = sessionmaker(
        bind=migrated_postgres_engine.execution_options(isolation_level="SERIALIZABLE")
    )
    with (
        serializable_factory() as session,
        session.begin(),
        pytest.raises(RuntimeError, match="READ COMMITTED"),
    ):
        service.authorize(
            session,
            plan_id=entries[0].plan.id,
            idempotency_key="serializable-must-fail",
        )

    barrier = Barrier(3)

    def reserve(entry, key) -> object:
        with factory.begin() as session:
            barrier.wait(timeout=10)
            return service.authorize(
                session,
                plan_id=entry.plan.id,
                idempotency_key=key,
            )

    with ThreadPoolExecutor(max_workers=3) as executor:
        results = tuple(
            executor.map(
                reserve,
                entries,
                (f"pg-{entry.plan.id}" for entry in entries),
            )
        )

    assert sorted(result.quantity for result in results) == [0, 0, 40]
    reserved_result = next(result for result in results if result.quantity > 0)
    reserved_plan = next(
        entry for entry, result in zip(entries, results, strict=True) if result.quantity
    )

    retry_barrier = Barrier(3)

    def retry_same_key() -> object:
        with factory.begin() as session:
            retry_barrier.wait(timeout=10)
            return service.authorize(
                session,
                plan_id=reserved_plan.plan.id,
                idempotency_key=f"pg-{reserved_plan.plan.id}",
            )

    with ThreadPoolExecutor(max_workers=3) as executor:
        retries = tuple(executor.map(lambda _: retry_same_key(), range(3)))
    assert all(
        result == reserved_result.model_copy(update={"replayed": True})
        for result in retries
    )

    with factory.begin() as session:
        different_key = service.authorize(
            session,
            plan_id=reserved_plan.plan.id,
            idempotency_key="different-key-for-already-authorized-plan",
        )
    assert different_key.decision.value == "DENY"
    assert different_key.reason == "PLAN_ALREADY_RESERVED"

    with factory() as session:
        reservations = session.scalars(
            select(RiskReservationEventRecord).where(
                RiskReservationEventRecord.account_id == account_id
            )
        ).all()
        snapshot_count = session.scalar(
            select(func.count())
            .select_from(PortfolioSnapshotRecord)
            .where(PortfolioSnapshotRecord.account_id == account_id)
        )
    assert len(reservations) == 1
    assert snapshot_count == 1
    for statement in (
        update(PortfolioSnapshotRecord)
        .where(PortfolioSnapshotRecord.account_id == account_id)
        .values(snapshot_data={}),
        delete(PortfolioSnapshotRecord).where(
            PortfolioSnapshotRecord.account_id == account_id
        ),
        update(RiskReservationEventRecord)
        .where(RiskReservationEventRecord.account_id == account_id)
        .values(event_data={}),
        delete(RiskReservationEventRecord).where(
            RiskReservationEventRecord.account_id == account_id
        ),
        text("TRUNCATE TABLE ats_risk_reservation_events"),
    ):
        with pytest.raises(DBAPIError, match="append-only"), factory.begin() as session:
            session.execute(statement)


def test_postgres_concurrent_same_key_authorization_reserves_once(
    migrated_postgres_engine: Engine,
):
    factory, service, _, entries = _postgres_layer3_setup(migrated_postgres_engine)
    barrier = Barrier(3)

    def authorize_same_request():
        with factory.begin() as session:
            barrier.wait(timeout=10)
            return service.authorize(
                session,
                plan_id=entries[0].plan.id,
                idempotency_key="concurrent-same-key",
            )

    with ThreadPoolExecutor(max_workers=3) as executor:
        results = tuple(executor.map(lambda _: authorize_same_request(), range(3)))

    assert results[0].quantity == 40
    assert len({result.reservation_id for result in results}) == 1
    assert sum(result.replayed for result in results) == 2
    assert all(not result.execution_enabled for result in results)
    with factory() as session:
        reservations = session.scalar(
            select(func.count()).select_from(RiskReservationEventRecord)
        )
    assert reservations == 1


def test_postgres_stale_snapshot_and_untrusted_provider_fail_closed(
    migrated_postgres_engine: Engine,
):
    factory, service, account_id, entries = _postgres_layer3_setup(
        migrated_postgres_engine, plan_count=3
    )
    snapshot = PortfolioSnapshot(
        account_id=account_id,
        version=1,
        as_of=POSTGRES_RISK_NOW,
        policy_version=POSTGRES_RISK_POLICY.version,
        taxonomy_version=POSTGRES_RISK_TAXONOMY.version,
        equity=Decimal(100000),
        positions=(),
        pending_orders=(),
        positions_complete=True,
        orders_complete=True,
        ats_managed_exposure_excluded=True,
    )
    with (
        pytest.raises(ValueError, match="increase monotonically"),
        factory.begin() as session,
    ):
        service.publish_snapshot(session, snapshot)

    with factory.begin() as session:
        decision = service.authorize(
            session,
            plan_id=entries[0].plan.id,
            idempotency_key="snapshot-version-replay",
        )
    assert decision.quantity > 0

    version_two = snapshot.model_copy(update={"version": 2})
    with factory.begin() as session:
        service.publish_snapshot(session, version_two)
    with factory.begin() as session:
        replay = service.authorize(
            session,
            plan_id=entries[0].plan.id,
            idempotency_key="snapshot-version-replay",
        )
    assert replay.reason == "SNAPSHOT_VERSION_CONFLICT"

    service._snapshot_verifier = _PostgresSnapshotVerifier(authenticated=False)
    with factory.begin() as session:
        untrusted = service.authorize(
            session,
            plan_id=entries[1].plan.id,
            idempotency_key="untrusted-snapshot-provider",
        )
    assert untrusted.reason == "SNAPSHOT_SOURCE_UNAVAILABLE"

    service._principal_provider = None
    with factory.begin() as session:
        unauthenticated = service.authorize(
            session,
            plan_id=entries[2].plan.id,
            idempotency_key="unauthenticated-principal",
        )
    assert unauthenticated.reason == "PRINCIPAL_UNAVAILABLE"
    with factory() as session:
        reservations = session.scalar(
            select(func.count()).select_from(RiskReservationEventRecord)
        )
    assert reservations == 1


def test_postgres_reservation_event_failure_rolls_back_authorization(
    migrated_postgres_engine: Engine,
):
    factory, service, _, entries = _postgres_layer3_setup(migrated_postgres_engine)
    with migrated_postgres_engine.begin() as connection:
        connection.exec_driver_sql(
            """
            CREATE FUNCTION ats_test_reject_reservation_insert()
            RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
                RAISE EXCEPTION 'injected reservation write failure';
            END;
            $$
            """
        )
        connection.exec_driver_sql(
            """
            CREATE TRIGGER ats_test_reject_reservation_insert
            BEFORE INSERT ON ats_risk_reservation_events
            FOR EACH ROW WHEN (NEW.event_type = 'fill')
            EXECUTE FUNCTION ats_test_reject_reservation_insert()
            """
        )

    try:
        with (
            pytest.raises(DBAPIError, match="injected reservation write failure"),
            factory.begin() as session,
        ):
            decision = service.authorize(
                session,
                plan_id=entries[0].plan.id,
                idempotency_key="reservation-write-failure",
            )
            assert decision.reservation_id is not None
            session.add(
                RiskReservationEventRecord(
                    id=uuid4(),
                    account_id=entries[0].plan.account_id,
                    reservation_id=decision.reservation_id,
                    sequence=2,
                    event_type="fill",
                    idempotency_key="partial-event-write-failure",
                    snapshot_version=1,
                    plan_id=entries[0].plan.id,
                    event_data={"partial": True},
                    created_at=POSTGRES_RISK_NOW,
                )
            )
            session.flush()
    finally:
        with migrated_postgres_engine.begin() as connection:
            connection.exec_driver_sql(
                "DROP TRIGGER ats_test_reject_reservation_insert "
                "ON ats_risk_reservation_events"
            )
            connection.exec_driver_sql(
                "DROP FUNCTION ats_test_reject_reservation_insert()"
            )

    with factory() as session:
        assert (
            session.scalar(select(func.count()).select_from(RiskReservationEventRecord))
            == 0
        )


def test_postgres_lock_timeout_can_retry_with_original_idempotency_key(
    migrated_postgres_engine: Engine,
):
    factory, service, account_id, entries = _postgres_layer3_setup(
        migrated_postgres_engine
    )
    account_lock = select(
        func.pg_advisory_xact_lock(func.hashtextextended(str(account_id), 0))
    )
    with factory.begin() as holder:
        holder.execute(account_lock)
        with (
            pytest.raises(DBAPIError, match="lock timeout"),
            factory.begin() as session,
        ):
            session.execute(text("SET LOCAL lock_timeout = '100ms'"))
            service.authorize(
                session,
                plan_id=entries[0].plan.id,
                idempotency_key="retry-after-lock-timeout",
            )

    with factory.begin() as session:
        retried = service.authorize(
            session,
            plan_id=entries[0].plan.id,
            idempotency_key="retry-after-lock-timeout",
        )
    assert retried.quantity == 40
    with factory() as session:
        assert (
            session.scalar(select(func.count()).select_from(RiskReservationEventRecord))
            == 1
        )


def test_postgres_deadlock_rolls_back_and_retries_whole_transaction(
    migrated_postgres_engine: Engine,
):
    factory, service, _account_id, plans = _postgres_layer3_setup(
        migrated_postgres_engine, plan_count=2
    )
    with migrated_postgres_engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TABLE ats_test_deadlock_rows "
            "(id integer PRIMARY KEY, value integer NOT NULL)"
        )
        connection.exec_driver_sql(
            "INSERT INTO ats_test_deadlock_rows (id, value) VALUES (1, 0), (2, 0)"
        )
        connection.exec_driver_sql(
            "CREATE TABLE ats_test_deadlock_requests "
            "(idempotency_key text PRIMARY KEY, transaction_attempt integer NOT NULL)"
        )

    barrier = Barrier(2)

    def first_transaction(idempotency_key: str, first: int, second: int):
        try:
            with migrated_postgres_engine.begin() as connection:
                connection.execute(
                    text(
                        "INSERT INTO ats_test_deadlock_requests "
                        "(idempotency_key, transaction_attempt) VALUES (:key, 1)"
                    ),
                    {"key": idempotency_key},
                )
                connection.execute(
                    text(
                        "SELECT id FROM ats_test_deadlock_rows WHERE id=:id FOR UPDATE"
                    ),
                    {"id": first},
                )
                connection.execute(
                    text(
                        "UPDATE ats_test_deadlock_rows SET value=value+1 WHERE id=:id"
                    ),
                    {"id": first},
                )
                barrier.wait(timeout=10)
                connection.execute(
                    text(
                        "SELECT id FROM ats_test_deadlock_rows WHERE id=:id FOR UPDATE"
                    ),
                    {"id": second},
                )
            return idempotency_key, None
        except DBAPIError as error:
            return idempotency_key, _postgres_sqlstate(error)

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = tuple(
            executor.map(
                lambda arguments: first_transaction(*arguments),
                (
                    ("deadlock-request-a", 1, 2),
                    ("deadlock-request-b", 2, 1),
                ),
            )
        )

    failed = [(key, code) for key, code in outcomes if code is not None]
    committed = [(key, code) for key, code in outcomes if code is None]
    assert len(failed) == 1
    assert failed[0][1] == "40P01"
    assert len(committed) == 1

    failed_key = failed[0][0]
    with migrated_postgres_engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO ats_test_deadlock_requests "
                "(idempotency_key, transaction_attempt) VALUES (:key, 2)"
            ),
            {"key": failed_key},
        )
        connection.execute(
            text("UPDATE ats_test_deadlock_rows SET value=value+1 WHERE id=1")
        )

    with migrated_postgres_engine.connect() as connection:
        request_rows = connection.execute(
            text(
                "SELECT idempotency_key, count(*) AS attempts "
                "FROM ats_test_deadlock_requests GROUP BY idempotency_key"
            )
        ).all()
        total_committed_updates = connection.scalar(
            text("SELECT sum(value) FROM ats_test_deadlock_rows")
        )
    assert {key: count for key, count in request_rows} == {
        "deadlock-request-a": 1,
        "deadlock-request-b": 1,
    }
    assert total_committed_updates == 2

    with factory.begin() as session:
        capacity_holder = service.authorize(
            session,
            plan_id=plans[0].plan.id,
            idempotency_key="capacity-consumed-before-deadlock-retry",
        )
    assert capacity_holder.quantity == 40
    with factory.begin() as session:
        retried_authorization = service.authorize(
            session,
            plan_id=plans[1].plan.id,
            idempotency_key=failed_key,
        )
    assert retried_authorization.quantity == 0
    assert retried_authorization.decision.value == "DENY"
    with factory() as session:
        reservation_count = session.scalar(
            select(func.count()).select_from(RiskReservationEventRecord)
        )
    assert reservation_count == 1


def test_postgres_serializable_failure_retries_whole_transaction_and_rechecks(
    migrated_postgres_engine: Engine,
):
    with migrated_postgres_engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TABLE ats_test_serializable_capacity "
            "(id integer PRIMARY KEY, value integer NOT NULL)"
        )
        connection.exec_driver_sql(
            "INSERT INTO ats_test_serializable_capacity (id, value) VALUES (1, 0)"
        )
        connection.exec_driver_sql(
            "CREATE TABLE ats_test_serializable_requests "
            "(idempotency_key text PRIMARY KEY)"
        )

    idempotency_key = "serializable-original-identity"
    retry_errors: list[str] = []
    attempts = 0
    while attempts < 3:
        attempts += 1
        try:
            with (
                migrated_postgres_engine.connect().execution_options(
                    isolation_level="SERIALIZABLE"
                ) as connection,
                connection.begin(),
            ):
                connection.execute(
                    text(
                        "INSERT INTO ats_test_serializable_requests "
                        "(idempotency_key) VALUES (:key)"
                    ),
                    {"key": idempotency_key},
                )
                current_capacity = connection.scalar(
                    text("SELECT value FROM ats_test_serializable_capacity WHERE id=1")
                )
                assert current_capacity is not None
                if attempts == 1:
                    with migrated_postgres_engine.begin() as concurrent:
                        concurrent.execute(
                            text(
                                "UPDATE ats_test_serializable_capacity "
                                "SET value=value+1 WHERE id=1"
                            )
                        )
                connection.execute(
                    text(
                        "UPDATE ats_test_serializable_capacity SET value=:value "
                        "WHERE id=1"
                    ),
                    {"value": current_capacity + 1},
                )
            break
        except DBAPIError as error:
            code = _postgres_sqlstate(error)
            if code != "40001":
                raise
            retry_errors.append(code)
            if attempts == 3:
                raise

    with migrated_postgres_engine.connect() as connection:
        capacity = connection.scalar(
            text("SELECT value FROM ats_test_serializable_capacity WHERE id=1")
        )
        request_count = connection.scalar(
            text(
                "SELECT count(*) FROM ats_test_serializable_requests "
                "WHERE idempotency_key=:key"
            ),
            {"key": idempotency_key},
        )
    assert retry_errors == ["40001"]
    assert attempts == 2
    assert capacity == 2
    assert request_count == 1


def test_postgres_serializable_retry_exhaustion_fails_closed(
    migrated_postgres_engine: Engine,
):
    with migrated_postgres_engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TABLE ats_test_serializable_exhaustion "
            "(id integer PRIMARY KEY, value integer NOT NULL)"
        )
        connection.exec_driver_sql(
            "INSERT INTO ats_test_serializable_exhaustion (id, value) VALUES (1, 0)"
        )
        connection.exec_driver_sql(
            "CREATE TABLE ats_test_serializable_exhausted_requests "
            "(idempotency_key text PRIMARY KEY)"
        )

    idempotency_key = "serializable-exhausted-original-identity"
    retry_errors: list[str] = []
    max_attempts = 2

    def exhaust_retries() -> None:
        last_error: DBAPIError | None = None
        for _ in range(max_attempts):
            try:
                with (
                    migrated_postgres_engine.connect().execution_options(
                        isolation_level="SERIALIZABLE"
                    ) as connection,
                    connection.begin(),
                ):
                    connection.execute(
                        text(
                            "INSERT INTO ats_test_serializable_exhausted_requests "
                            "(idempotency_key) VALUES (:key)"
                        ),
                        {"key": idempotency_key},
                    )
                    current_value = connection.scalar(
                        text(
                            "SELECT value FROM ats_test_serializable_exhaustion "
                            "WHERE id=1"
                        )
                    )
                    assert current_value is not None
                    with migrated_postgres_engine.begin() as concurrent:
                        concurrent.execute(
                            text(
                                "UPDATE ats_test_serializable_exhaustion "
                                "SET value=value+1 WHERE id=1"
                            )
                        )
                    connection.execute(
                        text(
                            "UPDATE ats_test_serializable_exhaustion SET value=:value "
                            "WHERE id=1"
                        ),
                        {"value": current_value + 1},
                    )
                raise AssertionError("concurrent update unexpectedly succeeded")
            except DBAPIError as error:
                code = _postgres_sqlstate(error)
                if code != "40001":
                    raise
                retry_errors.append(code)
                last_error = error
        assert last_error is not None
        raise last_error

    with pytest.raises(DBAPIError) as exhausted:
        exhaust_retries()

    with migrated_postgres_engine.connect() as connection:
        committed_request_count = connection.scalar(
            text(
                "SELECT count(*) FROM ats_test_serializable_exhausted_requests "
                "WHERE idempotency_key=:key"
            ),
            {"key": idempotency_key},
        )
        concurrent_value = connection.scalar(
            text("SELECT value FROM ats_test_serializable_exhaustion WHERE id=1")
        )
    assert _postgres_sqlstate(exhausted.value) == "40001"
    assert retry_errors == ["40001", "40001"]
    assert committed_request_count == 0
    assert concurrent_value == max_attempts


def test_postgres_partial_fill_racing_final_cancel_reconciles_once(
    migrated_postgres_engine: Engine,
):
    verifier = _PostgresBrokerEventVerifier()
    factory, service, account_id, entries = _postgres_layer3_setup(
        migrated_postgres_engine,
        broker_event_verifier=verifier,
    )
    with factory.begin() as session:
        decision = service.authorize(
            session,
            plan_id=entries[0].plan.id,
            idempotency_key="cancel-fill-race",
        )
    assert decision.reservation_id is not None

    with factory() as session:
        row = session.scalar(
            select(RiskReservationEventRecord).where(
                RiskReservationEventRecord.reservation_id == decision.reservation_id
            )
        )
    assert row is not None
    reservation = row.event_data["reservation"]
    order_attempt_id = UUID(reservation["order_attempt_id"])
    quantity = reservation["requested_quantity"]

    active = BrokerOrderUpdate(
        event_id="trusted:active",
        account_id=account_id,
        order_attempt_id=order_attempt_id,
        broker_order_id="broker-order-test",
        sequence=1,
        status=BrokerOrderStatus.ACTIVE,
        authorized_quantity=quantity,
        cumulative_filled_quantity=0,
        fills=(),
    )
    with factory.begin() as session:
        service.process_broker_order_update(
            session, reservation_id=decision.reservation_id, event=active
        )

    fill = BrokerFillEvent(fill_id="fill-race-1", quantity=10, price=Decimal(100))
    partial = active.model_copy(
        update={
            "event_id": "trusted:partial-race",
            "sequence": 2,
            "cumulative_filled_quantity": 10,
            "fills": (fill,),
        }
    )
    cancelled = active.model_copy(
        update={
            "event_id": "trusted:cancel-race",
            "sequence": 3,
            "status": BrokerOrderStatus.CANCELLED,
            "cumulative_filled_quantity": 10,
            "fills": (fill,),
        }
    )
    barrier = Barrier(2)

    def apply(event: BrokerOrderUpdate):
        with factory.begin() as session:
            barrier.wait(timeout=10)
            return service.process_broker_order_update(
                session, reservation_id=decision.reservation_id, event=event
            )

    outcomes = []
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(apply, event) for event in (partial, cancelled)]
        for future in futures:
            try:
                outcomes.append(future.result(timeout=20))
            except ValueError:
                outcomes.append(None)

    assert any(outcome is not None for outcome in outcomes)
    with factory() as session:
        latest = session.scalar(
            select(RiskReservationEventRecord)
            .where(RiskReservationEventRecord.reservation_id == decision.reservation_id)
            .order_by(RiskReservationEventRecord.sequence.desc())
            .limit(1)
        )
    assert latest is not None
    state = latest.event_data["reservation"]
    assert state["order_status"] == "CANCELLED"
    assert state["remaining_quantity"] == 0
    assert [item["quantity"] for item in state["fills"]] == [10]

    stop = BrokerStopConfirmation(
        event_id="trusted:stop",
        account_id=account_id,
        order_attempt_id=order_attempt_id,
        broker_order_id="broker-stop-test",
        sequence=4,
        covered_fill_ids=("fill-race-1",),
        active_stop=Decimal(98),
    )
    with factory.begin() as session:
        confirmed = service.confirm_filled_stops(
            session, reservation_id=decision.reservation_id, event=stop
        )
    assert confirmed.fills[0].active_stop == Decimal(98)
    reordered = stop.model_copy(
        update={"event_id": "trusted:stale-stop", "sequence": 3}
    )
    with (
        pytest.raises(ValueError, match="stale or reordered"),
        factory.begin() as session,
    ):
        service.confirm_filled_stops(
            session, reservation_id=decision.reservation_id, event=reordered
        )


def test_postgres_non_owner_runtime_role_cannot_mutate_or_own_journal_schema(
    migrated_postgres_database: tuple[Engine, str],
):
    engine, runtime_role = migrated_postgres_database
    entry = make_complete_trade()
    factory = sessionmaker(bind=engine)
    with factory.begin() as session:
        JournalRepository(session).save(entry)

    quoted_role = engine.dialect.identifier_preparer.quote(runtime_role)
    with engine.connect() as connection:
        table_owner = connection.scalar(
            text(
                "SELECT pg_get_userbyid(relowner) FROM pg_class "
                "WHERE relname = 'ats_journal_entries'"
            )
        )
        schema_name = connection.scalar(text("SELECT current_schema()"))
    assert table_owner != runtime_role
    with engine.connect() as connection:
        for table in (
            "ats_trades",
            "ats_score_snapshots",
            "ats_trade_plans",
            "ats_order_intents",
            "ats_fills",
            "ats_position_snapshots",
            "ats_trade_results",
            "ats_journal_entries",
            "ats_portfolio_snapshots",
            "ats_risk_reservation_events",
        ):
            qualified_table = f"{schema_name}.{table}"
            for privilege in ("UPDATE", "DELETE", "TRUNCATE"):
                assert not connection.scalar(
                    text("SELECT has_table_privilege(:role, :table, :privilege)"),
                    {
                        "role": runtime_role,
                        "table": qualified_table,
                        "privilege": privilege,
                    },
                )
            assert connection.scalar(
                text("SELECT has_table_privilege(:role, :table, 'INSERT')"),
                {"role": runtime_role, "table": qualified_table},
            )
        assert not connection.scalar(
            text("SELECT has_schema_privilege(:role, :schema, 'CREATE')"),
            {"role": runtime_role, "schema": schema_name},
        )
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TABLE ats_future_privilege_probe (id integer)"
        )
    with engine.connect() as connection:
        for privilege, allowed in (
            ("SELECT", True),
            ("INSERT", True),
            ("UPDATE", False),
        ):
            assert (
                connection.scalar(
                    text("SELECT has_table_privilege(:role, :table, :privilege)"),
                    {
                        "role": runtime_role,
                        "table": f"{schema_name}.ats_future_privilege_probe",
                        "privilege": privilege,
                    },
                )
                is allowed
            )

    def execute_as_runtime(statement: str) -> None:
        with engine.connect() as connection:
            connection.exec_driver_sql(f"SET ROLE {quoted_role}")
            try:
                connection.exec_driver_sql(statement)
            finally:
                connection.rollback()

    with engine.connect() as connection:
        connection.exec_driver_sql(f"SET ROLE {quoted_role}")
        assert connection.scalar(text("SELECT count(*) FROM ats_journal_entries")) == 1
        connection.rollback()

    protected_tables = (
        "ats_trades",
        "ats_score_snapshots",
        "ats_trade_plans",
        "ats_order_intents",
        "ats_fills",
        "ats_position_snapshots",
        "ats_trade_results",
        "ats_journal_entries",
        "ats_portfolio_snapshots",
        "ats_risk_reservation_events",
    )
    statements = [
        "UPDATE ats_journal_entries SET lesson = 'tampered' WHERE revision = 1",
        "DELETE FROM ats_journal_entries WHERE revision = 1",
        *(f"TRUNCATE TABLE {table}" for table in protected_tables),
        "ALTER TABLE ats_journal_entries ADD COLUMN runtime_probe integer",
        *(f"ALTER TABLE {table} DISABLE TRIGGER ALL" for table in protected_tables),
        (
            "ALTER FUNCTION ats_reject_append_only_mutation() "
            "RENAME TO ats_runtime_probe"
        ),
        "DROP TABLE ats_journal_entries",
        "CREATE TABLE ats_runtime_probe (id integer)",
        f'ALTER SCHEMA "{schema_name}" RENAME TO ats_runtime_probe',
        "UPDATE ats_future_privilege_probe SET id = 2",
    ]
    for statement in statements:
        with pytest.raises(DBAPIError):
            execute_as_runtime(statement)

    with factory() as session:
        assert session.scalar(select(func.count()).select_from(JournalEntryRecord)) == 1


def test_postgres_reads_legacy_jsonb_snapshots_without_rewriting_them(
    migrated_postgres_engine: Engine,
):
    entry = make_complete_trade()
    factory = sessionmaker(bind=migrated_postgres_engine)
    with factory.begin() as session:
        JournalRepository(session).save(entry)

    with migrated_postgres_engine.begin() as connection:
        connection.exec_driver_sql(
            "ALTER TABLE ats_journal_entries "
            "DISABLE TRIGGER ats_journal_entries_append_only"
        )
        connection.exec_driver_sql(
            "ALTER TABLE ats_score_snapshots "
            "DISABLE TRIGGER ats_score_snapshots_append_only"
        )
        stored_entry = connection.scalar(
            select(JournalEntryRecord.entry_data).where(
                JournalEntryRecord.id == entry.id
            )
        )
        legacy_entry = stored_entry["data"]
        for plan in (*legacy_entry["prior_plans"], legacy_entry["plan"]):
            for contribution in plan["score_snapshot"]["contributions"]:
                for evidence in contribution["evidence"]:
                    evidence.pop("verification_status", None)
        connection.execute(
            update(JournalEntryRecord)
            .where(JournalEntryRecord.id == entry.id)
            .values(entry_data=legacy_entry)
        )

        stored_snapshot = connection.scalar(
            select(ScoreSnapshotRecord.snapshot_data).where(
                ScoreSnapshotRecord.id == entry.plan.score_snapshot.id
            )
        )
        legacy_snapshot = stored_snapshot["data"]
        for contribution in legacy_snapshot["contributions"]:
            for evidence in contribution["evidence"]:
                evidence.pop("verification_status", None)
        connection.execute(
            update(ScoreSnapshotRecord)
            .where(ScoreSnapshotRecord.id == entry.plan.score_snapshot.id)
            .values(snapshot_data=legacy_snapshot)
        )
        connection.exec_driver_sql(
            "ALTER TABLE ats_score_snapshots "
            "ENABLE TRIGGER ats_score_snapshots_append_only"
        )
        connection.exec_driver_sql(
            "ALTER TABLE ats_journal_entries "
            "ENABLE TRIGGER ats_journal_entries_append_only"
        )

    with factory() as session:
        restored = JournalRepository(session).get(entry.id)
        persisted_entry = session.get(JournalEntryRecord, (entry.id, 1)).entry_data
        persisted_snapshot = session.get(
            ScoreSnapshotRecord, entry.plan.score_snapshot.id
        ).snapshot_data

    assert restored is not None
    historical_evidence = restored.plan.score_snapshot.contributions[0].evidence[0]
    assert historical_evidence.verification_status is EvidenceStatus.UNVERIFIED
    assert decode_score_snapshot(persisted_snapshot) == entry.plan.score_snapshot
    assert persisted_entry == legacy_entry
    assert persisted_snapshot == legacy_snapshot

    with factory.begin() as session:
        assert (
            JournalRepository(session).save(
                entry.model_copy(update={"lesson": "post-migration feedback"}),
                expected_revision=1,
            )
            == 2
        )
    with factory() as session:
        original_revision = session.get(JournalEntryRecord, (entry.id, 1)).entry_data
        original_snapshot = session.get(
            ScoreSnapshotRecord, entry.plan.score_snapshot.id
        ).snapshot_data
    assert original_revision == legacy_entry
    assert original_snapshot == legacy_snapshot


def test_postgres_evidence_is_append_only_and_runtime_privileges_are_restricted(
    migrated_postgres_database: tuple[Engine, str],
):
    engine, runtime_role = migrated_postgres_database
    decision_at = datetime(2026, 1, 2, 15, 0, tzinfo=UTC)
    source = SourceDocument(
        source_id="ats005-runtime-probe",
        version=1,
        source_type="filing",
        source_url="https://example.test/source",
        publisher="ABC Inc.",
        subject="ABC",
        published_at=decision_at - timedelta(hours=1),
        observed_at=decision_at - timedelta(minutes=30),
        content_hash="a" * 64,
    )
    evidence = EvidenceItem(
        source_document_version_id=source.id,
        subject="ABC",
        factor="demand_acceleration",
        excerpt="Demand increased.",
        confidence=Decimal("0.9"),
        verification_status=EvidenceStatus.VERIFIED,
        verifier_identity="reviewer:postgres-test",
        verified_at=decision_at - timedelta(minutes=10),
        verification_method="source_reconciliation",
        verification_policy_version="ats-evidence-1",
        observed_at=decision_at - timedelta(minutes=20),
    )
    factory = sessionmaker(bind=engine)
    with factory.begin() as session:
        repository = EvidenceRepository(session)
        repository.save_source(source)
        repository.save_evidence(evidence)

    with engine.connect() as connection:
        schema = connection.scalar(text("SELECT current_schema()"))
        for table in ("ats_source_documents", "ats_evidence_items"):
            for privilege, allowed in (
                ("SELECT", True),
                ("INSERT", True),
                ("UPDATE", False),
                ("DELETE", False),
                ("TRUNCATE", False),
            ):
                assert (
                    connection.scalar(
                        text("SELECT has_table_privilege(:role, :table, :privilege)"),
                        {
                            "role": runtime_role,
                            "table": f"{schema}.{table}",
                            "privilege": privilege,
                        },
                    )
                    is allowed
                )

    trigger_statements = (
        "UPDATE ats_evidence_items SET evidence_data = '{}'::jsonb",
        (
            "UPDATE ats_evidence_items "
            "SET evidence_data = jsonb_set(evidence_data, "
            "'{verification_status}', '\"rejected\"'::jsonb)"
        ),
        "DELETE FROM ats_evidence_items",
        "TRUNCATE TABLE ats_evidence_items",
        "UPDATE ats_source_documents SET source_data = '{}'::jsonb",
        "DELETE FROM ats_source_documents",
        "TRUNCATE TABLE ats_source_documents CASCADE",
    )
    for statement in trigger_statements:
        with (
            pytest.raises(DBAPIError, match="append-only"),
            engine.begin() as connection,
        ):
            connection.exec_driver_sql(statement)

    runtime_source = SourceDocument(
        source_id="ats005-runtime-insert",
        version=1,
        source_type="filing",
        publisher="ABC Inc.",
        subject="ABC",
        published_at=decision_at - timedelta(hours=1),
        observed_at=decision_at - timedelta(minutes=30),
        content_ref="filing://abc/runtime-insert",
    )
    runtime_evidence = EvidenceItem(
        source_document_version_id=runtime_source.id,
        subject="ABC",
        factor="demand_acceleration",
        excerpt="Demand increased.",
        confidence=Decimal("0.8"),
        observed_at=decision_at - timedelta(minutes=20),
    )
    quoted_role = engine.dialect.identifier_preparer.quote(runtime_role)
    with engine.begin() as connection:
        connection.exec_driver_sql(f"SET ROLE {quoted_role}")
        connection.execute(
            insert(SourceDocumentRecord).values(
                id=runtime_source.id,
                source_id=runtime_source.source_id,
                version=runtime_source.version,
                source_data=runtime_source.model_dump(mode="json"),
            )
        )
        connection.execute(
            insert(EvidenceItemRecord).values(
                id=runtime_evidence.id,
                version=runtime_evidence.version,
                source_document_version_id=runtime_source.id,
                evidence_data=runtime_evidence.model_dump(mode="json"),
            )
        )
        assert connection.scalar(text("SELECT count(*) FROM ats_source_documents")) == 2
        assert connection.scalar(text("SELECT count(*) FROM ats_evidence_items")) == 2

    forbidden_statements = (
        "UPDATE ats_evidence_items SET evidence_data = '{}'::jsonb",
        "DELETE FROM ats_evidence_items",
        "TRUNCATE TABLE ats_evidence_items",
        "ALTER TABLE ats_evidence_items ADD COLUMN runtime_probe integer",
        "ALTER TABLE ats_evidence_items DISABLE TRIGGER ALL",
        "CREATE TABLE ats_evidence_runtime_probe (id integer)",
        f'ALTER SCHEMA "{schema}" RENAME TO ats_evidence_runtime_probe',
    )
    for statement in forbidden_statements:
        with pytest.raises(DBAPIError), engine.connect() as connection:
            connection.exec_driver_sql(f"SET ROLE {quoted_role}")
            connection.exec_driver_sql(statement)

    with factory() as session:
        assert (
            session.scalar(select(func.count()).select_from(SourceDocumentRecord)) == 2
        )
        assert session.scalar(select(func.count()).select_from(EvidenceItemRecord)) == 2
        historical = EvidenceRepository(session).get_evidence(evidence.id, 1)
        assert historical == evidence


def test_postgres_traceability_from_evidence_through_score_to_journal(
    migrated_postgres_engine: Engine,
):
    decision_at = datetime(2026, 1, 2, 15, 0, tzinfo=UTC)
    source = SourceDocument(
        source_id="ats005-score-trace",
        version=1,
        source_type="filing",
        source_url="https://example.test/abc-filing",
        publisher="ABC Inc.",
        subject="ABC",
        published_at=decision_at - timedelta(hours=1),
        observed_at=decision_at - timedelta(minutes=30),
        content_ref="filing://abc/annual-report/2026",
        version_metadata={"accession": "0000001"},
    )
    evidence = EvidenceItem(
        source_document_version_id=source.id,
        subject="ABC",
        factor="demand_acceleration",
        inflection_stage=5,
        excerpt="Demand increased during the reporting period.",
        confidence=Decimal("0.96"),
        verification_status=EvidenceStatus.VERIFIED,
        verifier_identity="reviewer:trace-test",
        verified_at=decision_at - timedelta(minutes=10),
        verification_method="source_reconciliation",
        verification_policy_version="ats-evidence-1",
        verification_notes="Filing excerpt reconciled against reported figures.",
        observed_at=decision_at - timedelta(minutes=20),
    )
    eligibility = evaluate_scoring_eligibility(
        subject="ABC",
        factor="demand_acceleration",
        requested_contribution=Decimal(100),
        decision_at=decision_at,
        evidence_items=(evidence,),
        sources={source.id: source},
        inflection_stage=5,
    )
    assert eligibility.eligible
    contribution = score_contribution_from_eligibility(
        factor="demand_acceleration",
        result=eligibility,
        evidence_items=(evidence,),
        sources={source.id: source},
    )
    trade_template = make_complete_trade()
    original_entry = trade_template.model_copy(
        update={
            "orders": (),
            "fills": (),
            "position_snapshots": (),
            "result": None,
            "created_at": trade_template.plan.planned_at,
        }
    )
    original_snapshot = original_entry.plan.score_snapshot
    snapshot = original_snapshot.model_copy(
        update={
            "id": uuid4(),
            "decided_at": original_snapshot.decided_at + timedelta(minutes=1),
            "discovery": 15,
            "contributions": (contribution,),
        }
    )
    prior_plans = (*original_entry.prior_plans, original_entry.plan)
    plan = original_entry.plan.model_copy(
        update={
            "id": uuid4(),
            "version": original_entry.plan.version + 1,
            "supersedes_plan_id": original_entry.plan.id,
            "planned_at": original_entry.plan.planned_at + timedelta(minutes=1),
            "score_snapshot": snapshot,
        }
    )
    order = trade_template.orders[0].model_copy(
        update={
            "id": uuid4(),
            "plan_id": plan.id,
            "created_at": plan.planned_at + timedelta(minutes=1),
        }
    )
    rescored_entry = original_entry.model_copy(
        update={
            "plan": plan,
            "prior_plans": prior_plans,
            "orders": (order,),
            "created_at": order.created_at,
        }
    )
    factory = sessionmaker(bind=migrated_postgres_engine)
    with factory.begin() as session:
        evidence_repository = EvidenceRepository(session)
        evidence_repository.save_source(source)
        evidence_repository.save_evidence(evidence)
        journal_repository = JournalRepository(session)
        assert journal_repository.save(original_entry) == 1
        assert journal_repository.save(rescored_entry, expected_revision=1) == 2

    with factory() as session:
        journal_repository = JournalRepository(session)
        restored = journal_repository.get(rescored_entry.id)
        historical = journal_repository.get_revision(rescored_entry.id, 1)
        evidence_repository = EvidenceRepository(session)
        persisted_evidence = evidence_repository.get_evidence(evidence.id, 1)
        persisted_source = evidence_repository.get_source(source.id)
        stored_snapshot = session.get(ScoreSnapshotRecord, snapshot.id).snapshot_data
        historical_snapshot = session.get(
            ScoreSnapshotRecord, original_snapshot.id
        ).snapshot_data
        snapshot_count = session.scalar(
            select(func.count()).select_from(ScoreSnapshotRecord)
        )

    assert restored == rescored_entry
    assert historical == original_entry
    assert historical.plan.score_snapshot.id == original_snapshot.id
    assert restored.plan.score_snapshot.id == snapshot.id
    assert snapshot_count == 2
    assert decode_score_snapshot(historical_snapshot) == original_snapshot
    stored_ref = restored.plan.score_snapshot.contributions[0].evidence[0]
    assert stored_ref.id == evidence.id
    assert stored_ref.version == evidence.version
    assert stored_ref.source_document_version_id == source.id
    assert stored_ref.published_at == source.published_at
    assert stored_ref.observed_at == evidence.observed_at
    assert persisted_evidence == evidence
    assert persisted_source == source
    assert stored_snapshot["schema_version"] == 4
    assert stored_snapshot["data"]["contributions"][0]["evidence"][0]["version"] == 1
    stored_evidence_ref = stored_snapshot["data"]["contributions"][0]["evidence"][0]
    assert stored_evidence_ref[
        "verified_at"
    ] == evidence.verified_at.isoformat().replace("+00:00", "Z")
    assert stored_evidence_ref["verifier_identity"] == "reviewer:trace-test"
    assert stored_evidence_ref["verification_policy_version"] == "ats-evidence-1"
    assert original_entry.plan.score_snapshot is original_snapshot


def _loss_state_fixture_inputs(
    account_id: UUID,
    *,
    session_date: datetime,
    source_sequence: int,
    current_nlv: str,
    daily_opening_nlv: str = "100000",
    weekly_opening_nlv: str = "100000",
    daily_equity: str = "100000",
    weekly_equity: str = "50000",
):
    calendar = XNYSCalendar()
    local = session_date.astimezone(UTC)
    provisional = calendar.resolve(local.replace(hour=15, minute=0))
    assert provisional is not None
    monday = local.date() - timedelta(days=local.weekday())
    monday_open = datetime(monday.year, monday.month, monday.day, 13, 30, tzinfo=UTC)
    week_opening_session = calendar.resolve(monday_open)
    assert week_opening_session is not None
    daily_opened_at = provisional.open_utc
    weekly_opened_at = week_opening_session.open_utc
    as_of = daily_opened_at + timedelta(hours=1)
    resolved = calendar.resolve(as_of)
    assert resolved is not None
    session = replace(
        resolved,
        schedule_verified=True,
        authority_revision="TEST-ONLY-CALENDAR-ATTESTATION",
        eligible_new_risk=True,
    )
    valuation = VerifiedAccountValuation(
        account_id=account_id,
        source_id="TEST-ONLY-VALUATION-SOURCE",
        source_epoch="test-epoch-1",
        source_sequence=source_sequence,
        as_of=as_of,
        base_currency="USD",
        net_liquidation=Decimal(current_nlv),
        complete=True,
        reconciled=True,
        provenance_ref="TEST-ONLY-VALUATION-PROVENANCE",
    )
    daily_opening = PeriodOpeningEquity(
        account_id=account_id,
        period_kind=PeriodKind.DAILY,
        period_id=session.session_id,
        calendar_version=session.calendar_version,
        opened_at=daily_opened_at,
        opening_equity=Decimal(daily_equity),
        opening_nlv=Decimal(daily_opening_nlv),
        base_currency="USD",
        source_id="TEST-ONLY-VALUATION-SOURCE",
        source_epoch="test-epoch-1",
        source_sequence=1,
        immutable_version=1,
        complete=True,
        reconciled=True,
        provenance_ref="TEST-ONLY-DAILY-OPENING",
    )
    weekly_opening = PeriodOpeningEquity(
        account_id=account_id,
        period_kind=PeriodKind.WEEKLY,
        period_id=session.week_id,
        calendar_version=session.calendar_version,
        opened_at=weekly_opened_at,
        opening_equity=Decimal(weekly_equity),
        opening_nlv=Decimal(weekly_opening_nlv),
        base_currency="USD",
        source_id="TEST-ONLY-VALUATION-SOURCE",
        source_epoch="test-epoch-1",
        source_sequence=1,
        immutable_version=1,
        complete=True,
        reconciled=True,
        provenance_ref="TEST-ONLY-WEEKLY-OPENING",
    )
    daily_flows = ExternalFlowReconciliation(
        account_id=account_id,
        period_kind=PeriodKind.DAILY,
        period_id=session.session_id,
        window_start=daily_opened_at,
        as_of=as_of,
        base_currency="USD",
        source_id="TEST-ONLY-CASH-SOURCE",
        source_epoch="test-cash-epoch",
        source_sequence=1,
        reconciliation_id="TEST-ONLY-DAILY-RECONCILIATION",
        flows=(),
        complete=True,
        reconciled=True,
        provenance_ref="TEST-ONLY-DAILY-FLOW-RECONCILIATION",
    )
    weekly_flows = ExternalFlowReconciliation(
        account_id=account_id,
        period_kind=PeriodKind.WEEKLY,
        period_id=session.week_id,
        window_start=weekly_opened_at,
        as_of=as_of,
        base_currency="USD",
        source_id="TEST-ONLY-CASH-SOURCE",
        source_epoch="test-cash-epoch",
        source_sequence=1,
        reconciliation_id="TEST-ONLY-WEEKLY-RECONCILIATION",
        flows=(),
        complete=True,
        reconciled=True,
        provenance_ref="TEST-ONLY-WEEKLY-FLOW-RECONCILIATION",
    )
    evaluation = evaluate_loss_accounting(
        valuation=valuation,
        daily_opening=daily_opening,
        weekly_opening=weekly_opening,
        daily_flows=daily_flows,
        weekly_flows=weekly_flows,
        session=session,
        evaluated_at=as_of,
        max_valuation_age=timedelta(minutes=5),
    )
    return {
        "valuation": valuation,
        "daily_opening": daily_opening,
        "weekly_opening": weekly_opening,
        "session": session,
        "evaluation": evaluation,
        "as_of": as_of,
    }


def _persist_loss_state_inputs(
    repository: LossStateRepository,
    session,
    inputs,
    *,
    include_openings: bool = True,
):
    valuation = repository.append_valuation(session, inputs["valuation"])
    daily = weekly = None
    if include_openings:
        daily = repository.append_opening(session, inputs["daily_opening"])
        weekly = repository.append_opening(session, inputs["weekly_opening"])
    return valuation, daily, weekly


def _append_loss_evaluation(
    repository: LossStateRepository,
    session,
    inputs,
    stored,
    *,
    correction_of_state_version: int | None = None,
):
    valuation, daily, weekly = stored
    assert daily is not None and weekly is not None
    return repository.append_evaluation(
        session,
        evaluation=inputs["evaluation"],
        valuation_id=valuation.record_id,
        daily_opening_id=daily.record_id,
        weekly_opening_id=weekly.record_id,
        daily_flow_ids=(),
        weekly_flow_ids=(),
        daily_period_id=inputs["session"].session_id,
        weekly_period_id=inputs["session"].week_id,
        input_digest=sha256(b"TEST-ONLY-LOSS-INPUTS").hexdigest(),
        created_at=inputs["as_of"],
        correction_of_state_version=correction_of_state_version,
    )


def _seed_locked_then_recovered(factory, account_id):
    repository = LossStateRepository()
    first = _loss_state_fixture_inputs(
        account_id,
        session_date=datetime(2026, 10, 20, 13, 30, tzinfo=UTC),
        source_sequence=1,
        current_nlv="98500",
    )
    with factory.begin() as session:
        stored = _persist_loss_state_inputs(repository, session, first)
        _append_loss_evaluation(repository, session, first, stored)
    recovery = _loss_state_fixture_inputs(
        account_id,
        session_date=datetime(2026, 10, 20, 13, 30, tzinfo=UTC),
        source_sequence=2,
        current_nlv="100000",
    )
    with factory.begin() as session:
        recovered = _persist_loss_state_inputs(
            repository, session, recovery, include_openings=False
        )
        _append_loss_evaluation(
            repository, session, recovery, (recovered[0], stored[1], stored[2])
        )
    return recovery


def test_postgres_repository_provider_keeps_lock_after_recovery_and_fails_closed(
    migrated_postgres_engine: Engine,
):
    account_id = uuid4()
    factory = sessionmaker(bind=migrated_postgres_engine, expire_on_commit=False)
    recovery = _seed_locked_then_recovered(factory, account_id)
    provider = RepositoryLossLockProvider()

    with factory.begin() as session:
        state = provider.evaluate_in_transaction(session, account_id, recovery["as_of"])
    assert state is not None
    assert state.state_version == 3
    assert state.daily_locked and state.weekly_locked
    assert state.daily_loss_r == 0  # NLV recovered, yet the sticky lock holds
    assert state.blocks_risk_increase() is True
    assert state.daily_limit_r == Decimal(-3)
    assert state.weekly_limit_r == Decimal(-6)

    with factory.begin() as session:
        # Unknown account: no durable evidence, so no state.
        assert (
            provider.evaluate_in_transaction(session, uuid4(), recovery["as_of"])
            is None
        )
        # A later session has no authenticated opening, so fail closed.
        assert (
            provider.evaluate_in_transaction(
                session, account_id, recovery["as_of"] + timedelta(days=1)
            )
            is None
        )
        # No sessionless read is possible.
        assert provider.evaluate(account_id, recovery["as_of"]) is None


def test_postgres_layer3_binds_loss_state_version_and_denies_stale_replay(
    migrated_postgres_engine: Engine,
):
    factory, base_service, account_id, entries = _postgres_layer3_setup(
        migrated_postgres_engine
    )
    # The seeded state is locked, so authorization is denied and writes nothing.
    recovery = _seed_locked_then_recovered(factory, account_id)
    at = recovery["as_of"] + timedelta(minutes=1)
    service = Layer3AuthorizationService(
        policy=POSTGRES_RISK_POLICY,
        taxonomy=POSTGRES_RISK_TAXONOMY,
        loss_locks=RepositoryLossLockProvider(),
        principal_provider=_PostgresPrincipalProvider(account_id),
        snapshot_verifier=_PostgresSnapshotVerifier(),
        broker_event_verifier=None,
        ats009_policy_version="ATS-009-v0.1",
        clock=lambda: at,
    )
    del base_service
    with factory.begin() as session:
        decision = service.authorize(
            session, plan_id=entries[0].plan.id, idempotency_key="locked-1"
        )
    assert decision.decision is RiskDecision.DENY
    assert decision.reason == Layer3Reason.ATS009_LOSS_LOCKED.value
    with factory() as session:
        assert (
            session.scalar(select(func.count()).select_from(RiskReservationEventRecord))
            == 0
        )


def _unlocked_loss_authorization_setup(
    migrated_postgres_engine: Engine,
):
    factory, _, account_id, entries = _postgres_layer3_setup(migrated_postgres_engine)
    repository = LossStateRepository()
    first = _loss_state_fixture_inputs(
        account_id,
        session_date=datetime(2026, 10, 20, 13, 30, tzinfo=UTC),
        source_sequence=1,
        current_nlv="100000",
    )
    with factory.begin() as session:
        stored = _persist_loss_state_inputs(repository, session, first)
        _append_loss_evaluation(repository, session, first, stored)
    at = first["as_of"] + timedelta(minutes=1)
    with factory.begin() as session:
        snapshot_publisher = Layer3AuthorizationService(
            policy=POSTGRES_RISK_POLICY,
            taxonomy=POSTGRES_RISK_TAXONOMY,
            loss_locks=None,
            principal_provider=_PostgresPrincipalProvider(account_id),
            snapshot_verifier=_PostgresSnapshotVerifier(),
            broker_event_verifier=None,
            clock=lambda: at,
        )
        snapshot_publisher.publish_snapshot(
            session,
            PortfolioSnapshot(
                account_id=account_id,
                version=2,
                as_of=at,
                policy_version=POSTGRES_RISK_POLICY.version,
                taxonomy_version=POSTGRES_RISK_TAXONOMY.version,
                equity=Decimal(100000),
                positions=(
                    PositionExposure(
                        ticker="XYZ",
                        quantity=950,
                        average_entry=Decimal(10),
                        active_stop=Decimal(8),
                        stop_confirmed=True,
                    ),
                ),
                pending_orders=(),
                positions_complete=True,
                orders_complete=True,
                ats_managed_exposure_excluded=True,
            ),
        )
    service = Layer3AuthorizationService(
        policy=POSTGRES_RISK_POLICY,
        taxonomy=POSTGRES_RISK_TAXONOMY,
        loss_locks=RepositoryLossLockProvider(),
        principal_provider=_PostgresPrincipalProvider(account_id),
        snapshot_verifier=_PostgresSnapshotVerifier(),
        broker_event_verifier=None,
        ats009_policy_version="ATS-009-v0.1",
        clock=lambda: at,
    )
    return factory, service, account_id, entries, first, stored


def test_postgres_unlocked_reservation_binds_version_and_new_event_invalidates_replay(
    migrated_postgres_engine: Engine,
):
    factory, service, account_id, entries, _first, stored = (
        _unlocked_loss_authorization_setup(migrated_postgres_engine)
    )
    repository = LossStateRepository()
    plan_id = entries[0].plan.id
    with factory.begin() as session:
        reserved = service.authorize(session, plan_id=plan_id, idempotency_key="bind-1")
    assert reserved.decision is not RiskDecision.DENY
    with factory() as session:
        event = session.scalar(select(RiskReservationEventRecord))
        assert event.event_data["loss_state"] == {
            "version": 1,
            "policy_version": "ATS-009-v0.1",
        }
    with factory.begin() as session:
        assert service.authorize(
            session, plan_id=plan_id, idempotency_key="bind-1"
        ) == reserved.model_copy(update={"replayed": True})

    second = _loss_state_fixture_inputs(
        account_id,
        session_date=datetime(2026, 10, 20, 13, 30, tzinfo=UTC),
        source_sequence=2,
        current_nlv="99900",
    )
    with factory.begin() as session:
        later = _persist_loss_state_inputs(
            repository, session, second, include_openings=False
        )
        _append_loss_evaluation(
            repository, session, second, (later[0], stored[1], stored[2])
        )
    with factory.begin() as session:
        stale = service.authorize(session, plan_id=plan_id, idempotency_key="bind-1")
    assert stale.decision is RiskDecision.DENY
    assert stale.reason == Layer3Reason.LOSS_STATE_VERSION_CONFLICT.value


def test_postgres_transaction_retry_recovers_and_exhaustion_leaves_no_rows(
    migrated_postgres_engine: Engine,
):
    factory, service, account_id, entries = _postgres_layer3_setup(
        migrated_postgres_engine
    )
    account_lock = select(
        func.pg_advisory_xact_lock(func.hashtextextended(str(account_id), 0))
    )

    def work(session):
        session.execute(text("SET LOCAL lock_timeout = '100ms'"))
        return service.authorize(
            session,
            plan_id=entries[0].plan.id,
            idempotency_key="retry-helper-key",
        )

    with factory.begin() as holder:
        holder.execute(account_lock)
        with pytest.raises(TransactionRetryExhausted) as info:
            run_in_transaction(factory, work, max_attempts=2)
    assert info.value.sqlstate == "55P03"
    assert info.value.attempts == 2
    with factory() as session:
        assert (
            session.scalar(select(func.count()).select_from(RiskReservationEventRecord))
            == 0
        )

    attempts = []

    def work_after_release(session):
        attempts.append(1)
        return work(session)

    decision = run_in_transaction(factory, work_after_release, max_attempts=3)
    assert decision.quantity == 40
    with factory() as session:
        assert (
            session.scalar(select(func.count()).select_from(RiskReservationEventRecord))
            == 1
        )


def test_postgres_loss_state_threshold_and_sticky_lock_survive_recovery(
    migrated_postgres_engine: Engine,
):
    repository = LossStateRepository()
    account_id = uuid4()
    factory = sessionmaker(bind=migrated_postgres_engine, expire_on_commit=False)
    first = _loss_state_fixture_inputs(
        account_id,
        session_date=datetime(2026, 10, 20, 13, 30, tzinfo=UTC),
        source_sequence=1,
        current_nlv="98500",
    )
    assert first["evaluation"].daily_threshold_crossed
    assert first["evaluation"].daily_r == Fraction(-3)
    assert first["evaluation"].weekly_threshold_crossed
    with factory.begin() as session:
        stored = _persist_loss_state_inputs(repository, session, first)
        events = _append_loss_evaluation(repository, session, first, stored)
        assert [event.event_type for event in events] == ["OBSERVED", "LOCKED"]
        assert events[-1].policy_version == "ATS-009-v0.1"
        assert events[-1].daily_pnl_numerator == "-1500"
        assert events[-1].daily_pnl_denominator == "1"
        assert events[-1].daily_r_numerator == "-3"
        assert events[-1].daily_r_denominator == "1"
        assert events[-1].weekly_r_numerator == "-6"
        assert events[-1].weekly_r_denominator == "1"

    recovery = _loss_state_fixture_inputs(
        account_id,
        session_date=datetime(2026, 10, 20, 13, 30, tzinfo=UTC),
        source_sequence=2,
        current_nlv="100000",
    )
    assert not recovery["evaluation"].daily_threshold_crossed
    with factory.begin() as session:
        stored_recovery = _persist_loss_state_inputs(
            repository, session, recovery, include_openings=False
        )
        _append_loss_evaluation(
            repository,
            session,
            recovery,
            (stored_recovery[0], stored[1], stored[2]),
        )
    with factory.begin() as session:
        projection = LossStateRepository().rebuild_projection(
            session,
            account_id=account_id,
            active_daily_period_id=first["session"].session_id,
            active_weekly_period_id=first["session"].week_id,
        )

    assert projection.daily_locked
    assert projection.weekly_locked
    assert projection.state_version == 3


def test_postgres_correction_appends_evidence_without_unlocking(
    migrated_postgres_engine: Engine,
):
    repository = LossStateRepository()
    account_id = uuid4()
    factory = sessionmaker(bind=migrated_postgres_engine, expire_on_commit=False)
    first = _loss_state_fixture_inputs(
        account_id,
        session_date=datetime(2026, 10, 20, 13, 30, tzinfo=UTC),
        source_sequence=1,
        current_nlv="98500",
    )
    with factory.begin() as session:
        original_stored = _persist_loss_state_inputs(repository, session, first)
        original_events = _append_loss_evaluation(
            repository, session, first, original_stored
        )

    corrected = _loss_state_fixture_inputs(
        account_id,
        session_date=datetime(2026, 10, 20, 13, 30, tzinfo=UTC),
        source_sequence=2,
        current_nlv="100000",
    )
    with factory.begin() as session:
        corrected_valuation = repository.append_valuation(
            session,
            corrected["valuation"],
            correction_of_id=original_stored[0].record_id,
        )
        correction_events = repository.append_correction(
            session,
            evaluation=corrected["evaluation"],
            valuation_id=corrected_valuation.record_id,
            daily_opening_id=original_stored[1].record_id,
            weekly_opening_id=original_stored[2].record_id,
            daily_flow_ids=(),
            weekly_flow_ids=(),
            daily_period_id=first["session"].session_id,
            weekly_period_id=first["session"].week_id,
            input_digest=sha256(b"TEST-ONLY-CORRECTION").hexdigest(),
            created_at=corrected["as_of"],
            correction_of_state_version=original_events[-1].state_version,
        )
        projection = repository.rebuild_projection(
            session,
            account_id=account_id,
            active_daily_period_id=first["session"].session_id,
            active_weekly_period_id=first["session"].week_id,
        )
    assert correction_events[0].event_type == "CORRECTION"
    assert (
        correction_events[0].correction_of_state_version
        == original_events[-1].state_version
    )
    assert projection.daily_locked and projection.weekly_locked


def test_postgres_daily_rollover_preserves_weekly_lock_and_week_requires_opening(
    migrated_postgres_engine: Engine,
):
    repository = LossStateRepository()
    account_id = uuid4()
    factory = sessionmaker(bind=migrated_postgres_engine, expire_on_commit=False)
    first = _loss_state_fixture_inputs(
        account_id,
        session_date=datetime(2026, 10, 20, 13, 30, tzinfo=UTC),
        source_sequence=1,
        current_nlv="98500",
    )
    with factory.begin() as session:
        initial = _persist_loss_state_inputs(repository, session, first)
        _append_loss_evaluation(repository, session, first, initial)

    next_day = _loss_state_fixture_inputs(
        account_id,
        session_date=datetime(2026, 10, 21, 13, 30, tzinfo=UTC),
        source_sequence=2,
        current_nlv="100000",
    )
    with factory.begin() as session:
        repository.append_valuation(session, next_day["valuation"])
        day_opening = repository.append_opening(session, next_day["daily_opening"])
        projection = repository.rebuild_projection(
            session,
            account_id=account_id,
            active_daily_period_id=next_day["session"].session_id,
            active_weekly_period_id=next_day["session"].week_id,
        )

    assert not projection.daily_locked
    assert projection.weekly_locked
    assert day_opening.record_id != initial[1].record_id

    next_week = _loss_state_fixture_inputs(
        account_id,
        session_date=datetime(2026, 10, 26, 13, 30, tzinfo=UTC),
        source_sequence=3,
        current_nlv="100000",
    )
    with factory.begin() as session:
        repository.append_valuation(session, next_week["valuation"])
        repository.append_opening(session, next_week["daily_opening"])
        with pytest.raises(LossStateConflict, match="opening evidence"):
            repository.rebuild_projection(
                session,
                account_id=account_id,
                active_daily_period_id=next_week["session"].session_id,
                active_weekly_period_id=next_week["session"].week_id,
            )
        incomplete_week = next_week["weekly_opening"].model_copy(
            update={"complete": False}
        )
        with pytest.raises(LossStateConflict, match="incomplete"):
            repository.append_opening(session, incomplete_week)
        repository.append_opening(session, next_week["weekly_opening"])
        projection = repository.rebuild_projection(
            session,
            account_id=account_id,
            active_daily_period_id=next_week["session"].session_id,
            active_weekly_period_id=next_week["session"].week_id,
        )
    assert not projection.daily_locked
    assert not projection.weekly_locked


def test_postgres_loss_source_replay_conflict_and_regression(
    migrated_postgres_engine: Engine,
):
    repository = LossStateRepository()
    account_id = uuid4()
    factory = sessionmaker(bind=migrated_postgres_engine, expire_on_commit=False)
    inputs = _loss_state_fixture_inputs(
        account_id,
        session_date=datetime(2026, 10, 20, 13, 30, tzinfo=UTC),
        source_sequence=2,
        current_nlv="100000",
    )
    with factory.begin() as session:
        first = repository.append_valuation(session, inputs["valuation"])
    with factory.begin() as session:
        equivalent_decimal = inputs["valuation"].model_copy(
            update={"net_liquidation": Decimal("100000.00")}
        )
        replay = repository.append_valuation(session, equivalent_decimal)
    assert replay.replayed and replay.record_id == first.record_id

    conflicting = inputs["valuation"].model_copy(
        update={"net_liquidation": Decimal(99999)}
    )
    with (
        factory.begin() as session,
        pytest.raises(LossStateConflict, match="conflicting"),
    ):
        repository.append_valuation(session, conflicting)

    regressed = inputs["valuation"].model_copy(update={"source_sequence": 1})
    with (
        factory.begin() as session,
        pytest.raises(LossStateConflict, match="regressed"),
    ):
        repository.append_valuation(session, regressed)


def test_postgres_loss_flow_replay_conflict_and_reordered_source(
    migrated_postgres_engine: Engine,
):
    repository = LossStateRepository()
    account_id = uuid4()
    factory = sessionmaker(bind=migrated_postgres_engine, expire_on_commit=False)

    def flow(sequence: int, amount: str) -> ReconciledExternalFlow:
        return ReconciledExternalFlow(
            account_id=account_id,
            flow_id=f"TEST-ONLY-FLOW-{sequence}",
            source_id="TEST-ONLY-CASH-SOURCE",
            source_epoch="test-cash-epoch",
            source_sequence=sequence,
            occurred_at=datetime(2026, 10, 20, 14, 0, tzinfo=UTC),
            kind=ExternalFlowKind.CONTRIBUTION,
            signed_amount=Decimal(amount),
            base_currency="USD",
            provenance_ref="TEST-ONLY-CASH-PROVENANCE",
        )

    first_flow = flow(1, "100")
    with factory.begin() as session:
        first = repository.append_external_flow(session, first_flow)
    with factory.begin() as session:
        replay = repository.append_external_flow(session, first_flow)
    assert replay.replayed and replay.record_id == first.record_id

    conflicting = flow(1, "200")
    with (
        factory.begin() as session,
        pytest.raises(LossStateConflict, match="conflicting"),
    ):
        repository.append_external_flow(session, conflicting)

    with factory.begin() as session:
        repository.append_external_flow(session, flow(3, "300"))
    with (
        factory.begin() as session,
        pytest.raises(LossStateConflict, match="regressed"),
    ):
        repository.append_external_flow(session, flow(2, "200"))


def test_postgres_loss_state_partial_writes_roll_back_atomically(
    migrated_postgres_engine: Engine,
):
    repository = LossStateRepository()
    account_id = uuid4()
    factory = sessionmaker(bind=migrated_postgres_engine, expire_on_commit=False)
    inputs = _loss_state_fixture_inputs(
        account_id,
        session_date=datetime(2026, 10, 20, 13, 30, tzinfo=UTC),
        source_sequence=1,
        current_nlv="98500",
    )
    with (
        pytest.raises(RuntimeError, match="forced test rollback"),
        factory.begin() as session,
    ):
        stored = _persist_loss_state_inputs(repository, session, inputs)
        _append_loss_evaluation(repository, session, inputs, stored)
        raise RuntimeError("forced test rollback")

    with factory() as session:
        assert (
            session.scalar(
                select(func.count()).select_from(LossValuationObservationRecord)
            )
            == 0
        )
        assert (
            session.scalar(select(func.count()).select_from(LossPeriodOpeningRecord))
            == 0
        )
        assert (
            session.scalar(select(func.count()).select_from(LossStateEventRecord)) == 0
        )


def test_postgres_loss_state_replay_concurrency_rebuilds_same_projection(
    migrated_postgres_engine: Engine,
):
    repository = LossStateRepository()
    account_id = uuid4()
    factory = sessionmaker(bind=migrated_postgres_engine, expire_on_commit=False)
    inputs = _loss_state_fixture_inputs(
        account_id,
        session_date=datetime(2026, 10, 20, 13, 30, tzinfo=UTC),
        source_sequence=1,
        current_nlv="98500",
    )
    barrier = Barrier(2)

    def append_same_observation():
        with factory.begin() as session:
            barrier.wait(timeout=10)
            return repository.append_valuation(session, inputs["valuation"])

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = tuple(executor.map(lambda _: append_same_observation(), range(2)))
    assert sorted(result.replayed for result in results) == [False, True]
    assert results[0].record_id == results[1].record_id


def test_postgres_loss_tables_are_append_only_and_runtime_grants_are_restricted(
    migrated_postgres_database: tuple[Engine, str],
):
    engine, runtime_role = migrated_postgres_database
    with engine.begin() as connection:
        for table in (
            "ats_loss_valuation_observations",
            "ats_loss_external_flows",
            "ats_loss_period_openings",
            "ats_loss_state_events",
        ):
            privileges = connection.execute(
                text(
                    "SELECT has_table_privilege(:role, :table, 'SELECT'), "
                    "has_table_privilege(:role, :table, 'INSERT'), "
                    "has_table_privilege(:role, :table, 'UPDATE'), "
                    "has_table_privilege(:role, :table, 'DELETE'), "
                    "has_table_privilege(:role, :table, 'TRUNCATE')"
                ),
                {"role": runtime_role, "table": table},
            ).one()
            assert tuple(privileges) == (True, True, False, False, False)
            trigger_count = connection.scalar(
                text(
                    "SELECT count(*) FROM pg_trigger t "
                    "JOIN pg_class c ON c.oid = t.tgrelid "
                    "JOIN pg_namespace n ON n.oid = c.relnamespace "
                    "WHERE n.nspname = current_schema() "
                    "AND c.relname = :table AND NOT t.tgisinternal"
                ),
                {"table": table},
            )
            assert trigger_count == 2

    repository = LossStateRepository()
    account_id = uuid4()
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    inputs = _loss_state_fixture_inputs(
        account_id,
        session_date=datetime(2026, 10, 20, 13, 30, tzinfo=UTC),
        source_sequence=1,
        current_nlv="98500",
    )
    with factory.begin() as session:
        stored = _persist_loss_state_inputs(repository, session, inputs)
        repository.append_external_flow(
            session,
            ReconciledExternalFlow(
                account_id=account_id,
                flow_id="TEST-ONLY-FLOW-TRIGGER",
                source_id="TEST-ONLY-CASH-SOURCE",
                source_epoch="test-cash-epoch",
                source_sequence=1,
                occurred_at=inputs["as_of"],
                kind=ExternalFlowKind.CONTRIBUTION,
                signed_amount=Decimal(1),
                base_currency="USD",
                provenance_ref="TEST-ONLY-CASH-PROVENANCE",
            ),
        )
        _append_loss_evaluation(repository, session, inputs, stored)

    table_and_update = (
        ("ats_loss_valuation_observations", "digest = digest"),
        ("ats_loss_external_flows", "digest = digest"),
        ("ats_loss_period_openings", "digest = digest"),
        ("ats_loss_state_events", "input_digest = input_digest"),
    )
    for table, update_clause in table_and_update:
        for statement in (
            f"UPDATE {table} SET {update_clause}",
            f"DELETE FROM {table}",
            f"TRUNCATE TABLE {table} CASCADE",
        ):
            with (
                pytest.raises(DBAPIError, match="append-only"),
                factory.begin() as session,
            ):
                session.execute(text(statement))


@pytest.mark.parametrize("sqlstate", ["40001", "40P01", "55P03"])
@pytest.mark.parametrize("exhaust", [False, True])
def test_f1_boundary_retries_postgres_failure_after_reservation_atomically(
    migrated_postgres_engine, monkeypatch, sqlstate, exhaust
):
    factory, service, account_id, entries, _, _ = _unlocked_loss_authorization_setup(
        migrated_postgres_engine
    )
    original = service._append_event
    transactions = []
    observed_keys = []
    reads = []
    provider = service._loss_locks
    original_read = provider.evaluate_in_transaction

    def read(session, requested_account, at):
        assert requested_account == account_id
        reads.append((session, session.scalar(text("SELECT txid_current()"))))
        return original_read(session, requested_account, at)

    monkeypatch.setattr(provider, "evaluate_in_transaction", read)

    def append(session, **kwargs):
        original(session, **kwargs)
        xid = session.scalar(text("SELECT txid_current()"))
        transactions.append(xid)
        observed_keys.append(kwargs["idempotency_key"])
        assert len(reads) >= 2
        assert reads[-1] == reads[-2] == (session, xid)
        assert (
            session.scalar(select(func.count()).select_from(RiskReservationEventRecord))
            == 1
        )
        with factory() as observer:
            assert (
                observer.scalar(
                    select(func.count()).select_from(RiskReservationEventRecord)
                )
                == 0
            )
        if exhaust or len(transactions) == 1:
            # Server-raised SQLSTATE after real writes, not a mocked DBAPIError.
            session.execute(
                text(
                    f"DO $$ BEGIN RAISE EXCEPTION 'F1 fault' USING ERRCODE = '{sqlstate}'; END $$"
                )
            )

    monkeypatch.setattr(service, "_append_event", append)
    kwargs = {
        "plan_id": entries[0].plan.id,
        "idempotency_key": "f1-original-key",
        "max_attempts": 2,
    }
    if exhaust:
        with pytest.raises(TransactionRetryExhausted) as info:
            service.authorize_with_retry(factory, **kwargs)
        assert info.value.sqlstate == sqlstate
        assert info.value.attempts == 2
    else:
        result = service.authorize_with_retry(factory, **kwargs)
        assert result.quantity == 40
        assert not result.execution_enabled and not result.replayed
        replay = service.authorize_with_retry(factory, **kwargs)
        assert replay.replayed and not replay.execution_enabled
        assert replay.reservation_id == result.reservation_id
    assert len(set(transactions)) == 2
    assert observed_keys == ["reserve:f1-original-key"] * 2
    with factory() as session:
        assert session.scalar(
            select(func.count()).select_from(RiskReservationEventRecord)
        ) == (0 if exhaust else 1)
        assert (
            session.scalar(select(func.count()).select_from(LossStateEventRecord)) == 1
        )


@pytest.mark.parametrize("database_failure", [False, True])
def test_f1_boundary_does_not_retry_application_or_integrity_failure(
    migrated_postgres_engine, monkeypatch, database_failure
):
    factory, service, _, entries, _, _ = _unlocked_loss_authorization_setup(
        migrated_postgres_engine
    )
    original = service._append_event
    calls = []

    def append(session, **kwargs):
        original(session, **kwargs)
        calls.append(1)
        if database_failure:
            session.execute(
                text(
                    "DO $$ BEGIN RAISE EXCEPTION 'F1 integrity' USING ERRCODE = '23505'; END $$"
                )
            )
        raise ValueError("F1 application failure")

    monkeypatch.setattr(service, "_append_event", append)
    with pytest.raises(IntegrityError if database_failure else ValueError):
        service.authorize_with_retry(
            factory, plan_id=entries[0].plan.id, idempotency_key="f1-fail"
        )
    assert calls == [1]
    with factory() as session:
        assert (
            session.scalar(select(func.count()).select_from(RiskReservationEventRecord))
            == 0
        )


def test_f1_boundary_rejects_state_change_before_reservation(
    migrated_postgres_engine, monkeypatch
):
    factory, service, _, entries, _, _ = _unlocked_loss_authorization_setup(
        migrated_postgres_engine
    )
    provider = service._loss_locks
    original = provider.evaluate_in_transaction
    calls = []

    def changed_state(session, account_id, at):
        state = original(session, account_id, at)
        calls.append(1)
        return (
            state
            if len(calls) == 1
            else state.model_copy(update={"state_version": state.state_version + 1})
        )

    monkeypatch.setattr(provider, "evaluate_in_transaction", changed_state)
    result = service.authorize_with_retry(
        factory, plan_id=entries[0].plan.id, idempotency_key="f1-stale"
    )
    assert result.reason == Layer3Reason.LOSS_STATE_VERSION_CONFLICT.value
    assert result.decision is RiskDecision.DENY
    assert not result.execution_enabled
    assert len(calls) == 2
    with factory() as session:
        assert (
            session.scalar(select(func.count()).select_from(RiskReservationEventRecord))
            == 0
        )


def test_f1_concurrent_boundary_replays_share_one_durable_reservation(
    migrated_postgres_engine,
):
    factory, service, _, entries, _, _ = _unlocked_loss_authorization_setup(
        migrated_postgres_engine
    )
    barrier = Barrier(3)

    def reserve(_):
        barrier.wait(timeout=10)
        return service.authorize_with_retry(
            factory, plan_id=entries[0].plan.id, idempotency_key="f1-concurrent"
        )

    with ThreadPoolExecutor(max_workers=3) as executor:
        results = tuple(executor.map(reserve, range(3)))
    assert all(
        result.quantity == 40 and not result.execution_enabled for result in results
    )
    assert sum(result.replayed for result in results) == 2
    assert len({result.reservation_id for result in results}) == 1
    with factory() as session:
        assert (
            session.scalar(select(func.count()).select_from(RiskReservationEventRecord))
            == 1
        )


def test_f1_boundary_real_lock_timeout_exhausts_without_writes(
    migrated_postgres_engine,
):
    from sqlalchemy import event

    factory, service, account_id, entries, _, _ = _unlocked_loss_authorization_setup(
        migrated_postgres_engine
    )
    attempts = []

    @event.listens_for(factory, "after_begin")
    def timeout(session, transaction, connection):
        attempts.append(session)
        connection.execute(text("SET LOCAL lock_timeout = '50ms'"))

    with sessionmaker(bind=migrated_postgres_engine).begin() as holder:
        service._lock_account(holder, account_id)
        with pytest.raises(TransactionRetryExhausted) as info:
            service.authorize_with_retry(
                factory,
                plan_id=entries[0].plan.id,
                idempotency_key="f1-locked",
                max_attempts=2,
            )
    assert info.value.sqlstate == "55P03"
    assert len(attempts) == 2 and attempts[0] is not attempts[1]
    result = service.authorize_with_retry(
        factory, plan_id=entries[0].plan.id, idempotency_key="f1-locked"
    )
    assert result.quantity == 40 and not result.replayed
    with factory() as session:
        assert (
            session.scalar(select(func.count()).select_from(RiskReservationEventRecord))
            == 1
        )


@pytest.mark.parametrize("exhaust", [False, True])
def test_f1_retry_helper_handles_real_serialization_conflict(
    migrated_postgres_engine, exhaust
):
    engine = migrated_postgres_engine
    with engine.begin() as c:
        c.exec_driver_sql(
            "CREATE TABLE f1_capacity (id int primary key, value int not null)"
        )
        c.exec_driver_sql("INSERT INTO f1_capacity VALUES (1, 0)")
        c.exec_driver_sql("CREATE TABLE f1_requests (key text primary key)")
    factory = sessionmaker(
        bind=engine.execution_options(isolation_level="SERIALIZABLE")
    )
    attempts = []

    def work(session):
        attempts.append(session)
        session.execute(text("INSERT INTO f1_requests VALUES ('same-key')"))
        current = session.scalar(text("SELECT value FROM f1_capacity WHERE id=1"))
        if exhaust or len(attempts) == 1:
            with engine.begin() as c:
                c.execute(text("UPDATE f1_capacity SET value=value+1 WHERE id=1"))
        session.execute(
            text("UPDATE f1_capacity SET value=:v WHERE id=1"), {"v": current + 1}
        )
        return current

    if exhaust:
        with pytest.raises(TransactionRetryExhausted) as info:
            run_in_transaction(factory, work, max_attempts=2)
        assert info.value.sqlstate == "40001"
    else:
        assert run_in_transaction(factory, work, max_attempts=2) == 1
    assert len(attempts) == 2 and attempts[0] is not attempts[1]
    with engine.connect() as c:
        assert c.scalar(text("SELECT count(*) FROM f1_requests")) == (
            0 if exhaust else 1
        )
        assert c.scalar(text("SELECT value FROM f1_capacity WHERE id=1")) == 2


def test_f1_retry_helper_handles_real_deadlock(migrated_postgres_engine):
    engine = migrated_postgres_engine
    with engine.begin() as c:
        c.exec_driver_sql(
            "CREATE TABLE f1_deadlock (id int primary key, value int not null)"
        )
        c.exec_driver_sql("INSERT INTO f1_deadlock VALUES (1,0),(2,0)")
        c.exec_driver_sql("CREATE TABLE f1_deadlock_requests (key text primary key)")
    factory = sessionmaker(bind=engine)
    barrier = Barrier(2)
    attempts = []

    def competitor():
        with engine.begin() as c:
            c.execute(text("SET LOCAL deadlock_timeout = '5s'"))
            c.execute(text("SET LOCAL lock_timeout = '10s'"))
            c.execute(text("UPDATE f1_deadlock SET value=value+1 WHERE id=2"))
            barrier.wait(timeout=10)
            c.execute(text("UPDATE f1_deadlock SET value=value+1 WHERE id=1"))

    def work(session):
        attempts.append(session)
        session.execute(text("SET LOCAL deadlock_timeout = '50ms'"))
        session.execute(text("SET LOCAL lock_timeout = '10s'"))
        session.execute(text("INSERT INTO f1_deadlock_requests VALUES ('same-key')"))
        session.execute(text("UPDATE f1_deadlock SET value=value+1 WHERE id=1"))
        if len(attempts) == 1:
            barrier.wait(timeout=10)
        session.execute(text("UPDATE f1_deadlock SET value=value+1 WHERE id=2"))
        return "committed"

    with ThreadPoolExecutor(max_workers=1) as executor:
        other = executor.submit(competitor)
        assert run_in_transaction(factory, work, max_attempts=2) == "committed"
        other.result(timeout=15)
    assert len(attempts) == 2
    with engine.connect() as c:
        assert c.scalar(text("SELECT count(*) FROM f1_deadlock_requests")) == 1
        assert c.execute(
            text("SELECT value FROM f1_deadlock ORDER BY id")
        ).scalars().all() == [2, 2]


@pytest.mark.parametrize("provider_available", [False, True])
def test_f1_unbound_legacy_replay_is_denied_after_provider_change(
    migrated_postgres_engine, provider_available
):
    factory, legacy, account_id, entries = _postgres_layer3_setup(
        migrated_postgres_engine
    )
    first = legacy.authorize_with_retry(
        factory, plan_id=entries[0].plan.id, idempotency_key="f1-unbound-legacy"
    )
    assert first.decision is not RiskDecision.DENY
    provider = RepositoryLossLockProvider() if provider_available else None
    service = Layer3AuthorizationService(
        policy=POSTGRES_RISK_POLICY,
        taxonomy=POSTGRES_RISK_TAXONOMY,
        loss_locks=provider,
        principal_provider=_PostgresPrincipalProvider(account_id),
        snapshot_verifier=_PostgresSnapshotVerifier(),
        ats009_policy_version="ATS-009-v0.1",
        clock=lambda: POSTGRES_RISK_NOW,
    )
    replay = service.authorize_with_retry(
        factory, plan_id=entries[0].plan.id, idempotency_key="f1-unbound-legacy"
    )
    assert replay.decision is RiskDecision.DENY
    assert replay.reason == Layer3Reason.LOSS_STATE_VERSION_CONFLICT.value
    assert not replay.execution_enabled
    with factory() as session:
        event = session.scalar(select(RiskReservationEventRecord))
        assert "loss_state" not in event.event_data
        assert (
            session.scalar(select(func.count()).select_from(RiskReservationEventRecord))
            == 1
        )
