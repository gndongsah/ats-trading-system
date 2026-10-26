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


class PlaybookConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: str = Field(min_length=1)
    market_opportunity: MarketOpportunityConfig
    discovery: DiscoveryConfig


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
