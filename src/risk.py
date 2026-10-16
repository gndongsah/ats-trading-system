from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from fractions import Fraction

from src.domain.models import (
    Regime,
    RiskIntent,
    TradeSide,
    resolve_regime_risk_multiplier,
)

EXPERIMENTAL_RISK_FRACTION_MIN = Decimal("0.0025")
EXPERIMENTAL_RISK_FRACTION_MAX = Decimal("0.005")
ONE = Decimal(1)
ZERO = Decimal(0)


class SizingDecision(StrEnum):
    ALLOW = "ALLOW"
    RESIZE = "RESIZE"
    DENY = "DENY"


class SizingReason(StrEnum):
    WITHIN_RISK_AND_BUYING_POWER = "within_risk_and_buying_power"
    BUYING_POWER_LIMITED = "buying_power_limited"
    UNSUPPORTED_SIDE = "unsupported_side"
    UNSUPPORTED_INSTRUMENT = "unsupported_instrument"
    LEVERAGED_INSTRUMENT = "leveraged_instrument"
    FRACTIONAL_SHARES_UNSUPPORTED = "fractional_shares_unsupported"
    REGIME_MULTIPLIER_REQUIRED = "regime_multiplier_required"
    REGIME_NOT_AUTHORIZED = "regime_not_authorized"
    REGIME_NO_TRADE = "regime_no_trade"
    ZERO_RISK_BUDGET = "zero_risk_budget"
    ZERO_RISK_QUANTITY = "zero_risk_quantity"
    INSUFFICIENT_BUYING_POWER = "insufficient_buying_power"


@dataclass(frozen=True)
class PositionSizingInputs:
    equity: Decimal
    risk_fraction: Decimal
    entry: Decimal
    stop: Decimal
    available_buying_power: Decimal
    regime: Regime
    side: TradeSide
    instrument_type: str
    leverage: Decimal
    fractional_shares: bool
    special_setup: bool
    explicit_regime_multiplier: Decimal | None


@dataclass(frozen=True)
class PositionSizingResult:
    decision: SizingDecision
    reason: SizingReason
    inputs: PositionSizingInputs
    multiplier: Decimal | None
    adjusted_risk_budget: Decimal | None
    effective_risk_fraction: Decimal | None
    risk_quantity: int | None
    buying_power_quantity: int | None
    quantity: int
    actual_stop_risk_exposure: Decimal | None
    entry_notional: Decimal | None

    def to_risk_intent(self) -> RiskIntent:
        if not self._is_safe_positive_result():
            raise ValueError(
                "only a safe, positive sizing result can create a RiskIntent"
            )
        assert self.adjusted_risk_budget is not None
        assert self.effective_risk_fraction is not None
        return RiskIntent(
            stop_risk_budget=self.adjusted_risk_budget,
            portfolio_risk_fraction=self.effective_risk_fraction,
        )

    def _is_safe_positive_result(self) -> bool:
        if (
            self.decision is SizingDecision.DENY
            or self.quantity <= 0
            or not isinstance(self.inputs.regime, Regime)
            or self.inputs.regime is Regime.CAPITULATION_REVERSAL
            or self.inputs.side is not TradeSide.LONG
            or not isinstance(self.inputs.instrument_type, str)
            or self.inputs.instrument_type.casefold() != "equity"
            or self.inputs.leverage != ONE
            or not self.inputs.leverage.is_finite()
            or self.inputs.fractional_shares
            or self.inputs.equity <= ZERO
            or not self.inputs.equity.is_finite()
            or not (
                EXPERIMENTAL_RISK_FRACTION_MIN
                <= self.inputs.risk_fraction
                <= EXPERIMENTAL_RISK_FRACTION_MAX
            )
            or not self.inputs.risk_fraction.is_finite()
            or self.inputs.entry <= self.inputs.stop
            or self.inputs.stop <= ZERO
            or not self.inputs.entry.is_finite()
            or not self.inputs.stop.is_finite()
            or self.inputs.available_buying_power < ZERO
            or not self.inputs.available_buying_power.is_finite()
            or self.inputs.special_setup
            or (
                self.inputs.regime
                not in (Regime.RISK_OFF, Regime.CAPITULATION_REVERSAL)
                and self.inputs.explicit_regime_multiplier is not None
            )
            or self.adjusted_risk_budget is None
            or self.effective_risk_fraction is None
            or self.actual_stop_risk_exposure is None
            or self.entry_notional is None
        ):
            return False
        try:
            multiplier = resolve_regime_risk_multiplier(
                self.inputs.regime,
                side=self.inputs.side,
                explicit_multiplier=self.inputs.explicit_regime_multiplier,
                special_setup=self.inputs.special_setup,
            )
        except ValueError:
            return False
        if multiplier is None or multiplier <= ZERO:
            return False

        equity = _fraction(self.inputs.equity)
        risk_fraction = _fraction(self.inputs.risk_fraction)
        multiplier_fraction = _fraction(multiplier)
        stop_distance = _fraction(self.inputs.entry) - _fraction(self.inputs.stop)
        budget = equity * risk_fraction * multiplier_fraction
        effective_fraction = risk_fraction * multiplier_fraction
        risk_quantity = _floor_fraction(budget / stop_distance)
        buying_power_quantity = _floor_fraction(
            _fraction(self.inputs.available_buying_power) / _fraction(self.inputs.entry)
        )
        quantity = min(risk_quantity, buying_power_quantity)
        if quantity <= 0 or quantity != self.quantity:
            return False

        expected_decision = (
            SizingDecision.RESIZE
            if buying_power_quantity < risk_quantity
            else SizingDecision.ALLOW
        )
        expected_reason = (
            SizingReason.BUYING_POWER_LIMITED
            if expected_decision is SizingDecision.RESIZE
            else SizingReason.WITHIN_RISK_AND_BUYING_POWER
        )
        exposure = stop_distance * quantity
        notional = _fraction(self.inputs.entry) * quantity
        return (
            self.decision is expected_decision
            and self.reason is expected_reason
            and self.multiplier == multiplier
            and self.risk_quantity == risk_quantity
            and self.buying_power_quantity == buying_power_quantity
            and self.adjusted_risk_budget == _decimal_from_fraction(budget)
            and self.effective_risk_fraction
            == _decimal_from_fraction(effective_fraction)
            and self.actual_stop_risk_exposure == _decimal_from_fraction(exposure)
            and self.entry_notional == _decimal_from_fraction(notional)
            and exposure <= budget
            and notional <= _fraction(self.inputs.available_buying_power)
        )


def _decimal(value: Decimal | float | str, name: str) -> Decimal:
    if isinstance(value, bool):
        raise TypeError(f"{name} must be a finite number, not a boolean")
    try:
        converted = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as error:
        raise ValueError(f"{name} must be a finite number") from error
    if not converted.is_finite():
        raise ValueError(f"{name} must be a finite number")
    return converted


def _fraction(value: Decimal) -> Fraction:
    return Fraction(value)


def _decimal_from_fraction(value: Fraction) -> Decimal:
    """Convert a terminating rational to Decimal without using Decimal context."""
    numerator = value.numerator
    denominator = value.denominator
    twos = fives = 0
    while denominator % 2 == 0:
        denominator //= 2
        twos += 1
    while denominator % 5 == 0:
        denominator //= 5
        fives += 1
    if denominator != 1:
        raise ValueError("exact sizing value is not representable as a Decimal")

    scale = max(twos, fives)
    scaled_numerator = numerator * (2 ** (scale - twos)) * (5 ** (scale - fives))
    digits = tuple(int(digit) for digit in str(abs(scaled_numerator)))
    return Decimal((int(scaled_numerator < 0), digits, -scale))


def _floor_fraction(value: Fraction) -> int:
    return value.numerator // value.denominator


def _deny(
    *,
    inputs: PositionSizingInputs,
    reason: SizingReason,
    multiplier: Decimal | None = None,
    adjusted_risk_budget: Decimal | None = None,
    effective_risk_fraction: Decimal | None = None,
    risk_quantity: int | None = None,
    buying_power_quantity: int | None = None,
    entry_notional: Decimal | None = None,
) -> PositionSizingResult:
    return PositionSizingResult(
        decision=SizingDecision.DENY,
        reason=reason,
        inputs=inputs,
        multiplier=multiplier,
        adjusted_risk_budget=adjusted_risk_budget,
        effective_risk_fraction=effective_risk_fraction,
        risk_quantity=risk_quantity,
        buying_power_quantity=buying_power_quantity,
        quantity=0,
        actual_stop_risk_exposure=ZERO if adjusted_risk_budget is not None else None,
        entry_notional=entry_notional,
    )


def size_long_equity_position(
    *,
    equity: Decimal | float | str,
    risk_fraction: Decimal | float | str,
    entry: Decimal | float | str,
    stop: Decimal | float | str,
    available_buying_power: Decimal | float | str,
    regime: Regime,
    side: TradeSide,
    instrument_type: str,
    leverage: Decimal | float | str,
    fractional_shares: bool,
    special_setup: bool,
    explicit_regime_multiplier: Decimal | float | str | None,
) -> PositionSizingResult:
    """Size a whole-share long position; stop execution is not guaranteed."""
    equity_value = _decimal(equity, "equity")
    fraction_value = _decimal(risk_fraction, "risk_fraction")
    entry_value = _decimal(entry, "entry")
    stop_value = _decimal(stop, "stop")
    buying_power_value = _decimal(available_buying_power, "available_buying_power")
    leverage_value = _decimal(leverage, "leverage")

    if equity_value <= ZERO:
        raise ValueError("equity must be greater than zero")
    if (
        not EXPERIMENTAL_RISK_FRACTION_MIN
        <= fraction_value
        <= (EXPERIMENTAL_RISK_FRACTION_MAX)
    ):
        raise ValueError("risk_fraction must be between 0.0025 and 0.005 inclusive")
    if entry_value <= stop_value or stop_value <= ZERO:
        raise ValueError("long position requires entry > stop > 0")
    if buying_power_value < ZERO:
        raise ValueError("available_buying_power cannot be negative")
    if leverage_value <= ZERO:
        raise ValueError("leverage must be greater than zero")
    if not isinstance(regime, Regime):
        raise TypeError("regime must be a Regime value")
    if not isinstance(side, TradeSide):
        raise TypeError("side must be a TradeSide value")
    if not isinstance(special_setup, bool):
        raise TypeError("special_setup must be a boolean authorization")
    if not isinstance(fractional_shares, bool):
        raise TypeError("fractional_shares must be a boolean")
    if not isinstance(instrument_type, str):
        raise TypeError("instrument_type must be a string")
    if (
        regime not in (Regime.RISK_OFF, Regime.CAPITULATION_REVERSAL)
        and explicit_regime_multiplier is not None
    ):
        raise ValueError("explicit_regime_multiplier is only valid for regimes 4 and 5")
    if special_setup and regime is not Regime.CAPITULATION_REVERSAL:
        raise ValueError("special_setup authorization is only valid for regime 5")
    inputs = PositionSizingInputs(
        equity=equity_value,
        risk_fraction=fraction_value,
        entry=entry_value,
        stop=stop_value,
        available_buying_power=buying_power_value,
        regime=regime,
        side=side,
        instrument_type=instrument_type,
        leverage=leverage_value,
        fractional_shares=fractional_shares,
        special_setup=special_setup,
        explicit_regime_multiplier=(
            _decimal(explicit_regime_multiplier, "explicit_regime_multiplier")
            if explicit_regime_multiplier is not None
            else None
        ),
    )

    if regime is Regime.CAPITULATION_REVERSAL:
        return _deny(inputs=inputs, reason=SizingReason.REGIME_NOT_AUTHORIZED)
    if side is not TradeSide.LONG:
        return _deny(
            inputs=inputs,
            reason=SizingReason.UNSUPPORTED_SIDE,
        )
    if instrument_type.casefold() != "equity":
        return _deny(
            inputs=inputs,
            reason=SizingReason.UNSUPPORTED_INSTRUMENT,
        )
    if leverage_value != ONE:
        return _deny(
            inputs=inputs,
            reason=SizingReason.LEVERAGED_INSTRUMENT,
        )
    if fractional_shares:
        return _deny(
            inputs=inputs,
            reason=SizingReason.FRACTIONAL_SHARES_UNSUPPORTED,
        )

    if regime is Regime.RISK_OFF and explicit_regime_multiplier is None:
        return _deny(
            inputs=inputs,
            reason=SizingReason.REGIME_MULTIPLIER_REQUIRED,
        )

    try:
        multiplier = resolve_regime_risk_multiplier(
            regime,
            side=side,
            explicit_multiplier=inputs.explicit_regime_multiplier,
            special_setup=special_setup,
        )
    except ValueError as error:
        raise ValueError(f"invalid regime authorization: {error}") from error

    if multiplier is None:
        return _deny(
            inputs=inputs,
            reason=SizingReason.REGIME_NO_TRADE,
        )
    adjusted_budget_fraction = (
        _fraction(equity_value) * _fraction(fraction_value) * _fraction(multiplier)
    )
    adjusted_budget = _decimal_from_fraction(adjusted_budget_fraction)
    effective_fraction = _decimal_from_fraction(
        _fraction(fraction_value) * _fraction(multiplier)
    )
    stop_distance_fraction = _fraction(entry_value) - _fraction(stop_value)
    buying_power_fraction = _fraction(buying_power_value)
    if adjusted_budget <= ZERO:
        buying_power_quantity = _floor_fraction(
            buying_power_fraction / _fraction(entry_value)
        )
        return _deny(
            inputs=inputs,
            reason=SizingReason.ZERO_RISK_BUDGET,
            multiplier=multiplier,
            adjusted_risk_budget=adjusted_budget,
            effective_risk_fraction=effective_fraction,
            risk_quantity=0,
            buying_power_quantity=buying_power_quantity,
            entry_notional=ZERO,
        )

    risk_quantity = _floor_fraction(adjusted_budget_fraction / stop_distance_fraction)
    buying_power_quantity = _floor_fraction(
        buying_power_fraction / _fraction(inputs.entry)
    )
    quantity = min(risk_quantity, buying_power_quantity)
    if quantity == 0:
        reason = (
            SizingReason.ZERO_RISK_QUANTITY
            if risk_quantity == 0
            else SizingReason.INSUFFICIENT_BUYING_POWER
        )
        return _deny(
            inputs=inputs,
            reason=reason,
            multiplier=multiplier,
            adjusted_risk_budget=adjusted_budget,
            effective_risk_fraction=effective_fraction,
            risk_quantity=risk_quantity,
            buying_power_quantity=buying_power_quantity,
            entry_notional=ZERO,
        )

    actual_exposure = _decimal_from_fraction(stop_distance_fraction * quantity)
    entry_notional = _decimal_from_fraction(_fraction(inputs.entry) * quantity)
    if (
        _fraction(actual_exposure) > adjusted_budget_fraction
        or _fraction(entry_notional) > buying_power_fraction
    ):
        return _deny(
            inputs=inputs,
            reason=SizingReason.ZERO_RISK_QUANTITY,
            multiplier=multiplier,
            adjusted_risk_budget=adjusted_budget,
            effective_risk_fraction=effective_fraction,
            risk_quantity=risk_quantity,
            buying_power_quantity=buying_power_quantity,
            entry_notional=ZERO,
        )
    decision = (
        SizingDecision.RESIZE
        if buying_power_quantity < risk_quantity
        else SizingDecision.ALLOW
    )
    reason = (
        SizingReason.BUYING_POWER_LIMITED
        if decision is SizingDecision.RESIZE
        else SizingReason.WITHIN_RISK_AND_BUYING_POWER
    )
    return PositionSizingResult(
        decision=decision,
        reason=reason,
        inputs=inputs,
        multiplier=multiplier,
        adjusted_risk_budget=adjusted_budget,
        effective_risk_fraction=effective_fraction,
        risk_quantity=risk_quantity,
        buying_power_quantity=buying_power_quantity,
        quantity=quantity,
        actual_stop_risk_exposure=actual_exposure,
        entry_notional=entry_notional,
    )
