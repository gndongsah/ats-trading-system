from __future__ import annotations

import math
from pathlib import Path
from typing import Self

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

PLAYBOOK_PATH = Path(__file__).resolve().parent.parent / "playbook" / "playbook_v3.yaml"

REGIME_SIGNAL_NAMES = frozenset(
    {
        "spy_above_50dma",
        "dma50_above_200dma",
        "dma50_rising",
        "dma200_rising",
        "breadth_healthy",
        "advancers_leading",
        "new_highs_expanding",
        "index_trending_up",
        "breadth_deteriorating",
        "leadership_concentrated",
        "index_sideways_around_50dma",
        "breadth_mixed",
        "breakouts_failing_both_ways",
        "spy_below_50dma",
        "new_lows_expanding",
        "breadth_washed_out",
        "volatility_spiking",
        "early_reclaim_attempts",
    }
)


class PlaybookValidationError(ValueError):
    pass


class FactorConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    weight: float = Field(gt=0, allow_inf_nan=False)

    @field_validator("weight")
    @classmethod
    def reject_boolean_weights(cls, value: float) -> float:
        if isinstance(value, bool):
            raise TypeError("weight must be numeric, not boolean")
        return value


class ScoreSection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_score: float = Field(gt=0, allow_inf_nan=False)
    factors: dict[str, FactorConfig]
    thresholds: dict[str, float]

    @model_validator(mode="after")
    def validate_weights(self) -> Self:
        if not self.factors:
            raise ValueError("at least one factor is required")
        if any(not name.strip() for name in self.factors):
            raise ValueError("factor names must not be blank")
        weights = [factor.weight for factor in self.factors.values()]
        if not math.isclose(sum(weights), self.max_score, rel_tol=1e-9):
            raise ValueError("factor weights must sum to max_score")
        if any(
            not math.isfinite(threshold) or not 0 <= threshold <= self.max_score
            for threshold in self.thresholds.values()
        ):
            raise ValueError("thresholds must be finite and within the score range")
        return self


class MarketOpportunityConfig(ScoreSection):
    @model_validator(mode="after")
    def validate_thresholds(self) -> Self:
        if set(self.thresholds) != {"active", "watchlist"}:
            raise ValueError("thresholds must define active and watchlist")
        if self.thresholds["active"] <= self.thresholds["watchlist"]:
            raise ValueError("active threshold must be greater than watchlist")
        return self


class DiscoveryConfig(ScoreSection):
    @model_validator(mode="after")
    def validate_thresholds(self) -> Self:
        if set(self.thresholds) != {"tier_a", "tier_b", "watchlist"}:
            raise ValueError("thresholds must define tier_a, tier_b, and watchlist")
        if not (
            self.thresholds["tier_a"]
            > self.thresholds["tier_b"]
            > self.thresholds["watchlist"]
        ):
            raise ValueError("thresholds must descend from tier_a to watchlist")
        return self


class RegimeRuleConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    regime: int = Field(ge=1, le=5, strict=True)
    requires: tuple[str, ...]

    @field_validator("requires")
    @classmethod
    def validate_required_signals(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if not value:
            raise ValueError("regime rule requires at least one signal")
        normalized = tuple(signal.strip() for signal in value)
        if any(not signal for signal in normalized):
            raise ValueError("regime signal names must not be blank")
        if len(set(normalized)) != len(normalized):
            raise ValueError("regime rule cannot repeat required signals")
        unknown = set(normalized) - REGIME_SIGNAL_NAMES
        if unknown:
            raise ValueError(f"unknown regime signal(s): {', '.join(sorted(unknown))}")
        return normalized


class RegimeConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: str = Field(min_length=1)
    precedence: tuple[str, ...]
    rules: dict[str, RegimeRuleConfig]

    @model_validator(mode="after")
    def validate_rules(self) -> Self:
        if not self.rules:
            raise ValueError("at least one regime rule is required")
        if any(not name.strip() for name in self.rules):
            raise ValueError("regime rule names must not be blank")
        if len(set(self.precedence)) != len(self.precedence):
            raise ValueError("regime precedence cannot contain duplicates")
        if set(self.precedence) != set(self.rules):
            raise ValueError("regime precedence must list every configured rule once")
        configured_regimes = [rule.regime for rule in self.rules.values()]
        if len(set(configured_regimes)) != len(configured_regimes):
            raise ValueError("each regime number must be configured exactly once")
        if set(configured_regimes) != {1, 2, 3, 4, 5}:
            raise ValueError(
                "regime rules must configure regimes 1 through 5 exactly once"
            )
        return self


class SwingLongScreenConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    min_avg_daily_volume: int = Field(gt=0, strict=True)
    max_extension_pct_50dma: float = Field(ge=0, allow_inf_nan=False)
    min_days_to_earnings: int = Field(ge=0, strict=True)


class SetupScoreConfig(ScoreSection):
    version: str = Field(min_length=1)
    swing_long_screen: SwingLongScreenConfig

    @model_validator(mode="after")
    def validate_setup_score(self) -> Self:
        expected_factors = {
            "trend_alignment",
            "relative_strength_vs_spy",
            "structure_quality",
            "liquidity_volume_confirmation",
            "earnings_distance_safety",
        }
        if set(self.factors) != expected_factors:
            raise ValueError(
                "setup score factors must define the five Playbook v3 components"
            )
        if set(self.thresholds) != {"planned_size", "unproven_size"}:
            raise ValueError(
                "setup score thresholds must define planned_size and unproven_size"
            )
        if self.thresholds["planned_size"] <= self.thresholds["unproven_size"]:
            raise ValueError("planned_size threshold must exceed unproven_size")
        if not math.isclose(self.max_score, 100.0, rel_tol=0, abs_tol=1e-9):
            raise ValueError("setup score max_score must be exactly 100")
        expected_weights = {
            "trend_alignment": 25.0,
            "relative_strength_vs_spy": 25.0,
            "structure_quality": 25.0,
            "liquidity_volume_confirmation": 15.0,
            "earnings_distance_safety": 10.0,
        }
        actual_weights = {
            name: factor.weight for name, factor in self.factors.items()
        }
        if any(
            not math.isclose(actual_weights[name], weight, rel_tol=0, abs_tol=1e-9)
            for name, weight in expected_weights.items()
        ):
            raise ValueError("setup score weights must match Playbook v3 exactly")
        if not math.isclose(
            self.thresholds["planned_size"], 70.0, rel_tol=0, abs_tol=1e-9
        ) or not math.isclose(
            self.thresholds["unproven_size"], 50.0, rel_tol=0, abs_tol=1e-9
        ):
            raise ValueError("setup score thresholds must be 70 and 50")
        return self


class PlaybookConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: str = Field(min_length=1)
    market_opportunity: MarketOpportunityConfig
    discovery: DiscoveryConfig
    regime: RegimeConfig
    setup_score: SetupScoreConfig


def load_playbook(path: Path = PLAYBOOK_PATH) -> PlaybookConfig:
    try:
        raw_playbook = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(raw_playbook, dict):
            raise PlaybookValidationError(
                "Playbook validation failed: expected a YAML mapping"
            )
        return PlaybookConfig.model_validate(raw_playbook)
    except (OSError, yaml.YAMLError) as error:
        raise PlaybookValidationError(
            f"Unable to load playbook at {path}: {error}"
        ) from error
    except ValidationError as error:
        raise PlaybookValidationError(
            f"Playbook validation failed at {path}:\n{error}"
        ) from error


PLAYBOOK = load_playbook()
