from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest

from src.portfolio import (
    CandidateExposure,
    FilledExposure,
    PendingExposure,
    PortfolioRiskPolicy,
    PortfolioSnapshot,
    PositionExposure,
    ReservationExposure,
    RiskClassification,
    RiskDecision,
    RiskReason,
    RiskTaxonomy,
    assess_portfolio_risk,
)

NOW = datetime(2026, 10, 16, 12, 0, tzinfo=UTC)
ACCOUNT_ID = uuid4()
TAXONOMY = RiskTaxonomy(
    version="taxonomy-test-v1",
    classifications={
        "ABC": RiskClassification(theme="AI_NETWORKING", factors=("AI_CAPEX",)),
        "XYZ": RiskClassification(theme="INDUSTRIALS", factors=("INDUSTRIAL",)),
        "DEF": RiskClassification(
            theme="AI_NETWORKING", factors=("AI_CAPEX", "POWER_GRID")
        ),
        "GRID": RiskClassification(theme="INFRASTRUCTURE", factors=("POWER_GRID",)),
    },
)
POLICY = PortfolioRiskPolicy(
    version="risk-policy-test-v1",
    snapshot_max_age=timedelta(minutes=5),
)


def make_snapshot(**overrides) -> PortfolioSnapshot:
    values = {
        "account_id": ACCOUNT_ID,
        "version": 1,
        "as_of": NOW,
        "policy_version": POLICY.version,
        "taxonomy_version": TAXONOMY.version,
        "equity": Decimal(100000),
        "positions": (
            PositionExposure(
                ticker="XYZ",
                quantity=950,
                average_entry=Decimal(10),
                active_stop=Decimal(8),
                stop_confirmed=True,
            ),
        ),
        "pending_orders": (),
        "positions_complete": True,
        "orders_complete": True,
        "ats_managed_exposure_excluded": True,
    }
    values.update(overrides)
    return PortfolioSnapshot(**values)


def candidate(ticker="ABC", quantity=100):
    return CandidateExposure(
        ticker=ticker,
        requested_quantity=quantity,
        entry=Decimal(100),
        stop=Decimal(98),
    )


def assess(snapshot=None, reservations=(), candidate_override=None, policy=POLICY):
    return assess_portfolio_risk(
        snapshot=snapshot or make_snapshot(),
        candidate=candidate_override or candidate(),
        reservations=reservations,
        taxonomy=TAXONOMY,
        policy=policy,
        now=NOW,
    )


def test_total_heat_resizes_to_the_exact_largest_whole_share_quantity():
    snapshot = make_snapshot(
        positions=(
            PositionExposure(
                ticker="XYZ",
                quantity=950,
                average_entry=Decimal(10),
                active_stop=Decimal(8),
                stop_confirmed=True,
            ),
        )
    )
    result = assess(snapshot)
    assert result.decision is RiskDecision.RESIZE
    assert result.quantity == 50
    assert result.reason is RiskReason.PORTFOLIO_HEAT_LIMIT


def test_correlated_heat_is_counted_across_different_named_themes():
    snapshot = make_snapshot(
        positions=(
            PositionExposure(
                ticker="ABC",
                quantity=450,
                average_entry=Decimal(10),
                active_stop=Decimal(8),
                stop_confirmed=True,
            ),
        )
    )
    result = assess(snapshot)
    assert result.decision is RiskDecision.RESIZE
    assert result.quantity == 50
    assert result.reason is RiskReason.CORRELATED_HEAT_LIMIT


def test_theme_concentration_limits_gross_notional_separately_from_heat():
    snapshot = make_snapshot(
        positions=(
            PositionExposure(
                ticker="ABC",
                quantity=140,
                average_entry=Decimal(100),
                active_stop=Decimal(99),
                stop_confirmed=True,
            ),
        )
    )
    result = assess(snapshot)
    assert result.decision is RiskDecision.RESIZE
    assert result.quantity == 10
    assert result.reason is RiskReason.THEME_CONCENTRATION_LIMIT


def test_over_cap_theme_denies_candidate_in_a_different_theme():
    snapshot = make_snapshot(
        positions=(
            PositionExposure(
                ticker="ABC",
                quantity=151,
                average_entry=Decimal(100),
                active_stop=Decimal(99),
                stop_confirmed=True,
            ),
        )
    )

    result = assess(
        snapshot,
        candidate_override=candidate(ticker="XYZ", quantity=1),
    )

    assert result.decision is RiskDecision.DENY
    assert result.quantity == 0
    assert result.reason is RiskReason.THEME_CONCENTRATION_LIMIT


def test_candidate_reaching_theme_cap_exactly_is_allowed():
    snapshot = make_snapshot(
        positions=(
            PositionExposure(
                ticker="ABC",
                quantity=140,
                average_entry=Decimal(100),
                active_stop=Decimal(99),
                stop_confirmed=True,
            ),
        )
    )

    result = assess(
        snapshot,
        candidate_override=candidate(quantity=10),
    )

    assert result.decision is RiskDecision.ALLOW
    assert result.quantity == 10


def test_candidate_above_theme_cap_is_resized_to_boundary():
    snapshot = make_snapshot(
        positions=(
            PositionExposure(
                ticker="ABC",
                quantity=140,
                average_entry=Decimal(100),
                active_stop=Decimal(99),
                stop_confirmed=True,
            ),
        )
    )

    result = assess(
        snapshot,
        candidate_override=candidate(quantity=11),
    )

    assert result.decision is RiskDecision.RESIZE
    assert result.quantity == 10
    assert result.reason is RiskReason.THEME_CONCENTRATION_LIMIT


@pytest.mark.parametrize("existing_ticker", ("ABC", "GRID"))
def test_multiple_correlated_factors_each_apply_their_own_heat_limit(
    existing_ticker,
):
    snapshot = make_snapshot(
        positions=(
            PositionExposure(
                ticker=existing_ticker,
                quantity=450,
                average_entry=Decimal(10),
                active_stop=Decimal(8),
                stop_confirmed=True,
            ),
        )
    )

    result = assess(
        snapshot,
        candidate_override=candidate(ticker="DEF", quantity=600),
    )

    assert result.decision is RiskDecision.RESIZE
    assert result.quantity == 50
    assert result.reason is RiskReason.CORRELATED_HEAT_LIMIT
    assert result.binding_constraints == (RiskReason.CORRELATED_HEAT_LIMIT,)


def test_above_entry_stop_does_not_net_other_exposure_or_gross_notional():
    snapshot = make_snapshot(
        positions=(
            PositionExposure(
                ticker="ABC",
                quantity=50,
                average_entry=Decimal(100),
                active_stop=Decimal(99),
                stop_confirmed=True,
            ),
            PositionExposure(
                ticker="ABC",
                quantity=50,
                average_entry=Decimal(100),
                active_stop=Decimal(101),
                stop_confirmed=True,
            ),
        )
    )

    result = assess(snapshot, candidate_override=candidate(quantity=100))

    assert result.decision is RiskDecision.RESIZE
    assert result.quantity == 50
    assert result.reason is RiskReason.THEME_CONCENTRATION_LIMIT


def test_unconfirmed_and_missing_stops_fail_closed():
    missing = make_snapshot(
        positions=(
            PositionExposure(
                ticker="XYZ",
                quantity=1,
                average_entry=Decimal(10),
                active_stop=None,
                stop_confirmed=False,
            ),
        )
    )
    unconfirmed = make_snapshot(
        positions=(
            PositionExposure(
                ticker="XYZ",
                quantity=1,
                average_entry=Decimal(10),
                active_stop=Decimal(8),
                stop_confirmed=False,
            ),
        )
    )
    assert assess(missing).reason is RiskReason.STOP_MISSING
    assert assess(unconfirmed).reason is RiskReason.STOP_NOT_CONFIRMED


def test_stale_incomplete_and_unclassified_snapshots_are_denied():
    stale = make_snapshot(as_of=NOW - timedelta(minutes=6))
    incomplete = make_snapshot(orders_complete=False)
    unknown = make_snapshot(
        positions=(
            PositionExposure(
                ticker="UNKNOWN",
                quantity=1,
                average_entry=Decimal(10),
                active_stop=Decimal(8),
                stop_confirmed=True,
            ),
        )
    )
    assert assess(stale).reason is RiskReason.PORTFOLIO_STATE_STALE
    assert assess(incomplete).reason is RiskReason.PORTFOLIO_STATE_INCOMPLETE
    assert assess(unknown).reason is RiskReason.FACTOR_CLASSIFICATION_MISSING
    overlapping = make_snapshot(ats_managed_exposure_excluded=False)
    assert assess(overlapping).reason is RiskReason.PORTFOLIO_STATE_CONFLICT


def test_missing_freshness_policy_and_taxonomy_mismatch_fail_closed():
    unconfigured = PortfolioRiskPolicy(version=POLICY.version)
    mismatched = make_snapshot(taxonomy_version="other-taxonomy")
    assert assess(policy=unconfigured).reason is RiskReason.POLICY_NOT_CONFIGURED
    assert assess(mismatched).reason is RiskReason.TAXONOMY_VERSION_MISMATCH


def test_partial_fill_and_remaining_order_conserve_the_same_exact_risk():
    reservation = ReservationExposure(
        reservation_id=uuid4(),
        ticker="ABC",
        remaining_quantity=60,
        entry=Decimal(100),
        stop=Decimal(90),
        fills=(
            FilledExposure(quantity=30, price=Decimal(100), active_stop=Decimal(90)),
        ),
    )
    result = assess(
        make_snapshot(positions=()),
        reservations=(reservation,),
        candidate_override=candidate(quantity=100),
    )
    assert result.decision is RiskDecision.RESIZE
    assert result.quantity == 50


def test_fill_at_higher_price_uses_actual_risk_and_unconfirmed_stop_blocks():
    reservation = ReservationExposure(
        reservation_id=uuid4(),
        ticker="ABC",
        remaining_quantity=440,
        entry=Decimal(10),
        stop=Decimal(8),
        fills=(FilledExposure(quantity=30, price=Decimal(11), active_stop=Decimal(8)),),
    )
    result = assess(
        make_snapshot(positions=()),
        reservations=(reservation,),
        candidate_override=candidate(quantity=100),
    )
    assert result.quantity == 15
    assert result.reason is RiskReason.CORRELATED_HEAT_LIMIT

    no_confirmed_stop = ReservationExposure(
        reservation_id=uuid4(),
        ticker="ABC",
        remaining_quantity=70,
        entry=Decimal(100),
        stop=Decimal(98),
        fills=(FilledExposure(quantity=30, price=Decimal(100)),),
    )
    assert (
        assess(make_snapshot(positions=()), reservations=(no_confirmed_stop,)).reason
        is RiskReason.STOP_NOT_CONFIRMED
    )


def test_external_pending_orders_consume_risk_and_theme_capacity():
    snapshot = make_snapshot(
        positions=(),
        pending_orders=(
            PendingExposure(
                ticker="ABC",
                remaining_quantity=140,
                entry=Decimal(100),
                stop=Decimal(98),
                status_confirmed=True,
            ),
        ),
    )
    result = assess(snapshot)
    assert result.decision is RiskDecision.RESIZE
    assert result.quantity == 10
    assert result.reason is RiskReason.THEME_CONCENTRATION_LIMIT
