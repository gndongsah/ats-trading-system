import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from src.database import Base, create_database_engine
from src.persistence.models import (
    FillRecord,
    JournalEntryRecord,
    OrderIntentRecord,
    PositionSnapshotRecord,
    ScoreSnapshotRecord,
    TradePlanRecord,
    TradeRecord,
    TradeResultRecord,
)
from src.persistence.repository import JournalRepository, PersistenceConflict
from tests.persistence_fixtures import make_complete_trade


def test_complete_trade_round_trips_in_a_new_database_session():
    engine = create_database_engine("sqlite://")
    Base.metadata.create_all(engine)
    entry = make_complete_trade()
    try:
        with Session(engine) as session, session.begin():
            assert JournalRepository(session).save(entry) == 1

        with Session(engine) as session:
            restored = JournalRepository(session).get(entry.id)
            assert restored == entry
            assert restored is not None
            assert restored.plan.trade_id == entry.plan.trade_id
            assert restored.plan.score_snapshot == entry.prior_plans[0].score_snapshot
            assert restored.prior_plans == entry.prior_plans
            assert restored.plan.risk_intent == entry.plan.risk_intent
            assert restored.orders == entry.orders
            assert restored.fills == entry.fills
            assert restored.position_snapshots == entry.position_snapshots
            assert restored.result == entry.result
            assert restored.result is not None
            assert restored.result.mae_r == entry.result.mae_r
            assert restored.result.mfe_r == entry.result.mfe_r
            assert restored.plan_followed is True
            assert restored.mood == entry.mood
            assert restored.thesis == entry.thesis
            assert restored.lesson == entry.lesson
            expected_counts = (
                (TradeRecord, 1),
                (ScoreSnapshotRecord, 1),
                (TradePlanRecord, 2),
                (OrderIntentRecord, 1),
                (FillRecord, 1),
                (PositionSnapshotRecord, 1),
                (TradeResultRecord, 1),
                (JournalEntryRecord, 1),
            )
            for record_type, expected_count in expected_counts:
                count = session.scalar(select(func.count()).select_from(record_type))
                assert count == expected_count
            journal_document = session.get(JournalEntryRecord, (entry.id, 1)).entry_data
            snapshot_document = session.get(
                ScoreSnapshotRecord, entry.plan.score_snapshot.id
            ).snapshot_data
            assert journal_document["schema_version"] == 2
            assert journal_document["data"] == entry.model_dump(mode="json")
            assert snapshot_document["schema_version"] == 2
            assert snapshot_document["data"] == entry.plan.score_snapshot.model_dump(
                mode="json"
            )
    finally:
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_journal_feedback_changes_are_stored_as_new_revisions():
    engine = create_database_engine("sqlite://")
    Base.metadata.create_all(engine)
    entry = make_complete_trade()
    revised_entry = entry.model_copy(update={"mood": None, "lesson": "Review exit"})
    try:
        with Session(engine) as session, session.begin():
            repository = JournalRepository(session)
            assert repository.save(entry) == 1
        with Session(engine) as session, session.begin():
            assert JournalRepository(session).save(revised_entry) == 2

        with Session(engine) as session:
            restored = JournalRepository(session).get_by_trade_id(entry.plan.trade_id)
            assert restored == revised_entry
            revisions = session.scalars(
                select(JournalEntryRecord.revision).where(
                    JournalEntryRecord.id == entry.id
                )
            ).all()
            assert revisions == [1, 2]
        stale_entry = revised_entry.model_copy(update={"lesson": "stale update"})
        with (
            pytest.raises(PersistenceConflict, match="stale journal revision"),
            Session(engine) as session,
            session.begin(),
        ):
            JournalRepository(session).save(stale_entry, expected_revision=1)
        with Session(engine) as session:
            revisions = session.scalars(
                select(JournalEntryRecord.revision).where(
                    JournalEntryRecord.id == entry.id
                )
            ).all()
            assert revisions == [1, 2]
    finally:
        Base.metadata.drop_all(engine)
        engine.dispose()
