from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from threading import Barrier
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

from src.database import Base, create_database_engine
from src.domain.models import RiskIntent
from src.layer3 import (
    BROKER_EXECUTION_ENABLED,
    AuthenticatedAccountPrincipal,
    BrokerFillEvent,
    BrokerOrderStatus,
    BrokerOrderUpdate,
    BrokerStopConfirmation,
    ExecutionDisabledError,
    Layer3AuthorizationService,
    Layer3Reason,
    LossLockState,
    VerifiedBrokerOrderUpdate,
    VerifiedBrokerStopConfirmation,
    submit_order_to_broker,
)
from src.persistence.models import RiskReservationEventRecord
from src.persistence.repository import JournalRepository
from src.portfolio import (
    PortfolioRiskPolicy,
    PortfolioSnapshot,
    PositionExposure,
    RiskClassification,
    RiskDecision,
    RiskTaxonomy,
)
from tests.persistence_fixtures import make_complete_trade

NOW = datetime(2026, 10, 16, 12, 0, tzinfo=UTC)
POLICY = PortfolioRiskPolicy(
    version="risk-policy-test-v1",
    snapshot_max_age=timedelta(minutes=5),
)
TAXONOMY = RiskTaxonomy(
    version="taxonomy-test-v1",
    classifications={
        "ABC": RiskClassification(theme="AI_NETWORKING", factors=("AI_CAPEX",)),
        "XYZ": RiskClassification(theme="INDUSTRIALS", factors=("INDUSTRIAL",)),
    },
)


class FixedLossLockProvider:
    def __init__(self, state: LossLockState | None):
        self.state = state

    def evaluate(self, account_id: UUID, at: datetime) -> LossLockState | None:
        if self.state is not None:
            assert at == self.state.as_of
        if self.state is not None:
            assert self.state.account_id == account_id
        return self.state


class FixedPrincipalProvider:
    def __init__(self, account_id: UUID):
        self.principal = AuthenticatedAccountPrincipal(
            account_id=account_id,
            subject="test-user",
            authorization_id="test-authz",
        )

    def current_principal(self):
        return self.principal


class _TestSnapshotVerifier:
    def __init__(self, *, authenticated=True):
        self.authenticated = authenticated

    def verify_snapshot(self, snapshot, principal):
        if (
            not self.authenticated
            or snapshot.account_id != principal.account_id
            or not snapshot.positions_complete
            or not snapshot.orders_complete
            or not snapshot.ats_managed_exposure_excluded
        ):
            return None
        return snapshot


class _TestBrokerEventVerifier:
    def __init__(self, *, authenticated=True):
        self.authenticated = authenticated

    def verify_order_update(self, event, principal):
        if not self.authenticated or event.account_id != principal.account_id:
            return None
        if not event.event_id.startswith("trusted:"):
            return None
        return VerifiedBrokerOrderUpdate(
            event=event, verification_id="test-broker-verification"
        )

    def verify_stop_confirmation(self, event, principal):
        if not self.authenticated or event.account_id != principal.account_id:
            return None
        if not event.event_id.startswith("trusted:"):
            return None
        return VerifiedBrokerStopConfirmation(
            event=event, verification_id="test-stop-verification"
        )


def loss_state(account_id: UUID, **overrides) -> LossLockState:
    values = {
        "account_id": account_id,
        "as_of": NOW,
        "policy_version": "ats-009-test-v1",
        "session_id": "2026-10-16",
        "week_id": "2026-W42",
        "daily_loss_r": Decimal(0),
        "weekly_loss_r": Decimal(0),
        "daily_limit_r": Decimal(-3),
        "weekly_limit_r": Decimal(-6),
    }
    values.update(overrides)
    return LossLockState(**values)


def portfolio_snapshot(account_id: UUID) -> PortfolioSnapshot:
    return PortfolioSnapshot(
        account_id=account_id,
        version=1,
        as_of=NOW,
        policy_version=POLICY.version,
        taxonomy_version=TAXONOMY.version,
        equity=Decimal(100000),
        positions=(
            PositionExposure(
                ticker="XYZ",
                quantity=950,
                average_entry=Decimal(10),
                active_stop=Decimal(8),
                stop_confirmed=True,
            ),
        ),
        pending_orders=(),
        positions_complete=True,
        orders_complete=True,
        ats_managed_exposure_excluded=True,
    )


def create_service_and_db(
    tmp_path: Path,
    loss_state_override=None,
    *,
    snapshot_authenticated=True,
    broker_authenticated=True,
):
    database_path = (tmp_path / "layer3.db").as_posix()
    engine = create_database_engine(f"sqlite:///{database_path}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    account_id = uuid4()
    state = (
        loss_state(account_id, **loss_state_override)
        if loss_state_override is not None
        else loss_state(account_id)
    )
    service = Layer3AuthorizationService(
        policy=POLICY,
        taxonomy=TAXONOMY,
        loss_locks=FixedLossLockProvider(state),
        principal_provider=FixedPrincipalProvider(account_id),
        snapshot_verifier=_TestSnapshotVerifier(authenticated=snapshot_authenticated),
        broker_event_verifier=_TestBrokerEventVerifier(
            authenticated=broker_authenticated
        ),
        ats009_policy_version="ats-009-test-v1",
        clock=lambda: NOW,
    )
    entry = make_complete_trade()
    plan = entry.plan.model_copy(
        update={
            "account_id": account_id,
            "quantity": Decimal(100),
            "risk_intent": RiskIntent(stop_risk_budget=Decimal(250)),
        }
    )
    entry = entry.model_copy(update={"plan": plan})
    with factory() as session, session.begin():
        service.publish_snapshot(session, portfolio_snapshot(account_id))
        JournalRepository(session).save(entry)
    return engine, factory, service, account_id, plan.id


def save_second_plan(factory, account_id):
    entry = make_complete_trade()
    plan = entry.plan.model_copy(
        update={
            "account_id": account_id,
            "quantity": Decimal(100),
            "risk_intent": RiskIntent(stop_risk_budget=Decimal(250)),
        }
    )
    entry = entry.model_copy(update={"plan": plan})
    with factory() as session, session.begin():
        JournalRepository(session).save(entry)
    return plan.id


def authorize(factory, service, account_id, plan_id, key):
    principal = service._current_principal()
    if principal is not None:
        assert principal.account_id == account_id
    with factory() as session, session.begin():
        return service.authorize(
            session,
            plan_id=plan_id,
            idempotency_key=key,
        )


def process_order_event(factory, service, reservation_id, event):
    with factory() as session, session.begin():
        return service.process_broker_order_update(
            session, reservation_id=reservation_id, event=event
        )


def process_stop_event(factory, service, reservation_id, event):
    with factory() as session, session.begin():
        return service.confirm_filled_stops(
            session, reservation_id=reservation_id, event=event
        )


def get_reservation(factory, reservation_id):
    with factory() as session:
        row = session.scalar(
            select(RiskReservationEventRecord)
            .where(RiskReservationEventRecord.reservation_id == reservation_id)
            .order_by(RiskReservationEventRecord.sequence.desc())
            .limit(1)
        )
    return row.event_data["reservation"]


def order_update(
    account_id,
    reservation,
    *,
    event_id,
    sequence,
    status=BrokerOrderStatus.ACTIVE,
    fills=(),
):
    return BrokerOrderUpdate(
        event_id=event_id,
        account_id=account_id,
        order_attempt_id=UUID(reservation["order_attempt_id"]),
        broker_order_id="broker-entry-1",
        sequence=sequence,
        status=status,
        authorized_quantity=reservation["requested_quantity"],
        cumulative_filled_quantity=sum(fill.quantity for fill in fills),
        fills=fills,
    )


def test_layer3_reserves_a_bound_quantity_and_is_idempotent(tmp_path):
    engine, factory, service, account_id, plan_id = create_service_and_db(tmp_path)
    try:
        first = authorize(factory, service, account_id, plan_id, "request-1")
        retry = authorize(factory, service, account_id, plan_id, "request-1")
        assert first.decision is RiskDecision.RESIZE
        assert first.quantity == 40
        assert first.reservation_id is not None
        assert first.execution_enabled is False
        assert retry == first.model_copy(update={"replayed": True})
        with factory() as session:
            count = session.scalar(
                select(func.count()).select_from(RiskReservationEventRecord)
            )
        assert count == 1
    finally:
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_new_idempotency_key_cannot_reauthorize_same_plan_after_cancel(tmp_path):
    engine, factory, service, account_id, plan_id = create_service_and_db(tmp_path)
    try:
        first = authorize(factory, service, account_id, plan_id, "request-1")
        reservation = get_reservation(factory, first.reservation_id)
        cancelled = order_update(
            account_id,
            reservation,
            event_id="trusted:terminal-cancel",
            sequence=1,
            status=BrokerOrderStatus.CANCELLED,
        )
        process_order_event(factory, service, first.reservation_id, cancelled)

        retry = authorize(factory, service, account_id, plan_id, "request-1")
        replay = authorize(factory, service, account_id, plan_id, "request-2")

        assert retry == first.model_copy(update={"replayed": True})
        assert replay.decision is RiskDecision.DENY
        assert replay.reason == Layer3Reason.PLAN_ALREADY_RESERVED
        with factory() as session:
            count = session.scalar(
                select(func.count())
                .select_from(RiskReservationEventRecord)
                .where(RiskReservationEventRecord.event_type == "reserved")
            )
        assert count == 1
    finally:
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_active_order_does_not_release_and_cancel_requires_reconciled_final_fills(
    tmp_path,
):
    engine, factory, service, account_id, plan_id = create_service_and_db(tmp_path)
    try:
        decision = authorize(factory, service, account_id, plan_id, "request-1")
        reservation = get_reservation(factory, decision.reservation_id)
        active = order_update(
            account_id,
            reservation,
            event_id="trusted:active",
            sequence=1,
            status=BrokerOrderStatus.ACTIVE,
        )
        active_state = process_order_event(
            factory, service, decision.reservation_id, active
        )
        assert active_state.remaining_quantity == decision.quantity

        fills = (
            BrokerFillEvent(fill_id="fill-1", quantity=15, price=Decimal("100.5")),
        )
        inconsistent_cancel = order_update(
            account_id,
            reservation,
            event_id="trusted:bad-cancel",
            sequence=2,
            status=BrokerOrderStatus.CANCELLED,
            fills=fills,
        ).model_copy(update={"cumulative_filled_quantity": 14})
        with pytest.raises(ValueError, match="does not reconcile"):
            process_order_event(
                factory, service, decision.reservation_id, inconsistent_cancel
            )

        cancelled = order_update(
            account_id,
            reservation,
            event_id="trusted:good-cancel",
            sequence=2,
            status=BrokerOrderStatus.CANCELLED,
            fills=fills,
        )
        state = process_order_event(
            factory, service, decision.reservation_id, cancelled
        )
        assert state.remaining_quantity == 0
        assert sum(fill.quantity for fill in state.fills) == 15
        assert state.fills[0].price == Decimal("100.5")
    finally:
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_broker_update_cannot_change_approved_quantity_or_prior_fills(tmp_path):
    engine, factory, service, account_id, plan_id = create_service_and_db(tmp_path)
    try:
        decision = authorize(factory, service, account_id, plan_id, "request-1")
        reservation = get_reservation(factory, decision.reservation_id)
        altered = order_update(
            account_id,
            reservation,
            event_id="trusted:altered-quantity",
            sequence=1,
        ).model_copy(update={"authorized_quantity": decision.quantity - 1})
        with pytest.raises(ValueError, match="approved quantity"):
            process_order_event(factory, service, decision.reservation_id, altered)

        initial_fill = BrokerFillEvent(
            fill_id="fill-1", quantity=10, price=Decimal(100)
        )
        initial = order_update(
            account_id,
            reservation,
            event_id="trusted:first-fill",
            sequence=1,
            fills=(initial_fill,),
        )
        process_order_event(factory, service, decision.reservation_id, initial)
        changed_fill = BrokerFillEvent(
            fill_id="fill-1", quantity=10, price=Decimal(101)
        )
        altered_history = order_update(
            account_id,
            reservation,
            event_id="trusted:changed-fill",
            sequence=2,
            fills=(changed_fill,),
        )
        with pytest.raises(ValueError, match="altered a previously recorded fill"):
            process_order_event(
                factory, service, decision.reservation_id, altered_history
            )
    finally:
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_reordered_broker_updates_cannot_rewind_cumulative_fills(tmp_path):
    engine, factory, service, account_id, plan_id = create_service_and_db(tmp_path)
    try:
        decision = authorize(factory, service, account_id, plan_id, "request-1")
        reservation = get_reservation(factory, decision.reservation_id)
        early = order_update(
            account_id,
            reservation,
            event_id="trusted:sequence-1",
            sequence=1,
            fills=(),
        )
        later_fill = BrokerFillEvent(fill_id="fill-1", quantity=10, price=Decimal(100))
        later = order_update(
            account_id,
            reservation,
            event_id="trusted:sequence-2",
            sequence=2,
            fills=(later_fill,),
        )
        state = process_order_event(factory, service, decision.reservation_id, later)
        assert state.remaining_quantity == 30
        with pytest.raises(ValueError, match="stale or reordered"):
            process_order_event(factory, service, decision.reservation_id, early)
        assert (
            get_reservation(factory, decision.reservation_id)["remaining_quantity"]
            == 30
        )
    finally:
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_missing_principal_and_unauthenticated_snapshots_fail_closed(tmp_path):
    engine, factory, service, account_id, plan_id = create_service_and_db(tmp_path)
    try:
        service._principal_provider = None
        decision = authorize(factory, service, account_id, plan_id, "no-principal")
        assert decision.decision is RiskDecision.DENY
        assert decision.reason == Layer3Reason.PRINCIPAL_UNAVAILABLE

        service._principal_provider = FixedPrincipalProvider(account_id)
        service._snapshot_verifier = _TestSnapshotVerifier(authenticated=False)
        with (
            factory() as session,
            session.begin(),
            pytest.raises(PermissionError, match="authentication failed"),
        ):
            service.publish_snapshot(session, portfolio_snapshot(account_id))
        service._snapshot_verifier = _TestSnapshotVerifier()
        incomplete_snapshot = portfolio_snapshot(account_id).model_copy(
            update={"orders_complete": False}
        )
        with (
            factory() as session,
            session.begin(),
            pytest.raises(PermissionError, match="authentication failed"),
        ):
            service.publish_snapshot(session, incomplete_snapshot)
        service._snapshot_verifier = _TestSnapshotVerifier(authenticated=False)
        decision = authorize(
            factory, service, account_id, plan_id, "untrusted-snapshot"
        )
        assert decision.decision is RiskDecision.DENY
        assert decision.reason == Layer3Reason.SNAPSHOT_SOURCE_UNAVAILABLE
    finally:
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_missing_or_rejected_broker_adapter_cannot_mutate_reservation(tmp_path):
    engine, factory, service, account_id, plan_id = create_service_and_db(
        tmp_path, broker_authenticated=False
    )
    try:
        decision = authorize(factory, service, account_id, plan_id, "request-1")
        reservation = get_reservation(factory, decision.reservation_id)
        forged_fill = order_update(
            account_id,
            reservation,
            event_id="trusted:spoofed-but-rejected",
            sequence=1,
            fills=(
                BrokerFillEvent(fill_id="forged-fill", quantity=1, price=Decimal(100)),
            ),
        )
        with pytest.raises(PermissionError, match="authentication failed"):
            process_order_event(factory, service, decision.reservation_id, forged_fill)

        service._broker_event_verifier = None
        with pytest.raises(PermissionError, match="source is unavailable"):
            process_order_event(factory, service, decision.reservation_id, forged_fill)
        unchanged = get_reservation(factory, decision.reservation_id)
        assert unchanged["remaining_quantity"] == decision.quantity
        assert unchanged["fills"] == []
    finally:
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_stale_trusted_snapshot_denies_authorization(tmp_path):
    engine, factory, _service, account_id, plan_id = create_service_and_db(tmp_path)
    try:
        stale_time = NOW + timedelta(minutes=6)
        stale_service = Layer3AuthorizationService(
            policy=POLICY,
            taxonomy=TAXONOMY,
            loss_locks=FixedLossLockProvider(loss_state(account_id, as_of=stale_time)),
            principal_provider=FixedPrincipalProvider(account_id),
            snapshot_verifier=_TestSnapshotVerifier(),
            broker_event_verifier=_TestBrokerEventVerifier(),
            ats009_policy_version="ats-009-test-v1",
            clock=lambda: stale_time,
        )
        result = authorize(factory, stale_service, account_id, plan_id, "stale")
        assert result.decision is RiskDecision.DENY
        assert result.reason == "PORTFOLIO_STATE_STALE"
    finally:
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_idempotent_retry_cannot_reuse_reservation_after_snapshot_changes(tmp_path):
    engine, factory, service, account_id, plan_id = create_service_and_db(tmp_path)
    try:
        decision = authorize(factory, service, account_id, plan_id, "request-1")
        assert decision.quantity == 40
        newer_snapshot = portfolio_snapshot(account_id).model_copy(
            update={"version": 2, "as_of": NOW + timedelta(seconds=1)}
        )
        service_after_snapshot = Layer3AuthorizationService(
            policy=POLICY,
            taxonomy=TAXONOMY,
            loss_locks=FixedLossLockProvider(
                loss_state(account_id, as_of=NOW + timedelta(seconds=1))
            ),
            principal_provider=FixedPrincipalProvider(account_id),
            snapshot_verifier=_TestSnapshotVerifier(),
            broker_event_verifier=_TestBrokerEventVerifier(),
            ats009_policy_version="ats-009-test-v1",
            clock=lambda: NOW + timedelta(seconds=1),
        )
        with factory() as session, session.begin():
            service_after_snapshot.publish_snapshot(session, newer_snapshot)
        retry = authorize(factory, service, account_id, plan_id, "request-1")
        assert retry.decision is RiskDecision.DENY
        assert retry.reason == Layer3Reason.SNAPSHOT_VERSION_CONFLICT
    finally:
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_layer3_rejects_missing_or_locked_ats009_state(tmp_path):
    engine, factory, service, account_id, plan_id = create_service_and_db(
        tmp_path, {"weekly_limit_r": None}
    )
    try:
        unresolved = authorize(factory, service, account_id, plan_id, "unresolved")
        assert unresolved.decision is RiskDecision.DENY
        assert unresolved.reason == Layer3Reason.ATS009_POLICY_UNRESOLVED

        locked_service = Layer3AuthorizationService(
            policy=POLICY,
            taxonomy=TAXONOMY,
            loss_locks=FixedLossLockProvider(
                loss_state(account_id, daily_loss_r=Decimal(-3))
            ),
            principal_provider=FixedPrincipalProvider(account_id),
            snapshot_verifier=_TestSnapshotVerifier(),
            broker_event_verifier=_TestBrokerEventVerifier(),
            ats009_policy_version="ats-009-test-v1",
            clock=lambda: NOW,
        )
        locked = authorize(factory, locked_service, account_id, plan_id, "locked")
        assert locked.decision is RiskDecision.DENY
        assert locked.reason == Layer3Reason.ATS009_LOSS_LOCKED

        unavailable_service = Layer3AuthorizationService(
            policy=POLICY,
            taxonomy=TAXONOMY,
            loss_locks=None,
            principal_provider=FixedPrincipalProvider(account_id),
            snapshot_verifier=_TestSnapshotVerifier(),
            broker_event_verifier=_TestBrokerEventVerifier(),
            ats009_policy_version="ats-009-test-v1",
            clock=lambda: NOW,
        )
        unavailable = authorize(
            factory, unavailable_service, account_id, plan_id, "no-ats009"
        )
        assert unavailable.reason == Layer3Reason.ATS009_UNAVAILABLE
        unversioned_service = Layer3AuthorizationService(
            policy=POLICY,
            taxonomy=TAXONOMY,
            loss_locks=FixedLossLockProvider(loss_state(account_id)),
            principal_provider=FixedPrincipalProvider(account_id),
            snapshot_verifier=_TestSnapshotVerifier(),
            broker_event_verifier=_TestBrokerEventVerifier(),
            clock=lambda: NOW,
        )
        unversioned = authorize(
            factory, unversioned_service, account_id, plan_id, "unversioned-ats009"
        )
        assert unversioned.reason == Layer3Reason.ATS009_POLICY_UNRESOLVED
        with factory() as session:
            count = session.scalar(
                select(func.count()).select_from(RiskReservationEventRecord)
            )
        assert count == 0
    finally:
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_partial_fill_and_confirmed_cancel_preserve_filled_exposure(tmp_path):
    engine, factory, service, account_id, plan_id = create_service_and_db(tmp_path)
    try:
        decision = authorize(factory, service, account_id, plan_id, "request-1")
        assert decision.reservation_id is not None
        reservation = get_reservation(factory, decision.reservation_id)
        forged_cancel = order_update(
            account_id,
            reservation,
            event_id="forged:cancel",
            sequence=1,
            status=BrokerOrderStatus.CANCELLED,
        )
        with pytest.raises(PermissionError, match="authentication failed"):
            process_order_event(
                factory, service, decision.reservation_id, forged_cancel
            )

        active = order_update(
            account_id,
            reservation,
            event_id="trusted:active",
            sequence=1,
        )
        partial_fill = BrokerFillEvent(
            fill_id="fill-1", quantity=30, price=Decimal("100.5")
        )
        partial_update = order_update(
            account_id,
            reservation,
            event_id="trusted:partial",
            sequence=2,
            fills=(partial_fill,),
        )
        process_order_event(factory, service, decision.reservation_id, active)
        partial = process_order_event(
            factory, service, decision.reservation_id, partial_update
        )
        assert partial.remaining_quantity == 10
        assert sum(fill.quantity for fill in partial.fills) == 30
        next_plan = save_second_plan(factory, account_id)
        missing_stop = authorize(
            factory, service, account_id, next_plan, "request-after-fill"
        )
        assert missing_stop.decision is RiskDecision.DENY
        assert missing_stop.reason == "STOP_NOT_CONFIRMED"

        forged_stop = BrokerStopConfirmation(
            event_id="forged:stop",
            account_id=account_id,
            order_attempt_id=UUID(reservation["order_attempt_id"]),
            broker_order_id="broker-stop-1",
            sequence=3,
            covered_fill_ids=("fill-1",),
            active_stop=Decimal(98),
        )
        with pytest.raises(PermissionError, match="authentication failed"):
            process_stop_event(factory, service, decision.reservation_id, forged_stop)

        confirmed_stop = forged_stop.model_copy(update={"event_id": "trusted:stop"})
        confirmed = process_stop_event(
            factory, service, decision.reservation_id, confirmed_stop
        )
        assert all(fill.active_stop == Decimal(98) for fill in confirmed.fills)
        loosened_stop = confirmed_stop.model_copy(
            update={
                "event_id": "trusted:stop-loosen",
                "sequence": 4,
                "active_stop": Decimal(97),
            }
        )
        with pytest.raises(ValueError, match="stop loosening"):
            process_stop_event(factory, service, decision.reservation_id, loosened_stop)

        canceled = order_update(
            account_id,
            reservation,
            event_id="trusted:cancelled",
            sequence=5,
            status=BrokerOrderStatus.CANCELLED,
            fills=(partial_fill,),
        )
        released = process_order_event(
            factory, service, decision.reservation_id, canceled
        )
        assert released.remaining_quantity == 0
        assert sum(fill.quantity for fill in released.fills) == 30

        next_decision = authorize(factory, service, account_id, next_plan, "request-2")
        assert next_decision.decision is RiskDecision.RESIZE
        assert next_decision.quantity == 10
    finally:
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_partial_fills_use_execution_prices_and_replay_cannot_double_count(tmp_path):
    engine, factory, service, account_id, plan_id = create_service_and_db(tmp_path)
    try:
        decision = authorize(factory, service, account_id, plan_id, "request-1")
        assert decision.reservation_id is not None
        reservation = get_reservation(factory, decision.reservation_id)
        fill = BrokerFillEvent(fill_id="fill-1", quantity=30, price=Decimal(101))
        event = order_update(
            account_id,
            reservation,
            event_id="trusted:partial-fill",
            sequence=1,
            fills=(fill,),
        )
        partial = process_order_event(factory, service, decision.reservation_id, event)
        replay = process_order_event(factory, service, decision.reservation_id, event)
        assert replay == partial
        next_plan = save_second_plan(factory, account_id)
        next_decision = authorize(factory, service, account_id, next_plan, "request-2")
        assert next_decision.decision is RiskDecision.DENY
        assert next_decision.reason == "STOP_NOT_CONFIRMED"
    finally:
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_concurrent_requests_cannot_overreserve_the_same_account(tmp_path):
    engine, factory, service, account_id, first_plan = create_service_and_db(tmp_path)
    try:
        second_plan = save_second_plan(factory, account_id)
        barrier = Barrier(2)

        def attempt(plan_id, key):
            with factory() as session:
                barrier.wait(timeout=10)
                with session.begin():
                    return service.authorize(
                        session,
                        plan_id=plan_id,
                        idempotency_key=key,
                    )

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = (
                executor.submit(attempt, first_plan, "concurrent-1"),
                executor.submit(attempt, second_plan, "concurrent-2"),
            )
            results = tuple(future.result(timeout=20) for future in futures)

        positive = [result for result in results if result.quantity > 0]
        denied = [result for result in results if result.decision is RiskDecision.DENY]
        assert len(positive) == 1
        assert positive[0].quantity == 40
        assert len(denied) == 1
        with factory() as session:
            count = session.scalar(
                select(func.count()).select_from(RiskReservationEventRecord)
            )
        assert count == 1
    finally:
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_concurrent_partial_fill_and_cancel_preserve_final_cumulative_exposure(
    tmp_path,
):
    engine, factory, service, account_id, plan_id = create_service_and_db(tmp_path)
    try:
        decision = authorize(factory, service, account_id, plan_id, "request-1")
        reservation = get_reservation(factory, decision.reservation_id)
        first_fill = BrokerFillEvent(fill_id="fill-1", quantity=10, price=Decimal(100))
        final_fills = (
            first_fill,
            BrokerFillEvent(fill_id="fill-2", quantity=5, price=Decimal("100.5")),
        )
        partial = order_update(
            account_id,
            reservation,
            event_id="trusted:partial-race",
            sequence=1,
            fills=(first_fill,),
        )
        cancelled = order_update(
            account_id,
            reservation,
            event_id="trusted:cancel-race",
            sequence=2,
            status=BrokerOrderStatus.CANCELLED,
            fills=final_fills,
        )
        barrier = Barrier(2)

        def apply(event):
            with factory() as session:
                barrier.wait(timeout=10)
                with session.begin():
                    return service.process_broker_order_update(
                        session,
                        reservation_id=decision.reservation_id,
                        event=event,
                    )

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = (
                executor.submit(apply, partial),
                executor.submit(apply, cancelled),
            )
            for future in futures:
                try:
                    future.result(timeout=20)
                except ValueError as error:
                    assert "terminal event" in str(error) or "reordered" in str(error)

        final_state = get_reservation(factory, decision.reservation_id)
        assert final_state["order_status"] == "CANCELLED"
        assert final_state["remaining_quantity"] == 0
        assert sum(fill["quantity"] for fill in final_state["fills"]) == 15
        assert final_state["broker_event_sequence"] == 2
    finally:
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_plan_account_binding_and_broker_execution_remain_disabled(tmp_path):
    engine, factory, service, _account_id, plan_id = create_service_and_db(tmp_path)
    try:
        service._principal_provider = FixedPrincipalProvider(uuid4())
        with factory() as session, session.begin():
            result = service.authorize(
                session, plan_id=plan_id, idempotency_key="wrong"
            )
        assert result.reason == Layer3Reason.PLAN_ACCOUNT_MISMATCH
        assert BROKER_EXECUTION_ENABLED is False
        with pytest.raises(
            ExecutionDisabledError, match="broker execution is disabled"
        ):
            submit_order_to_broker(plan_id=plan_id)
    finally:
        Base.metadata.drop_all(engine)
        engine.dispose()
