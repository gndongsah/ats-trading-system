from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from pydantic import ValidationError

from src.domain.evidence import EvidenceItem, SourceDocument
from src.domain.models import EvidenceStatus
from src.evidence_verification import (
    EligibilityReason,
    score_market_opportunity_with_evidence,
)
from src.playbook import PLAYBOOK
from src.research import (
    ResearchEvidenceDraft,
    materialize_unverified_research_evidence,
    score_layer0_layer1_research,
)

DECISION_AT = datetime(2026, 10, 27, 14, 0, tzinfo=UTC)


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
        "available_at": DECISION_AT - timedelta(hours=1, minutes=30),
        "ingested_at": DECISION_AT - timedelta(hours=1),
        "content_hash": "a" * 64,
    }
    values.update(overrides)
    return SourceDocument(**values)


def verified_evidence(
    source: SourceDocument, factor: str, **overrides
) -> EvidenceItem:
    values = {
        "source_document_version_id": source.id,
        "subject": source.subject,
        "factor": factor,
        "excerpt": "Supported claim from the persisted source.",
        "confidence": Decimal("0.90"),
        "verification_status": EvidenceStatus.VERIFIED,
        "verifier_identity": "reviewer:ats",
        "verified_at": DECISION_AT - timedelta(minutes=10),
        "verification_method": "source_reconciliation",
        "verification_policy_version": "ats-evidence-1",
        "observed_at": source.observed_at + timedelta(minutes=5),
    }
    values.update(overrides)
    return EvidenceItem(**values)


def zero_scores(section) -> dict[str, float]:
    return {factor: 0.0 for factor in section.factors}


def test_research_draft_requires_excerpt_or_complete_metric():
    source = make_source()
    with pytest.raises(ValidationError, match="excerpt or structured metric"):
        ResearchEvidenceDraft(
            source_document_version_id=source.id,
            subject="ABC",
            factor="demand_acceleration",
            confidence=Decimal("0.8"),
        )

    with pytest.raises(ValidationError, match="name, value, and unit"):
        ResearchEvidenceDraft(
            source_document_version_id=source.id,
            subject="ABC",
            factor="demand_acceleration",
            metric_name="revenue_growth",
            confidence=Decimal("0.8"),
        )


def test_model_output_materializes_as_unverified_only():
    source = make_source()
    draft = ResearchEvidenceDraft(
        source_document_version_id=source.id,
        subject="ABC",
        factor="demand_acceleration",
        excerpt="Demand accelerated in the quarter.",
        confidence=Decimal(1),
    )

    evidence = materialize_unverified_research_evidence(
        draft=draft,
        source=source,
        observed_at=DECISION_AT - timedelta(minutes=30),
    )

    assert evidence.verification_status is EvidenceStatus.UNVERIFIED
    assert evidence.confidence == Decimal(1)
    assert evidence.verifier_identity is None
    assert evidence.verified_at is None


def test_research_draft_must_bind_to_the_persisted_source():
    source = make_source()
    draft = ResearchEvidenceDraft(
        source_document_version_id=uuid4(),
        subject="ABC",
        factor="demand_acceleration",
        excerpt="Demand accelerated.",
        confidence=Decimal("0.8"),
    )
    with pytest.raises(ValueError, match="source reference"):
        materialize_unverified_research_evidence(
            draft=draft,
            source=source,
            observed_at=DECISION_AT - timedelta(minutes=30),
        )


def test_market_opportunity_uncited_factor_receives_zero_credit():
    scores = zero_scores(PLAYBOOK.market_opportunity)
    scores["structural_demand_growth"] = 100.0

    result = score_market_opportunity_with_evidence(
        factor_scores=scores,
        evidence_by_factor={},
        sources={},
        subject="ABC",
        decision_at=DECISION_AT,
    )

    assert result.allowed_factor_scores["structural_demand_growth"] == 0
    assert result.score == 0
    assert (
        result.eligibility["structural_demand_growth"].reason_codes
        == (EligibilityReason.NO_EVIDENCE,)
    )


def test_market_opportunity_verified_evidence_can_receive_credit():
    source = make_source()
    evidence = verified_evidence(source, "structural_demand_growth")
    scores = zero_scores(PLAYBOOK.market_opportunity)
    scores["structural_demand_growth"] = 100.0

    result = score_market_opportunity_with_evidence(
        factor_scores=scores,
        evidence_by_factor={"structural_demand_growth": (evidence,)},
        sources={source.id: source},
        subject="ABC",
        decision_at=DECISION_AT,
    )

    assert result.allowed_factor_scores["structural_demand_growth"] == 100
    assert result.score == pytest.approx(20.0)


def test_model_confidence_cannot_substitute_for_verification():
    source = make_source()
    draft = ResearchEvidenceDraft(
        source_document_version_id=source.id,
        subject="ABC",
        factor="structural_demand_growth",
        excerpt="Demand accelerated.",
        confidence=Decimal(1),
    )
    evidence = materialize_unverified_research_evidence(
        draft=draft,
        source=source,
        observed_at=DECISION_AT - timedelta(minutes=30),
    )
    scores = zero_scores(PLAYBOOK.market_opportunity)
    scores["structural_demand_growth"] = 100.0

    result = score_market_opportunity_with_evidence(
        factor_scores=scores,
        evidence_by_factor={"structural_demand_growth": (evidence,)},
        sources={source.id: source},
        subject="ABC",
        decision_at=DECISION_AT,
    )

    eligibility = result.eligibility["structural_demand_growth"]
    assert not eligibility.eligible
    assert eligibility.allowed_contribution == 0
    assert EligibilityReason.STATUS_UNVERIFIED in eligibility.reason_codes


def test_future_market_evidence_receives_zero_credit():
    source = make_source(
        published_at=DECISION_AT + timedelta(minutes=1),
        available_at=DECISION_AT + timedelta(minutes=2),
        observed_at=DECISION_AT + timedelta(minutes=2),
        ingested_at=DECISION_AT + timedelta(minutes=3),
    )
    evidence = verified_evidence(
        source,
        "structural_demand_growth",
        observed_at=DECISION_AT + timedelta(minutes=4),
        verified_at=DECISION_AT + timedelta(minutes=5),
    )
    scores = zero_scores(PLAYBOOK.market_opportunity)
    scores["structural_demand_growth"] = 100.0

    result = score_market_opportunity_with_evidence(
        factor_scores=scores,
        evidence_by_factor={"structural_demand_growth": (evidence,)},
        sources={source.id: source},
        subject="ABC",
        decision_at=DECISION_AT,
    )

    eligibility = result.eligibility["structural_demand_growth"]
    assert eligibility.allowed_contribution == 0
    assert EligibilityReason.PUBLICATION_AFTER_DECISION in eligibility.reason_codes


def test_layer01_scoring_requires_written_thesis_breaker():
    with pytest.raises(ValueError, match="thesis-breaker"):
        score_layer0_layer1_research(
            subject="ABC",
            decision_at=DECISION_AT,
            thesis_breaker="   ",
            market_factor_scores=zero_scores(PLAYBOOK.market_opportunity),
            market_evidence_by_factor={},
            discovery_factor_scores=zero_scores(PLAYBOOK.discovery),
            discovery_evidence_by_factor={},
            sources={},
        )


def test_layer01_scoring_uses_verified_evidence_and_preserves_thesis_breaker():
    market_source = make_source()
    discovery_source = make_source(
        id=uuid4(),
        source_id="abc-call",
        source_type="earnings_call",
        content_hash="b" * 64,
    )
    market_evidence = verified_evidence(
        market_source, "structural_demand_growth"
    )
    discovery_evidence = verified_evidence(
        discovery_source, "demand_acceleration"
    )
    market_scores = zero_scores(PLAYBOOK.market_opportunity)
    discovery_scores = zero_scores(PLAYBOOK.discovery)
    market_scores["structural_demand_growth"] = 100.0
    discovery_scores["demand_acceleration"] = 100.0

    result = score_layer0_layer1_research(
        subject="ABC",
        decision_at=DECISION_AT,
        thesis_breaker="Exit if demand growth reverses for two consecutive quarters.",
        market_factor_scores=market_scores,
        market_evidence_by_factor={
            "structural_demand_growth": (market_evidence,)
        },
        discovery_factor_scores=discovery_scores,
        discovery_evidence_by_factor={
            "demand_acceleration": (discovery_evidence,)
        },
        sources={
            market_source.id: market_source,
            discovery_source.id: discovery_source,
        },
    )

    assert result.market_opportunity.score == pytest.approx(20.0)
    assert result.discovery.score == pytest.approx(15.0)
    assert result.thesis_breaker.startswith("Exit if")
