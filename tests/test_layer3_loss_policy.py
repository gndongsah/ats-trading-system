from decimal import Decimal
from uuid import UUID

import pytest
from sqlalchemy import func, select

from src.database import Base
from src.layer3 import (
    Layer3AuthorizationService,
    Layer3Reason,
    LossLockState,
    TransactionalLossLockProvider,
)
from src.persistence.models import RiskReservationEventRecord
from src.portfolio import RiskDecision
from tests.test_layer3 import (
    NOW,
    POLICY,
    TAXONOMY,
    FixedPrincipalProvider,
    _TestBrokerEventVerifier,
    _TestSnapshotVerifier,
    authorize,
    create_service_and_db,
    loss_state,
)


@pytest.mark.parametrize("weekly", ["-5", "-7", "-8", "-6.0001", "-5.9999"])
def test_weekly_limit_other_than_minus_six_is_rejected(weekly):
    state = loss_state(UUID(int=1), weekly_limit_r=Decimal(weekly))
    with pytest.raises(ValueError, match="-6R"):
        state.blocks_risk_increase()


@pytest.mark.parametrize("daily", ["-2", "-4", "-3.0001", "-2.9999"])
def test_daily_limit_other_than_minus_three_is_rejected(daily):
    state = loss_state(UUID(int=1), daily_limit_r=Decimal(daily))
    with pytest.raises(ValueError, match="-3R"):
        state.blocks_risk_increase()


@pytest.mark.parametrize(
    ("daily", "weekly", "blocked"),
    [
        ("-2.9999", "-5.9999", False),
        ("-3", "0", True),
        ("0", "-6", True),
        ("-3.0001", "0", True),
        ("0", "-6.0001", True),
    ],
)
def test_inclusive_boundaries(daily, weekly, blocked):
    state = loss_state(
        UUID(int=1), daily_loss_r=Decimal(daily), weekly_loss_r=Decimal(weekly)
    )
    assert state.blocks_risk_increase() is blocked


def test_sticky_flag_blocks_after_nlv_recovery():
    for field in ("daily_locked", "weekly_locked"):
        recovered = loss_state(
            UUID(int=1), state_version=2, daily_locked=False, weekly_locked=False
        ).model_copy(update={field: True})
        assert recovered.daily_loss_r == 0 and recovered.weekly_loss_r == 0
        assert recovered.blocks_risk_increase() is True


class VersionedProvider:
    """Synthetic transactional provider; its output is not authenticated data."""

    def __init__(self, account_id: UUID):
        self.account_id = account_id
        self.version = 1
        self.policy = "ats-009-test-v1"
        self.daily_locked = False
        self.omit_version = False

    def evaluate(self, account_id, at):
        return None

    def evaluate_in_transaction(self, session, account_id, at):
        return loss_state(
            self.account_id,
            state_version=None if self.omit_version else self.version,
            policy_version=self.policy,
            daily_locked=self.daily_locked,
            weekly_locked=False,
        )


def _service(account_id, provider):
    return Layer3AuthorizationService(
        policy=POLICY,
        taxonomy=TAXONOMY,
        loss_locks=provider,
        principal_provider=FixedPrincipalProvider(account_id),
        snapshot_verifier=_TestSnapshotVerifier(),
        broker_event_verifier=_TestBrokerEventVerifier(),
        ats009_policy_version="ats-009-test-v1",
        clock=lambda: NOW,
    )


def test_provider_protocol_detection():
    assert isinstance(VersionedProvider(UUID(int=1)), TransactionalLossLockProvider)


def test_reservation_binds_loss_state_and_stale_replay_is_denied(tmp_path):
    engine, factory, _, account_id, plan_id = create_service_and_db(tmp_path)
    try:
        provider = VersionedProvider(account_id)
        service = _service(account_id, provider)
        first = authorize(factory, service, account_id, plan_id, "k1")
        assert first.decision is not RiskDecision.DENY
        assert first.reservation_id is not None
        with factory() as session:
            event = session.scalar(select(RiskReservationEventRecord))
            assert event.event_data["loss_state"] == {
                "version": 1,
                "policy_version": "ats-009-test-v1",
            }
        assert authorize(
            factory, service, account_id, plan_id, "k1"
        ) == first.model_copy(update={"replayed": True})

        provider.version = 2
        stale = authorize(factory, service, account_id, plan_id, "k1")
        assert stale.decision is RiskDecision.DENY
        assert stale.reason == Layer3Reason.LOSS_STATE_VERSION_CONFLICT

        provider.version = 1
        provider.policy = "ats-009-other"
        changed = authorize(factory, service, account_id, plan_id, "k1")
        assert changed.reason == Layer3Reason.LOSS_STATE_VERSION_CONFLICT
    finally:
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_transactional_provider_without_version_or_sticky_flags_is_denied(tmp_path):
    engine, factory, _, account_id, plan_id = create_service_and_db(tmp_path)
    try:
        provider = VersionedProvider(account_id)
        provider.omit_version = True
        denied = authorize(
            factory, _service(account_id, provider), account_id, plan_id, "nv"
        )
        assert denied.reason == Layer3Reason.ATS009_POLICY_UNRESOLVED

        provider.omit_version = False
        provider.daily_locked = True
        locked = authorize(
            factory, _service(account_id, provider), account_id, plan_id, "lk"
        )
        assert locked.reason == Layer3Reason.ATS009_LOSS_LOCKED
        with factory() as session:
            count = session.scalar(
                select(func.count()).select_from(RiskReservationEventRecord)
            )
        assert count == 0
    finally:
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_loss_state_rejects_invalid_version():
    with pytest.raises(ValueError):
        LossLockState(account_id=UUID(int=1), as_of=NOW, state_version=0)
