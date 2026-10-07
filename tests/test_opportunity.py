import pytest

from src.opportunity import OpportunityStatus, classify_market_opportunity


def test_score_75_is_active():
    assert classify_market_opportunity(75) == OpportunityStatus.ACTIVE


def test_score_74_is_watchlist():
    assert classify_market_opportunity(74) == OpportunityStatus.WATCHLIST


def test_score_55_is_watchlist():
    assert classify_market_opportunity(55) == OpportunityStatus.WATCHLIST


def test_score_54_is_background():
    assert classify_market_opportunity(54) == OpportunityStatus.BACKGROUND


def test_invalid_score_is_rejected():
    with pytest.raises(ValueError):
        classify_market_opportunity(105)