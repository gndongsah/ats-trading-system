"""Deterministic Playbook v3 market-regime classifier."""

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, StrictBool, field_validator, model_validator

from src.domain.models import Regime, _require_utc
from src.playbook import PLAYBOOK, RegimeConfig


class RegimeModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class RegimeInputs(RegimeModel):
    """Normalized Layer 2 market-state signals evaluated at one UTC timestamp."""

    as_of: datetime

    spy_above_50dma: StrictBool = False
    dma50_above_200dma: StrictBool = False
    dma50_rising: StrictBool = False
    dma200_rising: StrictBool = False
    breadth_healthy: StrictBool = False
    advancers_leading: StrictBool = False
    new_highs_expanding: StrictBool = False

    index_trending_up: StrictBool = False
    breadth_deteriorating: StrictBool = False
    leadership_concentrated: StrictBool = False

    index_sideways_around_50dma: StrictBool = False
    breadth_mixed: StrictBool = False
    breakouts_failing_both_ways: StrictBool = False

    spy_below_50dma: StrictBool = False
    new_lows_expanding: StrictBool = False

    breadth_washed_out: StrictBool = False
    volatility_spiking: StrictBool = False
    early_reclaim_attempts: StrictBool = False

    @field_validator("as_of")
    @classmethod
    def as_of_is_utc(cls, value: datetime) -> datetime:
        return _require_utc(value)

    @model_validator(mode="after")
    def reject_contradictory_normalized_states(self) -> "RegimeInputs":
        if self.spy_above_50dma and self.spy_below_50dma:
            raise ValueError("SPY cannot be both above and below the 50DMA")
        breadth_states = (
            self.breadth_healthy,
            self.breadth_deteriorating,
            self.breadth_mixed,
            self.breadth_washed_out,
        )
        if sum(breadth_states) > 1:
            raise ValueError("normalized breadth states must be mutually exclusive")
        if self.index_trending_up and self.index_sideways_around_50dma:
            raise ValueError(
                "index cannot be both trending up and sideways around 50DMA"
            )
        return self

    @property
    def active_signals(self) -> frozenset[str]:
        return frozenset(
            name
            for name, value in self.model_dump(mode="python", exclude={"as_of"}).items()
            if value
        )


class RegimeClassification(RegimeModel):
    as_of: datetime
    regime: Regime
    rule_name: str
    rule_version: str
    playbook_version: str
    matched_signals: tuple[str, ...]
    active_signals: tuple[str, ...]
    fixed_risk_multiplier: Decimal | None

    @field_validator("as_of")
    @classmethod
    def as_of_is_utc(cls, value: datetime) -> datetime:
        return _require_utc(value)


class RegimeUnclassified(ValueError):
    """Normalized signals did not satisfy any configured regime rule."""


def _fixed_risk_multiplier(regime: Regime) -> Decimal | None:
    if regime is Regime.RISK_ON:
        return Decimal("1.00")
    if regime is Regime.RISK_ON_NARROW:
        return Decimal("0.75")
    if regime is Regime.NEUTRAL_CHOP:
        return Decimal("0.50")
    return None


def classify_market_regime(
    inputs: RegimeInputs,
    *,
    config: RegimeConfig | None = None,
    playbook_version: str | None = None,
) -> RegimeClassification:
    """Classify normalized market signals using explicit playbook precedence."""

    selected_config = config or PLAYBOOK.regime
    selected_playbook_version = playbook_version or PLAYBOOK.version
    active = inputs.active_signals

    for rule_name in selected_config.precedence:
        rule = selected_config.rules[rule_name]
        required = frozenset(rule.requires)
        if required <= active:
            regime = Regime(rule.regime)
            return RegimeClassification(
                as_of=inputs.as_of,
                regime=regime,
                rule_name=rule_name,
                rule_version=selected_config.version,
                playbook_version=selected_playbook_version,
                matched_signals=rule.requires,
                active_signals=tuple(sorted(active)),
                fixed_risk_multiplier=_fixed_risk_multiplier(regime),
            )

    rendered = ", ".join(sorted(active)) or "none"
    raise RegimeUnclassified(
        f"no configured market regime matches active signals: {rendered}"
    )
