from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from src.database import Base, create_database_engine
from src.domain.evidence import EvidenceItem, SourceDocument
from src.domain.models import EvidenceStatus
from src.engine import score_discovery
from src.evidence_verification import (
    EligibilityReason,
    evaluate_scoring_eligibility,
    score_contribution_from_eligibility,
    score_discovery_with_evidence,
)
from src.persistence.evidence_repository import EvidenceRepository
from src.persistence.models import EvidenceItemRecord, SourceDocumentRecord
from src.persistence.repository import PersistenceConflict
from src.playbook import PLAYBOOK
from tests.persistence_fixtures import make_complete_trade

DECISION_AT = datetime(2026, 10, 14, 14, 0, tzinfo=UTC)


def make_source(**overrides) -> SourceDocument:
    values = {
        "source_id": "abc-q3-filing",
        "version": 1,
        "source_type": "regulatory_filing",
        "source_url": "https://example.test/abc-q3",
        "publisher": "ABC Inc.",
        "subject": "ABC",
        "published_at": DECISION_AT - timedelta(hours=2),
        "observed_at": DECISION_AT - timedelta(hours=1),
        "content_hash": "a" * 64,
        "version_metadata": {"filing_id": "0000123"},
    }
    values.update(overrides)
    return SourceDocument(**values)


def make_evidence(source: SourceDocument, **overrides) -> EvidenceItem:
    values = {
        "source_document_version_id": source.id,
        "subject": "ABC",
        "factor": "demand_acceleration",
        "inflection_stage": 5,
        "excerpt": "Quarterly demand increased year over year.",
        "confidence": Decimal("0.94"),
        "verification_status": EvidenceStatus.VERIFIED,
        "verifier_identity": "reviewer:analyst-17",
        "verified_at": DECISION_AT - timedelta(minutes=10),
        "verification_method": "source_reconciliation",
        "verification_policy_version": "ats-evidence-1",
        "verification_notes": "Claim matches the filing context.",
        "observed_at": source.observed_at + timedelta(minutes=5),
    }
    values.update(overrides)
    return EvidenceItem(**values)


def evaluate(
    source: SourceDocument,
    evidence_items: tuple[EvidenceItem, ...],
    **overrides,
):
    values = {
        "subject": "ABC",
        "factor": "demand_acceleration",
        "requested_contribution": Decimal(40),
        "decision_at": DECISION_AT,
        "evidence_items": evidence_items,
        "sources": {source.id: source},
        "inflection_stage": 5,
    }
    values.update(overrides)
    return evaluate_scoring_eligibility(**values)


def test_verified_traceable_evidence_is_eligible_and_versioned():
    source = make_source()
    evidence = make_evidence(source)

    result = evaluate(source, (evidence,))

    assert result.eligible
    assert result.reason_codes == ()
    assert result.requested_contribution == Decimal(40)
    assert result.allowed_contribution == Decimal(40)
    assert result.supporting_evidence[0].evidence_id == evidence.id
    assert result.supporting_evidence[0].evidence_version == evidence.version
    assert result.supporting_evidence[0].source_document_version_id == source.id


def test_verified_evidence_cannot_precede_observation():
    source = make_source()

    with pytest.raises(ValidationError, match="before it is observed"):
        make_evidence(
            source,
            verified_at=DECISION_AT - timedelta(minutes=58),
            observed_at=DECISION_AT - timedelta(minutes=55),
        )


def test_verification_provenance_after_decision_is_ineligible():
    source = make_source()
    evidence = make_evidence(
        source,
        verified_at=DECISION_AT + timedelta(seconds=1),
    )

    result = evaluate(source, (evidence,))

    assert not result.eligible
    assert result.allowed_contribution == 0
    assert EligibilityReason.VERIFICATION_AFTER_DECISION in result.reason_codes


def test_verified_status_requires_verifier_provenance():
    source = make_source()
    with pytest.raises(ValidationError, match="requires verifier identity"):
        make_evidence(
            source,
            verifier_identity=None,
            verified_at=None,
            verification_method=None,
            verification_policy_version=None,
        )


def test_model_confidence_does_not_replace_verifier_provenance():
    source = make_source()
    evidence = make_evidence(
        source,
        verification_status=EvidenceStatus.UNVERIFIED,
        verifier_identity=None,
        verified_at=None,
        verification_method=None,
        verification_policy_version=None,
        confidence=Decimal(1),
    )

    result = evaluate(source, (evidence,))

    assert not result.eligible
    assert result.allowed_contribution == 0
    assert EligibilityReason.STATUS_UNVERIFIED in result.reason_codes


def test_url_without_original_content_reference_or_hash_is_not_traceable():
    source = make_source(content_hash=None)
    evidence = make_evidence(source)

    result = evaluate(source, (evidence,))

    assert not result.eligible
    assert result.allowed_contribution == 0
    assert EligibilityReason.SOURCE_NOT_TRACEABLE in result.reason_codes


def test_missing_evidence_receives_no_positive_credit():
    source = make_source()
    result = evaluate(source, ())

    assert not result.eligible
    assert result.allowed_contribution == 0
    assert result.reason_codes == (EligibilityReason.NO_EVIDENCE,)


def test_missing_or_mismatched_source_reference_is_ineligible():
    source = make_source()
    evidence = make_evidence(source)
    missing = evaluate(source, (evidence,), sources={})
    mismatched_source = make_source(id=uuid4())
    mismatched = evaluate(
        source,
        (evidence,),
        sources={source.id: mismatched_source},
    )

    assert not missing.eligible
    assert missing.allowed_contribution == 0
    assert missing.reason_codes == (EligibilityReason.MISSING_SOURCE,)
    assert not mismatched.eligible
    assert mismatched.allowed_contribution == 0
    assert EligibilityReason.SOURCE_REFERENCE_MISMATCH in mismatched.reason_codes


@pytest.mark.parametrize(
    ("status", "reason"),
    [
        (EvidenceStatus.UNVERIFIED, EligibilityReason.STATUS_UNVERIFIED),
        (EvidenceStatus.REJECTED, EligibilityReason.STATUS_REJECTED),
    ],
)
def test_unverified_or_rejected_evidence_receives_no_positive_credit(status, reason):
    source = make_source()
    evidence = make_evidence(source, verification_status=status)

    result = evaluate(source, (evidence,))

    assert not result.eligible
    assert result.allowed_contribution == 0
    assert reason in result.reason_codes


@pytest.mark.parametrize(
    ("source_changes", "evidence_changes", "reason"),
    [
        (
            {
                "published_at": DECISION_AT + timedelta(seconds=1),
                "observed_at": DECISION_AT + timedelta(seconds=2),
            },
            {"verified_at": DECISION_AT + timedelta(minutes=6)},
            EligibilityReason.PUBLICATION_AFTER_DECISION,
        ),
        (
            {"observed_at": DECISION_AT + timedelta(seconds=1)},
            {"verified_at": DECISION_AT + timedelta(minutes=10)},
            EligibilityReason.OBSERVATION_AFTER_DECISION,
        ),
        (
            {},
            {
                "observed_at": DECISION_AT + timedelta(seconds=1),
                "verified_at": DECISION_AT + timedelta(seconds=2),
            },
            EligibilityReason.OBSERVATION_AFTER_DECISION,
        ),
        (
            {"subject": "XYZ"},
            {"subject": "XYZ"},
            EligibilityReason.SUBJECT_MISMATCH,
        ),
        (
            {},
            {"factor": "valuation"},
            EligibilityReason.FACTOR_MISMATCH,
        ),
    ],
)
def test_temporal_subject_and_factor_mismatches_are_ineligible(
    source_changes, evidence_changes, reason
):
    source = make_source(**source_changes)
    evidence = make_evidence(source, **evidence_changes)

    result = evaluate(source, (evidence,))

    assert not result.eligible
    assert result.allowed_contribution == 0
    assert reason in result.reason_codes


def test_multiple_evidence_items_require_every_cited_item_to_be_eligible():
    source = make_source()
    verified = make_evidence(source)
    pending = make_evidence(
        source,
        id=uuid4(),
        verification_status=EvidenceStatus.UNVERIFIED,
        excerpt="Another claim still awaiting review.",
    )

    result = evaluate(source, (verified, pending))

    assert not result.eligible
    assert result.allowed_contribution == 0
    assert len(result.supporting_evidence) == 2
    assert EligibilityReason.STATUS_UNVERIFIED in result.reason_codes


def test_semantic_contradiction_remains_pending_not_automatically_approved():
    source = make_source()
    favorable = make_evidence(source)
    conflicting = make_evidence(
        source,
        id=uuid4(),
        excerpt="A separate source disputes the demand trend.",
        verification_status=EvidenceStatus.UNVERIFIED,
        verification_notes="Potential contradiction; manual review required.",
    )

    result = evaluate(source, (favorable, conflicting))

    assert not result.eligible
    assert result.allowed_contribution == 0
    assert EligibilityReason.STATUS_UNVERIFIED in result.reason_codes


def test_structured_metric_can_support_evidence_without_an_excerpt():
    source = make_source()
    evidence = make_evidence(
        source,
        excerpt=None,
        metric_name="revenue_growth",
        metric_value=Decimal("0.27"),
        metric_unit="fraction",
    )

    assert evaluate(source, (evidence,)).eligible


def test_structured_metric_must_include_name_value_and_unit():
    source = make_source()
    with pytest.raises(ValidationError, match="require a name, value, and unit"):
        make_evidence(source, excerpt=None, metric_name="revenue_growth")


def test_discovery_score_gates_unsupported_positive_factor_and_keeps_weights():
    source = make_source()
    evidence = make_evidence(source)
    scores = {factor: 0.0 for factor in PLAYBOOK.discovery.factors}
    scores["demand_acceleration"] = 40.0

    eligible = score_discovery_with_evidence(
        factor_scores=scores,
        evidence_by_factor={"demand_acceleration": (evidence,)},
        sources={source.id: source},
        subject="ABC",
        decision_at=DECISION_AT,
    )
    unsupported = score_discovery_with_evidence(
        factor_scores=scores,
        evidence_by_factor={},
        sources={source.id: source},
        subject="ABC",
        decision_at=DECISION_AT,
    )

    assert eligible.allowed_factor_scores["demand_acceleration"] == 40
    assert eligible.score == score_discovery({**scores, "demand_acceleration": 40.0})
    assert unsupported.allowed_factor_scores["demand_acceleration"] == 0
    assert unsupported.score == 0
    assert not unsupported.eligibility["demand_acceleration"].eligible


def test_evidence_contribution_is_recorded_as_immutable_snapshot_reference():
    source = make_source()
    evidence = make_evidence(source)
    result = evaluate(source, (evidence,))

    contribution = score_contribution_from_eligibility(
        factor="demand_acceleration",
        result=result,
        evidence_items=(evidence,),
        sources={source.id: source},
    )

    assert contribution.points == 40
    assert contribution.evidence[0].id == evidence.id
    assert contribution.evidence[0].version == evidence.version
    assert contribution.evidence[0].source_document_version_id == source.id
    with pytest.raises(ValueError, match="factor does not match"):
        score_contribution_from_eligibility(
            factor="valuation",
            result=result,
            evidence_items=(evidence,),
            sources={source.id: source},
        )


def test_sqlite_repository_retrieves_historical_evidence_versions():
    engine = create_database_engine("sqlite://")
    Base.metadata.create_all(engine)
    source_v1 = make_source()
    evidence_v1 = make_evidence(source_v1)
    source_v2 = make_source(
        id=uuid4(),
        version=2,
        source_id=source_v1.source_id,
        observed_at=DECISION_AT - timedelta(minutes=30),
        version_metadata={"revision": "corrected"},
    )
    evidence_v2 = make_evidence(
        source_v2,
        id=evidence_v1.id,
        version=2,
        verifier_identity="reviewer:analyst-18",
        verified_at=DECISION_AT - timedelta(minutes=10),
        verification_notes="Corrected source revision reviewed.",
        observed_at=source_v2.observed_at + timedelta(minutes=5),
    )
    try:
        with Session(engine) as session, session.begin():
            repository = EvidenceRepository(session)
            repository.save_source(source_v1)
            repository.save_evidence(evidence_v1)
            repository.save_source(source_v2)
            repository.save_evidence(evidence_v2)
        with Session(engine) as session:
            repository = EvidenceRepository(session)
            assert repository.get_evidence(evidence_v1.id, 1) == evidence_v1
            assert repository.get_evidence(evidence_v1.id, 2) == evidence_v2
            assert repository.get_latest_evidence(evidence_v1.id) == evidence_v2
            assert repository.get_source(source_v1.id) == source_v1
            assert repository.get_source(source_v2.id) == source_v2
            assert repository.get_evidence(evidence_v1.id, 1).verified_at == (
                DECISION_AT - timedelta(minutes=10)
            )
            assert repository.get_evidence(evidence_v1.id, 1).verifier_identity == (
                "reviewer:analyst-17"
            )
            assert repository.get_evidence(evidence_v1.id, 2).verifier_identity == (
                "reviewer:analyst-18"
            )
            assert (
                session.scalar(select(func.count()).select_from(EvidenceItemRecord))
                == 2
            )
            assert (
                session.scalar(select(func.count()).select_from(SourceDocumentRecord))
                == 2
            )
    finally:
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_evidence_repository_rejects_version_gaps_without_changing_history():
    engine = create_database_engine("sqlite://")
    Base.metadata.create_all(engine)
    source = make_source()
    evidence = make_evidence(source)
    evidence_v3 = make_evidence(
        source,
        id=evidence.id,
        version=3,
        verifier_identity="reviewer:analyst-19",
        verified_at=DECISION_AT - timedelta(minutes=5),
    )
    try:
        with Session(engine) as session, session.begin():
            repository = EvidenceRepository(session)
            repository.save_source(source)
            repository.save_evidence(evidence)
        with (
            pytest.raises(PersistenceConflict, match="version must be 2"),
            Session(engine) as session,
            session.begin(),
        ):
            EvidenceRepository(session).save_evidence(evidence_v3)
        with Session(engine) as session:
            repository = EvidenceRepository(session)
            assert repository.get_evidence(evidence.id, 1) == evidence
            assert repository.get_evidence(evidence.id, 3) is None
    finally:
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_rescoring_uses_new_snapshot_without_mutating_the_original():
    from src.domain.models import JournalEntry

    source = make_source()
    evidence = make_evidence(source)
    result = evaluate(source, (evidence,))
    contribution = score_contribution_from_eligibility(
        factor="demand_acceleration",
        result=result,
        evidence_items=(evidence,),
        sources={source.id: source},
    )
    original_entry = make_complete_trade()
    original_snapshot = original_entry.plan.score_snapshot
    rescored_snapshot = original_snapshot.model_copy(
        update={
            "id": uuid4(),
            "contributions": (contribution,),
            "discovery": 15,
        }
    )
    prior_plans = (*original_entry.prior_plans, original_entry.plan)
    plan = original_entry.plan.model_copy(
        update={
            "id": uuid4(),
            "version": original_entry.plan.version + 1,
            "supersedes_plan_id": original_entry.plan.id,
            "planned_at": original_entry.plan.planned_at + timedelta(minutes=1),
            "score_snapshot": rescored_snapshot,
        }
    )
    rescored_entry: JournalEntry = original_entry.model_copy(
        update={"plan": plan, "prior_plans": prior_plans}
    )

    assert rescored_entry.plan.score_snapshot.id != original_snapshot.id
    assert original_entry.plan.score_snapshot == original_snapshot
    assert (
        rescored_entry.plan.score_snapshot.contributions[0].evidence[0].id
        == evidence.id
    )
    assert rescored_entry.plan.score_snapshot.contributions[0].evidence[0].version == 1
