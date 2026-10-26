from collections.abc import Mapping
from datetime import datetime
from decimal import Decimal
from enum import Enum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from src.domain.evidence import EvidenceItem, SourceDocument
from src.domain.models import (
    EvidenceRef,
    EvidenceStatus,
    ScoreContribution,
    _require_utc,
)
from src.engine import score_discovery, validate_factor_scores
from src.playbook import PLAYBOOK, PlaybookConfig, ScoreSection


class EligibilityReason(str, Enum):
    NO_EVIDENCE = "no_evidence"
    MISSING_EVIDENCE = "missing_evidence"
    MISSING_SOURCE = "missing_source"
    SOURCE_NOT_TRACEABLE = "source_not_traceable"
    SOURCE_REFERENCE_MISMATCH = "source_reference_mismatch"
    PUBLICATION_AFTER_DECISION = "publication_after_decision"
    AVAILABILITY_AFTER_DECISION = "availability_after_decision"
    INGESTION_AFTER_DECISION = "ingestion_after_decision"
    OBSERVATION_AFTER_DECISION = "observation_after_decision"
    SUBJECT_MISMATCH = "subject_mismatch"
    FACTOR_MISMATCH = "factor_mismatch"
    FACTOR_NOT_CONFIGURED = "factor_not_configured"
    STATUS_UNVERIFIED = "status_unverified"
    STATUS_REJECTED = "status_rejected"
    MISSING_VERIFICATION_PROVENANCE = "missing_verification_provenance"
    VERIFICATION_AFTER_DECISION = "verification_after_decision"
    INSUFFICIENT_SUPPORT = "insufficient_support"
    INFLECTION_STAGE_MISMATCH = "inflection_stage_mismatch"
    EVIDENCE_PRECEDES_SOURCE_OBSERVATION = "evidence_precedes_source_observation"


class EvidenceVersionRef(BaseModel):
    model_config = ConfigDict(frozen=True)

    evidence_id: UUID
    evidence_version: int = Field(ge=1)
    source_document_version_id: UUID


class ScoringEligibility(BaseModel):
    model_config = ConfigDict(frozen=True)

    factor: str
    eligible: bool
    reason_codes: tuple[EligibilityReason, ...]
    supporting_evidence: tuple[EvidenceVersionRef, ...]
    requested_contribution: Decimal = Field(ge=0, allow_inf_nan=False)
    allowed_contribution: Decimal = Field(ge=0, allow_inf_nan=False)

    @model_validator(mode="after")
    def validate_allowed_credit(self) -> "ScoringEligibility":
        if self.allowed_contribution > self.requested_contribution:
            raise ValueError(
                "allowed contribution cannot exceed requested contribution"
            )
        if self.allowed_contribution > 0 and not self.eligible:
            raise ValueError("ineligible evidence cannot receive positive credit")
        if self.eligible and (self.reason_codes or not self.supporting_evidence):
            raise ValueError("eligible contributions require verified evidence")
        return self


class EvidenceScoredDiscovery(BaseModel):
    model_config = ConfigDict(frozen=True)

    score: float
    allowed_factor_scores: dict[str, Decimal]
    eligibility: dict[str, ScoringEligibility]


def _same_subject(left: str, right: str) -> bool:
    return left.strip().casefold() == right.strip().casefold()


def evaluate_scoring_eligibility(
    *,
    subject: str,
    factor: str,
    requested_contribution: Decimal,
    decision_at: datetime,
    evidence_items: tuple[EvidenceItem, ...],
    sources: Mapping[UUID, SourceDocument],
    unresolved_evidence_count: int = 0,
    inflection_stage: int | None = None,
    section: ScoreSection | None = None,
    playbook: PlaybookConfig = PLAYBOOK,
) -> ScoringEligibility:
    decision_at = _require_utc(decision_at)
    if isinstance(requested_contribution, bool):
        raise TypeError("requested contribution must be numeric, not boolean")
    requested_contribution = Decimal(str(requested_contribution))
    if section is None:
        section = playbook.discovery
    if factor not in section.factors:
        reason_codes = (EligibilityReason.FACTOR_NOT_CONFIGURED,)
        return ScoringEligibility(
            factor=factor,
            eligible=False,
            reason_codes=reason_codes,
            supporting_evidence=(),
            requested_contribution=requested_contribution,
            allowed_contribution=Decimal(0),
        )
    if (
        not requested_contribution.is_finite()
        or requested_contribution < 0
        or requested_contribution > Decimal(str(section.max_score))
    ):
        raise ValueError(
            f"requested contribution must be between 0 and {section.max_score}"
        )
    if unresolved_evidence_count < 0:
        raise ValueError("unresolved evidence count cannot be negative")
    if not evidence_items and unresolved_evidence_count == 0:
        return ScoringEligibility(
            factor=factor,
            eligible=False,
            reason_codes=(EligibilityReason.NO_EVIDENCE,),
            supporting_evidence=(),
            requested_contribution=requested_contribution,
            allowed_contribution=Decimal(0),
        )

    reasons: set[EligibilityReason] = set()
    if unresolved_evidence_count:
        reasons.add(EligibilityReason.MISSING_EVIDENCE)
    references: list[EvidenceVersionRef] = []
    for evidence in evidence_items:
        source = sources.get(evidence.source_document_version_id)
        if source is None:
            reasons.add(EligibilityReason.MISSING_SOURCE)
            continue
        if source.id != evidence.source_document_version_id:
            reasons.add(EligibilityReason.SOURCE_REFERENCE_MISMATCH)
            continue
        references.append(
            EvidenceVersionRef(
                evidence_id=evidence.id,
                evidence_version=evidence.version,
                source_document_version_id=source.id,
            )
        )
        if source.content_ref is None and source.content_hash is None:
            reasons.add(EligibilityReason.SOURCE_NOT_TRACEABLE)
        if source.published_at > decision_at:
            reasons.add(EligibilityReason.PUBLICATION_AFTER_DECISION)
        if source.effective_available_at > decision_at:
            reasons.add(EligibilityReason.AVAILABILITY_AFTER_DECISION)
        if source.effective_ingested_at > decision_at:
            reasons.add(EligibilityReason.INGESTION_AFTER_DECISION)
        if source.observed_at > decision_at or evidence.observed_at > decision_at:
            reasons.add(EligibilityReason.OBSERVATION_AFTER_DECISION)
        if evidence.observed_at < source.observed_at:
            reasons.add(EligibilityReason.EVIDENCE_PRECEDES_SOURCE_OBSERVATION)
        if not _same_subject(evidence.subject, subject) or not _same_subject(
            source.subject, subject
        ):
            reasons.add(EligibilityReason.SUBJECT_MISMATCH)
        if evidence.factor.casefold() != factor.casefold():
            reasons.add(EligibilityReason.FACTOR_MISMATCH)
        if (
            inflection_stage is not None
            and evidence.inflection_stage != inflection_stage
        ):
            reasons.add(EligibilityReason.INFLECTION_STAGE_MISMATCH)
        if evidence.verification_status is EvidenceStatus.UNVERIFIED:
            reasons.add(EligibilityReason.STATUS_UNVERIFIED)
        elif evidence.verification_status is EvidenceStatus.REJECTED:
            reasons.add(EligibilityReason.STATUS_REJECTED)
        elif any(
            value is None
            for value in (
                evidence.verifier_identity,
                evidence.verified_at,
                evidence.verification_method,
                evidence.verification_policy_version,
            )
        ):
            reasons.add(EligibilityReason.MISSING_VERIFICATION_PROVENANCE)
        elif evidence.verified_at > decision_at:
            reasons.add(EligibilityReason.VERIFICATION_AFTER_DECISION)
        if not (evidence.excerpt and evidence.excerpt.strip()) and not (
            evidence.metric_name
            and evidence.metric_value is not None
            and evidence.metric_unit
        ):
            reasons.add(EligibilityReason.INSUFFICIENT_SUPPORT)

    ordered_reasons = tuple(sorted(reasons, key=lambda reason: reason.value))
    eligible = not ordered_reasons
    return ScoringEligibility(
        factor=factor,
        eligible=eligible,
        reason_codes=ordered_reasons,
        supporting_evidence=tuple(references),
        requested_contribution=requested_contribution,
        allowed_contribution=(requested_contribution if eligible else Decimal(0)),
    )


def evidence_ref(evidence: EvidenceItem, source: SourceDocument) -> EvidenceRef:
    return EvidenceRef(
        id=evidence.id,
        version=evidence.version,
        source_document_version_id=source.id,
        source=source.publisher,
        source_url=str(source.source_url) if source.source_url is not None else None,
        published_at=source.published_at,
        observed_at=evidence.observed_at,
        metric=(
            f"{evidence.metric_name}={evidence.metric_value} {evidence.metric_unit}"
            if evidence.metric_name is not None
            else None
        ),
        excerpt=evidence.excerpt,
        confidence=evidence.confidence,
        verification_status=evidence.verification_status,
        verifier_identity=evidence.verifier_identity,
        verified_at=evidence.verified_at,
        verification_method=evidence.verification_method,
        verification_policy_version=evidence.verification_policy_version,
        verification_notes=evidence.verification_notes,
    )


def score_contribution_from_eligibility(
    *,
    factor: str,
    result: ScoringEligibility,
    evidence_items: tuple[EvidenceItem, ...],
    sources: Mapping[UUID, SourceDocument],
) -> ScoreContribution:
    if result.factor != factor:
        raise ValueError("eligibility result factor does not match contribution factor")
    references = {
        (evidence.id, evidence.version): evidence_ref(evidence, sources[source_id])
        for evidence in evidence_items
        if (source_id := evidence.source_document_version_id) in sources
    }
    selected_refs = tuple(
        references[(reference.evidence_id, reference.evidence_version)]
        for reference in result.supporting_evidence
        if (reference.evidence_id, reference.evidence_version) in references
    )
    return ScoreContribution(
        factor=factor,
        points=result.allowed_contribution,
        evidence=selected_refs,
    )


def score_discovery_with_evidence(
    *,
    factor_scores: Mapping[str, float | Decimal],
    evidence_by_factor: Mapping[str, tuple[EvidenceItem, ...]],
    sources: Mapping[UUID, SourceDocument],
    subject: str,
    decision_at: datetime,
    unresolved_evidence_by_factor: Mapping[str, int] | None = None,
    playbook: PlaybookConfig = PLAYBOOK,
) -> EvidenceScoredDiscovery:
    if any(isinstance(value, bool) for value in factor_scores.values()):
        raise TypeError("Discovery factor scores must be numeric, not boolean")
    normalized_scores = {
        factor: float(value) for factor, value in factor_scores.items()
    }
    validate_factor_scores(normalized_scores, playbook.discovery)
    eligibility = {
        factor: evaluate_scoring_eligibility(
            subject=subject,
            factor=factor,
            requested_contribution=Decimal(str(factor_scores[factor])),
            decision_at=decision_at,
            evidence_items=evidence_by_factor.get(factor, ()),
            sources=sources,
            unresolved_evidence_count=(unresolved_evidence_by_factor or {}).get(
                factor, 0
            ),
            section=playbook.discovery,
            playbook=playbook,
        )
        for factor in factor_scores
    }
    allowed_scores = {
        factor: result.allowed_contribution for factor, result in eligibility.items()
    }
    score = score_discovery(
        {factor: float(value) for factor, value in allowed_scores.items()}, playbook
    )
    return EvidenceScoredDiscovery(
        score=score,
        allowed_factor_scores=allowed_scores,
        eligibility=eligibility,
    )
