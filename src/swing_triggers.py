"""Deterministic Playbook v3 swing trigger and invalidation contracts."""

from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    field_validator,
    model_validator,
)

from src.domain.models import TradeSide, TradingStyle, _require_utc
from src.playbook import PLAYBOOK, SwingTriggerConfig
from src.setup_score import SetupActionability, SetupScoreResult

ZERO = Decimal(0)
ONE_HUNDRED = Decimal(100)


class TriggerModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class SwingTriggerType(StrEnum):
    BREAKOUT_RETEST = "breakout_retest"
    EMA21_PULLBACK = "ema21_pullback"
    BREAKDOWN_SHORT = "breakdown_short"


class SwingTriggerActionability(StrEnum):
    ACTIONABLE = "actionable"
    NO_SETUP = "no_setup"


class SwingTriggerFailure(StrEnum):
    SETUP_SCORE_BELOW_SWING_MINIMUM = "setup_score_below_swing_minimum"
    SETUP_NOT_ACTIONABLE = "setup_not_actionable"
    SETUP_NOT_SWING = "setup_not_swing"
    SETUP_SIDE_MISMATCH = "setup_side_mismatch"

    FLAT_BASE_NOT_CLEARED = "flat_base_not_cleared"
    VOLUME_NOT_RISING = "volume_not_rising"
    RETEST_NOT_HELD = "retest_not_held"

    NOT_STEADY_UPTREND = "not_steady_uptrend"
    EMA21_NOT_RISING = "ema21_not_rising"
    TAP_OUTSIDE_FIRST_SECOND = "tap_outside_first_second"

    NOT_BELOW_50DMA = "not_below_50dma"
    DMA50_NOT_FALLING = "dma50_not_falling"
    BOUNCE_NOT_FAILED_AT_RESISTANCE = "bounce_not_failed_at_resistance"
    VOLUME_NOT_CONTRACTING = "volume_not_contracting"
    SWING_LOW_NOT_BROKEN = "swing_low_not_broken"


class BreakoutRetestInput(TriggerModel):
    as_of: datetime
    cleared_flat_base: StrictBool
    rising_volume: StrictBool
    retest_holds: StrictBool
    breakout_level: Decimal = Field(gt=ZERO, allow_inf_nan=False)
    base_low: Decimal = Field(gt=ZERO, allow_inf_nan=False)
    stop_pct_below_base: Decimal = Field(ge=ZERO, allow_inf_nan=False)

    @field_validator("as_of")
    @classmethod
    def as_of_is_utc(cls, value: datetime) -> datetime:
        return _require_utc(value)

    @model_validator(mode="after")
    def base_geometry_is_valid(self) -> "BreakoutRetestInput":
        if self.base_low >= self.breakout_level:
            raise ValueError("breakout retest requires base low below breakout level")
        return self


class Ema21PullbackInput(TriggerModel):
    as_of: datetime
    steady_uptrend: StrictBool
    ema21_rising: StrictBool
    tap_number: StrictInt = Field(ge=1)
    ema21_level: Decimal = Field(gt=ZERO, allow_inf_nan=False)
    swing_low: Decimal = Field(gt=ZERO, allow_inf_nan=False)
    invalidation_level: Decimal = Field(gt=ZERO, allow_inf_nan=False)

    @field_validator("as_of")
    @classmethod
    def as_of_is_utc(cls, value: datetime) -> datetime:
        return _require_utc(value)

    @model_validator(mode="after")
    def invalidation_is_below_swing_low(self) -> "Ema21PullbackInput":
        if self.swing_low >= self.ema21_level:
            raise ValueError(
                "21EMA pullback requires swing low below EMA trigger level"
            )
        if self.invalidation_level >= self.swing_low:
            raise ValueError("21EMA pullback invalidation must be below swing low")
        return self


class BreakdownShortInput(TriggerModel):
    as_of: datetime
    below_50dma: StrictBool
    dma50_falling: StrictBool
    bounce_failed_at_resistance: StrictBool
    contracting_volume: StrictBool
    swing_low_broken: StrictBool
    swing_low: Decimal = Field(gt=ZERO, allow_inf_nan=False)
    failed_bounce_high: Decimal = Field(gt=ZERO, allow_inf_nan=False)
    invalidation_level: Decimal = Field(gt=ZERO, allow_inf_nan=False)

    @field_validator("as_of")
    @classmethod
    def as_of_is_utc(cls, value: datetime) -> datetime:
        return _require_utc(value)

    @model_validator(mode="after")
    def invalidation_is_above_failed_bounce(self) -> "BreakdownShortInput":
        if self.failed_bounce_high <= self.swing_low:
            raise ValueError(
                "breakdown short requires failed-bounce high above swing low"
            )
        if self.invalidation_level <= self.failed_bounce_high:
            raise ValueError("breakdown short invalidation must be above failed bounce")
        return self


class SwingTriggerResult(TriggerModel):
    as_of: datetime
    trigger_type: SwingTriggerType
    side: TradeSide
    trigger_level: Decimal = Field(gt=ZERO, allow_inf_nan=False)
    invalidation_level: Decimal = Field(gt=ZERO, allow_inf_nan=False)
    actionability: SwingTriggerActionability
    failures: tuple[SwingTriggerFailure, ...]
    setup_score: SetupScoreResult
    trigger_rule_version: str
    playbook_version: str

    @field_validator("as_of")
    @classmethod
    def as_of_is_utc(cls, value: datetime) -> datetime:
        return _require_utc(value)

    @model_validator(mode="after")
    def trigger_geometry_matches_side(self) -> "SwingTriggerResult":
        if (
            self.side is TradeSide.LONG
            and self.invalidation_level >= self.trigger_level
        ):
            raise ValueError("long trigger requires invalidation below trigger")
        if (
            self.side is TradeSide.SHORT
            and self.invalidation_level <= self.trigger_level
        ):
            raise ValueError("short trigger requires invalidation above trigger")
        return self


def _setup_failures(
    setup: SetupScoreResult,
    *,
    as_of: datetime,
    config: SwingTriggerConfig,
    expected_side: TradeSide,
) -> list[SwingTriggerFailure]:
    if setup.as_of != as_of:
        raise ValueError("setup score timestamp must match trigger timestamp")

    failures: list[SwingTriggerFailure] = []
    if setup.trading_style is not TradingStyle.SWING:
        failures.append(SwingTriggerFailure.SETUP_NOT_SWING)
    if setup.side is not expected_side:
        failures.append(SwingTriggerFailure.SETUP_SIDE_MISMATCH)
    minimum = Decimal(str(config.min_setup_score))
    if setup.score < minimum:
        failures.append(SwingTriggerFailure.SETUP_SCORE_BELOW_SWING_MINIMUM)
    if setup.actionability is not SetupActionability.PLANNED_SIZE:
        failures.append(SwingTriggerFailure.SETUP_NOT_ACTIONABLE)
    return failures


def evaluate_breakout_retest(
    candidate: BreakoutRetestInput,
    *,
    setup: SetupScoreResult,
    config: SwingTriggerConfig | None = None,
    playbook_version: str | None = None,
) -> SwingTriggerResult:
    """Evaluate the Playbook breakout-retest swing setup."""

    selected_config = config or PLAYBOOK.swing_triggers
    stop_pct = candidate.stop_pct_below_base
    minimum = Decimal(str(selected_config.breakout_retest.stop_below_base_pct_min))
    maximum = Decimal(str(selected_config.breakout_retest.stop_below_base_pct_max))
    if stop_pct < minimum or stop_pct > maximum:
        raise ValueError("breakout retest stop must be 3% to 5% below the base")

    failures = _setup_failures(
        setup,
        as_of=candidate.as_of,
        config=selected_config,
        expected_side=TradeSide.LONG,
    )
    if not candidate.cleared_flat_base:
        failures.append(SwingTriggerFailure.FLAT_BASE_NOT_CLEARED)
    if not candidate.rising_volume:
        failures.append(SwingTriggerFailure.VOLUME_NOT_RISING)
    if not candidate.retest_holds:
        failures.append(SwingTriggerFailure.RETEST_NOT_HELD)

    invalidation = candidate.base_low * (ONE_HUNDRED - stop_pct) / ONE_HUNDRED
    return SwingTriggerResult(
        as_of=candidate.as_of,
        trigger_type=SwingTriggerType.BREAKOUT_RETEST,
        side=TradeSide.LONG,
        trigger_level=candidate.breakout_level,
        invalidation_level=invalidation,
        actionability=(
            SwingTriggerActionability.NO_SETUP
            if failures
            else SwingTriggerActionability.ACTIONABLE
        ),
        failures=tuple(failures),
        setup_score=setup,
        trigger_rule_version=selected_config.version,
        playbook_version=playbook_version or PLAYBOOK.version,
    )


def evaluate_ema21_pullback(
    candidate: Ema21PullbackInput,
    *,
    setup: SetupScoreResult,
    config: SwingTriggerConfig | None = None,
    playbook_version: str | None = None,
) -> SwingTriggerResult:
    """Evaluate the first/second pullback to a rising 21-day EMA."""

    selected_config = config or PLAYBOOK.swing_triggers
    failures = _setup_failures(
        setup,
        as_of=candidate.as_of,
        config=selected_config,
        expected_side=TradeSide.LONG,
    )
    if not candidate.steady_uptrend:
        failures.append(SwingTriggerFailure.NOT_STEADY_UPTREND)
    if not candidate.ema21_rising:
        failures.append(SwingTriggerFailure.EMA21_NOT_RISING)
    if candidate.tap_number > selected_config.ema21_pullback.max_tap_number:
        failures.append(SwingTriggerFailure.TAP_OUTSIDE_FIRST_SECOND)

    return SwingTriggerResult(
        as_of=candidate.as_of,
        trigger_type=SwingTriggerType.EMA21_PULLBACK,
        side=TradeSide.LONG,
        trigger_level=candidate.ema21_level,
        invalidation_level=candidate.invalidation_level,
        actionability=(
            SwingTriggerActionability.NO_SETUP
            if failures
            else SwingTriggerActionability.ACTIONABLE
        ),
        failures=tuple(failures),
        setup_score=setup,
        trigger_rule_version=selected_config.version,
        playbook_version=playbook_version or PLAYBOOK.version,
    )


def evaluate_breakdown_short(
    candidate: BreakdownShortInput,
    *,
    setup: SetupScoreResult,
    config: SwingTriggerConfig | None = None,
    playbook_version: str | None = None,
) -> SwingTriggerResult:
    """Evaluate the Playbook breakdown-short swing setup."""

    selected_config = config or PLAYBOOK.swing_triggers
    failures = _setup_failures(
        setup,
        as_of=candidate.as_of,
        config=selected_config,
        expected_side=TradeSide.SHORT,
    )
    if not candidate.below_50dma:
        failures.append(SwingTriggerFailure.NOT_BELOW_50DMA)
    if not candidate.dma50_falling:
        failures.append(SwingTriggerFailure.DMA50_NOT_FALLING)
    if not candidate.bounce_failed_at_resistance:
        failures.append(SwingTriggerFailure.BOUNCE_NOT_FAILED_AT_RESISTANCE)
    if not candidate.contracting_volume:
        failures.append(SwingTriggerFailure.VOLUME_NOT_CONTRACTING)
    if not candidate.swing_low_broken:
        failures.append(SwingTriggerFailure.SWING_LOW_NOT_BROKEN)

    return SwingTriggerResult(
        as_of=candidate.as_of,
        trigger_type=SwingTriggerType.BREAKDOWN_SHORT,
        side=TradeSide.SHORT,
        trigger_level=candidate.swing_low,
        invalidation_level=candidate.invalidation_level,
        actionability=(
            SwingTriggerActionability.NO_SETUP
            if failures
            else SwingTriggerActionability.ACTIONABLE
        ),
        failures=tuple(failures),
        setup_score=setup,
        trigger_rule_version=selected_config.version,
        playbook_version=playbook_version or PLAYBOOK.version,
    )
