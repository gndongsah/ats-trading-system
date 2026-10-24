"""Approved 4R / 2R / 15% portfolio-policy acceptance boundaries."""

from datetime import timedelta
from decimal import Decimal

import pytest

from src.portfolio import (
    PortfolioRiskPolicy,
    PositionExposure,
    RiskClassification,
    RiskDecision,
    RiskReason,
    RiskTaxonomy,
    assess_portfolio_risk,
)
from tests.test_portfolio import NOW, POLICY, candidate, make_snapshot


@pytest.mark.parametrize("source", ["position", "pending", "reservation", "filled"])
def test_same_theme_disjoint_factors_share_two_r_budget(source):
    from uuid import uuid4

    from src.portfolio import FilledExposure, PendingExposure, ReservationExposure

    taxonomy = RiskTaxonomy(
        version="acceptance",
        classifications={
            "OLD": RiskClassification(theme="AI", factors=("POWER",)),
            "ABC": RiskClassification(theme="AI", factors=("NETWORK",)),
        },
    )
    kwargs = {
        "positions": (),
        "pending_orders": (),
        "taxonomy_version": taxonomy.version,
    }
    reservations = ()
    if source == "position":
        kwargs["positions"] = (
            PositionExposure(
                ticker="OLD",
                quantity=450,
                average_entry=Decimal(10),
                active_stop=Decimal(8),
                stop_confirmed=True,
            ),
        )
    elif source == "pending":
        kwargs["pending_orders"] = (
            PendingExposure(
                ticker="OLD",
                remaining_quantity=450,
                entry=Decimal(10),
                stop=Decimal(8),
                status_confirmed=True,
            ),
        )
    else:
        reservations = (
            ReservationExposure(
                reservation_id=uuid4(),
                ticker="OLD",
                remaining_quantity=0 if source == "filled" else 450,
                entry=Decimal(10),
                stop=Decimal(8),
                fills=(
                    FilledExposure(
                        quantity=450, price=Decimal(10), active_stop=Decimal(8)
                    ),
                )
                if source == "filled"
                else (),
            ),
        )
    result = assess_portfolio_risk(
        snapshot=make_snapshot(**kwargs),
        candidate=candidate(),
        reservations=reservations,
        taxonomy=taxonomy,
        policy=POLICY,
        now=NOW,
    )
    assert result.decision is RiskDecision.RESIZE
    assert result.quantity == 50
    assert result.reason is RiskReason.CORRELATED_HEAT_LIMIT


@pytest.mark.parametrize(
    "budget,requested,expected,decision",
    [
        ("total", 50, 50, RiskDecision.ALLOW),
        ("total", 51, 50, RiskDecision.RESIZE),
        ("factor", 50, 50, RiskDecision.ALLOW),
        ("factor", 51, 50, RiskDecision.RESIZE),
        ("theme", 10, 10, RiskDecision.ALLOW),
        ("theme", 11, 10, RiskDecision.RESIZE),
    ],
)
def test_exact_approved_boundaries(budget, requested, expected, decision):
    from tests.test_portfolio import assess

    ticker, quantity, entry, stop = {
        "total": ("XYZ", 950, "10", "8"),
        "factor": ("ABC", 450, "10", "8"),
        "theme": ("ABC", 140, "100", "99"),
    }[budget]
    snapshot = make_snapshot(
        positions=(
            PositionExposure(
                ticker=ticker,
                quantity=quantity,
                average_entry=Decimal(entry),
                active_stop=Decimal(stop),
                stop_confirmed=True,
            ),
        )
    )
    result = assess(snapshot, candidate_override=candidate(quantity=requested))
    assert (result.decision, result.quantity) == (decision, expected)


def test_factor_budget_applies_across_different_themes():
    from tests.test_portfolio import TAXONOMY

    snapshot = make_snapshot(
        positions=(
            PositionExposure(
                ticker="DEF",
                quantity=450,
                average_entry=Decimal(10),
                active_stop=Decimal(8),
                stop_confirmed=True,
            ),
        )
    )
    result = assess_portfolio_risk(
        snapshot=snapshot,
        candidate=candidate(ticker="GRID"),
        reservations=(),
        taxonomy=TAXONOMY,
        policy=POLICY,
        now=NOW,
    )
    assert (result.decision, result.quantity) == (RiskDecision.RESIZE, 50)


@pytest.mark.parametrize(
    "field,value",
    [
        ("reference_fraction", "0.01"),
        ("total_cap_r", "5"),
        ("correlated_cap_r", "2.5"),
        ("theme_concentration", "0.2"),
    ],
)
def test_legacy_or_unapproved_policy_limits_are_rejected(field, value):
    with pytest.raises(ValueError):
        PortfolioRiskPolicy(
            version="invalid",
            snapshot_max_age=timedelta(seconds=30),
            **{field: Decimal(value)},
        )


def test_small_decimal_context_does_not_change_budget_result():
    from decimal import Inexact, Rounded, localcontext

    from tests.test_portfolio import assess

    with localcontext() as context:
        context.prec = 2
        context.traps[Inexact] = True
        context.traps[Rounded] = True
        result = assess(candidate_override=candidate(quantity=51))
    assert (result.decision, result.quantity) == (RiskDecision.RESIZE, 50)
