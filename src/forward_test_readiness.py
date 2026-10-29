"""ATS-035 deterministic forward-test readiness gate."""

from collections import defaultdict
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from src.forward_test_campaign import (
    ForwardTestCampaignManifest,
    campaign_manifest_digest,
)
from src.forward_test_report import (
    CampaignPerformanceSlice,
    ForwardTestEvidenceReport,
    ForwardTestReportKind,
)
from src.run_type import RunType

MIN_FORWARD_TEST_DURATION = timedelta(weeks=8)
MIN_TRADES_PER_SETUP = 20
MIN_ADHERENCE_EXCLUSIVE = Decimal("0.80")
ZERO = Decimal(0)


class ForwardTestGateDecision(StrEnum):
    PASS = "PASS"
    HOLD = "HOLD"
    FAIL = "FAIL"


class ForwardTestGateReason(StrEnum):
    CAMPAIGN_NOT_MATURE = "CAMPAIGN_NOT_MATURE"
    INSUFFICIENT_SETUP_SAMPLE = "INSUFFICIENT_SETUP_SAMPLE"
    NON_POSITIVE_EXPECTANCY = "NON_POSITIVE_EXPECTANCY"
    ADHERENCE_NOT_ABOVE_THRESHOLD = "ADHERENCE_NOT_ABOVE_THRESHOLD"
    RISK_GATE_BREACH = "RISK_GATE_BREACH"
    EVIDENCE_INTEGRITY_EXCEPTION = "EVIDENCE_INTEGRITY_EXCEPTION"
    CAMPAIGN_ENDED_BELOW_GATE = "CAMPAIGN_ENDED_BELOW_GATE"


class EvidenceReference(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    label: str = Field(min_length=1)
    reference: str = Field(min_length=1)


class SetupReadiness(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    setup: str
    closed_trades: int = Field(ge=0)
    cumulative_r: Decimal
    expectancy_after_costs_r: Decimal | None = None
    adherence_observations: int = Field(ge=0)
    adherence_rate: Decimal | None = Field(default=None, ge=ZERO, le=Decimal(1))
    sample_ready: bool
    expectancy_ready: bool
    adherence_ready: bool
    ready: bool


class ForwardTestReadinessEvaluation(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    decision: ForwardTestGateDecision
    evaluated_at: datetime
    campaign_id: UUID
    campaign_digest: str = Field(min_length=64, max_length=64)
    report_digest: str = Field(min_length=64, max_length=64)
    elapsed_days: int = Field(ge=0)
    campaign_complete: bool
    setup_readiness: tuple[SetupReadiness, ...]
    risk_gate_breaches: int = Field(ge=0)
    unresolved_integrity_issues: int = Field(ge=0)
    reasons: tuple[ForwardTestGateReason, ...]
    evidence: tuple[EvidenceReference, ...]
    live_approval_granted: bool = False
    ats009_override: bool = False


def _weighted_setup_metrics(
    slices: tuple[CampaignPerformanceSlice, ...],
) -> tuple[int, Decimal, Decimal | None]:
    closed = sum(item.closed_trades for item in slices)
    cumulative_r = sum((item.cumulative_r for item in slices), ZERO)

    observations = sum(item.adherence_observations for item in slices)
    if not observations:
        return closed, cumulative_r, None

    followed = sum(
        (
            Decimal(str(item.adherence_rate)) * Decimal(item.adherence_observations)
            for item in slices
            if item.adherence_rate is not None
        ),
        ZERO,
    )
    return closed, cumulative_r, followed / Decimal(observations)


def _setup_readiness(
    report: ForwardTestEvidenceReport,
    setup_scope: tuple[str, ...],
) -> tuple[SetupReadiness, ...]:
    grouped: dict[str, list[CampaignPerformanceSlice]] = defaultdict(list)
    for item in report.by_setup_regime:
        grouped[item.setup].append(item)

    output = []
    for setup in setup_scope:
        slices = tuple(grouped.get(setup, ()))
        closed, cumulative_r, adherence_rate = _weighted_setup_metrics(slices)
        expectancy = cumulative_r / Decimal(closed) if closed else None
        adherence_observations = sum(item.adherence_observations for item in slices)

        sample_ready = closed >= MIN_TRADES_PER_SETUP
        expectancy_ready = expectancy is not None and expectancy > ZERO
        adherence_ready = (
            adherence_rate is not None
            and adherence_rate > MIN_ADHERENCE_EXCLUSIVE
        )
        output.append(
            SetupReadiness(
                setup=setup,
                closed_trades=closed,
                cumulative_r=cumulative_r,
                expectancy_after_costs_r=expectancy,
                adherence_observations=adherence_observations,
                adherence_rate=adherence_rate,
                sample_ready=sample_ready,
                expectancy_ready=expectancy_ready,
                adherence_ready=adherence_ready,
                ready=sample_ready and expectancy_ready and adherence_ready,
            )
        )
    return tuple(output)


def evaluate_forward_test_readiness(
    *,
    campaign: ForwardTestCampaignManifest,
    report: ForwardTestEvidenceReport,
    evaluated_at: datetime | None = None,
) -> ForwardTestReadinessEvaluation:
    """Evaluate ATS-035 without granting LIVE authority."""

    if report.kind is not ForwardTestReportKind.CAMPAIGN:
        raise ValueError("ATS-035 requires a CAMPAIGN evidence report")
    if report.run_type is not RunType.PAPER:
        raise ValueError("ATS-035 accepts PAPER evidence only")

    expected_digest = campaign_manifest_digest(campaign)
    if report.campaign_id != campaign.campaign_id:
        raise ValueError("readiness report belongs to a different campaign")
    if report.campaign_digest != expected_digest:
        raise ValueError("readiness report campaign digest mismatch")
    if report.period_start != campaign.start_at.date():
        raise ValueError("readiness report must start at campaign start")
    expected_period_end = min(
        campaign.end_at.date(),
        report.generated_as_of.astimezone(UTC).date(),
    )
    if report.period_end != expected_period_end:
        raise ValueError("readiness report does not cover its full campaign window")

    evaluated = evaluated_at or report.generated_as_of
    if evaluated.tzinfo is None or evaluated.utcoffset() is None:
        raise ValueError("evaluated_at must be timezone-aware")
    evaluated = evaluated.astimezone(UTC)
    if evaluated < report.generated_as_of:
        raise ValueError("readiness evaluation cannot precede report generation")

    effective_end = min(evaluated, campaign.end_at)
    if effective_end < campaign.start_at:
        raise ValueError("readiness evaluation precedes campaign start")

    elapsed = effective_end - campaign.start_at
    campaign_complete = evaluated >= campaign.end_at
    setups = _setup_readiness(report, campaign.setup_scope)

    reasons: list[ForwardTestGateReason] = []
    hard_fail = False

    if report.risk_gate_breaches:
        reasons.append(ForwardTestGateReason.RISK_GATE_BREACH)
        hard_fail = True
    if report.evidence_integrity_exceptions:
        reasons.append(ForwardTestGateReason.EVIDENCE_INTEGRITY_EXCEPTION)
        hard_fail = True

    if elapsed < MIN_FORWARD_TEST_DURATION:
        reasons.append(ForwardTestGateReason.CAMPAIGN_NOT_MATURE)

    if any(not setup.sample_ready for setup in setups):
        reasons.append(ForwardTestGateReason.INSUFFICIENT_SETUP_SAMPLE)
    if any(not setup.expectancy_ready for setup in setups):
        reasons.append(ForwardTestGateReason.NON_POSITIVE_EXPECTANCY)
    if any(not setup.adherence_ready for setup in setups):
        reasons.append(ForwardTestGateReason.ADHERENCE_NOT_ABOVE_THRESHOLD)

    all_ready = (
        elapsed >= MIN_FORWARD_TEST_DURATION
        and all(setup.ready for setup in setups)
        and report.risk_gate_breaches == 0
        and not report.evidence_integrity_exceptions
    )

    if all_ready:
        decision = ForwardTestGateDecision.PASS
        reasons = []
    elif hard_fail:
        decision = ForwardTestGateDecision.FAIL
    elif campaign_complete:
        decision = ForwardTestGateDecision.FAIL
        reasons.append(ForwardTestGateReason.CAMPAIGN_ENDED_BELOW_GATE)
    else:
        decision = ForwardTestGateDecision.HOLD

    references = (
        EvidenceReference(
            label="campaign",
            reference=f"campaign:{campaign.campaign_id}",
        ),
        EvidenceReference(
            label="campaign-report",
            reference=f"sha256:{report.report_digest}",
        ),
        *(
            EvidenceReference(
                label=f"setup:{setup.setup}",
                reference=f"sha256:{report.report_digest}#setup={setup.setup}",
            )
            for setup in setups
        ),
    )

    return ForwardTestReadinessEvaluation(
        decision=decision,
        evaluated_at=evaluated,
        campaign_id=campaign.campaign_id,
        campaign_digest=expected_digest,
        report_digest=report.report_digest,
        elapsed_days=elapsed.days,
        campaign_complete=campaign_complete,
        setup_readiness=setups,
        risk_gate_breaches=report.risk_gate_breaches,
        unresolved_integrity_issues=len(report.evidence_integrity_exceptions),
        reasons=tuple(dict.fromkeys(reasons)),
        evidence=references,
        live_approval_granted=False,
        ats009_override=False,
    )
