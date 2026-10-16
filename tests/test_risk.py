from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import (
    ROUND_CEILING,
    ROUND_DOWN,
    ROUND_FLOOR,
    ROUND_HALF_UP,
    Decimal,
    localcontext,
)
from fractions import Fraction

import pytest
from pydantic import ValidationError

from src.domain.models import (
    Regime,
    RiskIntent,
    ScoreSnapshot,
    SecurityRef,
    SetupType,
    TradePlan,
    TradeSide,
    TradingStyle,
)
from src.risk import (
    PositionSizingResult,
    SizingDecision,
    SizingReason,
    size_long_equity_position,
)

DECISION_TIME = datetime(2026, 1, 2, 15, 0, tzinfo=UTC)


def size_position(**overrides) -> PositionSizingResult:
    values = {
        "equity": Decimal(50000),
        "risk_fraction": Decimal("0.005"),
        "entry": Decimal(100),
        "stop": Decimal(98),
        "available_buying_power": Decimal(50000),
        "regime": Regime.RISK_ON,
        "side": TradeSide.LONG,
        "instrument_type": "equity",
        "leverage": Decimal(1),
        "fractional_shares": False,
        "special_setup": False,
        "explicit_regime_multiplier": None,
    }
    values.update(overrides)
    return size_long_equity_position(**values)


def test_position_sizing_playbook_example_and_regime_scaling():
    assert size_position().quantity == 125
    assert size_position(regime=Regime.RISK_ON_NARROW).quantity == 93
    assert size_position(regime=Regime.NEUTRAL_CHOP).quantity == 62


@pytest.mark.parametrize(
    ("multiplier", "expected_quantity"),
    [(Decimal(0), 0), (Decimal("0.25"), 31)],
)
def test_regime_four_explicit_multiplier(multiplier, expected_quantity):
    result = size_position(
        regime=Regime.RISK_OFF,
        explicit_regime_multiplier=multiplier,
    )
    if multiplier == 0:
        assert result.decision is SizingDecision.DENY
        assert result.reason is SizingReason.ZERO_RISK_BUDGET
    else:
        assert result.decision is SizingDecision.ALLOW
        assert result.quantity == expected_quantity


def test_regime_four_requires_multiplier_and_rejects_invalid_multiplier():
    missing = size_position(regime=Regime.RISK_OFF)
    assert missing.decision is SizingDecision.DENY
    assert missing.reason is SizingReason.REGIME_MULTIPLIER_REQUIRED

    for invalid in (Decimal("-0.01"), Decimal("0.2501"), Decimal("NaN"), True):
        with pytest.raises((ValueError, TypeError)):
            size_position(
                regime=Regime.RISK_OFF,
                explicit_regime_multiplier=invalid,
            )
    with pytest.raises(ValueError, match="finite number"):
        size_position(
            regime=Regime.RISK_OFF,
            explicit_regime_multiplier=Decimal("Infinity"),
        )


@pytest.mark.parametrize("special_setup", [False, True])
@pytest.mark.parametrize(
    "explicit_multiplier",
    [None, Decimal(0), Decimal("0.1"), Decimal(1), Decimal(2), Decimal(10)],
)
def test_regime_five_always_denies_without_executable_quantity(
    special_setup, explicit_multiplier
):
    result = size_position(
        regime=Regime.CAPITULATION_REVERSAL,
        special_setup=special_setup,
        explicit_regime_multiplier=explicit_multiplier,
    )
    assert result.decision is SizingDecision.DENY
    assert result.reason is SizingReason.REGIME_NOT_AUTHORIZED
    assert result.quantity == 0
    with pytest.raises(ValueError, match="safe, positive"):
        result.to_risk_intent()


def test_buying_power_resizes_risk_quantity_or_denies_zero_shares():
    resized = size_position(available_buying_power=Decimal(8000))
    assert resized.decision is SizingDecision.RESIZE
    assert resized.reason is SizingReason.BUYING_POWER_LIMITED
    assert resized.risk_quantity == 125
    assert resized.buying_power_quantity == 80
    assert resized.quantity == 80

    no_buying_power = size_position(available_buying_power=Decimal("99.99"))
    assert no_buying_power.decision is SizingDecision.DENY
    assert no_buying_power.reason is SizingReason.INSUFFICIENT_BUYING_POWER
    assert no_buying_power.quantity == 0


def test_whole_share_rounding_boundaries_and_small_risk_budget():
    assert size_position(equity=Decimal("49999.8")).quantity == 124
    assert size_position(equity=Decimal(50000)).quantity == 125
    assert size_position(equity=Decimal("50000.2")).quantity == 125
    assert (
        size_position(available_buying_power=Decimal("9999.99")).buying_power_quantity
        == 99
    )
    assert (
        size_position(available_buying_power=Decimal(10000)).buying_power_quantity
        == 100
    )

    tiny_budget = size_position(
        equity=Decimal(10),
        risk_fraction=Decimal("0.0025"),
    )
    assert tiny_budget.decision is SizingDecision.DENY
    assert tiny_budget.reason is SizingReason.ZERO_RISK_QUANTITY
    assert tiny_budget.adjusted_risk_budget == Decimal("0.0250")


@pytest.mark.parametrize("precision", [2, 6, 28, 50])
@pytest.mark.parametrize(
    "rounding",
    [ROUND_DOWN, ROUND_FLOOR, ROUND_CEILING, ROUND_HALF_UP],
)
def test_sizing_is_independent_of_decimal_context(precision, rounding):
    with localcontext() as context:
        context.prec = precision
        context.rounding = rounding
        result = size_position()
    assert result.decision is SizingDecision.ALLOW
    assert result.quantity == 125
    assert result.risk_quantity == 125
    assert result.buying_power_quantity == 500
    assert result.adjusted_risk_budget == Decimal(250)
    assert result.actual_stop_risk_exposure == Decimal(250)
    assert result.entry_notional == Decimal(12500)
    intent = result.to_risk_intent()
    assert intent.stop_risk_budget == Decimal(250)
    assert intent.portfolio_risk_fraction == Decimal("0.005")


def test_context_variations_in_one_process_return_identical_decisions():
    results = []
    for precision in (2, 6, 28, 50):
        for rounding in (ROUND_DOWN, ROUND_FLOOR, ROUND_CEILING, ROUND_HALF_UP):
            with localcontext() as context:
                context.prec = precision
                context.rounding = rounding
                results.append(size_position())
    assert all(result == results[0] for result in results[1:])


def test_high_precision_values_floor_exactly_at_and_around_share_boundary():
    below = size_position(
        equity=Decimal(1000),
        risk_fraction=Decimal("0.005"),
        entry=Decimal("1.10000000000000000000000000001"),
        stop=Decimal(1),
        available_buying_power=Decimal(1000),
    )
    exact = size_position(
        equity=Decimal(1000),
        risk_fraction=Decimal("0.005"),
        entry=Decimal("1.1"),
        stop=Decimal(1),
        available_buying_power=Decimal(1000),
    )
    assert below.risk_quantity == 49
    assert below.quantity == 49
    assert exact.risk_quantity == 50
    assert exact.quantity == 50
    assert Fraction(below.actual_stop_risk_exposure) <= Fraction(
        below.adjusted_risk_budget
    )
    assert Fraction(below.entry_notional) <= Fraction(
        below.inputs.available_buying_power
    )
    with localcontext() as context:
        context.prec = 2
        context.rounding = ROUND_CEILING
        constrained_context = size_position(
            equity=Decimal(1000),
            risk_fraction=Decimal("0.005"),
            entry=Decimal("1.10000000000000000000000000001"),
            stop=Decimal(1),
            available_buying_power=Decimal(1000),
        )
    assert constrained_context == below


def test_exact_financial_limits_for_sub_cent_boundary_cases():
    risk_limited = size_position(
        equity=Decimal("49999.998"),
        risk_fraction=Decimal("0.005"),
        entry=Decimal(100),
        stop=Decimal(98),
        available_buying_power=Decimal(50000),
    )
    assert risk_limited.quantity == 124
    assert risk_limited.actual_stop_risk_exposure == Decimal(248)
    assert Fraction(250) - Fraction(risk_limited.adjusted_risk_budget) == Fraction(
        1, 100000
    )
    assert Fraction(risk_limited.actual_stop_risk_exposure) <= Fraction(
        risk_limited.adjusted_risk_budget
    )
    assert Fraction(risk_limited.entry_notional) <= Fraction(
        risk_limited.inputs.available_buying_power
    )

    buying_power_limited = size_position(
        equity=Decimal(50000),
        risk_fraction=Decimal("0.005"),
        entry=Decimal("100.00000000000000000000000000001"),
        stop=Decimal(98),
        available_buying_power=Decimal("7999.999999999999999999999999999"),
    )
    assert buying_power_limited.decision is SizingDecision.RESIZE
    assert buying_power_limited.risk_quantity == 124
    assert buying_power_limited.buying_power_quantity == 79
    assert buying_power_limited.quantity == 79
    next_share_notional = Fraction(buying_power_limited.inputs.entry) * 80
    excess_for_next_share = next_share_notional - Fraction(
        buying_power_limited.inputs.available_buying_power
    )
    assert Fraction(0) < excess_for_next_share < Fraction(1, 100)
    assert Fraction(buying_power_limited.actual_stop_risk_exposure) <= Fraction(
        buying_power_limited.adjusted_risk_budget
    )
    assert Fraction(buying_power_limited.entry_notional) <= Fraction(
        buying_power_limited.inputs.available_buying_power
    )


@pytest.mark.parametrize(
    ("entry", "stop"),
    [
        (Decimal(98), Decimal(98)),
        (Decimal(98), Decimal(100)),
        (Decimal(100), Decimal(0)),
        (Decimal(100), Decimal(-1)),
    ],
)
def test_invalid_long_stop_direction_is_rejected(entry, stop):
    with pytest.raises(ValueError, match="entry > stop > 0"):
        size_position(entry=entry, stop=stop)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("equity", Decimal(0), "equity must be greater than zero"),
        ("equity", Decimal(-1), "equity must be greater than zero"),
        ("equity", Decimal("NaN"), "equity must be a finite number"),
        ("equity", Decimal("Infinity"), "equity must be a finite number"),
        ("risk_fraction", Decimal("0.0024"), "risk_fraction"),
        ("risk_fraction", Decimal(0), "risk_fraction"),
        ("risk_fraction", Decimal("0.0051"), "risk_fraction"),
        ("risk_fraction", Decimal("Infinity"), "finite number"),
        ("risk_fraction", Decimal("NaN"), "risk_fraction must be a finite number"),
        ("risk_fraction", True, "risk_fraction must be a finite number"),
        ("entry", Decimal(0), "entry > stop > 0"),
        ("entry", Decimal(-1), "entry > stop > 0"),
        ("entry", Decimal("Infinity"), "entry must be a finite number"),
        ("stop", Decimal("NaN"), "stop must be a finite number"),
        ("stop", Decimal("Infinity"), "stop must be a finite number"),
        ("available_buying_power", Decimal(-1), "cannot be negative"),
        ("available_buying_power", Decimal("NaN"), "finite number"),
        ("available_buying_power", Decimal("Infinity"), "finite number"),
        ("leverage", Decimal(0), "leverage must be greater than zero"),
        ("leverage", Decimal("Infinity"), "finite number"),
    ],
)
def test_invalid_numeric_inputs_are_rejected(field, value, message):
    with pytest.raises((ValueError, TypeError), match=message):
        size_position(**{field: value})


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({"side": TradeSide.SHORT}, SizingReason.UNSUPPORTED_SIDE),
        ({"instrument_type": "option"}, SizingReason.UNSUPPORTED_INSTRUMENT),
        ({"leverage": Decimal(2)}, SizingReason.LEVERAGED_INSTRUMENT),
        (
            {"fractional_shares": True},
            SizingReason.FRACTIONAL_SHARES_UNSUPPORTED,
        ),
    ],
)
def test_unsupported_position_types_are_explicitly_denied(overrides, reason):
    result = size_position(**overrides)
    assert result.decision is SizingDecision.DENY
    assert result.reason is reason


def test_risk_intent_and_trade_plan_use_adjusted_budget_without_exceeding_it():
    result = size_position(regime=Regime.RISK_ON_NARROW)
    assert result.decision is SizingDecision.ALLOW
    assert result.actual_stop_risk_exposure == Decimal(186)
    assert result.actual_stop_risk_exposure <= result.adjusted_risk_budget

    intent = result.to_risk_intent()
    assert isinstance(intent, RiskIntent)
    assert intent.stop_risk_budget == result.adjusted_risk_budget
    assert intent.portfolio_risk_fraction == result.effective_risk_fraction
    assert result.inputs.equity * intent.portfolio_risk_fraction == (
        intent.stop_risk_budget
    )

    snapshot = ScoreSnapshot(
        decided_at=DECISION_TIME,
        market_opportunity=80,
        discovery=75,
        setup=90,
        regime_score=2,
        regime=Regime.RISK_ON_NARROW,
        inflection_stage=5,
    )
    plan = TradePlan(
        security=SecurityRef(ticker="abc"),
        side=TradeSide.LONG,
        trading_style=TradingStyle.SWING,
        setup_type=SetupType.BREAKOUT,
        entry=result.inputs.entry,
        stop=result.inputs.stop,
        target=result.inputs.entry + Decimal(2),
        quantity=Decimal(result.quantity),
        planned_at=DECISION_TIME + timedelta(minutes=1),
        score_snapshot=snapshot,
        risk_intent=intent,
    )
    assert (plan.entry - plan.stop) * plan.quantity == Decimal(186)

    denied = size_position(regime=Regime.RISK_OFF, explicit_regime_multiplier=0)
    with pytest.raises(ValueError, match="safe, positive"):
        denied.to_risk_intent()


def test_precision_boundary_sizing_is_safe_to_use_in_trade_plan():
    result = size_position(
        equity=Decimal(1000),
        risk_fraction=Decimal("0.005"),
        entry=Decimal("1.10000000000000000000000000001"),
        stop=Decimal(1),
        available_buying_power=Decimal(1000),
    )
    assert result.quantity == 49
    assert Fraction(result.actual_stop_risk_exposure) <= Fraction(
        result.adjusted_risk_budget
    )
    assert Fraction(result.entry_notional) <= Fraction(
        result.inputs.available_buying_power
    )
    snapshot = ScoreSnapshot(
        decided_at=DECISION_TIME,
        market_opportunity=80,
        discovery=75,
        setup=90,
        regime_score=1,
        inflection_stage=5,
    )
    plan = TradePlan(
        security=SecurityRef(ticker="abc"),
        side=TradeSide.LONG,
        trading_style=TradingStyle.SWING,
        setup_type=SetupType.BREAKOUT,
        entry=result.inputs.entry,
        stop=result.inputs.stop,
        target=Decimal("1.2"),
        quantity=Decimal(result.quantity),
        planned_at=DECISION_TIME + timedelta(minutes=1),
        score_snapshot=snapshot,
        risk_intent=result.to_risk_intent(),
    )
    assert plan.quantity == 49
    exact_plan_risk = (Fraction(plan.entry) - Fraction(plan.stop)) * int(plan.quantity)
    assert exact_plan_risk <= Fraction(plan.risk_intent.stop_risk_budget)


def test_planned_stop_risk_never_exceeds_budget():
    for equity in (Decimal(50000), Decimal("49999.8"), Decimal("12345.67")):
        for regime in (Regime.RISK_ON, Regime.RISK_ON_NARROW, Regime.NEUTRAL_CHOP):
            result = size_position(equity=equity, regime=regime)
            assert result.actual_stop_risk_exposure <= result.adjusted_risk_budget


def test_identical_sizing_inputs_produce_identical_results():
    assert size_position() == size_position()


def test_forged_regime_five_result_cannot_create_risk_intent():
    denied = size_position(
        regime=Regime.CAPITULATION_REVERSAL,
        special_setup=True,
        explicit_regime_multiplier=Decimal(10),
    )
    forged = replace(
        denied,
        decision=SizingDecision.ALLOW,
        quantity=1,
        multiplier=Decimal(1),
        adjusted_risk_budget=Decimal(250),
        effective_risk_fraction=Decimal("0.005"),
        risk_quantity=1,
        buying_power_quantity=1,
        actual_stop_risk_exposure=Decimal(2),
        entry_notional=Decimal(100),
    )
    with pytest.raises(ValueError, match="safe, positive"):
        forged.to_risk_intent()


def test_trade_plan_model_rejects_quantity_above_sizing_budget():
    result = size_position()
    snapshot = ScoreSnapshot(
        decided_at=DECISION_TIME,
        market_opportunity=80,
        discovery=75,
        setup=90,
        regime_score=1,
        inflection_stage=5,
    )
    with pytest.raises(ValidationError, match="stop-risk budget"):
        TradePlan(
            security=SecurityRef(ticker="abc"),
            side=TradeSide.LONG,
            trading_style=TradingStyle.SWING,
            setup_type=SetupType.BREAKOUT,
            entry=result.inputs.entry,
            stop=result.inputs.stop,
            target=result.inputs.entry + Decimal(2),
            quantity=Decimal(result.quantity + 1),
            planned_at=DECISION_TIME + timedelta(minutes=1),
            score_snapshot=snapshot,
            risk_intent=result.to_risk_intent(),
        )
