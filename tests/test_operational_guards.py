from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest

from src.domain.models import OrderAction, OrderIntent, OrderType
from src.execution import BrokerSubmissionRequest
from src.operational_guards import (
    OperationalGuard,
    OperationalGuardContext,
    OperationalGuardPolicy,
    OperationalGuardViolation,
    XnysMarketHours,
)
from src.run_type import RunType

AT = datetime(2026, 10, 27, 15, 0, tzinfo=UTC)


class Hours:
    def __init__(self, is_open: bool):
        self._is_open = is_open

    def is_open(self, at):
        return self._is_open


def request(*, quantity=Decimal(10), price=Decimal(100)):
    intent = OrderIntent(
        plan_id=uuid4(),
        action=OrderAction.BUY,
        order_type=OrderType.MARKET,
        quantity=quantity,
        created_at=AT - timedelta(minutes=1),
    )
    return BrokerSubmissionRequest(
        idempotency_key="guard-case",
        run_type=RunType.PAPER,
        symbol="ABC",
        intent=intent,
        reference_price=price,
        submitted_at=AT,
    )


def policy(**overrides):
    values = {
        "kill_switch_engaged": False,
        "max_order_quantity": Decimal(100),
        "max_order_notional": Decimal(20_000),
        "max_orders_per_day": 10,
        "max_market_data_age": timedelta(seconds=30),
    }
    values.update(overrides)
    return OperationalGuardPolicy(**values)


def context(**overrides):
    values = {
        "evaluated_at": AT,
        "market_data_observed_at": AT - timedelta(seconds=5),
        "orders_submitted_today": 0,
    }
    values.update(overrides)
    return OperationalGuardContext(**values)


def test_passing_operational_controls_allow_paper_request():
    guard = OperationalGuard(policy=policy(), market_hours=Hours(True))

    guard.validate(request=request(), context=context())


def test_kill_switch_fails_closed_before_submission():
    guard = OperationalGuard(
        policy=policy(kill_switch_engaged=True),
        market_hours=Hours(True),
    )

    with pytest.raises(OperationalGuardViolation, match="kill switch"):
        guard.validate(request=request(), context=context())


def test_order_quantity_cap_fails_closed():
    guard = OperationalGuard(policy=policy(), market_hours=Hours(True))

    with pytest.raises(OperationalGuardViolation, match="quantity"):
        guard.validate(
            request=request(quantity=Decimal(101)),
            context=context(),
        )


def test_order_notional_cap_fails_closed():
    guard = OperationalGuard(policy=policy(), market_hours=Hours(True))

    with pytest.raises(OperationalGuardViolation, match="notional"):
        guard.validate(
            request=request(quantity=Decimal(50), price=Decimal(500)),
            context=context(),
        )


def test_daily_order_count_cap_fails_closed():
    guard = OperationalGuard(policy=policy(), market_hours=Hours(True))

    with pytest.raises(OperationalGuardViolation, match="order-count"):
        guard.validate(
            request=request(),
            context=context(orders_submitted_today=10),
        )


def test_closed_market_fails_closed():
    guard = OperationalGuard(policy=policy(), market_hours=Hours(False))

    with pytest.raises(OperationalGuardViolation, match="market is closed"):
        guard.validate(request=request(), context=context())


def test_future_market_data_timestamp_fails_closed():
    guard = OperationalGuard(policy=policy(), market_hours=Hours(True))

    with pytest.raises(OperationalGuardViolation, match="future"):
        guard.validate(
            request=request(),
            context=context(
                market_data_observed_at=AT + timedelta(seconds=1),
            ),
        )


def test_stale_market_data_fails_closed_at_strict_age_boundary():
    guard = OperationalGuard(policy=policy(), market_hours=Hours(True))

    with pytest.raises(OperationalGuardViolation, match="stale"):
        guard.validate(
            request=request(),
            context=context(
                market_data_observed_at=AT - timedelta(seconds=31),
            ),
        )


def test_exact_max_market_data_age_is_allowed():
    guard = OperationalGuard(policy=policy(), market_hours=Hours(True))

    guard.validate(
        request=request(),
        context=context(
            market_data_observed_at=AT - timedelta(seconds=30),
        ),
    )


def test_guard_evaluation_time_must_equal_submission_time():
    guard = OperationalGuard(policy=policy(), market_hours=Hours(True))

    with pytest.raises(OperationalGuardViolation, match="submission time"):
        guard.validate(
            request=request(),
            context=context(evaluated_at=AT + timedelta(seconds=1)),
        )


def test_xnys_calendar_recognizes_open_session_and_weekend():
    hours = XnysMarketHours()

    assert hours.is_open(datetime(2026, 10, 27, 15, 0, tzinfo=UTC))
    assert not hours.is_open(datetime(2026, 10, 31, 15, 0, tzinfo=UTC))
