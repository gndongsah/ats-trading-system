import math

import pytest

from src.discovery import DiscoveryDecision, classify_discovery
from src.playbook import PLAYBOOK


def test_discovery_thresholds_are_read_from_playbook():
    thresholds = PLAYBOOK.discovery.thresholds
    assert classify_discovery(thresholds["tier_a"]) == DiscoveryDecision.TIER_A
    assert classify_discovery(thresholds["tier_a"] - 0.01) == DiscoveryDecision.TIER_B
    assert classify_discovery(thresholds["tier_b"]) == DiscoveryDecision.TIER_B
    assert (
        classify_discovery(thresholds["tier_b"] - 0.01) == DiscoveryDecision.WATCHLIST
    )
    assert classify_discovery(thresholds["watchlist"]) == DiscoveryDecision.WATCHLIST
    assert (
        classify_discovery(thresholds["watchlist"] - 0.01) == DiscoveryDecision.REJECT
    )


@pytest.mark.parametrize("score", [-1, math.inf, -math.inf, math.nan])
def test_invalid_discovery_score_is_rejected(score):
    with pytest.raises(ValueError):
        classify_discovery(score)
