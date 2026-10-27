from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from pydantic import ValidationError

from src.domain.models import OrderAction, OrderIntent, OrderType
from src.execution import (
    BrokerSubmissionRequest,
    ExecutionService,
    PaperBrokerAdapter,
    SubmissionStatus,
)
from src.run_type import RunType

NOW = datetime(2026, 10, 27, 17, 30, tzinfo=UTC)


def intent(
    *,
    action=OrderAction.BUY,
    order_type=OrderType.MARKET,
    quantity=Decimal("10"),
    limit_price=None,
    stop_price=None,
):
    return OrderIntent(
        plan_id=uuid4(),
        action=action,
        order_type=order_type,
        quantity=quantity,
        limit_price=limit_price,
        stop_price=stop_price,
        created_at=NOW,
    )


def request(*, order_intent, key="case-1", reference_price=Decimal("100")):
    return BrokerSubmissionRequest(
        idempotency_key=key,
        run_type=RunType.PAPER,
        symbol="abc",
        intent=order_intent,
        reference_price=reference_price,
        submitted_at=NOW + timedelta(seconds=1),
    )


def test_execution_service_depends_only_on_gateway_contract():
    expected = object()

    class FakeGateway:
        def __init__(self):
            self.received = None

        def submit(self, req):
            self.received = req
            return expected

    gateway = FakeGateway()
    service = ExecutionService(gateway)
    req = request(order_intent=intent())

    result = service.submit(req)

    assert result is expected
    assert gateway.received is req


def test_submission_is_strictly_paper_only():
    with pytest.raises(ValidationError, match="PAPER-only"):
        BrokerSubmissionRequest(
            idempotency_key="live-attempt",
            run_type=RunType.LIVE,
            symbol="ABC",
            intent=intent(),
            reference_price=Decimal("100"),
            submitted_at=NOW + timedelta(seconds=1),
        )


def test_market_submission_fills_at_reference_price():
    adapter = PaperBrokerAdapter()
    req = request(order_intent=intent(), reference_price=Decimal("101.25"))

    receipt = adapter.submit(req)

    assert receipt.run_type is RunType.PAPER
    assert receipt.status is SubmissionStatus.FILLED
    assert receipt.symbol == "ABC"
    assert receipt.fill is not None
    assert receipt.fill.quantity == Decimal("10")
    assert receipt.fill.price == Decimal("101.25")
    assert receipt.fill.fees == Decimal(0)


def test_unmarketable_limit_is_accepted_without_fill():
    adapter = PaperBrokerAdapter()
    req = request(
        order_intent=intent(
            order_type=OrderType.LIMIT,
            limit_price=Decimal("99"),
        ),
        reference_price=Decimal("100"),
    )

    receipt = adapter.submit(req)

    assert receipt.status is SubmissionStatus.ACCEPTED
    assert receipt.fill is None


def test_marketable_limit_uses_better_reference_price():
    adapter = PaperBrokerAdapter()
    req = request(
        order_intent=intent(
            order_type=OrderType.LIMIT,
            limit_price=Decimal("101"),
        ),
        reference_price=Decimal("100"),
    )

    receipt = adapter.submit(req)

    assert receipt.status is SubmissionStatus.FILLED
    assert receipt.fill is not None
    assert receipt.fill.price == Decimal("100")


def test_stop_waits_until_trigger_then_fills():
    adapter = PaperBrokerAdapter()
    pending = request(
        order_intent=intent(
            order_type=OrderType.STOP,
            stop_price=Decimal("105"),
        ),
        key="stop-pending",
        reference_price=Decimal("100"),
    )
    triggered = request(
        order_intent=intent(
            order_type=OrderType.STOP,
            stop_price=Decimal("105"),
        ),
        key="stop-triggered",
        reference_price=Decimal("106"),
    )

    assert adapter.submit(pending).status is SubmissionStatus.ACCEPTED
    filled = adapter.submit(triggered)
    assert filled.status is SubmissionStatus.FILLED
    assert filled.fill is not None
    assert filled.fill.price == Decimal("106")


def test_idempotent_replay_returns_same_receipt():
    adapter = PaperBrokerAdapter()
    req = request(order_intent=intent(), key="same-key")

    first = adapter.submit(req)
    second = adapter.submit(req)

    assert first == second
    assert first.submission_id == second.submission_id


def test_idempotency_key_reuse_with_different_payload_fails_closed():
    adapter = PaperBrokerAdapter()
    first = request(order_intent=intent(), key="collision")
    second = request(
        order_intent=intent(quantity=Decimal("11")),
        key="collision",
    )

    adapter.submit(first)
    with pytest.raises(ValueError, match="different request"):
        adapter.submit(second)


@pytest.mark.parametrize(
    ("order_type", "limit_price", "stop_price", "message"),
    [
        (OrderType.LIMIT, None, None, "limit_price"),
        (OrderType.STOP, None, None, "stop_price"),
        (OrderType.STOP_LIMIT, Decimal("101"), None, "stop_price"),
        (OrderType.STOP_LIMIT, None, Decimal("99"), "limit_price"),
    ],
)
def test_required_price_instructions_fail_closed(
    order_type,
    limit_price,
    stop_price,
    message,
):
    with pytest.raises(ValidationError, match=message):
        request(
            order_intent=intent(
                order_type=order_type,
                limit_price=limit_price,
                stop_price=stop_price,
            )
        )


def test_submission_cannot_predate_intent():
    order_intent = intent()
    with pytest.raises(ValidationError, match="cannot predate"):
        BrokerSubmissionRequest(
            idempotency_key="bad-time",
            run_type=RunType.PAPER,
            symbol="ABC",
            intent=order_intent,
            reference_price=Decimal("100"),
            submitted_at=NOW - timedelta(seconds=1),
        )
