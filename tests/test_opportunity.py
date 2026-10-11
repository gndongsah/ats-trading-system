import math

import pytest

from src.opportunity import OpportunityStatus, classify_market_opportunity
from src.playbook import PLAYBOOK


def test_market_opportunity_thresholds_are_read_from_playbook():
    thresholds = PLAYBOOK.market_opportunity.thresholds
    assert classify_market_opportunity(thresholds["active"]) == OpportunityStatus.ACTIVE
    assert (
        classify_market_opportunity(thresholds["active"] - 0.01)
        == OpportunityStatus.WATCHLIST
    )
    assert (
        classify_market_opportunity(thresholds["watchlist"])
        == OpportunityStatus.WATCHLIST
    )
    assert (
        classify_market_opportunity(thresholds["watchlist"] - 0.01)
        == OpportunityStatus.BACKGROUND
    )


@pytest.mark.parametrize("score", [-1, math.inf, -math.inf, math.nan])
def test_invalid_market_opportunity_score_is_rejected(score):
    with pytest.raises(ValueError):
        classify_market_opportunity(score)


def test_score_above_playbook_maximum_is_rejected():
    with pytest.raises(ValueError):
        classify_market_opportunity(PLAYBOOK.market_opportunity.max_score + 1)
