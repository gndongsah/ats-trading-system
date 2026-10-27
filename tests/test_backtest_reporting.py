from datetime import UTC, datetime, timedelta
from decimal import Decimal, localcontext
from uuid import NAMESPACE_URL, uuid5

import pytest
from pydantic import ValidationError

from src.backtest import (
    BacktestSizingSnapshot,
    BacktestTradeOutcome,
    ExactRatio,
)
from src.backtest_reporting import (
    BacktestReportingUnavailable,
    WalkForwardPlan,
    WalkForwardWindow,
    build_backtest_performance_report,
)
from src.domain.models import Regime, TradeResult
from src.run_type import RunType
from src.swing_triggers import SwingTriggerType

BASE = datetime(2026, 1, 1, 14, 0, tzinfo=UTC)


def ratio(value: str) -> ExactRatio:
    decimal = Decimal(value)
    exponent = -decimal.as_tuple().exponent
    denominator = 10**exponent
    numerator = int(decimal * denominator)
    return ExactRatio(
        numerator=numerator,
        denominator=denominator,
        decimal=decimal,
    )


def outcome(
    key: str,
    *,
    day: int,
    r: str,
    trigger_type: SwingTriggerType = SwingTriggerType.BREAKOUT_RETEST,
    regime: Regime = Regime.RISK_ON,
    mae: str | None = None,
    mfe: str | None = None,
    run_type: RunType = RunType.REPLAY,
) -> BacktestTradeOutcome:
    decision_at = BASE + timedelta(days=day)
    request_digest = key.encode().hex().ljust(64, "0")[:64]
    result_id = uuid5(NAMESPACE_URL, f"result:{key}")
    plan_id = uuid5(NAMESPACE_URL, f"plan:{key}")
    r_value = Decimal(r)
    mae_value = Decimal(mae) if mae is not None else None
    mfe_value = Decimal(mfe) if mfe is not None else None

    return BacktestTradeOutcome(
        symbol="ABC",
        decision_at=decision_at,
        trigger_type=trigger_type,
        regime=regime,
        setup_score=Decimal(75),
        context_digest="a" * 64,
        request_digest=request_digest,
        sizing=BacktestSizingSnapshot(
            decision="ALLOW",
            reason="ok",
            quantity=10,
            adjusted_risk_budget=Decimal(100),
            effective_risk_fraction=Decimal("0.001"),
            actual_stop_risk_exposure=Decimal(100),
            entry_notional=Decimal(1000),
        ),
        planned_entry=Decimal(100),
        planned_invalidation=Decimal(90),
        entry_fill=Decimal(100),
        exit_fill=Decimal(100) + r_value * Decimal(10),
        gross_pnl=r_value * Decimal(100),
        total_fees=Decimal(0),
        net_pnl=r_value * Decimal(100),
        initial_risk=Decimal(100),
        r_exact=ratio(r),
        mae_exact=ratio(mae) if mae is not None else None,
        mfe_exact=ratio(mfe) if mfe is not None else None,
        trade_result=TradeResult(
            id=result_id,
            plan_id=plan_id,
            run_type=run_type,
            closed_at=decision_at + timedelta(days=1),
            realized_pnl=r_value * Decimal(100),
            r_multiple=r_value,
            outcome="win" if r_value > 0 else "loss" if r_value < 0 else "flat",
            mae_r=mae_value,
            mfe_r=mfe_value,
            excursion_source="fixture" if mae is not None or mfe is not None else None,
        ),
    )


def sample_outcomes() -> tuple[BacktestTradeOutcome, ...]:
    return (
        outcome("t1", day=0, r="2", mae="0.5", mfe="3"),
        outcome("t2", day=1, r="-1", mae="0.8", mfe="1"),
        outcome(
            "t3",
            day=2,
            r="1",
            trigger_type=SwingTriggerType.EMA21_PULLBACK,
            regime=Regime.RISK_ON_NARROW,
        ),
        outcome("t4", day=3, r="-2", mae="1.2", mfe="0.4"),
    )


def test_overall_metrics_are_deterministic_and_cover_missing_excursions():
    report = build_backtest_performance_report(sample_outcomes())
    metrics = report.overall.overall

    assert metrics.trade_count == 4
    assert metrics.wins == 2
    assert metrics.losses == 2
    assert metrics.breakeven == 0
    assert metrics.win_rate == Decimal("0.500000000000")
    assert metrics.cumulative_r == Decimal(0)
    assert metrics.expectancy_r == Decimal("0E-12")
    assert metrics.max_drawdown_r == Decimal(2)
    assert metrics.profit_factor == Decimal("1.000000000000")

    assert metrics.mae_count == 3
    assert metrics.average_mae_r == Decimal("0.833333333333")
    assert metrics.median_mae_r == Decimal("0.800000000000")
    assert metrics.mfe_count == 3
    assert metrics.average_mfe_r == Decimal("1.466666666667")
    assert metrics.median_mfe_r == Decimal("1.000000000000")


def test_reports_are_available_by_setup_regime_and_setup_regime_pair():
    report = build_backtest_performance_report(sample_outcomes())

    assert [row.trigger_type for row in report.overall.by_setup] == [
        SwingTriggerType.BREAKOUT_RETEST,
        SwingTriggerType.EMA21_PULLBACK,
    ]
    breakout = report.overall.by_setup[0].metrics
    assert breakout.trade_count == 3
    assert breakout.cumulative_r == Decimal(-1)
    assert breakout.expectancy_r == Decimal("-0.333333333333")
    assert breakout.max_drawdown_r == Decimal(3)
    assert breakout.profit_factor == Decimal("0.666666666667")

    assert [row.regime for row in report.overall.by_regime] == [
        Regime.RISK_ON,
        Regime.RISK_ON_NARROW,
    ]
    risk_on = report.overall.by_regime[0].metrics
    assert risk_on.trade_count == 3
    assert risk_on.cumulative_r == Decimal(-1)

    pairs = {
        (row.trigger_type, row.regime): row.metrics.trade_count
        for row in report.overall.by_setup_regime
    }
    assert pairs == {
        (SwingTriggerType.BREAKOUT_RETEST, Regime.RISK_ON): 3,
        (SwingTriggerType.EMA21_PULLBACK, Regime.RISK_ON_NARROW): 1,
    }


def test_walk_forward_windows_use_decision_time_and_half_open_boundaries():
    plan = WalkForwardPlan(
        windows=(
            WalkForwardWindow(
                label="w1",
                start=BASE,
                end=BASE + timedelta(days=2),
            ),
            WalkForwardWindow(
                label="w2",
                start=BASE + timedelta(days=2),
                end=BASE + timedelta(days=4),
            ),
        )
    )
    report = build_backtest_performance_report(
        sample_outcomes(),
        walk_forward=plan,
    )

    assert [window.window.label for window in report.windows] == ["w1", "w2"]
    first = report.windows[0].breakdown.overall
    second = report.windows[1].breakdown.overall

    assert first.trade_count == 2
    assert first.cumulative_r == Decimal(1)
    assert first.expectancy_r == Decimal("0.500000000000")
    assert first.max_drawdown_r == Decimal(1)

    assert second.trade_count == 2
    assert second.cumulative_r == Decimal(-1)
    assert second.expectancy_r == Decimal("-0.500000000000")
    assert second.max_drawdown_r == Decimal(2)


def test_empty_walk_forward_window_is_reported_not_dropped():
    plan = WalkForwardPlan(
        windows=(
            WalkForwardWindow(
                label="trades",
                start=BASE,
                end=BASE + timedelta(days=4),
            ),
            WalkForwardWindow(
                label="empty",
                start=BASE + timedelta(days=4),
                end=BASE + timedelta(days=5),
            ),
        )
    )
    report = build_backtest_performance_report(
        sample_outcomes(),
        walk_forward=plan,
    )

    empty = report.windows[1].breakdown.overall
    assert empty.trade_count == 0
    assert empty.expectancy_r is None
    assert empty.win_rate is None
    assert empty.profit_factor is None
    assert empty.cumulative_r == Decimal(0)
    assert empty.max_drawdown_r == Decimal(0)


def test_input_order_does_not_change_report_or_digest():
    values = sample_outcomes()
    forward = build_backtest_performance_report(values)
    reversed_report = build_backtest_performance_report(tuple(reversed(values)))

    assert forward == reversed_report
    assert forward.digest == reversed_report.digest
    assert forward.outcome_digests == reversed_report.outcome_digests


def test_duplicate_deterministic_identity_is_rejected():
    first = sample_outcomes()[0]
    with pytest.raises(BacktestReportingUnavailable, match="DUPLICATE"):
        build_backtest_performance_report((first, first))


def test_non_replay_outcome_is_rejected():
    paper = outcome("paper", day=0, r="1", run_type=RunType.PAPER)
    with pytest.raises(BacktestReportingUnavailable, match="NON_REPLAY"):
        build_backtest_performance_report((paper,))


def test_backtest_result_integrity_mismatches_are_rejected():
    value = sample_outcomes()[0]
    bad_result = value.trade_result.model_copy(update={"r_multiple": Decimal(99)})
    corrupted = value.model_copy(update={"trade_result": bad_result})

    with pytest.raises(BacktestReportingUnavailable, match="R_MULTIPLE_MISMATCH"):
        build_backtest_performance_report((corrupted,))


def test_profit_factor_is_undefined_when_no_losses_exist():
    values = (
        outcome("p1", day=0, r="2"),
        outcome("p2", day=1, r="1"),
    )
    metrics = build_backtest_performance_report(values).overall.overall

    assert metrics.cumulative_r == Decimal(3)
    assert metrics.profit_factor is None


def test_walk_forward_plan_requires_ordered_unique_non_overlapping_windows():
    with pytest.raises(ValidationError, match="unique"):
        WalkForwardPlan(
            windows=(
                WalkForwardWindow(
                    label="same",
                    start=BASE,
                    end=BASE + timedelta(days=1),
                ),
                WalkForwardWindow(
                    label="same",
                    start=BASE + timedelta(days=1),
                    end=BASE + timedelta(days=2),
                ),
            )
        )

    with pytest.raises(ValidationError, match="overlap"):
        WalkForwardPlan(
            windows=(
                WalkForwardWindow(
                    label="a",
                    start=BASE,
                    end=BASE + timedelta(days=2),
                ),
                WalkForwardWindow(
                    label="b",
                    start=BASE + timedelta(days=1),
                    end=BASE + timedelta(days=3),
                ),
            )
        )


def test_walk_forward_plan_must_cover_every_reported_outcome_exactly_once():
    plan = WalkForwardPlan(
        windows=(
            WalkForwardWindow(
                label="partial",
                start=BASE,
                end=BASE + timedelta(days=2),
            ),
        )
    )

    with pytest.raises(BacktestReportingUnavailable, match="WINDOW_ASSIGNMENT"):
        build_backtest_performance_report(
            sample_outcomes(),
            walk_forward=plan,
        )


def test_report_models_are_immutable():
    report = build_backtest_performance_report(sample_outcomes())

    with pytest.raises(ValidationError):
        report.outcome_count = 0
    with pytest.raises(ValidationError):
        report.overall.overall.trade_count = 0


def test_report_is_independent_of_ambient_decimal_precision():
    baseline = build_backtest_performance_report(sample_outcomes())

    with localcontext() as decimal_context:
        decimal_context.prec = 6
        repeated = build_backtest_performance_report(sample_outcomes())

    assert repeated == baseline
    assert repeated.digest == baseline.digest
