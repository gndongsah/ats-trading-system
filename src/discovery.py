import math
from enum import Enum

from src.playbook import PLAYBOOK, PlaybookConfig


class DiscoveryDecision(str, Enum):
    TIER_A = "TIER_A"
    TIER_B = "TIER_B"
    WATCHLIST = "WATCHLIST"
    REJECT = "REJECT"


def classify_discovery(
    score: float, playbook: PlaybookConfig = PLAYBOOK
) -> DiscoveryDecision:
    section = playbook.discovery
    if not math.isfinite(score) or not 0 <= score <= section.max_score:
        raise ValueError(f"Discovery Score must be between 0 and {section.max_score}")

    if score >= section.thresholds["tier_a"]:
        return DiscoveryDecision.TIER_A
    if score >= section.thresholds["tier_b"]:
        return DiscoveryDecision.TIER_B
    if score >= section.thresholds["watchlist"]:
        return DiscoveryDecision.WATCHLIST
    return DiscoveryDecision.REJECT
