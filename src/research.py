"""Typed Layer 0/1 research boundary for evidence-first scoring."""

from collections.abc import Mapping
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from src.domain.evidence import EvidenceItem, SourceDocument
from src.domain.models import EvidenceStatus, _require_utc
from src.evidence_verification import (
    EvidenceScoredDiscovery,
    EvidenceScoredMarketOpportunity,
    score_discovery_with_evidence,
    score_market_opportunity_with_evidence,
)
from src.playbook import PLAYBOOK, PlaybookConfig


class ResearchEvidenceDraft(BaseModel):
    """Structured model output that cannot self-assert verification."""

    model_config = ConfigDict(frozen=True)

    source_document_version_id: UUID
    subject: str = Field(min_length=1, max_length=255)
    factor: str = Field(min_length=1, max_length=128)
    inflection_stage: int | None = Field(default=None, ge=1, le=9)
    excerpt: str | None = Field(default=None, max_length=10000)
    metric_name: str | None = Field(default=None, max_length=128)
    metric_value: Decimal | None = Field(default=None, allow_inf_nan=False)
    metric_unit: str | None = Field(default=None, max_length=64)
    confidence: Decimal = Field(ge=Decimal(0), le=Decimal(1))

    @field_validator("subject", "factor", "excerpt", "metric_name", "metric_unit")
    @classmethod
    def trim_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            return None
        return value

    @field_validator("metric_value", "confidence", mode="before")
    @classmethod
    def reject_boolean_numeric_values(cls, value: object) -> object:
        if isinstance(value, bool):
            raise TypeError("numeric research values must not be boolean")
        return value

    @model_validator(mode="after")
    def require_supported_claim(self) -> "ResearchEvidenceDraft":
        metric_values = (self.metric_name, self.metric_value, self.metric_unit)
        has_metric = any(value is not None for value in metric_values)
        if has_metric and not all(value is not None for value in metric_values):
            raise ValueError("structured metrics require a name, value, and unit")
        if self.excerpt is None and not has_metric:
            raise ValueError(\n                "research evidence requires an excerpt or structured metric"\n            )
        return self


class Layer01ResearchScore(BaseModel):
    model_config = ConfigDict(frozen=True)

    thesis_breaker: str
    market_opportunity: EvidenceScoredMarketOpportunity
    discovery: EvidenceScoredDiscovery


def materialize_unverified_research_evidence(
    *,
    draft: ResearchEvidenceDraft,
    source: SourceDocument,
    observed_at: datetime,
) -> EvidenceItem:
    """Bind model output to a persisted source without granting verification."""

    observed_at = _require_utc(observed_at)
    if draft.source_document_version_id != source.id:
        raise ValueError(\n            "research draft source reference does not match source document"\n        )
    if draft.subject.strip().casefold() != source.subject.strip().casefold():
        raise ValueError("research draft subject does not match source document")
    if observed_at < source.observed_at:
        raise ValueError("research evidence cannot be observed before its source")

    return EvidenceItem(
        source_document_version_id=source.id,
        subject=draft.subject,
        factor=draft.factor,
        inflection_stage=draft.inflection_stage,
        excerpt=draft.excerpt,
        metric_name=draft.metric_name,
        metric_value=draft.metric_value,
        metric_unit=draft.metric_unit,
        confidence=draft.confidence,
        verification_status=EvidenceStatus.UNVERIFIED,
        observed_at=observed_at,
    )


def score_layer0_layer1_research(
    *,
    subject: str,
    decision_at: datetime,
    thesis_breaker: str,
    market_factor_scores: Mapping[str, float | Decimal],
    market_evidence_by_factor: Mapping[str, tuple[EvidenceItem, ...]],
    discovery_factor_scores: Mapping[str, float | Decimal],
    discovery_evidence_by_factor: Mapping[str, tuple[EvidenceItem, ...]],
    sources: Mapping[UUID, SourceDocument],
    unresolved_market_evidence_by_factor: Mapping[str, int] | None = None,
    unresolved_discovery_evidence_by_factor: Mapping[str, int] | None = None,
    playbook: PlaybookConfig = PLAYBOOK,
) -> Layer01ResearchScore:
    """Score Layer 0 and Layer 1 only from eligible persisted evidence."""

    thesis_breaker = thesis_breaker.strip()
    if not thesis_breaker:
        raise ValueError("Discovery scoring requires a written thesis-breaker")

    market = score_market_opportunity_with_evidence(
        factor_scores=market_factor_scores,
        evidence_by_factor=market_evidence_by_factor,
        sources=sources,
        subject=subject,
        decision_at=decision_at,
        unresolved_evidence_by_factor=unresolved_market_evidence_by_factor,
        playbook=playbook,
    )
    discovery = score_discovery_with_evidence(
        factor_scores=discovery_factor_scores,
        evidence_by_factor=discovery_evidence_by_factor,
        sources=sources,
        subject=subject,
        decision_at=decision_at,
        unresolved_evidence_by_factor=unresolved_discovery_evidence_by_factor,
        playbook=playbook,
    )
    return Layer01ResearchScore(
        thesis_breaker=thesis_breaker,
        market_opportunity=market,
        discovery=discovery,
    )
