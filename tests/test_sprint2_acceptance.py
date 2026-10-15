from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker

from src.api import app
from src.database import get_db
from src.domain.evidence import EvidenceItem, SourceDocument
from src.domain.models import (
    EvidenceStatus,
    Fill,
    JournalEntry,
    Mood,
    OrderAction,
    OrderIntent,
    OrderType,
    PositionSnapshot,
    TradeResult,
    TradeSide,
)
from src.evidence_verification import (
    evaluate_scoring_eligibility,
    score_contribution_from_eligibility,
)
from src.persistence.evidence_repository import EvidenceRepository
from src.persistence.models import (
    EvidenceItemRecord,
    JournalEntryRecord,
    ScoreSnapshotRecord,
    SourceDocumentRecord,
)
from src.persistence.repository import (
    JournalRepository,
    PersistenceConflict,
    decode_score_snapshot,
)
from src.playbook import PLAYBOOK
from tests.persistence_fixtures import make_complete_trade

pytest_plugins = ("tests.test_postgres_persistence",)


def test_sprint2_evidence_scoring_and_journal_lifecycle(
    migrated_postgres_engine: Engine, monkeypatch: pytest.MonkeyPatch
):
    decision_at = datetime(2026, 1, 2, 15, 0, tzinfo=UTC)
    factory = sessionmaker(bind=migrated_postgres_engine)

    verified_source = SourceDocument(
        source_id="sprint2-acceptance-source",
        version=1,
        source_type="regulatory_filing",
        publisher="ABC Inc.",
        subject="ABC",
        published_at=decision_at - timedelta(hours=2),
        observed_at=decision_at - timedelta(hours=1),
        content_hash="a" * 64,
        version_metadata={"accession": "sprint2-acceptance"},
    )
    verified_evidence = EvidenceItem(
        source_document_version_id=verified_source.id,
        subject="ABC",
        factor="demand_acceleration",
        inflection_stage=5,
        excerpt="Quarterly demand increased year over year.",
        confidence=Decimal("0.94"),
        verification_status=EvidenceStatus.VERIFIED,
        verifier_identity="reviewer:sprint2-acceptance",
        verified_at=decision_at - timedelta(minutes=10),
        verification_method="source_reconciliation",
        verification_policy_version="ats-evidence-1",
        verification_notes="Claim reconciled against the source document.",
        observed_at=decision_at - timedelta(minutes=30),
    )
    unverified_source = SourceDocument(
        source_id="sprint2-acceptance-unverified",
        version=1,
        source_type="company_statement",
        publisher="ABC Inc.",
        subject="ABC",
        published_at=decision_at - timedelta(hours=1),
        observed_at=decision_at - timedelta(minutes=30),
        content_hash="b" * 64,
    )
    unverified_evidence = EvidenceItem(
        source_document_version_id=unverified_source.id,
        subject="ABC",
        factor="demand_acceleration",
        excerpt="Management expects demand to accelerate.",
        confidence=Decimal("0.99"),
        verification_status=EvidenceStatus.UNVERIFIED,
        observed_at=decision_at - timedelta(minutes=20),
    )
    late_verification_evidence = EvidenceItem(
        source_document_version_id=verified_source.id,
        subject="ABC",
        factor="demand_acceleration",
        excerpt="A claim verified after the score decision.",
        confidence=Decimal("0.99"),
        verification_status=EvidenceStatus.VERIFIED,
        verifier_identity="reviewer:sprint2-acceptance",
        verified_at=decision_at + timedelta(minutes=5),
        verification_method="source_reconciliation",
        verification_policy_version="ats-evidence-1",
        observed_at=decision_at - timedelta(minutes=5),
    )

    with factory.begin() as session:
        repository = EvidenceRepository(session)
        repository.save_source(verified_source)
        repository.save_evidence(verified_evidence)
        repository.save_source(unverified_source)
        repository.save_evidence(unverified_evidence)
        repository.save_evidence(late_verification_evidence)

    def override_get_db():
        with factory() as session:
            yield session

    monkeypatch.setitem(app.dependency_overrides, get_db, override_get_db)
    client = TestClient(app)
    factor_scores = {factor: 0 for factor in PLAYBOOK.discovery.factors}
    factor_scores["demand_acceleration"] = 100

    def score_with_evidence(evidence_id, version: int = 1):
        return client.post(
            "/layer1/discovery",
            json={
                "factor_scores": factor_scores,
                "subject": "ABC",
                "decision_at": decision_at.isoformat(),
                "evidence_by_factor": {
                    "demand_acceleration": [
                        {"evidence_id": str(evidence_id), "version": version}
                    ]
                },
            },
        )

    # API-level scoring coverage: these requests traverse the actual FastAPI route.
    discovery_response = score_with_evidence(verified_evidence.id)
    assert discovery_response.status_code == 200
    discovery_result = discovery_response.json()
    assert discovery_result["score"] == 15
    eligibility = discovery_result["eligibility"]["demand_acceleration"]
    assert eligibility["eligible"] is True
    assert Decimal(eligibility["allowed_contribution"]) == Decimal(100)
    assert eligibility["supporting_evidence"][0]["evidence_version"] == 1
    assert eligibility["supporting_evidence"][0]["source_document_version_id"] == str(
        verified_source.id
    )

    missing_response = score_with_evidence(uuid4())
    unverified_response = score_with_evidence(unverified_evidence.id)
    late_verification_response = score_with_evidence(late_verification_evidence.id)
    for response in (
        missing_response,
        unverified_response,
        late_verification_response,
    ):
        assert response.status_code == 200
        body = response.json()
        assert body["score"] == 0
        factor_result = body["eligibility"]["demand_acceleration"]
        assert factor_result["eligible"] is False
        assert Decimal(factor_result["allowed_contribution"]) == Decimal(0)
    assert (
        "missing_evidence"
        in missing_response.json()["eligibility"]["demand_acceleration"]["reason_codes"]
    )
    assert (
        "status_unverified"
        in unverified_response.json()["eligibility"]["demand_acceleration"][
            "reason_codes"
        ]
    )
    assert (
        "verification_after_decision"
        in late_verification_response.json()["eligibility"]["demand_acceleration"][
            "reason_codes"
        ]
    )

    scoring_eligibility = evaluate_scoring_eligibility(
        subject="ABC",
        factor="demand_acceleration",
        requested_contribution=Decimal(100),
        decision_at=decision_at,
        evidence_items=(verified_evidence,),
        sources={verified_source.id: verified_source},
        inflection_stage=5,
    )
    contribution = score_contribution_from_eligibility(
        factor="demand_acceleration",
        result=scoring_eligibility,
        evidence_items=(verified_evidence,),
        sources={verified_source.id: verified_source},
    )
    trade_template = make_complete_trade()
    assert discovery_result["score"] == 15
    initial_score_snapshot = trade_template.prior_plans[0].score_snapshot.model_copy(
        update={
            "id": uuid4(),
            "decided_at": decision_at,
            "discovery": int(discovery_result["score"]),
            "contributions": (contribution,),
        }
    )
    initial_plan = trade_template.prior_plans[0].model_copy(
        update={"score_snapshot": initial_score_snapshot}
    )
    initial_entry = JournalEntry(
        id=uuid4(),
        plan=initial_plan,
        mood=Mood.CALM,
        thesis="Decision supported by the verified filing evidence.",
        created_at=initial_plan.planned_at,
    )

    # Repository-level lifecycle coverage: no trade-lifecycle HTTP endpoints exist.
    with factory.begin() as session:
        assert JournalRepository(session).save(initial_entry) == 1

    order = OrderIntent(
        plan_id=initial_plan.id,
        action=OrderAction.BUY,
        order_type=OrderType.LIMIT,
        quantity=initial_plan.quantity,
        limit_price=initial_plan.entry,
        created_at=initial_plan.planned_at + timedelta(minutes=1),
    )
    fill = Fill(
        order_intent_id=order.id,
        filled_at=order.created_at + timedelta(seconds=3),
        quantity=order.quantity,
        price=initial_plan.entry,
        fees=Decimal("1.25"),
    )
    position = PositionSnapshot(
        plan_id=initial_plan.id,
        as_of=fill.filled_at + timedelta(hours=2),
        side=TradeSide.LONG,
        quantity=fill.quantity,
        average_entry=fill.price,
        unrealized_pnl=Decimal(75),
    )
    result = TradeResult(
        plan_id=initial_plan.id,
        closed_at=position.as_of + timedelta(hours=4),
        realized_pnl=Decimal(250),
        r_multiple=Decimal("2.5"),
        outcome="win",
        mae_r=Decimal("0.25"),
        mfe_r=Decimal("3.1"),
        excursion_source="synthetic acceptance-test observations",
    )
    completed_entry = initial_entry.model_copy(
        update={
            "orders": (order,),
            "fills": (fill,),
            "position_snapshots": (position,),
            "result": result,
            "plan_followed": True,
            "lesson": "Synthetic lifecycle completed without broker access.",
            "created_at": result.closed_at,
        }
    )
    with factory.begin() as session:
        journal_repository = JournalRepository(session)
        assert journal_repository.save(completed_entry, expected_revision=1) == 2

    stale_entry = initial_entry.model_copy(
        update={"thesis": "Attempted stale overwrite."}
    )
    with (
        pytest.raises(PersistenceConflict, match="stale journal revision"),
        factory.begin() as session,
    ):
        JournalRepository(session).save(stale_entry, expected_revision=1)

    # Fresh session verifies both immutable journal revisions and their linked data.
    with factory() as session:
        journal_repository = JournalRepository(session)
        revision_one = journal_repository.get_revision(initial_entry.id, 1)
        revision_two = journal_repository.get_revision(initial_entry.id, 2)
        current = journal_repository.get(initial_entry.id)
        evidence_repository = EvidenceRepository(session)
        persisted_evidence = evidence_repository.get_evidence(
            verified_evidence.id, verified_evidence.version
        )
        persisted_source = evidence_repository.get_source(verified_source.id)
        stored_snapshot = session.get(
            ScoreSnapshotRecord, initial_score_snapshot.id
        ).snapshot_data
        journal_revisions = session.scalars(
            select(JournalEntryRecord.revision)
            .where(JournalEntryRecord.trade_id == initial_plan.trade_id)
            .order_by(JournalEntryRecord.revision)
        ).all()
        snapshot_count = session.scalar(
            select(func.count()).select_from(ScoreSnapshotRecord)
        )
        evidence_count = session.scalar(
            select(func.count()).select_from(EvidenceItemRecord)
        )
        source_count = session.scalar(
            select(func.count()).select_from(SourceDocumentRecord)
        )

    assert revision_one == initial_entry
    assert revision_two == completed_entry
    assert current == completed_entry
    assert revision_one.plan == revision_two.plan == initial_plan
    assert revision_one.plan.score_snapshot == initial_score_snapshot
    assert revision_two.plan.score_snapshot == initial_score_snapshot
    assert revision_one.plan.planned_at == revision_two.plan.planned_at
    assert persisted_evidence == verified_evidence
    assert persisted_source == verified_source
    assert journal_revisions == [1, 2]
    assert snapshot_count == 1
    assert evidence_count == 3
    assert source_count == 2
    assert decode_score_snapshot(stored_snapshot) == initial_score_snapshot
    stored_reference = revision_two.plan.score_snapshot.contributions[0].evidence[0]
    assert stored_reference.id == verified_evidence.id
    assert stored_reference.version == verified_evidence.version
    assert stored_reference.source_document_version_id == verified_source.id
    assert stored_reference.published_at == verified_source.published_at
    assert stored_reference.observed_at == verified_evidence.observed_at
    assert stored_reference.verified_at == verified_evidence.verified_at
    assert stored_reference.verifier_identity == verified_evidence.verifier_identity
    assert initial_entry.plan.score_snapshot == initial_score_snapshot
    assert initial_entry.thesis == "Decision supported by the verified filing evidence."
