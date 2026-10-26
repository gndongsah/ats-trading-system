from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from pydantic import ValidationError

from src.portfolio import (
    CandidateExposure,
    ExposureSummary,
    PortfolioRiskPolicy,
    PortfolioRunType,
    PortfolioSnapshot,
    PortfolioState,
    PortfolioValueProvenance,
    RiskClassification,
    RiskDecision,
    RiskReason,
    RiskTaxonomy,
    assess_portfolio_risk,
)

NOW = datetime(2026, 10, 26, 18, 0, tzinfo=UTC)
ACCOUNT_ID = uuid4()
POLICY = PortfolioRiskPolicy(
    version="risk-policy-test-v1",
    snapshot_max_age=timedelta(minutes=5),
)
TAXONOMY = RiskTaxonomy(
    version="taxonomy-test-v1",
    classifications={
        "ABC": RiskClassification(theme="AI_NETWORKING", factors=("AI_CAPEX",)),
    },
)


def state(**overrides) -> PortfolioState:
    values = {
        "account_id": ACCOUNT_ID,
        "version": 1,
        "as_of": NOW,
        "policy_version": POLICY.version,
        "taxonomy_version": TAXONOMY.version,
        "equity": Decimal(100000),
        "positions": (),
        "pending_orders": (),
        "positions_complete": True,
        "orders_complete": True,
        "ats_managed_exposure_excluded": True,
        "run_type": PortfolioRunType.REPLAY,
        "source": "synthetic-replay",
        "source_version": "fixture-v1",
        "observed_at": NOW - timedelta(minutes=1),
        "ingested_at": NOW,
        "cash": Decimal(25000),
        "buying_power": Decimal(50000),
        "equity_provenance": PortfolioValueProvenance(
            source="synthetic-replay",
            source_version="fixture-v1",
            observed_at=NOW - timedelta(minutes=1),
            ingested_at=NOW,
            provenance_ref="fixture://account/equity/1",
            reconciled=True,
        ),
        "cash_provenance": PortfolioValueProvenance(
            source="synthetic-replay",
            source_version="fixture-v1",
            observed_at=NOW - timedelta(minutes=1),
            ingested_at=NOW,
            provenance_ref="fixture://account/cash/1",
            reconciled=True,
        ),
        "buying_power_provenance": PortfolioValueProvenance(
            source="synthetic-replay",
            source_version="fixture-v1",
            observed_at=NOW - timedelta(minutes=1),
            ingested_at=NOW,
            provenance_ref="fixture://account/buying-power/1",
            reconciled=True,
        ),
        "open_r": Decimal("1.25"),
        "theme_exposure": (
            ExposureSummary(
                key="ai_networking",
                risk_r=Decimal("0.75"),
                gross_notional=Decimal(15000),
            ),
        ),
        "factor_exposure": (
            ExposureSummary(key="ai_capex", risk_r=Decimal("0.75")),
        ),
        "daily_realized_r": Decimal("-0.5"),
        "weekly_realized_r": Decimal("1.5"),
        "validation_stage": "experimental",
    }
    values.update(overrides)
    return PortfolioState(**values)


def test_portfolio_snapshot_remains_a_compatibility_alias():
    assert PortfolioSnapshot is PortfolioState
    legacy = PortfolioSnapshot(
        account_id=ACCOUNT_ID,
        version=1,
        as_of=NOW,
        policy_version=POLICY.version,
        taxonomy_version=TAXONOMY.version,
        equity=Decimal(100000),
        positions=(),
        pending_orders=(),
        positions_complete=True,
        orders_complete=True,
        ats_managed_exposure_excluded=True,
    )
    assert legacy.run_type is PortfolioRunType.PAPER
    assert legacy.source == "LEGACY_UNSPECIFIED"
    assert not legacy.has_explicit_source_provenance


def test_state_carries_architecture_v11_portfolio_context():
    snapshot = state()
    assert snapshot.run_type is PortfolioRunType.REPLAY
    assert snapshot.cash == Decimal(25000)
    assert snapshot.buying_power == Decimal(50000)
    assert snapshot.open_r == Decimal("1.25")
    assert snapshot.theme_exposure[0].key == "AI_NETWORKING"
    assert snapshot.factor_exposure[0].key == "AI_CAPEX"
    assert snapshot.daily_realized_r == Decimal("-0.5")
    assert snapshot.weekly_realized_r == Decimal("1.5")
    assert snapshot.validation_stage == "EXPERIMENTAL"
    assert snapshot.has_explicit_source_provenance


def test_provenance_rejects_non_utc_and_reverse_chronology():
    with pytest.raises(ValidationError):
        PortfolioValueProvenance(
            source="broker",
            source_version="1",
            observed_at=NOW.replace(tzinfo=None),
            ingested_at=NOW,
            provenance_ref="broker://equity",
            reconciled=True,
        )

    with pytest.raises(ValidationError):
        PortfolioValueProvenance(
            source="broker",
            source_version="1",
            observed_at=NOW,
            ingested_at=NOW - timedelta(seconds=1),
            provenance_ref="broker://equity",
            reconciled=True,
        )


def test_freshness_uses_source_observation_time_when_present():
    snapshot = state(observed_at=NOW - timedelta(minutes=6))
    assert not snapshot.is_fresh(NOW, timedelta(minutes=5))


def test_risk_engine_consumes_portfolio_state_freshness():
    snapshot = state(observed_at=NOW - timedelta(minutes=6))
    result = assess_portfolio_risk(
        snapshot=snapshot,
        candidate=CandidateExposure(
            ticker="ABC",
            requested_quantity=10,
            entry=Decimal(100),
            stop=Decimal(98),
        ),
        reservations=(),
        taxonomy=TAXONOMY,
        policy=POLICY,
        now=NOW,
    )
    assert result.decision is RiskDecision.DENY
    assert result.reason is RiskReason.PORTFOLIO_STATE_STALE
