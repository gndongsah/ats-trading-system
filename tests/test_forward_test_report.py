from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest

from src.domain.models import (
    JournalEntry,
    RiskIntent,
    ScoreSnapshot,
    SecurityRef,
    SetupType,
    TradePlan,
    TradeResult,
    TradeSide,
    TradingStyle,
)
from src.forward_test import ForwardTestRunEvidence, ForwardTestRunStatus
from src.forward_test_campaign import (
    build_forward_test_campaign,
    campaign_manifest_digest,
)
from src.forward_test_report import (
    ForwardTestReportKind,
    build_campaign_forward_test_report,
    build_weekly_forward_test_report,
)
from src.forward_test_scorecard import ForwardTestDailyScorecard
from src.run_type import RunType

START = datetime(2026, 11, 2, 14, 30, tzinfo=UTC)
CAMPAIGN = build_forward_test_campaign(
    created_at=START - timedelta(minutes=5),
    start_at=START,
    end_at=START + timedelta(weeks=8),
    setup_scope=("breakout", "pullback"),
    operational_policy_version="ops-v1",
)
DAY = START.date()


def scorecard(day: date, *, proposals=2, risk=1, guard=0, fills=1):
    return ForwardTestDailyScorecard(
        trading_day=day,
        proposals=proposals,
        approvals=proposals,
        rejections=0,
        blocked_runs=risk + guard,
        risk_denials=risk,
        guard_denials=guard,
        other_denials=0,
        fills=fills,
        closed_trades=0,
        cumulative_r=Decimal(0),
        expectancy_r=None,
        max_drawdown_r=Decimal(0),
        adherence_observations=0,
        adherence_rate=None,
    )


def run(\n    *,\n    day_offset=0,\n    reason="PermissionError:fresh risk check denied the proposal",\n    status=ForwardTestRunStatus.BLOCKED,\n    risk_gate_breach=False,\n):\n    at = START + timedelta(days=day_offset, hours=1)
    return ForwardTestRunEvidence(
        run_id=uuid4(),
        status=status,
        as_of=at,
        proposal_id=uuid4(),
        proposal_fingerprint="a" * 64,
        approval_decision="APPROVE",
        approver_identity="human:owner",
        campaign_id=CAMPAIGN.campaign_id,
        campaign_digest=campaign_manifest_digest(CAMPAIGN),
        playbook_version=CAMPAIGN.playbook_version,
        playbook_digest=CAMPAIGN.playbook_digest,
        market_source="fixture",
        market_symbol="ABC",
        market_revision=1,
        market_event_time=at - timedelta(minutes=1),
        market_available_at=at - timedelta(seconds=50),
        market_ingested_at=at - timedelta(seconds=40),
        blocked_reason=reason,
        risk_gate_breach=risk_gate_breach,
    )


def journal(
    r_value: str,
    *,
    day_offset=0,
    setup=SetupType.BREAKOUT,
    regime=1,
    followed=True,
    mae="0.5",
    mfe="2.0",
):
    closed = START + timedelta(days=day_offset, hours=2)
    plan = TradePlan(
        security=SecurityRef(ticker="ABC"),
        side=TradeSide.LONG,
        trading_style=TradingStyle.SWING,
        setup_type=setup,
        entry=Decimal(100),
        stop=Decimal(98),
        target=Decimal(106),
        quantity=Decimal(50),
        planned_at=closed - timedelta(hours=1),
        score_snapshot=ScoreSnapshot(
            decided_at=closed - timedelta(hours=2),
            market_opportunity=80,
            discovery=80,
            setup=85,
            regime_score=regime,
            inflection_stage=4,
        ),
        risk_intent=RiskIntent(stop_risk_budget=Decimal(100)),
    )
    result = TradeResult(
        plan_id=plan.id,
        run_type=RunType.PAPER,
        closed_at=closed,
        realized_pnl=Decimal(100) * Decimal(r_value),
        r_multiple=Decimal(r_value),
        outcome="win" if Decimal(r_value) > 0 else "loss",
        mae_r=Decimal(mae),
        mfe_r=Decimal(mfe),
        excursion_source="fixture",
    )
    return JournalEntry(
        plan=plan,
        result=result,
        plan_followed=followed,
        created_at=closed,
    )


def test_weekly_report_aggregates_flow_performance_and_slices():
    report = build_weekly_forward_test_report(
        campaign=CAMPAIGN,
        week_start=DAY,
        as_of=START + timedelta(days=6, hours=8),
        scorecards=(
            scorecard(DAY, proposals=3, risk=1, fills=1),
            scorecard(DAY + timedelta(days=1), proposals=2, guard=1, fills=1),
        ),
        run_evidence=(
            run(\n                status=ForwardTestRunStatus.FILLED,\n                reason=None,\n                risk_gate_breach=True,\n            ),\n            run(day_offset=1, reason="ValueError:market data is stale"),\n        ),
        journals=(
            journal("2.0", followed=True, mae="0.25", mfe="3"),
            journal("-1.0", followed=False, mae="1", mfe="0.5"),
            journal(
                "1.0",
                day_offset=1,
                setup=SetupType.PULLBACK,
                regime=2,
                followed=True,
            ),
        ),
    )

    assert report.kind is ForwardTestReportKind.WEEKLY
    assert report.run_type is RunType.PAPER
    assert report.campaign_id == CAMPAIGN.campaign_id
    assert report.scorecard_days == 2
    assert report.proposals == 5
    assert report.risk_denials == 2
    assert report.guard_denials == 1
    assert report.fills == 2
    assert report.closed_trades == 3
    assert report.cumulative_r == Decimal("2.0")
    assert report.expectancy_after_costs_r == Decimal("2.0") / Decimal(3)
    assert report.max_drawdown_r == Decimal("1.0")
    assert report.average_mae_r == Decimal("0.5833333333333333333333333333")
    assert report.average_mfe_r == Decimal("1.833333333333333333333333333")
    assert report.adherence_observations == 3
    assert report.adherence_rate == pytest.approx(2 / 3)
    assert report.risk_gate_breaches == 1
    assert [(item.setup, item.regime) for item in report.by_setup_regime] == [
        ("breakout", 1),
        ("pullback", 2),
    ]
    assert len(report.report_digest) == 64


def test_report_digest_is_reproducible():
    kwargs = {
        "campaign": CAMPAIGN,
        "week_start": DAY,
        "as_of": START + timedelta(days=6),
        "scorecards": (scorecard(DAY),),
        "run_evidence": (run(),),
        "journals": (journal("1.0"),),
    }

    first = build_weekly_forward_test_report(**kwargs)
    second = build_weekly_forward_test_report(**kwargs)

    assert first == second
    assert first.report_digest == second.report_digest


def test_campaign_report_caps_period_at_as_of():
    as_of = START + timedelta(days=10)

    report = build_campaign_forward_test_report(
        campaign=CAMPAIGN,
        as_of=as_of,
        scorecards=(scorecard(DAY),),
        run_evidence=(run(),),
        journals=(journal("1.0"),),
    )

    assert report.kind is ForwardTestReportKind.CAMPAIGN
    assert report.period_start == CAMPAIGN.start_at.date()
    assert report.period_end == as_of.date()


def test_duplicate_evidence_is_reported_without_double_counting():
    duplicate_day = scorecard(DAY)
    duplicate_run = run()
    duplicate_journal = journal("1.0")

    report = build_weekly_forward_test_report(
        campaign=CAMPAIGN,
        week_start=DAY,
        as_of=START + timedelta(days=6),
        scorecards=(duplicate_day, duplicate_day),
        run_evidence=(duplicate_run, duplicate_run),
        journals=(duplicate_journal, duplicate_journal),
    )

    assert report.scorecard_days == 1
    assert report.proposals == duplicate_day.proposals
    assert report.closed_trades == 1
    assert {item.code for item in report.evidence_integrity_exceptions} == {
        "DUPLICATE_SCORECARD_DAY",
        "DUPLICATE_RUN_ID",
        "DUPLICATE_TRADE_RESULT",
    }


@pytest.mark.parametrize("source", ["scorecard", "run", "journal"])
def test_future_evidence_is_rejected(source):
    kwargs = {
        "campaign": CAMPAIGN,
        "week_start": DAY,
        "as_of": START + timedelta(days=2),
        "scorecards": (),
        "run_evidence": (),
        "journals": (),
    }
    if source == "scorecard":
        kwargs["scorecards"] = (scorecard(DAY + timedelta(days=3)),)
    elif source == "run":
        kwargs["run_evidence"] = (run(day_offset=3),)
    else:
        kwargs["journals"] = (journal("1.0", day_offset=3),)

    with pytest.raises(ValueError, match="future"):
        build_weekly_forward_test_report(**kwargs)


@pytest.mark.parametrize("source", ["scorecard", "run", "journal"])
def test_non_paper_evidence_is_rejected(source):
    kwargs = {
        "campaign": CAMPAIGN,
        "week_start": DAY,
        "as_of": START + timedelta(days=6),
        "scorecards": (),
        "run_evidence": (),
        "journals": (),
    }
    if source == "scorecard":
        kwargs["scorecards"] = (
            scorecard(DAY).model_copy(update={"run_type": RunType.LIVE}),
        )
    elif source == "run":
        kwargs["run_evidence"] = (run().model_copy(update={"run_type": RunType.LIVE}),)
    else:
        entry = journal("1.0")
        kwargs["journals"] = (
            entry.model_copy(
                update={
                    "result": entry.result.model_copy(update={"run_type": RunType.LIVE})
                }
            ),
        )

    with pytest.raises(ValueError, match="PAPER"):
        build_weekly_forward_test_report(**kwargs)


def test_mismatched_campaign_identity_is_rejected():
    bad = run().model_copy(update={"campaign_digest": "f" * 64})

    with pytest.raises(ValueError, match="campaign digest mismatch"):
        build_weekly_forward_test_report(
            campaign=CAMPAIGN,
            week_start=DAY,
            as_of=START + timedelta(days=6),
            scorecards=(),
            run_evidence=(bad,),
            journals=(),
        )
