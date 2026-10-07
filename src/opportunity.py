from enum import Enum


class OpportunityStatus(str, Enum):
    ACTIVE = "ACTIVE"
    WATCHLIST = "WATCHLIST"
    BACKGROUND = "BACKGROUND"


def classify_market_opportunity(score: float) -> OpportunityStatus:
    if not 0 <= score <= 100:
        raise ValueError("Market Opportunity Score must be between 0 and 100")

    if score >= 75:
        return OpportunityStatus.ACTIVE

    if score >= 55:
        return OpportunityStatus.WATCHLIST

    return OpportunityStatus.BACKGROUND