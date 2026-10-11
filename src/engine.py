import math
from collections.abc import Mapping

from src.playbook import PLAYBOOK, PlaybookConfig, ScoreSection


def calculate_weighted_score(
    factor_scores: Mapping[str, float],
    section: ScoreSection,
) -> float:
    expected_factors = set(section.factors)
    supplied_factors = set(factor_scores)
    if supplied_factors != expected_factors:
        missing = sorted(expected_factors - supplied_factors)
        unexpected = sorted(supplied_factors - expected_factors)
        details = []
        if missing:
            details.append(f"missing factors: {', '.join(missing)}")
        if unexpected:
            details.append(f"unexpected factors: {', '.join(unexpected)}")
        raise ValueError("; ".join(details))

    for name, score in factor_scores.items():
        if not math.isfinite(score) or not 0 <= score <= section.max_score:
            raise ValueError(
                f"Factor '{name}' must be between 0 and {section.max_score}"
            )

    total_weight = sum(factor.weight for factor in section.factors.values())
    return (
        sum(
            factor_scores[name] * factor.weight
            for name, factor in section.factors.items()
        )
        / total_weight
    )


def score_market_opportunity(
    factor_scores: Mapping[str, float], playbook: PlaybookConfig = PLAYBOOK
) -> float:
    return calculate_weighted_score(factor_scores, playbook.market_opportunity)


def score_discovery(
    factor_scores: Mapping[str, float], playbook: PlaybookConfig = PLAYBOOK
) -> float:
    return calculate_weighted_score(factor_scores, playbook.discovery)
