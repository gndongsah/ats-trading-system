from datetime import UTC, datetime
from decimal import Decimal

import pytest
from pydantic import ValidationError

from src.domain.models import Regime
from src.playbook import PLAYBOOK
from src.regime import (
    RegimeInputs,
    RegimeUnclassified,
    classify_market_regime,
)

AS_OF = datetime(2026, 10, 26, 23, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    ("expected", "signals"),
    [
        (
            Regime.RISK_ON,
            {
                "spy_above_50dma": True,
                "dma50_above_200dma": True,
                "dma50_rising": True,
                "dma200_rising": True,
                "breadth_healthy": True,
                "advancers_leading": True,
                "new_highs_expanding": True,
            },
        ),
        (
            Regime.RISK_ON_NARROW,
            {
                "index_trending_up": True,
                "breadth_deteriorating": True,
                "leadership_concentrated": True,
            },
        ),
        (
            Regime.NEUTRAL_CHOP,
            {
                "index_sideways_around_50dma": True,
                "breadth_mixed": True,
                "breakouts_failing_both_ways": True,
            },
        ),
        (
            Regime.RISK_OFF,
            {
                "spy_below_50dma": True,
                "breadth_deteriorating": True,
                "new_lows_expanding": True,
            },
        ),
        (
            Regime.CAPITULATION_REVERSAL,
            {
                "breadth_washed_out": True,
                "volatility_spiking": True,
                "early_reclaim_attempts": True,
            },
        ),
    ],
)
def test_classifier_reproduces_all_five_playbook_regimes(expected, signals):
    result = classify_market_regime(RegimeInputs(as_of=AS_OF, **signals))

    assert result.regime is expected
    assert result.rule_version == PLAYBOOK.regime.version
    assert result.playbook_version == PLAYBOOK.version
    assert set(result.matched_signals) <= set(result.active_signals)


@pytest.mark.parametrize(
    ("regime", "expected_multiplier"),
    [
        (Regime.RISK_ON, Decimal("1.00")),
        (Regime.RISK_ON_NARROW, Decimal("0.75")),
        (Regime.NEUTRAL_CHOP, Decimal("0.50")),
    ],
)
def test_classifier_exposes_only_fixed_playbook_multipliers(regime, expected_multiplier):
    signals = {
        Regime.RISK_ON: {
            "spy_above_50dma": True,
            "dma50_above_200dma": True,
            "dma50_rising": True,
            "dma200_rising": True,
            "breadth_healthy": True,
            "advancers_leading": True,
            "new_highs_expanding": True,
        },
        Regime.RISK_ON_NARROW: {
            "index_trending_up": True,
            "breadth_deteriorating": True,
            "leadership_concentrated": True,
        },
        Regime.NEUTRAL_CHOP: {
            "index_sideways_around_50dma": True,
            "breadth_mixed": True,
            "breakouts_failing_both_ways": True,
        },
    }[regime]
    result = classify_market_regime(RegimeInputs(as_of=AS_OF, **signals))
    assert result.fixed_risk_multiplier == expected_multiplier


@pytest.mark.parametrize(
    "signals",
    [
        {
            "spy_below_50dma": True,
            "breadth_deteriorating": True,
            "new_lows_expanding": True,
        },
        {
            "breadth_washed_out": True,
            "volatility_spiking": True,
            "early_reclaim_attempts": True,
        },
    ],
)
def test_classifier_does_not_invent_variable_or_special_setup_multiplier(signals):
    result = classify_market_regime(RegimeInputs(as_of=AS_OF, **signals))
    assert result.fixed_risk_multiplier is None


def test_configured_precedence_resolves_overlapping_matching_rules():
    inputs = RegimeInputs(
        as_of=AS_OF,
        index_trending_up=True,
        spy_below_50dma=True,
        breadth_deteriorating=True,
        leadership_concentrated=True,
        new_lows_expanding=True,
    )
    result = classify_market_regime(inputs)

    assert result.regime is Regime.RISK_OFF
    assert result.rule_name == "risk_off"


def test_no_match_fails_closed_instead_of_guessing_neutral():
    with pytest.raises(RegimeUnclassified, match="no configured market regime"):
        classify_market_regime(RegimeInputs(as_of=AS_OF))


def test_inputs_reject_contradictory_normalized_states():
    with pytest.raises(ValidationError, match="both above and below"):
        RegimeInputs(
            as_of=AS_OF,
            spy_above_50dma=True,
            spy_below_50dma=True,
        )
    with pytest.raises(ValidationError, match="breadth states"):
        RegimeInputs(
            as_of=AS_OF,
            breadth_healthy=True,
            breadth_mixed=True,
        )
    with pytest.raises(ValidationError, match="both trending up and sideways"):
        RegimeInputs(
            as_of=AS_OF,
            index_trending_up=True,
            index_sideways_around_50dma=True,
        )


def test_inputs_require_strict_booleans_and_utc_timestamp():
    with pytest.raises(ValidationError):
        RegimeInputs(as_of=AS_OF, breadth_healthy=1)
    with pytest.raises(ValidationError, match="UTC"):
        RegimeInputs(as_of=AS_OF.replace(tzinfo=None))


def test_inputs_and_classification_are_immutable():
    inputs = RegimeInputs(
        as_of=AS_OF,
        index_sideways_around_50dma=True,
        breadth_mixed=True,
        breakouts_failing_both_ways=True,
    )
    result = classify_market_regime(inputs)

    with pytest.raises(ValidationError):
        inputs.breadth_mixed = False
    with pytest.raises(ValidationError):
        result.rule_name = "risk_on"
