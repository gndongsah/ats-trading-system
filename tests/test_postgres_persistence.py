import os
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from threading import Barrier
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, delete, func, insert, select, text, update
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import sessionmaker

from src.domain.evidence import EvidenceItem, SourceDocument
from src.domain.models import EvidenceStatus, OrderAction, OrderIntent, OrderType
from src.evidence_verification import (
    evaluate_scoring_eligibility,
    score_contribution_from_eligibility,
)
from src.persistence.evidence_repository import EvidenceRepository
from src.persistence.models import (
    EvidenceItemRecord,
    FillRecord,
    JournalEntryRecord,
    OrderIntentRecord,
    PositionSnapshotRecord,
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
from tests.persistence_fixtures import make_complete_trade


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
    admin_engine = create_engine(database_url, pool_pre_ping=True)
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
        test_engine = create_engine(scoped_url, pool_pre_ping=True)
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
