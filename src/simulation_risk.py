"""F12 explicit simulation integration. Never installed in broker/API runtime."""

from datetime import UTC, timedelta
from decimal import Decimal

from sqlalchemy import select

from src.ats009_accounting import ATS009_POLICY_VERSION
from src.domain.models import TradePlan
from src.layer3 import Layer3AuthorizationService, Layer3Reason
from src.persistence.models import LossStateEventRecord, LossValuationObservationRecord
from src.portfolio import PortfolioRiskPolicy, PortfolioSnapshot
from src.risk import size_long_equity_position
from src.simulation_opening import SimulationCaptureLossProvider


class SimulationRiskService(Layer3AuthorizationService):
    """Harness-owned snapshot and buying power; these are not authenticated facts.

    Each instance is bound to one immutable snapshot. Publish it explicitly using
    publish_snapshot. A new valuation needs a new snapshot/version and instance.
    Opening capture and loss evaluation must already be persisted. All reads,
    sizing and reservation run under the existing account transaction lock.
    """

    def __init__(
        self, *, snapshot, buying_power, calendar, taxonomy, principal_provider, clock
    ):
        if not isinstance(snapshot, PortfolioSnapshot):
            raise TypeError("simulation snapshot is required")
        if (
            not isinstance(buying_power, Decimal)
            or not buying_power.is_finite()
            or buying_power < 0
        ):
            raise ValueError(
                "simulation buying power must be a finite nonnegative Decimal"
            )
        self.snapshot = snapshot
        self.buying_power = buying_power
        super().__init__(
            policy=PortfolioRiskPolicy(
                version=snapshot.policy_version, snapshot_max_age=timedelta(seconds=30)
            ),
            taxonomy=taxonomy,
            loss_locks=SimulationCaptureLossProvider(calendar),
            principal_provider=principal_provider,
            snapshot_verifier=self,
            ats009_policy_version=ATS009_POLICY_VERSION,
            clock=clock,
        )

    def verify_snapshot(self, snapshot, principal):
        return (
            snapshot
            if snapshot == self.snapshot and snapshot.account_id == principal.account_id
            else None
        )

    def authorize(self, session, *, plan_id, idempotency_key):
        self._require_transaction(session)
        principal = self._current_principal()
        if principal is None:
            return self._deny(Layer3Reason.PRINCIPAL_UNAVAILABLE)
        now = self._clock()
        self._require_utc(now)
        self._lock_account(session, principal.account_id)
        state = self._read_loss_state(session, principal.account_id, now)
        if state is None:
            return self._deny(Layer3Reason.ATS009_UNAVAILABLE)
        event = session.scalar(
            select(LossStateEventRecord).where(
                LossStateEventRecord.account_id == principal.account_id,
                LossStateEventRecord.state_version == state.state_version,
            )
        )
        valuation = session.get(
            LossValuationObservationRecord, event.valuation_observation_id
        )
        if (
            self.snapshot.account_id != principal.account_id
            or valuation.net_liquidation != self.snapshot.equity
            or valuation.as_of.astimezone(UTC) != self.snapshot.as_of
            or not timedelta(0) <= now - self.snapshot.as_of <= timedelta(seconds=30)
        ):
            return self._deny(Layer3Reason.SNAPSHOT_VERSION_CONFLICT)
        return super().authorize(
            session, plan_id=plan_id, idempotency_key=idempotency_key
        )

    def _sized_quantity(self, plan: TradePlan, snapshot: PortfolioSnapshot) -> int:
        regime = plan.score_snapshot.regime
        if regime is None:
            return 0
        result = size_long_equity_position(
            equity=snapshot.equity,
            risk_fraction=Decimal("0.005"),
            entry=plan.entry,
            stop=plan.stop,
            available_buying_power=self.buying_power,
            regime=regime,
            side=plan.side,
            instrument_type=plan.security.asset_class,
            leverage=Decimal(1),
            fractional_shares=False,
            special_setup=False,
            explicit_regime_multiplier=None,
        )
        return result.quantity

    def _append_event(self, session, **kwargs):
        if kwargs["event_type"] == "reserved":
            kwargs["event_data"] = kwargs["event_data"] | {
                "simulation_basis": {
                    "source": "SIMULATION-ONLY",
                    "snapshot_version": self.snapshot.version,
                    "as_of": self.snapshot.as_of.isoformat(),
                    "nlv": str(self.snapshot.equity),
                    "buying_power": str(self.buying_power),
                    "risk_fraction": "0.005",
                }
            }
        return super()._append_event(session, **kwargs)
