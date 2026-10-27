"""Second-pass critic and evidence-verifier workflow for ATS research."""

from datetime import datetime
from enum import Enum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from src.domain.evidence import EvidenceItem, SourceDocument
from src.domain.models import EvidenceStatus, _require_utc
from src.evidence_verification import EvidenceVersionRef


class CriticDisposition(str, Enum):
    SUPPORTED = "supported"
    CONTRADICTED = "contradicted"
    INSUFFICIENT = "insufficient"


class CriticFinding(BaseModel):
    model_config = ConfigDict(frozen=True)

    factor: str = Field(min_length=1, max_length=128)
    challenged_claim: str = Field(min_length=1, max_length=2000)
    disposition: CriticDisposition
    evidence: tuple[EvidenceVersionRef, ...]
    rationale: str = Field(min_length=1, max_length=4000)

    @field_validator("factor", "challenged_claim", "rationale")
    @classmethod
    def trim_required_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("value must not be blank")
        return value

    @model_validator(mode="after")
    def require_evidence_for_material_findings(self) -> "CriticFinding":
        if self.disposition is not CriticDisposition.INSUFFICIENT and not self.evidence:
            raise ValueError("supported or contradicted findings require evidence references")
        return self


class ResearchCritique(BaseModel):
    model_config = ConfigDict(frozen=True)

    subject: str = Field(min_length=1, max_length=255)
    researcher_identity: str = Field(min_length=1, max_length=255)
    critic_identity: str = Field(min_length=1, max_length=255)
    thesis: str = Field(min_length=1, max_length=10000)
    thesis_breaker: str = Field(min_length=1, max_length=4000)
    findings: tuple[CriticFinding, ...]

    @field_validator(
        "subject",
        "researcher_identity",
        "critic_identity",
        "thesis",
        "thesis_breaker",
    )
    @classmethod
    def trim_required_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("value must not be blank")
        return value

    @model_validator(mode="after")
    def require_independent_second_pass(self) -> "ResearchCritique":
        if self.researcher_identity.casefold() == self.critic_identity.casefold():
            raise ValueError("critic identity must differ from researcher identity")
        if not self.findings:
            raise ValueError("critic workflow requires at least one finding")
        return self


class CritiqueReviewReason(str, Enum):
    MISSING_EVIDENCE = "missing_evidence"
    EVIDENCE_VERSION_MISMATCH = "evidence_version_mismatch"
    SOURCE_REFERENCE_MISMATCH = "source_reference_mismatch"
    SUBJECT_MISMATCH = "subject_mismatch"
    CONTRADICTION = "contradiction"
    INSUFFICIENT_SUPPORT = "insufficient_support"


class CritiqueReview(BaseModel):
    model_config = ConfigDict(frozen=True)

    accepted: bool
    reason_codes: tuple[CritiqueReviewReason, ...]
    contradicted_factors: tuple[str, ...]


class EvidenceVerificationAttestation(BaseModel):
    model_config = ConfigDict(frozen=True)

    verifier_identity: str = Field(min_length=1, max_length=255)
    verified_at: datetime
    verification_method: str = Field(min_length=1, max_length=128)
    verification_policy_version: str = Field(min_length=1, max_length=64)
    accepted: bool
    notes: str | None = None

    @field_validator(
        "verifier_identity",
        "verification_method",
        "verification_policy_version",
    )
    @classmethod
    def trim_required_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("value must not be blank")
        return value

    @field_validator("notes")
    @classmethod
    def trim_optional_notes(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        return value or None

    @field_validator("verified_at")
    @classmethod
    def verified_at_is_utc(cls, value: datetime) -> datetime:
        return _require_utc(value)


def review_research_critique(
    *,
    critique: ResearchCritique,
    evidence_items: dict[UUID, EvidenceItem],
    sources: dict[UUID, SourceDocument],
) -> CritiqueReview:
    """Validate critic references and fail closed on contradiction/insufficiency."""

    reasons: set[CritiqueReviewReason] = set()
    contradicted_factors: set[str] = set()

    for finding in critique.findings:
        if finding.disposition is CriticDisposition.CONTRADICTED:
            reasons.add(CritiqueReviewReason.CONTRADICTION)
            contradicted_factors.add(finding.factor)
        elif finding.disposition is CriticDisposition.INSUFFICIENT:
            reasons.add(CritiqueReviewReason.INSUFFICIENT_SUPPORT)

        for reference in finding.evidence:
            evidence = evidence_items.get(reference.evidence_id)
            if evidence is None:
                reasons.add(CritiqueReviewReason.MISSING_EVIDENCE)
                continue
            if evidence.version != reference.evidence_version:
                reasons.add(CritiqueReviewReason.EVIDENCE_VERSION_MISMATCH)
            if evidence.source_document_version_id != reference.source_document_version_id:
                reasons.add(CritiqueReviewReason.SOURCE_REFERENCE_MISMATCH)
                continue
            source = sources.get(reference.source_document_version_id)
            if source is None or source.id != evidence.source_document_version_id:
                reasons.add(CritiqueReviewReason.SOURCE_REFERENCE_MISMATCH)
                continue
            if (
                evidence.subject.strip().casefold() != critique.subject.strip().casefold()
                or source.subject.strip().casefold()
                != critique.subject.strip().casefold()
            ):
                reasons.add(CritiqueReviewReason.SUBJECT_MISMATCH)

    ordered = tuple(sorted(reasons, key=lambda reason: reason.value))
    return CritiqueReview(
        accepted=not ordered,
        reason_codes=ordered,
        contradicted_factors=tuple(sorted(contradicted_factors)),
    )


def apply_evidence_verification(
    *,
    evidence: EvidenceItem,
    source: SourceDocument,
    researcher_identity: str,
    attestation: EvidenceVerificationAttestation,
) -> EvidenceItem:
    """Create the next immutable evidence version from an explicit verifier attestation."""

    if evidence.verification_status is not EvidenceStatus.UNVERIFIED:
        raise ValueError("only unverified evidence can enter verifier workflow")
    if evidence.source_document_version_id != source.id:
        raise ValueError("evidence source reference does not match source document")
    if researcher_identity.strip().casefold() == attestation.verifier_identity.casefold():
        raise ValueError("verifier identity must differ from researcher identity")
    if attestation.verified_at < evidence.observed_at:
        raise ValueError("evidence cannot be verified before it is observed")

    return evidence.model_copy(
        update={
            "version": evidence.version + 1,
            "verification_status": (
                EvidenceStatus.VERIFIED
                if attestation.accepted
                else EvidenceStatus.REJECTED
            ),
            "verifier_identity": attestation.verifier_identity,
            "verified_at": attestation.verified_at,
            "verification_method": attestation.verification_method,
            "verification_policy_version": attestation.verification_policy_version,
            "verification_notes": attestation.notes,
        }
    )
