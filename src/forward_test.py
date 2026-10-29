"""ATS-031 deterministic real-time PAPER forward-test orchestration."""

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from src.domain.models import JournalEntry, _require_utc
from src.execution import BrokerSubmissionReceipt, SubmissionStatus
from src.feedback import PaperFillJournalService
from src.forward_test_campaign import (
    ForwardTestCampaignManifest,
    campaign_manifest_digest,
    playbook_digest,
    validate_campaign_preflight,
)
from src.market_data import Bar, Observation
from src.playbook import PLAYBOOK, PlaybookConfig
from src.proposals import (
    HumanApproval,
    ProposalExecutionGate,
    TradeProposal,
    execution_authorization_from_human_approval,
)
from src.run_type import RunType



class ForwardTestRunStatus(StrEnum):
    BLOCKED = "BLOCKED"
    ACCEPTED = "ACCEPTED"
    FILLED = "FILLED"


class ForwardTestRunInput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    run_id: UUID = Field(default_factory=uuid4)
    proposal: TradeProposal
    human_approval: HumanApproval
    market_observation: Observation
    as_of: datetime
    orders_submitted_today: int = Field(ge=0, strict=True)
    idempotency_key: str = Field(min_length=1, max_length=120)
    journal_entry: JournalEntry
    expected_journal_revision: int | None = Field(default=None, ge=0)

    @field_validator("as_of")
    @classmethod
    def as_of_is_utc(cls, value: datetime) -> datetime:
        return _require_utc(value)

    @model_validator(mode="after")
    def current_market_input_is_point_in_time_safe(self) -> "ForwardTestRunInput":
        observation = self.market_observation
        event = observation.event
        if event.symbol != self.proposal.plan.security.ticker:
            raise ValueError("market observation symbol must match proposal")
        if not isinstance(event.payload, Bar):
            raise TypeError("forward-test execution requires a bar observation")
        if event.event_time > self.as_of:
            raise ValueError("market event is in the future")
        if event.available_at > self.as_of:
            raise ValueError("market data was not yet available")
        if observation.ingested_at > self.as_of:
            raise ValueError("market data was not yet ingested")
        if self.journal_entry.plan.id != self.proposal.plan.id:
            raise ValueError("journal entry must reference the proposal plan")
        return self

    @property
    def reference_price(self) -> Decimal:
        payload = self.market_observation.event.payload
        assert isinstance(payload, Bar)
        return payload.close


class ForwardTestRunEvidence(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    run_id: UUID
    run_type: RunType = RunType.PAPER
    status: ForwardTestRunStatus
    as_of: datetime
    proposal_id: UUID
    proposal_fingerprint: str
    approval_decision: str
    approver_identity: str
    campaign_id: UUID | None = None
    campaign_digest: str | None = Field(default=None, min_length=64, max_length=64)
    playbook_version: str
    playbook_digest: str = Field(min_length=64, max_length=64)
    market_source: str
    market_symbol: str
    market_revision: int
    market_event_time: datetime
    market_available_at: datetime
    market_ingested_at: datetime
    broker_adapter: str | None = None
    broker_submission_id: UUID | None = None
    journal_revision: int | None = None
    blocked_reason: str | None = None
    manual_step_types: tuple[str, ...] = ("HUMAN_APPROVAL",)
    live_execution_used: bool = False
    autonomy_policy_used: bool = False

    @field_validator(
        "as_of",
        "market_event_time",
        "market_available_at",
        "market_ingested_at",
    )
    @classmethod
    def timestamps_are_utc(cls, value: datetime) -> datetime:
        return _require_utc(value)


class ForwardTestRunResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    evidence: ForwardTestRunEvidence
    broker_receipt: BrokerSubmissionReceipt | None = None
    journal_entry: JournalEntry


class ForwardTestRunRepositoryProtocol:
    def save(self, evidence: ForwardTestRunEvidence) -> None:
        raise NotImplementedError


class ForwardTestOrchestrator:
    """Runs one point-in-time PAPER proposal through the accepted MVP pipeline."""

    def __init__(
        self,
        *,
        execution_gate: ProposalExecutionGate,
        journal_service: PaperFillJournalService,
        run_repository: ForwardTestRunRepositoryProtocol,
        campaign: ForwardTestCampaignManifest,
        operational_policy_version: str,
        playbook: PlaybookConfig = PLAYBOOK,
    ) -> None:
        self._execution_gate = execution_gate
        self._journal_service = journal_service
        self._run_repository = run_repository
        self._campaign = campaign
        self._campaign_digest = campaign_manifest_digest(campaign)
        self._operational_policy_version = operational_policy_version
        self._playbook = playbook
        self._frozen_playbook_version = playbook.version
        self._frozen_playbook_digest = playbook_digest(playbook)

    def run(self, request: ForwardTestRunInput) -> ForwardTestRunResult:
        current_digest = playbook_digest(self._playbook)
        common = {
            "run_id": request.run_id,
            "as_of": request.as_of,
            "proposal_id": request.proposal.id,
            "proposal_fingerprint": request.proposal.fingerprint,
            "approval_decision": request.human_approval.decision.value,
            "approver_identity": request.human_approval.approver_identity,
            "campaign_id": self._campaign.campaign_id,
            "campaign_digest": self._campaign_digest,
            "playbook_version": self._frozen_playbook_version,
            "playbook_digest": self._frozen_playbook_digest,
            "market_source": request.market_observation.event.source,
            "market_symbol": request.market_observation.event.symbol,
            "market_revision": request.market_observation.event.revision,
            "market_event_time": request.market_observation.event.event_time,
            "market_available_at": request.market_observation.event.available_at,
            "market_ingested_at": request.market_observation.ingested_at,
        }

        try:
            validate_campaign_preflight(
                self._campaign,
                playbook=self._playbook,
                operational_policy_version=self._operational_policy_version,
                as_of=request.as_of,
                setup=request.proposal.plan.setup_type.value,
            )
        except PermissionError as exc:
            evidence = ForwardTestRunEvidence(
                **common,
                status=ForwardTestRunStatus.BLOCKED,
                blocked_reason=f"CAMPAIGN_PREFLIGHT:{exc}",
            )
            self._run_repository.save(evidence)
            return ForwardTestRunResult(
                evidence=evidence,
                journal_entry=request.journal_entry,
            )

        if (
            self._playbook.version != self._frozen_playbook_version
            or current_digest != self._frozen_playbook_digest
        ):
            evidence = ForwardTestRunEvidence(
                **common,
                status=ForwardTestRunStatus.BLOCKED,
                blocked_reason="PLAYBOOK_POLICY_DRIFT",
            )
            self._run_repository.save(evidence)
            return ForwardTestRunResult(
                evidence=evidence,
                journal_entry=request.journal_entry,
            )

        try:
            authorization = execution_authorization_from_human_approval(
                request.human_approval
            )
            receipt = self._execution_gate.execute(
                proposal=request.proposal,
                execution_authorization=authorization,
                reference_price=request.reference_price,
                idempotency_key=request.idempotency_key,
                at=request.as_of,
                market_data_observed_at=request.market_observation.event.event_time,
                orders_submitted_today=request.orders_submitted_today,
            )
        except (PermissionError, ValueError) as exc:
            evidence = ForwardTestRunEvidence(
                **common,
                status=ForwardTestRunStatus.BLOCKED,
                blocked_reason=f"{type(exc).__name__}:{exc}",
            )
            self._run_repository.save(evidence)
            return ForwardTestRunResult(
                evidence=evidence,
                journal_entry=request.journal_entry,
            )

        journal_entry = request.journal_entry
        journal_revision = request.expected_journal_revision
        status = ForwardTestRunStatus.ACCEPTED
        if receipt.status is SubmissionStatus.FILLED:
            journal_entry, journal_revision = self._journal_service.capture(
                entry=request.journal_entry,
                intent=request.proposal.intent,
                receipt=receipt,
                captured_at=request.as_of,
                expected_revision=request.expected_journal_revision,
            )
            status = ForwardTestRunStatus.FILLED

        evidence = ForwardTestRunEvidence(
            **common,
            status=status,
            broker_adapter=receipt.adapter,
            broker_submission_id=receipt.submission_id,
            journal_revision=journal_revision,
        )
        self._run_repository.save(evidence)
        return ForwardTestRunResult(
            evidence=evidence,
            broker_receipt=receipt,
            journal_entry=journal_entry,
        )
