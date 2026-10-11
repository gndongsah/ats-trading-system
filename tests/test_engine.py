import pytest

from src.discovery import DiscoveryDecision, classify_discovery
from src.engine import score_discovery, score_market_opportunity
from src.opportunity import OpportunityStatus, classify_market_opportunity
from src.playbook import PLAYBOOK


@pytest.mark.parametrize(
    ("section_name", "score_function", "classifier", "best_decision", "worst_decision"),
    [
        (
            "market_opportunity",
            score_market_opportunity,
            classify_market_opportunity,
            OpportunityStatus.ACTIVE,
            OpportunityStatus.BACKGROUND,
        ),
        (
            "discovery",
            score_discovery,
            classify_discovery,
            DiscoveryDecision.TIER_A,
            DiscoveryDecision.REJECT,
        ),
    ],
)
def test_weighted_scores_and_decisions_are_deterministic(
    section_name, score_function, classifier, best_decision, worst_decision
):
    section = getattr(PLAYBOOK, section_name)
    best_scores = {factor: section.max_score for factor in section.factors}
    worst_scores = {factor: 0 for factor in section.factors}

    assert score_function(best_scores) == section.max_score
    assert classifier(score_function(best_scores)) == best_decision
    assert score_function(worst_scores) == 0
    assert classifier(score_function(worst_scores)) == worst_decision


def test_weighted_scoring_requires_all_expected_factors():
    with pytest.raises(ValueError, match="missing factors"):
        score_market_opportunity({})


def test_weighted_scoring_rejects_scores_outside_configured_range():
    section = PLAYBOOK.market_opportunity
    invalid_scores = {factor: 0 for factor in section.factors}
    invalid_scores[next(iter(section.factors))] = section.max_score + 1

    with pytest.raises(ValueError, match="must be between"):
        score_market_opportunity(invalid_scores)
