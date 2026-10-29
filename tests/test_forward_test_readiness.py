from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from src.forward_test_campaign import (
    build_forward_test_campaign,
    campaign_manifest_digest,
)
from src.forward_test_readiness import (
    ForwardTestGateDecision,
    ForwardTestGateReason,
    evaluate_forward_test_readiness,
)
from src.forward_test_report import (
    CampaignPerformanceSlice,
    EvidenceIntegrityException,
    ForwardTestEvidenceReport,
    ForwardTestReportKind,
)
from src.run_type import RunType

START = datetime(2026, 11, 2, 14, 30, tzinfo=UTC)
CAMPAIGN = build_forward_test_campaign(
    created_at=START - timedelta(minutes=5),
    start_at=START,
    end_at=START + timedelta(weeks=8),
    setup_scope=("breakout", "pullback"),
    operational_policy_version="ops-v1",
)


def performance(
    setup: str,
    *,
    trades: int = 20,
    cumulative_r: str = "4.0",
    adherence_rate: float = 0.85,
    adherence_observations: int | None = None,
    regime: int = 1,
):
    observations = trades if adherence_observations is None else adherence_observations
    return CampaignPerformanceSlice(
        setup=setup,
        regime=regime,
        closed_trades=trades,
        cumulative_r=Decimal(cumulative_r),
        expectancy_after_costs_r=(
            Decimal(cumulative_r) / Decimal(trades) if trades else None
        ),
        max_drawdown_r=Decimal(2),
        average_mae_r=Decimal("0.5"),
        average_mfe_r=Decimal("1.5"),
        adherence_observations=observations,
        adherence_rate=adherence_rate if observations else None,
    )


def report(
    *,
    generated_at: datetime | None = None,
    slices: tuple[CampaignPerformanceSlice, ...] | None = None,
    breaches: int = 0,
    exceptions: tuple[EvidenceIntegrityException, ...] = (),
):
    generated = generated_at or CAMPAIGN.end_at
    slices = slices or (
        performance("breakout"),
        performance("pullback"),
    )
    return ForwardTestEvidenceReport(
        kind=ForwardTestReportKind.CAMPAIGN,
        campaign_id=CAMPAIGN.campaign_id,
        campaign_digest=campaign_manifest_digest(CAMPAIGN),
        period_start=CAMPAIGN.start_at.date(),
        period_end=min(CAMPAIGN.end_at.date(), generated.date()),
        generated_as_of=generated,
        report_digest="d" * 64,
        scorecard_days=40,
        proposals=100,
        approvals=80,
        rejections=20,
        blocked_runs=10,
        risk_denials=0,
        guard_denials=10,
        other_denials=0,
        fills=60,
        closed_trades=sum(item.closed_trades for item in slices),
        cumulative_r=sum((item.cumulative_r for item in slices), Decimal(0)),
        expectancy_after_costs_r=Decimal("0.2"),
        max_drawdown_r=Decimal(3),
        average_mae_r=Decimal("0.5"),
        average_mfe_r=Decimal("1.5"),
        adherence_observations=sum(item.adherence_observations for item in slices),
        adherence_rate=0.85,
        risk_gate_breaches=breaches,
        evidence_integrity_exceptions=exceptions,
        by_setup_regime=slices,
    )


def test_gate_passes_mature_campaign_when_every_setup_meets_thresholds():
    result = evaluate_forward_test_readiness(
        campaign=CAMPAIGN,
        report=report(),
    )

    assert result.decision is ForwardTestGateDecision.PASS
    assert result.reasons == ()
    assert all(item.ready for item in result.setup_readiness)
    assert result.elapsed_days == 56
    assert not result.live_approval_granted
    assert not result.ats009_override
    assert result.evidence[0].reference == f"campaign:{CAMPAIGN.campaign_id}"
    assert result.evidence[1].reference == "sha256:" + ("d" * 64)


def test_gate_holds_before_eight_weeks_even_when_metrics_are_ready():
    generated = START + timedelta(weeks=7, days=6)

    result = evaluate_forward_test_readiness(
        campaign=CAMPAIGN,
        report=report(generated_at=generated),
    )

    assert result.decision is ForwardTestGateDecision.HOLD
    assert ForwardTestGateReason.CAMPAIGN_NOT_MATURE in result.reasons


def test_gate_holds_for_recoverable_sample_gap_before_campaign_end():
    generated = START + timedelta(weeks=7)
    thin = (
        performance("breakout", trades=19),
        performance("pullback"),
    )

    result = evaluate_forward_test_readiness(
        campaign=CAMPAIGN,
        report=report(generated_at=generated, slices=thin),
    )

    assert result.decision is ForwardTestGateDecision.HOLD
    assert ForwardTestGateReason.INSUFFICIENT_SETUP_SAMPLE in result.reasons


def test_gate_fails_at_campaign_end_when_sample_is_still_insufficient():
    thin = (
        performance("breakout", trades=19),
        performance("pullback"),
    )

    result = evaluate_forward_test_readiness(
        campaign=CAMPAIGN,
        report=report(slices=thin),
    )

    assert result.decision is ForwardTestGateDecision.FAIL
    assert ForwardTestGateReason.INSUFFICIENT_SETUP_SAMPLE in result.reasons
    assert ForwardTestGateReason.CAMPAIGN_ENDED_BELOW_GATE in result.reasons


def test_gate_fails_immediately_on_risk_gate_breach():
    generated = START + timedelta(weeks=4)

    result = evaluate_forward_test_readiness(
        campaign=CAMPAIGN,
        report=report(generated_at=generated, breaches=1),
    )

    assert result.decision is ForwardTestGateDecision.FAIL
    assert result.risk_gate_breaches == 1
    assert ForwardTestGateReason.RISK_GATE_BREACH in result.reasons


def test_gate_fails_immediately_on_integrity_exception():
    generated = START + timedelta(weeks=4)
    exceptions = (
        EvidenceIntegrityException(code="DUPLICATE_RUN_ID", detail="run-1"),
    )

    result = evaluate_forward_test_readiness(
        campaign=CAMPAIGN,
        report=report(generated_at=generated, exceptions=exceptions),
    )

    assert result.decision is ForwardTestGateDecision.FAIL
    assert result.unresolved_integrity_issues == 1
    assert ForwardTestGateReason.EVIDENCE_INTEGRITY_EXCEPTION in result.reasons


def test_adherence_threshold_is_strictly_greater_than_eighty_percent():
    slices = (
        performance("breakout", adherence_rate=0.80),
        performance("pullback"),
    )

    result = evaluate_forward_test_readiness(
        campaign=CAMPAIGN,
        report=report(slices=slices),
    )

    breakout = next(item for item in result.setup_readiness if item.setup == "breakout")
    assert breakout.adherence_rate == Decimal("0.8")
    assert not breakout.adherence_ready
    assert result.decision is ForwardTestGateDecision.FAIL


def test_expectancy_is_aggregated_across_regimes_per_setup():
    slices = (
        performance(
            "breakout",
            trades=10,
            cumulative_r="3.0",
            adherence_rate=0.9,
            regime=1,
        ),
        performance(
            "breakout",
            trades=10,
            cumulative_r="1.0",
            adherence_rate=0.9,
            regime=2,
        ),
        performance("pullback"),
    )

    result = evaluate_forward_test_readiness(
        campaign=CAMPAIGN,
        report=report(slices=slices),
    )

    breakout = next(item for item in result.setup_readiness if item.setup == "breakout")
    assert breakout.closed_trades == 20
    assert breakout.cumulative_r == Decimal("4.0")
    assert breakout.expectancy_after_costs_r == Decimal("0.2")
    assert breakout.ready


def test_missing_setup_is_treated_as_zero_sample():
    result = evaluate_forward_test_readiness(
        campaign=CAMPAIGN,
        report=report(slices=(performance("breakout"),)),
    )

    pullback = next(item for item in result.setup_readiness if item.setup == "pullback")
    assert pullback.closed_trades == 0
    assert not pullback.ready
    assert result.decision is ForwardTestGateDecision.FAIL


def test_gate_rejects_non_campaign_or_non_paper_report():
    weekly = report().model_copy(update={"kind": ForwardTestReportKind.WEEKLY})
    with pytest.raises(ValueError, match="CAMPAIGN"):
        evaluate_forward_test_readiness(campaign=CAMPAIGN, report=weekly)

    live = report().model_copy(update={"run_type": RunType.LIVE})
    with pytest.raises(ValueError, match="PAPER"):
        evaluate_forward_test_readiness(campaign=CAMPAIGN, report=live)


def test_gate_rejects_campaign_identity_mismatch():
    bad = report().model_copy(update={"campaign_digest": "f" * 64})

    with pytest.raises(ValueError, match="campaign digest mismatch"):
        evaluate_forward_test_readiness(campaign=CAMPAIGN, report=bad)


def test_evaluation_cannot_precede_report_generation():
    campaign_report = report()

    with pytest.raises(ValueError, match="cannot precede"):
        evaluate_forward_test_readiness(
            campaign=CAMPAIGN,
            report=campaign_report,
            evaluated_at=CAMPAIGN.end_at - timedelta(seconds=1),
        )


def test_non_positive_expectancy_fails_at_campaign_end():
    slices = (
        performance("breakout", cumulative_r="0"),
        performance("pullback"),
    )

    result = evaluate_forward_test_readiness(
        campaign=CAMPAIGN,
        report=report(slices=slices),
    )

    breakout = next(item for item in result.setup_readiness if item.setup == "breakout")
    assert breakout.expectancy_after_costs_r == Decimal(0)
    assert not breakout.expectancy_ready
    assert result.decision is ForwardTestGateDecision.FAIL
    assert ForwardTestGateReason.NON_POSITIVE_EXPECTANCY in result.reasons


def test_gate_rejects_partial_campaign_report_window():
    partial = report().model_copy(
        update={"period_start": CAMPAIGN.start_at.date() + timedelta(days=1)}
    )

    with pytest.raises(ValueError, match="start at campaign start"):
        evaluate_forward_test_readiness(campaign=CAMPAIGN, report=partial)

    truncated = report().model_copy(
        update={"period_end": CAMPAIGN.end_at.date() - timedelta(days=1)}
    )
    with pytest.raises(ValueError, match="full campaign window"):
        evaluate_forward_test_readiness(campaign=CAMPAIGN, report=truncated)
