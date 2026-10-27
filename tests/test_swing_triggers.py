from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from pydantic import ValidationError

from src.domain.models import Regime, TradeSide, TradingStyle
from src.regime import RegimeClassification
from src.setup_score import (
    SetupActionability,
    SetupScoreComponents,
    SwingLongSetupScreen,
    score_setup,
)
from src.swing_triggers import (
    BreakdownShortInput,
    BreakoutRetestInput,
    Ema21PullbackInput,
    SwingTriggerActionability,
    SwingTriggerFailure,
    SwingTriggerType,
    evaluate_breakdown_short,
    evaluate_breakout_retest,
    evaluate_ema21_pullback,
)

AS_OF = datetime(2026, 10, 27, 16, 0, tzinfo=UTC)


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


def components_for_total(total: int) -> SetupScoreComponents:
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


def long_setup(total: int = 70, **screen_overrides):
    screen_values = {
        "above_50dma": True,
        "dma50_above_200dma": True,
        "rs_3m_outperforming_spy": True,
        "rs_6m_outperforming_spy": True,
        "avg_daily_volume": 500001,
        "extension_pct_50dma": Decimal(10),
        "days_to_earnings": 11,
        "explicit_earnings_plan": False,
    }
    screen_values.update(screen_overrides)
    return score_setup(
        as_of=AS_OF,
        components=components_for_total(total),
        regime=regime(),
        trading_style=TradingStyle.SWING,
        side=TradeSide.LONG,
        swing_long_screen=SwingLongSetupScreen(**screen_values),
    )


def short_setup(total: int = 70):
    return score_setup(
        as_of=AS_OF,
        components=components_for_total(total),
        regime=regime(),
        trading_style=TradingStyle.SWING,
        side=TradeSide.SHORT,
    )


@pytest.mark.parametrize(
    ("stop_pct", "expected"),
    [
        (Decimal(3), Decimal("92.15")),
        (Decimal(5), Decimal("90.25")),
    ],
)
def test_breakout_retest_stop_boundaries_are_3_to_5_percent_below_base(
    stop_pct,
    expected,
):
    result = evaluate_breakout_retest(
        BreakoutRetestInput(
            as_of=AS_OF,
            cleared_flat_base=True,
            rising_volume=True,
            retest_holds=True,
            breakout_level=Decimal(100),
            base_low=Decimal(95),
            stop_pct_below_base=stop_pct,
        ),
        setup=long_setup(),
    )

    assert result.trigger_type is SwingTriggerType.BREAKOUT_RETEST
    assert result.side is TradeSide.LONG
    assert result.trigger_level == Decimal(100)
    assert result.invalidation_level == expected
    assert result.actionability is SwingTriggerActionability.ACTIONABLE
    assert result.failures == ()


@pytest.mark.parametrize("stop_pct", [Decimal("2.99"), Decimal("5.01")])
def test_breakout_retest_rejects_stop_outside_playbook_range(stop_pct):
    with pytest.raises(ValueError, match="3% to 5%"):
        evaluate_breakout_retest(
            BreakoutRetestInput(
                as_of=AS_OF,
                cleared_flat_base=True,
                rising_volume=True,
                retest_holds=True,
                breakout_level=Decimal(100),
                base_low=Decimal(95),
                stop_pct_below_base=stop_pct,
            ),
            setup=long_setup(),
        )


@pytest.mark.parametrize(
    ("field", "failure"),
    [
        ("cleared_flat_base", SwingTriggerFailure.FLAT_BASE_NOT_CLEARED),
        ("rising_volume", SwingTriggerFailure.VOLUME_NOT_RISING),
        ("retest_holds", SwingTriggerFailure.RETEST_NOT_HELD),
    ],
)
def test_breakout_missing_confirmation_returns_no_setup(field, failure):
    values = {
        "as_of": AS_OF,
        "cleared_flat_base": True,
        "rising_volume": True,
        "retest_holds": True,
        "breakout_level": Decimal(100),
        "base_low": Decimal(95),
        "stop_pct_below_base": Decimal(4),
    }
    values[field] = False

    result = evaluate_breakout_retest(
        BreakoutRetestInput(**values),
        setup=long_setup(),
    )

    assert result.actionability is SwingTriggerActionability.NO_SETUP
    assert failure in result.failures


@pytest.mark.parametrize("tap_number", [1, 2])
def test_ema21_pullback_accepts_only_first_or_second_tap(tap_number):
    result = evaluate_ema21_pullback(
        Ema21PullbackInput(
            as_of=AS_OF,
            steady_uptrend=True,
            ema21_rising=True,
            tap_number=tap_number,
            ema21_level=Decimal(100),
            swing_low=Decimal(96),
            invalidation_level=Decimal(95),
        ),
        setup=long_setup(),
    )

    assert result.trigger_type is SwingTriggerType.EMA21_PULLBACK
    assert result.side is TradeSide.LONG
    assert result.trigger_level == Decimal(100)
    assert result.invalidation_level == Decimal(95)
    assert result.actionability is SwingTriggerActionability.ACTIONABLE


def test_third_ema21_tap_is_not_actionable():
    result = evaluate_ema21_pullback(
        Ema21PullbackInput(
            as_of=AS_OF,
            steady_uptrend=True,
            ema21_rising=True,
            tap_number=3,
            ema21_level=Decimal(100),
            swing_low=Decimal(96),
            invalidation_level=Decimal(95),
        ),
        setup=long_setup(),
    )

    assert result.actionability is SwingTriggerActionability.NO_SETUP
    assert SwingTriggerFailure.TAP_OUTSIDE_FIRST_SECOND in result.failures


@pytest.mark.parametrize(
    ("field", "failure"),
    [
        ("steady_uptrend", SwingTriggerFailure.NOT_STEADY_UPTREND),
        ("ema21_rising", SwingTriggerFailure.EMA21_NOT_RISING),
    ],
)
def test_ema21_pullback_requires_written_structure(field, failure):
    values = {
        "as_of": AS_OF,
        "steady_uptrend": True,
        "ema21_rising": True,
        "tap_number": 1,
        "ema21_level": Decimal(100),
        "swing_low": Decimal(96),
        "invalidation_level": Decimal(95),
    }
    values[field] = False
    result = evaluate_ema21_pullback(
        Ema21PullbackInput(**values),
        setup=long_setup(),
    )
    assert result.actionability is SwingTriggerActionability.NO_SETUP
    assert failure in result.failures


def test_ema21_pullback_requires_explicit_invalidation_below_swing_low():
    with pytest.raises(ValidationError, match="below swing low"):
        Ema21PullbackInput(
            as_of=AS_OF,
            steady_uptrend=True,
            ema21_rising=True,
            tap_number=1,
            ema21_level=Decimal(100),
            swing_low=Decimal(96),
            invalidation_level=Decimal(96),
        )


def test_breakdown_short_returns_swing_low_trigger_and_stop_above_failed_bounce():
    result = evaluate_breakdown_short(
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

    assert result.trigger_type is SwingTriggerType.BREAKDOWN_SHORT
    assert result.side is TradeSide.SHORT
    assert result.trigger_level == Decimal(90)
    assert result.invalidation_level == Decimal(97)
    assert result.actionability is SwingTriggerActionability.ACTIONABLE
    assert result.failures == ()


@pytest.mark.parametrize(
    ("field", "failure"),
    [
        ("below_50dma", SwingTriggerFailure.NOT_BELOW_50DMA),
        ("dma50_falling", SwingTriggerFailure.DMA50_NOT_FALLING),
        (
            "bounce_failed_at_resistance",
            SwingTriggerFailure.BOUNCE_NOT_FAILED_AT_RESISTANCE,
        ),
        ("contracting_volume", SwingTriggerFailure.VOLUME_NOT_CONTRACTING),
        ("swing_low_broken", SwingTriggerFailure.SWING_LOW_NOT_BROKEN),
    ],
)
def test_breakdown_short_requires_all_written_confirmations(field, failure):
    values = {
        "as_of": AS_OF,
        "below_50dma": True,
        "dma50_falling": True,
        "bounce_failed_at_resistance": True,
        "contracting_volume": True,
        "swing_low_broken": True,
        "swing_low": Decimal(90),
        "failed_bounce_high": Decimal(96),
        "invalidation_level": Decimal(97),
    }
    values[field] = False
    result = evaluate_breakdown_short(
        BreakdownShortInput(**values),
        setup=short_setup(),
    )
    assert result.actionability is SwingTriggerActionability.NO_SETUP
    assert failure in result.failures


def test_breakdown_short_requires_invalidation_above_failed_bounce():
    with pytest.raises(ValidationError, match="above failed bounce"):
        BreakdownShortInput(
            as_of=AS_OF,
            below_50dma=True,
            dma50_falling=True,
            bounce_failed_at_resistance=True,
            contracting_volume=True,
            swing_low_broken=True,
            swing_low=Decimal(90),
            failed_bounce_high=Decimal(96),
            invalidation_level=Decimal(96),
        )


@pytest.mark.parametrize(
    ("score", "expected_failure"),
    [
        (69, SwingTriggerFailure.SETUP_SCORE_BELOW_SWING_MINIMUM),
        (70, None),
    ],
)
def test_swing_trigger_requires_setup_score_at_least_70(score, expected_failure):
    result = evaluate_breakout_retest(
        BreakoutRetestInput(
            as_of=AS_OF,
            cleared_flat_base=True,
            rising_volume=True,
            retest_holds=True,
            breakout_level=Decimal(100),
            base_low=Decimal(95),
            stop_pct_below_base=Decimal(4),
        ),
        setup=long_setup(score),
    )

    if expected_failure is None:
        assert result.actionability is SwingTriggerActionability.ACTIONABLE
    else:
        assert result.actionability is SwingTriggerActionability.NO_SETUP
        assert expected_failure in result.failures
        assert SwingTriggerFailure.SETUP_NOT_ACTIONABLE in result.failures


def test_setup_gate_failure_blocks_trigger_even_with_score_100():
    result = evaluate_breakout_retest(
        BreakoutRetestInput(
            as_of=AS_OF,
            cleared_flat_base=True,
            rising_volume=True,
            retest_holds=True,
            breakout_level=Decimal(100),
            base_low=Decimal(95),
            stop_pct_below_base=Decimal(4),
        ),
        setup=long_setup(100, above_50dma=False),
    )

    assert result.setup_score.score == Decimal(100)
    assert result.setup_score.actionability is SetupActionability.NO_TRADE
    assert result.actionability is SwingTriggerActionability.NO_SETUP
    assert SwingTriggerFailure.SETUP_NOT_ACTIONABLE in result.failures


def test_trigger_and_setup_timestamps_must_match():
    with pytest.raises(ValueError, match="timestamp must match"):
        evaluate_breakout_retest(
            BreakoutRetestInput(
                as_of=AS_OF + timedelta(seconds=1),
                cleared_flat_base=True,
                rising_volume=True,
                retest_holds=True,
                breakout_level=Decimal(100),
                base_low=Decimal(95),
                stop_pct_below_base=Decimal(4),
            ),
            setup=long_setup(),
        )


def test_trigger_models_are_immutable():
    candidate = BreakoutRetestInput(
        as_of=AS_OF,
        cleared_flat_base=True,
        rising_volume=True,
        retest_holds=True,
        breakout_level=Decimal(100),
        base_low=Decimal(95),
        stop_pct_below_base=Decimal(4),
    )
    result = evaluate_breakout_retest(candidate, setup=long_setup())

    with pytest.raises(ValidationError):
        candidate.retest_holds = False
    with pytest.raises(ValidationError):
        result.actionability = SwingTriggerActionability.NO_SETUP


def test_trigger_requires_swing_setup_context():
    day_setup = score_setup(
        as_of=AS_OF,
        components=components_for_total(70),
        regime=regime(),
        trading_style=TradingStyle.DAY,
        side=TradeSide.LONG,
    )
    result = evaluate_breakout_retest(
        BreakoutRetestInput(
            as_of=AS_OF,
            cleared_flat_base=True,
            rising_volume=True,
            retest_holds=True,
            breakout_level=Decimal(100),
            base_low=Decimal(95),
            stop_pct_below_base=Decimal(4),
        ),
        setup=day_setup,
    )

    assert result.actionability is SwingTriggerActionability.NO_SETUP
    assert SwingTriggerFailure.SETUP_NOT_SWING in result.failures


def test_trigger_requires_setup_side_to_match_trigger_side():
    result = evaluate_breakout_retest(
        BreakoutRetestInput(
            as_of=AS_OF,
            cleared_flat_base=True,
            rising_volume=True,
            retest_holds=True,
            breakout_level=Decimal(100),
            base_low=Decimal(95),
            stop_pct_below_base=Decimal(4),
        ),
        setup=short_setup(),
    )

    assert result.actionability is SwingTriggerActionability.NO_SETUP
    assert SwingTriggerFailure.SETUP_SIDE_MISMATCH in result.failures
