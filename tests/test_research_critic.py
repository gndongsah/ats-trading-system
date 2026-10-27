from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from pydantic import ValidationError

from src.domain.evidence import EvidenceItem, SourceDocument
from src.domain.models import EvidenceStatus
from src.evidence_verification import EvidenceVersionRef, evaluate_scoring_eligibility
from src.research_critic import (
    CriticDisposition,
    CriticFinding,
    CritiqueReviewReason,
    EvidenceVerificationAttestation,
    ResearchCritique,
    apply_evidence_verification,
    review_research_critique,
)

DECISION_AT = datetime(2026, 10, 27, 15, 0, tzinfo=UTC)


def make_source(**overrides) -> SourceDocument:
    values = {
        "source_id": "abc-q3",
        "version": 1,
        "source_type": "regulatory_filing",
        "source_url": "https://example.test/abc-q3",
        "publisher": "ABC Inc.",
        "subject": "ABC",
        "published_at": DECISION_AT - timedelta(hours=2),
        "observed_at": DECISION_AT - timedelta(hours=1),
        "available_at": DECISION_AT - timedelta(hours=1),
        "ingested_at": DECISION_AT - timedelta(minutes=50),
        "content_hash": "a" * 64,
    }
    values.update(overrides)
    return SourceDocument(**values)


def make_unverified(source: SourceDocument, **overrides) -> EvidenceItem:
    values = {
        "source_document_version_id": source.id,
        "subject": "ABC",
        "factor": "demand_acceleration",
        "excerpt": "Demand accelerated year over year.",
        "confidence": Decimal("0.95"),
        "verification_status": EvidenceStatus.UNVERIFIED,
        "observed_at": DECISION_AT - timedelta(minutes=45),
    }
    values.update(overrides)
    return EvidenceItem(**values)


def ref(evidence: EvidenceItem) -> EvidenceVersionRef:
    return EvidenceVersionRef(
        evidence_id=evidence.id,
        evidence_version=evidence.version,
        source_document_version_id=evidence.source_document_version_id,
    )


def finding(
    evidence: EvidenceItem,
    disposition: CriticDisposition = CriticDisposition.SUPPORTED,
) -> CriticFinding:
    return CriticFinding(
        factor="demand_acceleration",
        challenged_claim="Demand is accelerating.",
        disposition=disposition,
        evidence=(ref(evidence),),
        rationale="Second-pass review reconciled the claim to persisted evidence.",
    )


def critique(evidence: EvidenceItem, **overrides) -> ResearchCritique:
    values = {
        "subject": "ABC",
        "researcher_identity": "research-agent:primary",
        "critic_identity": "research-agent:critic",
        "thesis": "ABC is entering an accelerating demand cycle.",
        "thesis_breaker": "Two consecutive quarters of demand contraction.",
        "findings": (finding(evidence),),
    }
    values.update(overrides)
    return ResearchCritique(**values)


def test_critic_requires_written_thesis_breaker():
    source = make_source()
    evidence = make_unverified(source)
    with pytest.raises(ValidationError):
        critique(evidence, thesis_breaker="   ")


def test_critic_must_be_independent_from_researcher():
    source = make_source()
    evidence = make_unverified(source)
    with pytest.raises(ValidationError, match="must differ"):
        critique(evidence, critic_identity="RESEARCH-AGENT:PRIMARY")


def test_contradiction_is_detected_and_fails_closed():
    source = make_source()
    evidence = make_unverified(source)
    challenged = critique(
        evidence,
        findings=(finding(evidence, CriticDisposition.CONTRADICTED),),
    )
    result = review_research_critique(
        critique=challenged,
        evidence_items={evidence.id: evidence},
        sources={source.id: source},
    )
    assert not result.accepted
    assert CritiqueReviewReason.CONTRADICTION in result.reason_codes
    assert result.contradicted_factors == ("demand_acceleration",)


def test_insufficient_finding_fails_closed_without_evidence():
    source = make_source()
    evidence = make_unverified(source)
    insufficient = CriticFinding(
        factor="valuation",
        challenged_claim="Valuation is attractive.",
        disposition=CriticDisposition.INSUFFICIENT,
        evidence=(),
        rationale="No persisted evidence is sufficient to support this claim.",
    )
    result = review_research_critique(
        critique=critique(evidence, findings=(insufficient,)),
        evidence_items={evidence.id: evidence},
        sources={source.id: source},
    )
    assert not result.accepted
    assert CritiqueReviewReason.INSUFFICIENT_SUPPORT in result.reason_codes


def test_missing_or_wrong_evidence_version_is_detected():
    source = make_source()
    evidence = make_unverified(source)
    missing_ref = ref(evidence).model_copy(update={"evidence_id": uuid4()})
    wrong_version_ref = ref(evidence).model_copy(update={"evidence_version": 2})
    findings = (
        CriticFinding(
            factor="demand_acceleration",
            challenged_claim="Demand is accelerating.",
            disposition=CriticDisposition.SUPPORTED,
            evidence=(missing_ref,),
            rationale="Reference must resolve.",
        ),
        CriticFinding(
            factor="demand_acceleration",
            challenged_claim="Demand is accelerating.",
            disposition=CriticDisposition.SUPPORTED,
            evidence=(wrong_version_ref,),
            rationale="Version must match.",
        ),
    )
    result = review_research_critique(
        critique=critique(evidence, findings=findings),
        evidence_items={evidence.id: evidence},
        sources={source.id: source},
    )
    assert not result.accepted
    assert CritiqueReviewReason.MISSING_EVIDENCE in result.reason_codes
    assert CritiqueReviewReason.EVIDENCE_VERSION_MISMATCH in result.reason_codes


def test_verifier_creates_new_immutable_verified_version():
    source = make_source()
    evidence = make_unverified(source)
    attestation = EvidenceVerificationAttestation(
        verifier_identity="reviewer:independent",
        verified_at=DECISION_AT - timedelta(minutes=5),
        verification_method="source_reconciliation",
        verification_policy_version="ats-evidence-2",
        accepted=True,
        notes="Claim reconciled to source context.",
    )
    verified = apply_evidence_verification(
        evidence=evidence,
        source=source,
        researcher_identity="research-agent:primary",
        attestation=attestation,
    )
    assert evidence.version == 1
    assert evidence.verification_status is EvidenceStatus.UNVERIFIED
    assert verified.id == evidence.id
    assert verified.version == 2
    assert verified.verification_status is EvidenceStatus.VERIFIED


def test_rejected_attestation_creates_rejected_version():
    source = make_source()
    evidence = make_unverified(source)
    attestation = EvidenceVerificationAttestation(
        verifier_identity="reviewer:independent",
        verified_at=DECISION_AT - timedelta(minutes=5),
        verification_method="source_reconciliation",
        verification_policy_version="ats-evidence-2",
        accepted=False,
        notes="Source context contradicts the extracted claim.",
    )
    rejected = apply_evidence_verification(
        evidence=evidence,
        source=source,
        researcher_identity="research-agent:primary",
        attestation=attestation,
    )
    assert rejected.version == 2
    assert rejected.verification_status is EvidenceStatus.REJECTED


def test_verifier_must_be_independent_from_researcher():
    source = make_source()
    evidence = make_unverified(source)
    attestation = EvidenceVerificationAttestation(
        verifier_identity="research-agent:primary",
        verified_at=DECISION_AT - timedelta(minutes=5),
        verification_method="source_reconciliation",
        verification_policy_version="ats-evidence-2",
        accepted=True,
    )
    with pytest.raises(ValueError, match="must differ"):
        apply_evidence_verification(
            evidence=evidence,
            source=source,
            researcher_identity="RESEARCH-AGENT:PRIMARY",
            attestation=attestation,
        )


def test_verified_version_passes_existing_eligibility_gate():
    source = make_source()
    evidence = make_unverified(source)
    attestation = EvidenceVerificationAttestation(
        verifier_identity="reviewer:independent",
        verified_at=DECISION_AT - timedelta(minutes=5),
        verification_method="source_reconciliation",
        verification_policy_version="ats-evidence-2",
        accepted=True,
    )
    verified = apply_evidence_verification(
        evidence=evidence,
        source=source,
        researcher_identity="research-agent:primary",
        attestation=attestation,
    )
    original = evaluate_scoring_eligibility(
        subject="ABC",
        factor="demand_acceleration",
        requested_contribution=Decimal(40),
        decision_at=DECISION_AT,
        evidence_items=(evidence,),
        sources={source.id: source},
    )
    promoted = evaluate_scoring_eligibility(
        subject="ABC",
        factor="demand_acceleration",
        requested_contribution=Decimal(40),
        decision_at=DECISION_AT,
        evidence_items=(verified,),
        sources={source.id: source},
    )
    assert original.allowed_contribution == 0
    assert promoted.allowed_contribution == 40
