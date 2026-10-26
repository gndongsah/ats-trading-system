"""Point-in-time market data binding for the explicit simulation harness only."""

from src.domain.models import TradePlan
from src.layer3 import Layer3Reason
from src.market_data import Bar, MarketDataStore, Reference, ReplayUnavailable
from src.persistence.models import TradePlanRecord
from src.simulation_risk import SimulationRiskService


class ReplaySimulationRiskService(SimulationRiskService):
    def __init__(self, *, market_snapshot, requirements, **kwargs):
        super().__init__(**kwargs)
        self.market_snapshot = market_snapshot
        self.requirements = tuple(requirements)

    def authorize(self, session, *, plan_id, idempotency_key):
        self._require_transaction(session)
        now = self._clock()
        try:
            replay = MarketDataStore(session).replay(
                as_of=now, requirements=self.requirements
            )
        except ReplayUnavailable:
            return self._deny(Layer3Reason.MARKET_DATA_UNAVAILABLE)
        # A service instance is bound to one historical instant and input set.
        if replay != self.market_snapshot or self.snapshot.as_of != now:
            return self._deny(Layer3Reason.MARKET_DATA_BASIS_MISMATCH)
        record = session.get(TradePlanRecord, plan_id)
        if record is None:
            return self._deny(Layer3Reason.PLAN_NOT_FOUND)
        plan = TradePlan.model_validate(record.plan_data)
        symbols = (
            {plan.security.ticker}
            | {p.ticker for p in self.snapshot.positions}
            | {p.ticker for p in self.snapshot.pending_orders}
        )
        for symbol in symbols:
            bars = [
                o.event.payload
                for o in replay.observations
                if o.event.symbol == symbol and isinstance(o.event.payload, Bar)
            ]
            references = [
                o.event.payload
                for o in replay.observations
                if o.event.symbol == symbol and isinstance(o.event.payload, Reference)
            ]
            if len(bars) != 1 or len(references) != 1 or not references[0].active:
                return self._deny(Layer3Reason.MARKET_DATA_UNAVAILABLE)
            if symbol == plan.security.ticker and plan.entry != bars[0].close:
                return self._deny(Layer3Reason.MARKET_DATA_BASIS_MISMATCH)
        if plan.planned_at > now or plan.score_snapshot.decided_at > now:
            return self._deny(Layer3Reason.MARKET_DATA_BASIS_MISMATCH)
        return super().authorize(
            session, plan_id=plan_id, idempotency_key=idempotency_key
        )

    def _append_event(self, session, **kwargs):
        if kwargs["event_type"] == "reserved":
            kwargs["event_data"] = kwargs["event_data"] | {
                "market_replay_basis": {
                    "digest": self.market_snapshot.digest,
                    "snapshot": self.market_snapshot.model_dump(mode="json"),
                }
            }
        return super()._append_event(session, **kwargs)
