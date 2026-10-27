from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from pydantic import ValidationError

from src.domain.models import Regime, TradeSide, TradingStyle
from src.regime import RegimeClassification
from src.setup_score import (
    SetupActionability,
    SetupGateFailure,
    SetupScoreComponents,
    SwingLongSetupScreen,
    score_setup,
)

AS_OF = datetime(2026, 10, 27, 14, 0, tzinfo=UTC)


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


def passing_screen(**overrides) -> SwingLongSetupScreen:
    values = {
        "above_50dma": True,
        "dma50_above_200dma": True,
        "rs_3m_outperforming_spy": True,
        "rs_6m_outperforming_spy": True,
        "avg_daily_volume": 500001,
        "extension_pct_50dma": Decimal(10),
        "days_to_earnings": 11,
        "explicit_earnings_plan": False,
    }
    values.update(overrides)
    return SwingLongSetupScreen(**values)


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


@pytest.mark.parametrize(
    ("total", "expected_band"),
    [
        (0, SetupActionability.NO_TRADE),
        (49, SetupActionability.NO_TRADE),
        (50, SetupActionability.UNPROVEN_SIZE),
        (69, SetupActionability.UNPROVEN_SIZE),
        (70, SetupActionability.PLANNED_SIZE),
        (100, SetupActionability.PLANNED_SIZE),
    ],
)
def test_setup_score_actionability_boundaries(total, expected_band):
    result = score_setup(
        as_of=AS_OF,
        components=components_for_total(total),
        regime=regime(),
        trading_style=TradingStyle.SWING,
        side=TradeSide.LONG,
        swing_long_screen=passing_screen(),
    )

    assert result.score == Decimal(total)
    assert result.score_band is expected_band
    assert result.actionability is expected_band
    assert result.gate_failures == ()


def test_setup_score_sums_five_components_exactly():
    components = SetupScoreComponents(
        trend_alignment=Decimal("24.5"),
        relative_strength_vs_spy=Decimal("23.25"),
        structure_quality=Decimal("20.75"),
        liquidity_volume_confirmation=Decimal("14.5"),
        earnings_distance_safety=Decimal("9.5"),
    )
    result = score_setup(
        as_of=AS_OF,
        components=components,
        regime=regime(),
        trading_style=TradingStyle.SWING,
        side=TradeSide.LONG,
        swing_long_screen=passing_screen(),
    )

    assert result.score == Decimal("92.50")
    assert result.score == components.total


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("trend_alignment", Decimal("25.01")),
        ("relative_strength_vs_spy", Decimal("25.01")),
        ("structure_quality", Decimal("25.01")),
        ("liquidity_volume_confirmation", Decimal("15.01")),
        ("earnings_distance_safety", Decimal("10.01")),
    ],
)
def test_component_points_cannot_exceed_configured_weight(field, value):
    values = components_for_total(0).model_dump(mode="python")
    values[field] = value

    with pytest.raises(ValueError, match="exceeds configured maximum"):
        score_setup(
            as_of=AS_OF,
            components=SetupScoreComponents(**values),
            regime=regime(),
            trading_style=TradingStyle.SWING,
            side=TradeSide.LONG,
            swing_long_screen=passing_screen(),
        )


def test_negative_or_non_finite_component_points_are_rejected():
    values = components_for_total(0).model_dump(mode="python")
    values["trend_alignment"] = Decimal("-0.01")
    with pytest.raises(ValidationError):
        SetupScoreComponents(**values)

    values["trend_alignment"] = Decimal("NaN")
    with pytest.raises(ValidationError):
        SetupScoreComponents(**values)


@pytest.mark.parametrize(
    ("screen", "failure"),
    [
        (
            passing_screen(above_50dma=False),
            SetupGateFailure.NOT_ABOVE_50DMA,
        ),
        (
            passing_screen(dma50_above_200dma=False),
            SetupGateFailure.MA50_NOT_ABOVE_200DMA,
        ),
        (
            passing_screen(rs_3m_outperforming_spy=False),
            SetupGateFailure.RS_3M_NOT_OUTPERFORMING,
        ),
        (
            passing_screen(rs_6m_outperforming_spy=False),
            SetupGateFailure.RS_6M_NOT_OUTPERFORMING,
        ),
        (
            passing_screen(avg_daily_volume=500000),
            SetupGateFailure.AVG_DAILY_VOLUME_NOT_ABOVE_MINIMUM,
        ),
        (
            passing_screen(extension_pct_50dma=Decimal("10.01")),
            SetupGateFailure.EXTENDED_MORE_THAN_MAX_50DMA,
        ),
        (
            passing_screen(days_to_earnings=10),
            SetupGateFailure.EARNINGS_WITHIN_MIN_DAYS,
        ),
        (
            passing_screen(days_to_earnings=None),
            SetupGateFailure.EARNINGS_DATE_UNKNOWN,
        ),
    ],
)
def test_failed_swing_long_screen_forces_no_trade_even_with_100_score(
    screen,
    failure,
):
    result = score_setup(
        as_of=AS_OF,
        components=components_for_total(100),
        regime=regime(),
        trading_style=TradingStyle.SWING,
        side=TradeSide.LONG,
        swing_long_screen=screen,
    )

    assert result.score_band is SetupActionability.PLANNED_SIZE
    assert result.actionability is SetupActionability.NO_TRADE
    assert failure in result.gate_failures


def test_swing_long_screen_boundaries_are_literal_playbook_boundaries():
    result = score_setup(
        as_of=AS_OF,
        components=components_for_total(70),
        regime=regime(),
        trading_style=TradingStyle.SWING,
        side=TradeSide.LONG,
        swing_long_screen=passing_screen(
            avg_daily_volume=500001,
            extension_pct_50dma=Decimal(10),
            days_to_earnings=11,
        ),
    )
    assert result.actionability is SetupActionability.PLANNED_SIZE


def test_explicit_earnings_plan_waives_ordinary_10_day_screen():
    result = score_setup(
        as_of=AS_OF,
        components=components_for_total(70),
        regime=regime(),
        trading_style=TradingStyle.SWING,
        side=TradeSide.LONG,
        swing_long_screen=passing_screen(
            days_to_earnings=0,
            explicit_earnings_plan=True,
        ),
    )

    assert result.actionability is SetupActionability.PLANNED_SIZE
    assert SetupGateFailure.EARNINGS_WITHIN_MIN_DAYS not in result.gate_failures


def test_swing_long_requires_screen_and_other_contexts_cannot_supply_it():
    with pytest.raises(ValueError, match="requires the Playbook execution screen"):
        score_setup(
            as_of=AS_OF,
            components=components_for_total(70),
            regime=regime(),
            trading_style=TradingStyle.SWING,
            side=TradeSide.LONG,
        )

    with pytest.raises(ValueError, match="only valid for swing longs"):
        score_setup(
            as_of=AS_OF,
            components=components_for_total(70),
            regime=regime(),
            trading_style=TradingStyle.DAY,
            side=TradeSide.LONG,
            swing_long_screen=passing_screen(),
        )


def test_regime_and_setup_timestamps_must_match():
    with pytest.raises(ValueError, match="timestamp must match"):
        score_setup(
            as_of=AS_OF,
            components=components_for_total(70),
            regime=regime(AS_OF - timedelta(seconds=1)),
            trading_style=TradingStyle.SWING,
            side=TradeSide.LONG,
            swing_long_screen=passing_screen(),
        )


def test_setup_models_are_immutable():
    components = components_for_total(70)
    result = score_setup(
        as_of=AS_OF,
        components=components,
        regime=regime(),
        trading_style=TradingStyle.SWING,
        side=TradeSide.LONG,
        swing_long_screen=passing_screen(),
    )

    with pytest.raises(ValidationError):
        components.trend_alignment = Decimal(0)
    with pytest.raises(ValidationError):
        result.score = Decimal(0)
