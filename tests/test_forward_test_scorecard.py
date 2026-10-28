from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from src.agent_evaluation import AgentEvaluationSummary, ClassificationMetrics
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
from src.forward_test_scorecard import build_forward_test_daily_scorecard
from src.jev_experiment import CalibrationSummary, ExperimentArm
from src.run_type import RunType


DAY = date(2026, 10, 27)
AT = datetime(2026, 10, 27, 15, 0, tzinfo=UTC)


def run(status, *, approval="APPROVE", reason=None, minutes=0):
    return ForwardTestRunEvidence(
        run_id=uuid4(),
        status=status,
        as_of=AT + timedelta(minutes=minutes),
        proposal_id=uuid4(),
        proposal_fingerprint="a" * 64,
        approval_decision=approval,
        approver_identity="human:owner",
        playbook_version="3.0",
        playbook_digest="b" * 64,
        market_source="fixture",
        market_symbol="ABC",
        market_revision=1,
        market_event_time=AT - timedelta(minutes=1),
        market_available_at=AT - timedelta(seconds=50),
        market_ingested_at=AT - timedelta(seconds=40),
        blocked_reason=reason,
    )


def journal(r_value, *, regime=1, setup=SetupType.BREAKOUT, followed=True, mae="0.5", mfe="2.0", days=0):
    closed = AT + timedelta(days=days)
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
        created_at=plan.planned_at,
    )


def evaluation(arm=ExperimentArm.LLM_ONLY):
    return AgentEvaluationSummary(
        experiment_id=uuid4(),
        arm=arm,
        positive_label="review",
        evaluated_at=AT,
        as_of=AT,
        observations=10,
        classification=ClassificationMetrics(
            true_positive=4,
            false_positive=1,
            false_negative=1,
            true_negative=4,
            precision=0.8,
            recall=0.8,
            f1=0.8,
            accuracy=0.8,
        ),
        calibration=CalibrationSummary(
            arm=arm,
            observations=10,
            accuracy=0.8,
            mean_confidence=0.75,
            brier_score=0.12,
            calibration_gap=0.05,
            rmse=0.3464101615,
        ),
    )


def test_daily_scorecard_reports_flow_and_cumulative_performance():
    scorecard = build_forward_test_daily_scorecard(
        trading_day=DAY,
        run_evidence=(
            run(ForwardTestRunStatus.FILLED),
            run(ForwardTestRunStatus.BLOCKED, approval="REJECT", reason="PermissionError:rejected proposal cannot authorize execution"),
            run(ForwardTestRunStatus.BLOCKED, reason="PermissionError:fresh risk check denied the proposal"),
            run(ForwardTestRunStatus.BLOCKED, reason="ValueError:market data is stale"),
        ),
        journals=(
            journal("2.0", regime=1, followed=True, mae="0.25", mfe="3.0"),
            journal("-1.0", regime=1, followed=False, mae="1.0", mfe="0.5"),
        ),
        agent_evaluations=(evaluation(),),
    )

    assert scorecard.run_type is RunType.PAPER
    assert scorecard.proposals == 4
    assert scorecard.approvals == 3
    assert scorecard.rejections == 1
    assert scorecard.blocked_runs == 3
    assert scorecard.risk_denials == 1
    assert scorecard.guard_denials == 1
    assert scorecard.other_denials == 0
    assert scorecard.fills == 1
    assert scorecard.closed_trades == 2
    assert scorecard.cumulative_r == Decimal("1.0")
    assert scorecard.expectancy_r == Decimal("0.5")
    assert scorecard.max_drawdown_r == Decimal("1.0")
    assert scorecard.average_mae_r == Decimal("0.625")
    assert scorecard.average_mfe_r == Decimal("1.75")
    assert scorecard.adherence_observations == 2
    assert scorecard.adherence_rate == 0.5
    assert scorecard.by_setup_regime[0].closed_trades == 2
    assert scorecard.agent_calibration[0].brier_score == pytest.approx(0.12)


def test_scorecard_isolates_setup_and_regime_slices():
    scorecard = build_forward_test_daily_scorecard(
        trading_day=DAY,
        run_evidence=(),
        journals=(
            journal("1.0", regime=1, setup=SetupType.BREAKOUT),
            journal("2.0", regime=2, setup=SetupType.PULLBACK),
        ),
    )

    assert [(item.setup, item.regime) for item in scorecard.by_setup_regime] == [
        (SetupType.BREAKOUT.value, 1),
        (SetupType.PULLBACK.value, 2),
    ]


def test_scorecard_rejects_non_paper_run_evidence():
    bad = run(ForwardTestRunStatus.FILLED).model_copy(update={"run_type": RunType.LIVE})

    with pytest.raises(ValueError, match="PAPER run evidence"):
        build_forward_test_daily_scorecard(
            trading_day=DAY,
            run_evidence=(bad,),
            journals=(),
        )


def test_scorecard_rejects_non_paper_trade_outcomes():
    entry = journal("1.0")
    assert entry.result is not None
    entry = entry.model_copy(
        update={"result": entry.result.model_copy(update={"run_type": RunType.REPLAY})}
    )

    with pytest.raises(ValueError, match="PAPER journal outcomes"):
        build_forward_test_daily_scorecard(
            trading_day=DAY,
            run_evidence=(),
            journals=(entry,),
        )


def test_future_journal_and_agent_evidence_are_excluded():
    future_eval = evaluation().model_copy(
        update={
            "as_of": AT + timedelta(days=1),
            "evaluated_at": AT + timedelta(days=1),
        }
    )
    scorecard = build_forward_test_daily_scorecard(
        trading_day=DAY,
        run_evidence=(),
        journals=(journal("1.0", days=1),),
        agent_evaluations=(future_eval,),
    )

    assert scorecard.closed_trades == 0
    assert scorecard.cumulative_r == 0
    assert scorecard.agent_calibration == ()
