import math
from enum import Enum

from src.playbook import PLAYBOOK, PlaybookConfig


class OpportunityStatus(str, Enum):
    ACTIVE = "ACTIVE"
    WATCHLIST = "WATCHLIST"
    BACKGROUND = "BACKGROUND"


def classify_market_opportunity(
    score: float, playbook: PlaybookConfig = PLAYBOOK
) -> OpportunityStatus:
    section = playbook.market_opportunity
    if not math.isfinite(score) or not 0 <= score <= section.max_score:
        raise ValueError(
            f"Market Opportunity Score must be between 0 and {section.max_score}"
        )

    if score >= section.thresholds["active"]:
        return OpportunityStatus.ACTIVE

    if score >= section.thresholds["watchlist"]:
        return OpportunityStatus.WATCHLIST

    return OpportunityStatus.BACKGROUND
