from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from pydantic import ValidationError

from src.backtest import (
    BacktestCostModel,
    BacktestSizingInputs,
    BacktestUnavailable,
    DeterministicBacktestHarness,
    HistoricalExitObservation,
)
from src.domain.evidence import SourceDocument
from src.domain.models import Regime, TradeSide, TradingStyle
from src.historical_replay import (
    HistoricalReplayContext,
    ResearchReplayRequirement,
    ResearchReplaySelection,
)
from src.market_data import (
    Bar,
    MarketEvent,
    Observation,
    ReplayRequirement,
    ReplaySnapshot,
)
from src.regime import RegimeClassification
from src.run_type import RunType
from src.setup_score import (
    SetupScoreComponents,
    SwingLongSetupScreen,
    score_setup,
)
from src.swing_triggers import (
    BreakdownShortInput,
    BreakoutRetestInput,
    SwingTriggerActionability,
    evaluate_breakdown_short,
    evaluate_breakout_retest,
)

AS_OF = datetime(2026, 10, 27, 14, 0, tzinfo=UTC)
EXIT_AT = AS_OF + timedelta(days=3)


def regime(as_of: datetime = AS_OF) -> RegimeClassification:
    return RegimeClassification(
        as_of=as_of,
        regime=Regime.RISK_ON,
        rule_name="risk_on",
        rule_version="1.0",
        playbook_version="3.0",
        matched_signals=(
            "spy_above_50dma",
            "dma50_above_200dma",
            "dma50_rising",
            "dma200_rising",
            "breadth_healthy",
            "advancers_leading",
            "new_highs_expanding",
        ),
        active_signals=(
            "advancers_leading",
            "breadth_healthy",
            "dma200_rising",
            "dma50_above_200dma",
            "dma50_rising",
            "new_highs_expanding",
            "spy_above_50dma",
        ),
        fixed_risk_multiplier=Decimal("1.00"),
    )


def components(total: int = 70) -> SetupScoreComponents:
    remaining = Decimal(total)
    maxima = (
        ("trend_alignment", Decimal(25)),
        ("relative_strength_vs_spy", Decimal(25)),
        ("structure_quality", Decimal(25)),
        ("liquidity_volume_confirmation", Decimal(15)),
        ("earnings_distance_safety", Decimal(10)),
    )
    values = {}
    for name, maximum in maxima:
        points = min(remaining, maximum)
        values[name] = points
        remaining -= points
    assert remaining == 0
    return SetupScoreComponents(**values)


def long_setup(total: int = 70):
    return score_setup(
        as_of=AS_OF,
        components=components(total),
        regime=regime(),
        trading_style=TradingStyle.SWING,
        side=TradeSide.LONG,
        swing_long_screen=SwingLongSetupScreen(
            above_50dma=True,
            dma50_above_200dma=True,
            rs_3m_outperforming_spy=True,
            rs_6m_outperforming_spy=True,
            avg_daily_volume=500001,
            extension_pct_50dma=Decimal(10),
            days_to_earnings=11,
        ),
    )


def short_setup(total: int = 70):
    return score_setup(
        as_of=AS_OF,
        components=components(total),
        regime=regime(),
        trading_style=TradingStyle.SWING,
        side=TradeSide.SHORT,
    )


def breakout_trigger(total: int = 70, *, retest_holds=True):
    return evaluate_breakout_retest(
        BreakoutRetestInput(
            as_of=AS_OF,
            cleared_flat_base=True,
            rising_volume=True,
            retest_holds=retest_holds,
            breakout_level=Decimal(100),
            base_low=Decimal(95),
            stop_pct_below_base=Decimal(4),
        ),
        setup=long_setup(total),
    )


def breakdown_trigger():
    return evaluate_breakdown_short(
        BreakdownShortInput(
            as_of=AS_OF,
            below_50dma=True,
            dma50_falling=True,
            bounce_failed_at_resistance=True,
            contracting_volume=True,
            swing_low_broken=True,
            swing_low=Decimal(90),
            failed_bounce_high=Decimal(96),
            invalidation_level=Decimal(97),
        ),
        setup=short_setup(),
    )


def context(*, symbol="ABC", as_of=AS_OF) -> HistoricalReplayContext:
    event = MarketEvent(
        source="ats016-fixture",
        symbol=symbol,
        event_time=as_of - timedelta(minutes=1),
        available_at=as_of - timedelta(seconds=50),
        revision=1,
        payload=Bar(
            interval="1m",
            open=Decimal(99),
            high=Decimal(101),
            low=Decimal(98),
            close=Decimal(100),
            volume=1000,
        ),
    )
    observation = Observation(
        event=event,
        ingested_at=as_of - timedelta(seconds=40),
    )
    source = SourceDocument(
        source_id=f"sec:{symbol}:10-q",
        version=1,
        source_type="regulatory_filing",
        publisher="SEC",
        subject=symbol,
        published_at=as_of - timedelta(hours=2),
        observed_at=as_of - timedelta(hours=1),
        available_at=as_of - timedelta(hours=1),
        ingested_at=as_of - timedelta(minutes=50),
        content_hash="a" * 64,
    )
    return HistoricalReplayContext(
        as_of=as_of,
        market=ReplaySnapshot(as_of=as_of, observations=(observation,)),
        research=(
            ResearchReplaySelection(
                requirement=ResearchReplayRequirement(
                    subject=symbol,
                    source_type="regulatory_filing",
                    max_publication_age=timedelta(hours=4),
                ),
                sources=(source,),
            ),
        ),
    )


def sizing(*, buying_power="100000") -> BacktestSizingInputs:
    return BacktestSizingInputs(
        equity=Decimal(100000),
        risk_fraction=Decimal("0.005"),
        available_buying_power=Decimal(buying_power),
    )


def zero_costs() -> BacktestCostModel:
    return BacktestCostModel(
        entry_slippage_bps=Decimal(0),
        exit_slippage_bps=Decimal(0),
        fee_per_share_per_side=Decimal(0),
        fixed_fee_per_side=Decimal(0),
    )


def exit_observation(
    price,
    *,
    low=None,
    high=None,
    source="synthetic-history-v1",
):
    return HistoricalExitObservation(
        source=source,
        exit_at=EXIT_AT,
        exit_price=Decimal(str(price)),
        period_low=Decimal(str(low)) if low is not None else None,
        period_high=Decimal(str(high)) if high is not None else None,
    )


def run(
    price,
    *,
    trigger=None,
    sizing_inputs=None,
    costs=None,
    observation=None,
):
    return DeterministicBacktestHarness().run(
        symbol="ABC",
        context=context(),
        trigger=trigger or breakout_trigger(),
        sizing=sizing_inputs or sizing(),
        costs=costs or zero_costs(),
        exit_observation=observation or exit_observation(price),
    )


@pytest.mark.parametrize(
    ("exit_price", "label", "sign"),
    [
        (110, "win", 1),
        (90, "loss", -1),
        (100, "flat", 0),
    ],
)
def test_win_loss_flat_are_deterministic_replay_results(exit_price, label, sign):
    outcome = run(exit_price)

    assert outcome.trade_result.run_type is RunType.REPLAY
    assert outcome.trade_result.outcome == label
    assert (outcome.net_pnl > 0) - (outcome.net_pnl < 0) == sign
    assert outcome.trade_result.realized_pnl == outcome.net_pnl
    assert outcome.trade_result.r_multiple == outcome.r_exact.decimal


def test_explicit_slippage_and_fees_are_applied_exactly_once():
    costs = BacktestCostModel(
        entry_slippage_bps=Decimal(10),
        exit_slippage_bps=Decimal(10),
        fee_per_share_per_side=Decimal("0.01"),
        fixed_fee_per_side=Decimal("0.50"),
    )
    outcome = run(110, costs=costs)

    assert outcome.sizing.quantity == 56
    assert outcome.entry_fill == Decimal("100.1")
    assert outcome.exit_fill == Decimal("109.89")
    assert outcome.gross_pnl == Decimal("548.24")
    assert outcome.total_fees == Decimal("2.12")
    assert outcome.net_pnl == Decimal("546.12")
    assert outcome.initial_risk == Decimal("492.8")
    assert outcome.r_exact.numerator == 13653
    assert outcome.r_exact.denominator == 12320
    assert outcome.r_exact.decimal == Decimal("1.108198051948")


def test_position_sizing_reuses_accepted_buying_power_resize():
    outcome = run(110, sizing_inputs=sizing(buying_power="1000"))

    assert outcome.sizing.decision == "RESIZE"
    assert outcome.sizing.reason == "buying_power_limited"
    assert outcome.sizing.quantity == 10
    assert outcome.sizing.entry_notional == Decimal(1000)
    assert outcome.initial_risk == Decimal(88)


def test_repeated_identical_inputs_produce_identical_identity_and_output():
    first = run(110)
    second = run(110)

    assert first == second
    assert first.request_digest == second.request_digest
    assert first.digest == second.digest
    assert first.trade_result.id == second.trade_result.id
    assert first.trade_result.plan_id == second.trade_result.plan_id


def test_mae_mfe_are_deterministic_price_excursions_in_r():
    outcome = run(
        110,
        observation=exit_observation(110, low=95, high=112, source="ohlc-window-v1"),
    )

    assert outcome.mae_exact is not None
    assert outcome.mfe_exact is not None
    assert outcome.mae_exact.numerator == 25
    assert outcome.mae_exact.denominator == 44
    assert outcome.mfe_exact.numerator == 15
    assert outcome.mfe_exact.denominator == 11
    assert outcome.trade_result.mae_r == outcome.mae_exact.decimal
    assert outcome.trade_result.mfe_r == outcome.mfe_exact.decimal
    assert outcome.trade_result.excursion_source == "ohlc-window-v1"


def test_non_actionable_trigger_fails_closed():
    trigger = breakout_trigger(retest_holds=False)
    assert trigger.actionability is SwingTriggerActionability.NO_SETUP

    with pytest.raises(BacktestUnavailable, match="TRIGGER_NOT_ACTIONABLE"):
        run(110, trigger=trigger)


def test_short_trigger_fails_closed_until_risk_layer_supports_short_sizing():
    with pytest.raises(BacktestUnavailable, match="UNSUPPORTED_SIDE:SHORT"):
        run(80, trigger=breakdown_trigger())


def test_context_trigger_timestamp_and_exit_order_are_enforced():
    mismatched = breakout_trigger().model_copy(
        update={"as_of": AS_OF + timedelta(seconds=1)}
    )
    with pytest.raises(BacktestUnavailable, match="TIMESTAMP_MISMATCH"):
        run(110, trigger=mismatched)

    early = HistoricalExitObservation(
        source="synthetic-history-v1",
        exit_at=AS_OF,
        exit_price=Decimal(110),
    )
    with pytest.raises(BacktestUnavailable, match="EXIT_MUST_FOLLOW"):
        run(110, observation=early)


def test_symbol_must_exist_in_point_in_time_market_context():
    with pytest.raises(BacktestUnavailable, match="SYMBOL_NOT_IN_REPLAY_CONTEXT"):
        DeterministicBacktestHarness().run(
            symbol="XYZ",
            context=context(symbol="ABC"),
            trigger=breakout_trigger(),
            sizing=sizing(),
            costs=zero_costs(),
            exit_observation=exit_observation(110),
        )


def test_historical_context_rejects_future_market_observation():
    as_of = AS_OF
    event = MarketEvent(
        source="future-fixture",
        symbol="ABC",
        event_time=as_of + timedelta(seconds=1),
        available_at=as_of + timedelta(seconds=1),
        revision=1,
        payload=Bar(
            interval="1m",
            open=Decimal(99),
            high=Decimal(101),
            low=Decimal(98),
            close=Decimal(100),
            volume=1000,
        ),
    )
    observation = Observation(
        event=event,
        ingested_at=as_of + timedelta(seconds=1),
    )

    with pytest.raises(ValidationError, match="future market data"):
        HistoricalReplayContext(
            as_of=as_of,
            market=ReplaySnapshot(as_of=as_of, observations=(observation,)),
            research=context().research,
        )


def test_exit_excursion_range_must_be_complete_and_coherent():
    with pytest.raises(ValidationError, match="supplied together"):
        HistoricalExitObservation(
            source="history",
            exit_at=EXIT_AT,
            exit_price=Decimal(100),
            period_low=Decimal(95),
        )

    with pytest.raises(ValidationError, match="within the excursion range"):
        exit_observation(110, low=95, high=105)


def test_cost_model_is_explicit_and_immutable():
    with pytest.raises(ValidationError):
        BacktestCostModel()

    costs = zero_costs()
    with pytest.raises(ValidationError):
        costs.entry_slippage_bps = Decimal(5)
