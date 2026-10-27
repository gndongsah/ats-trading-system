"""Deterministic Playbook v3 Setup Score and actionability."""

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
)

from src.domain.models import TradeSide, TradingStyle, _require_utc
from src.playbook import PLAYBOOK, SetupScoreConfig
from src.regime import RegimeClassification

ZERO = Decimal(0)


class SetupModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class SetupActionability(StrEnum):
    PLANNED_SIZE = "planned_size"
    UNPROVEN_SIZE = "unproven_size"
    NO_TRADE = "no_trade"


class SetupGateFailure(StrEnum):
    NOT_ABOVE_50DMA = "not_above_50dma"
    MA50_NOT_ABOVE_200DMA = "ma50_not_above_200dma"
    RS_3M_NOT_OUTPERFORMING = "rs_3m_not_outperforming"
    RS_6M_NOT_OUTPERFORMING = "rs_6m_not_outperforming"
    AVG_DAILY_VOLUME_NOT_ABOVE_MINIMUM = "avg_daily_volume_not_above_minimum"
    EXTENDED_MORE_THAN_MAX_50DMA = "extended_more_than_max_50dma"
    EARNINGS_WITHIN_MIN_DAYS = "earnings_within_min_days"
    EARNINGS_DATE_UNKNOWN = "earnings_date_unknown"


class SetupScoreComponents(SetupModel):
    """Caller-supplied deterministic component points.

    Playbook v3 defines each component maximum but not a fractional sub-formula,
    so this contract validates bounded points without inventing one.
    """

    trend_alignment: Decimal = Field(ge=ZERO, allow_inf_nan=False)
    relative_strength_vs_spy: Decimal = Field(ge=ZERO, allow_inf_nan=False)
    structure_quality: Decimal = Field(ge=ZERO, allow_inf_nan=False)
    liquidity_volume_confirmation: Decimal = Field(ge=ZERO, allow_inf_nan=False)
    earnings_distance_safety: Decimal = Field(ge=ZERO, allow_inf_nan=False)

    @property
    def total(self) -> Decimal:
        return sum(
            (
                self.trend_alignment,
                self.relative_strength_vs_spy,
                self.structure_quality,
                self.liquidity_volume_confirmation,
                self.earnings_distance_safety,
            ),
            ZERO,
        )


class SwingLongSetupScreen(SetupModel):
    """Playbook v3 ordinary swing-long execution screen."""

    above_50dma: StrictBool
    dma50_above_200dma: StrictBool
    rs_3m_outperforming_spy: StrictBool
    rs_6m_outperforming_spy: StrictBool
    avg_daily_volume: StrictInt = Field(ge=0)
    extension_pct_50dma: Decimal = Field(ge=ZERO, allow_inf_nan=False)
    days_to_earnings: StrictInt | None = Field(default=None, ge=0)
    explicit_earnings_plan: StrictBool = False


class SetupScoreResult(SetupModel):
    as_of: datetime
    score: Decimal = Field(ge=ZERO, le=Decimal(100), allow_inf_nan=False)
    score_band: SetupActionability
    actionability: SetupActionability
    components: SetupScoreComponents
    gate_failures: tuple[SetupGateFailure, ...]
    regime: RegimeClassification
    setup_rule_version: str
    playbook_version: str

    @field_validator("as_of")
    @classmethod
    def as_of_is_utc(cls, value: datetime) -> datetime:
        return _require_utc(value)


def _validate_component_maxima(
    components: SetupScoreComponents,
    config: SetupScoreConfig,
) -> None:
    values = components.model_dump(mode="python")
    for name, points in values.items():
        maximum = Decimal(str(config.factors[name].weight))
        if points > maximum:
            raise ValueError(
                f"setup score component {name} exceeds configured maximum {maximum}"
            )


def _score_band(score: Decimal, config: SetupScoreConfig) -> SetupActionability:
    planned = Decimal(str(config.thresholds["planned_size"]))
    unproven = Decimal(str(config.thresholds["unproven_size"]))
    if score >= planned:
        return SetupActionability.PLANNED_SIZE
    if score >= unproven:
        return SetupActionability.UNPROVEN_SIZE
    return SetupActionability.NO_TRADE


def _swing_long_gate_failures(
    screen: SwingLongSetupScreen,
    config: SetupScoreConfig,
) -> tuple[SetupGateFailure, ...]:
    failures: list[SetupGateFailure] = []
    rules = config.swing_long_screen

    if not screen.above_50dma:
        failures.append(SetupGateFailure.NOT_ABOVE_50DMA)
    if not screen.dma50_above_200dma:
        failures.append(SetupGateFailure.MA50_NOT_ABOVE_200DMA)
    if not screen.rs_3m_outperforming_spy:
        failures.append(SetupGateFailure.RS_3M_NOT_OUTPERFORMING)
    if not screen.rs_6m_outperforming_spy:
        failures.append(SetupGateFailure.RS_6M_NOT_OUTPERFORMING)
    if screen.avg_daily_volume <= rules.min_avg_daily_volume:
        failures.append(SetupGateFailure.AVG_DAILY_VOLUME_NOT_ABOVE_MINIMUM)
    if screen.extension_pct_50dma > Decimal(
        str(rules.max_extension_pct_50dma)
    ):
        failures.append(SetupGateFailure.EXTENDED_MORE_THAN_MAX_50DMA)
    if not screen.explicit_earnings_plan:
        if screen.days_to_earnings is None:
            failures.append(SetupGateFailure.EARNINGS_DATE_UNKNOWN)
        elif screen.days_to_earnings <= rules.min_days_to_earnings:
            failures.append(SetupGateFailure.EARNINGS_WITHIN_MIN_DAYS)

    return tuple(failures)


def score_setup(
    *,
    as_of: datetime,
    components: SetupScoreComponents,
    regime: RegimeClassification,
    trading_style: TradingStyle,
    side: TradeSide,
    swing_long_screen: SwingLongSetupScreen | None = None,
    config: SetupScoreConfig | None = None,
    playbook_version: str | None = None,
) -> SetupScoreResult:
    """Calculate exact Setup Score and apply explicit Playbook actionability gates."""

    as_of = _require_utc(as_of)
    if regime.as_of != as_of:
        raise ValueError("regime classification timestamp must match setup timestamp")

    selected_config = config or PLAYBOOK.setup_score
    selected_playbook_version = playbook_version or PLAYBOOK.version
    _validate_component_maxima(components, selected_config)

    score = components.total
    maximum = Decimal(str(selected_config.max_score))
    if score > maximum:
        raise ValueError("setup score exceeds configured maximum")

    band = _score_band(score, selected_config)
    failures: tuple[SetupGateFailure, ...] = ()

    if trading_style is TradingStyle.SWING and side is TradeSide.LONG:
        if swing_long_screen is None:
            raise ValueError("swing-long setup requires the Playbook execution screen")
        failures = _swing_long_gate_failures(swing_long_screen, selected_config)
    elif swing_long_screen is not None:
        raise ValueError("swing-long execution screen is only valid for swing longs")

    actionability = (
        SetupActionability.NO_TRADE if failures else band
    )

    return SetupScoreResult(
        as_of=as_of,
        score=score,
        score_band=band,
        actionability=actionability,
        components=components,
        gate_failures=failures,
        regime=regime,
        setup_rule_version=selected_config.version,
        playbook_version=selected_playbook_version,
    )
