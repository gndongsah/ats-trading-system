from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session

from src.database import get_db
from src.discovery import classify_discovery
from src.engine import score_market_opportunity
from src.evidence_verification import score_discovery_with_evidence
from src.opportunity import classify_market_opportunity
from src.persistence.evidence_repository import EvidenceRepository
from src.playbook import PLAYBOOK


class FactorScoresRequest(BaseModel):
    factor_scores: dict[str, float]

    @field_validator("factor_scores")
    @classmethod
    def reject_boolean_factor_scores(cls, value: dict[str, float]) -> dict[str, float]:
        for factor, score in value.items():
            if isinstance(score, bool):
                raise TypeError(f"Factor '{factor}' must be numeric, not boolean")
        return value


class EvidenceVersionRequest(BaseModel):
    evidence_id: UUID
    version: int = Field(ge=1)


class DiscoveryScoreRequest(FactorScoresRequest):
    subject: str | None = None
    decision_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    evidence_by_factor: dict[str, tuple[EvidenceVersionRequest, ...]] = Field(
        default_factory=dict
    )


app = FastAPI(title="ATS Trading System")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "healthy"}


@app.get("/playbook/version")
def playbook_version() -> dict[str, str]:
    return {"version": PLAYBOOK.version}


@app.post("/layer0/market-opportunity")
def market_opportunity(request: FactorScoresRequest) -> dict[str, float | str]:
    try:
        score = score_market_opportunity(request.factor_scores)
        decision = classify_market_opportunity(score)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return {"score": score, "decision": decision.value}


@app.post("/layer1/discovery")
def discovery(
    request: DiscoveryScoreRequest, session: Annotated[Session, Depends(get_db)]
) -> dict[str, object]:
    try:
        unexpected_evidence_factors = set(request.evidence_by_factor) - set(
            request.factor_scores
        )
        if unexpected_evidence_factors:
            raise ValueError(
                "evidence supplied for factors without scores: "
                + ", ".join(sorted(unexpected_evidence_factors))
            )
        repository = EvidenceRepository(session)
        evidence_by_factor = {}
        sources = {}
        unresolved_evidence = {}
        for factor, evidence_refs in request.evidence_by_factor.items():
            evidence_items = []
            for evidence_ref in evidence_refs:
                evidence = repository.get_evidence(
                    evidence_ref.evidence_id, evidence_ref.version
                )
                if evidence is None:
                    unresolved_evidence[factor] = unresolved_evidence.get(factor, 0) + 1
                    continue
                evidence_items.append(evidence)
                source = repository.get_source(evidence.source_document_version_id)
                if source is not None:
                    sources[source.id] = source
            evidence_by_factor[factor] = tuple(evidence_items)
        scored = score_discovery_with_evidence(
            factor_scores=request.factor_scores,
            evidence_by_factor=evidence_by_factor,
            sources=sources,
            subject=request.subject or "",
            decision_at=request.decision_at,
            unresolved_evidence_by_factor=unresolved_evidence,
        )
        decision = classify_discovery(scored.score)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return {
        "score": scored.score,
        "decision": decision.value,
        "decision_at": request.decision_at.isoformat(),
        "eligibility": {
            factor: {
                "eligible": result.eligible,
                "reason_codes": [reason.value for reason in result.reason_codes],
                "supporting_evidence": [
                    reference.model_dump(mode="json")
                    for reference in result.supporting_evidence
                ],
                "requested_contribution": str(result.requested_contribution),
                "allowed_contribution": str(result.allowed_contribution),
            }
            for factor, result in scored.eligibility.items()
        },
    }
