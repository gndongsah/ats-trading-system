"""ATS-023 proposal, human approval, and fresh-risk execution gate."""

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from hashlib import sha256
from typing import Protocol
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from src.domain.models import OrderIntent, TradePlan, _require_utc
from src.execution import (
    BrokerSubmissionReceipt,
    BrokerSubmissionRequest,
    ExecutionService,
)
from src.operational_guards import OperationalGuard, OperationalGuardContext
from src.portfolio import RiskDecision
from src.run_type import RunType


class ApprovalDecision(StrEnum):
    APPROVE = "APPROVE"
    REJECT = "REJECT"


class ProposalRiskSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    plan_id: UUID
    decision: RiskDecision
    approved_quantity: int = Field(ge=0, strict=True)
    portfolio_heat_r: Decimal = Field(ge=0)
    checked_at: datetime
    policy_version: str = Field(min_length=1)
    taxonomy_version: str = Field(min_length=1)

    @field_validator("checked_at")
    @classmethod
    def checked_at_is_utc(cls, value: datetime) -> datetime:
        return _require_utc(value)


class TradeProposal(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: UUID = Field(default_factory=uuid4)
    version: int = Field(default=1, ge=1)
    plan: TradePlan
    intent: OrderIntent
    risk: ProposalRiskSnapshot
    created_at: datetime

    @field_validator("created_at")
    @classmethod
    def created_at_is_utc(cls, value: datetime) -> datetime:
        return _require_utc(value)

    @model_validator(mode="after")
    def links_are_consistent(self) -> "TradeProposal":
        if self.intent.plan_id != self.plan.id:
            raise ValueError("proposal intent must reference the proposal plan")
        if self.risk.plan_id != self.plan.id:
            raise ValueError("proposal risk snapshot must reference the proposal plan")
        if self.created_at < self.plan.planned_at:
            raise ValueError("proposal cannot predate the trade plan")
        if self.risk.checked_at > self.created_at:
            raise ValueError("proposal risk snapshot cannot be from the future")
        return self

    @property
    def fingerprint(self) -> str:
        payload = self.model_dump_json(exclude={"id"})
        return sha256(payload.encode("utf-8")).hexdigest()

    def display_summary(self) -> dict[str, object]:
        scores = self.plan.score_snapshot
        return {
            "symbol": self.plan.security.ticker,
            "market_opportunity_score": scores.market_opportunity,
            "discovery_score": scores.discovery,
            "setup_score": scores.setup,
            "regime_score": scores.regime_score,
            "entry": self.plan.entry,
            "stop": self.plan.stop,
            "target": self.plan.target,
            "size": self.intent.quantity,
            "stop_risk_budget": self.plan.risk_intent.stop_risk_budget,
            "portfolio_heat_r": self.risk.portfolio_heat_r,
            "risk_decision": self.risk.decision.value,
        }


class HumanApproval(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    proposal_id: UUID
    proposal_fingerprint: str = Field(min_length=64, max_length=64)
    decision: ApprovalDecision
    approver_identity: str = Field(min_length=1, max_length=255)
    decided_at: datetime

    @field_validator("approver_identity")
    @classmethod
    def approver_is_not_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("approver identity must not be blank")
        return value

    @field_validator("decided_at")
    @classmethod
    def decided_at_is_utc(cls, value: datetime) -> datetime:
        return _require_utc(value)


class ProposalRiskChecker(Protocol):
    def check(self, proposal: TradeProposal, at: datetime) -> ProposalRiskSnapshot: ...


class ProposalExecutionGate:
    """Only exact approved proposals with a fresh passing risk check reach PAPER."""

    def __init__(
        self,
        *,
        risk_checker: ProposalRiskChecker,
        execution_service: ExecutionService,
        operational_guard: OperationalGuard,
    ) -> None:
        self._risk_checker = risk_checker
        self._execution_service = execution_service
        self._operational_guard = operational_guard

    def execute(
        self,
        *,
        proposal: TradeProposal,
        approval: HumanApproval,
        reference_price: Decimal,
        idempotency_key: str,
        at: datetime,
        market_data_observed_at: datetime,
        orders_submitted_today: int,
    ) -> BrokerSubmissionReceipt:
        at = _require_utc(at)
        if approval.proposal_id != proposal.id:
            raise PermissionError("approval does not belong to this proposal")
        if approval.proposal_fingerprint != proposal.fingerprint:
            raise PermissionError("approval does not match the immutable proposal")
        if approval.decision is not ApprovalDecision.APPROVE:
            raise PermissionError("proposal is not approved")
        if approval.decided_at > at:
            raise ValueError("execution cannot predate human approval")

        fresh_risk = self._risk_checker.check(proposal, at)
        if fresh_risk.plan_id != proposal.plan.id:
            raise PermissionError("fresh risk result references the wrong plan")
        if fresh_risk.checked_at < approval.decided_at:
            raise PermissionError("risk must be rechecked after human approval")
        if fresh_risk.checked_at > at:
            raise ValueError("fresh risk check cannot occur after execution time")
        if fresh_risk.decision is RiskDecision.DENY:
            raise PermissionError("fresh risk check denied the proposal")

        requested_quantity = int(proposal.intent.quantity)
        if Decimal(requested_quantity) != proposal.intent.quantity:
            raise PermissionError(
                "paper execution currently requires whole-share quantity"
            )
        if fresh_risk.approved_quantity < requested_quantity:
            raise PermissionError("fresh risk check no longer supports approved size")

        request = BrokerSubmissionRequest(
            idempotency_key=idempotency_key,
            run_type=RunType.PAPER,
            symbol=proposal.plan.security.ticker,
            intent=proposal.intent,
            reference_price=reference_price,
            submitted_at=at,
        )
        self._operational_guard.validate(
            request=request,
            context=OperationalGuardContext(
                evaluated_at=at,
                market_data_observed_at=market_data_observed_at,
                orders_submitted_today=orders_submitted_today,
            ),
        )
        return self._execution_service.submit(request)
