"""ATS-022 broker-neutral execution boundary and deterministic paper adapter."""

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Protocol
from uuid import NAMESPACE_URL, UUID, uuid5

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from src.domain.models import OrderAction, OrderIntent, OrderType, _require_utc
from src.run_type import RunType


class SubmissionStatus(StrEnum):
    ACCEPTED = "ACCEPTED"
    FILLED = "FILLED"


class BrokerFill(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    quantity: Decimal = Field(gt=0)
    price: Decimal = Field(gt=0)
    fees: Decimal = Field(default=Decimal(0), ge=0)
    filled_at: datetime

    @field_validator("filled_at")
    @classmethod
    def filled_at_is_utc(cls, value: datetime) -> datetime:
        return _require_utc(value)


class BrokerSubmissionRequest(BaseModel):
    """Broker-neutral request. ATS-022 permits PAPER submissions only."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    idempotency_key: str = Field(min_length=1, max_length=120)
    run_type: RunType = RunType.PAPER
    symbol: str = Field(min_length=1, max_length=32)
    intent: OrderIntent
    reference_price: Decimal = Field(gt=0)
    submitted_at: datetime

    @field_validator("idempotency_key")
    @classmethod
    def idempotency_key_is_not_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("idempotency_key must not be blank")
        return value

    @field_validator("symbol")
    @classmethod
    def normalize_symbol(cls, value: str) -> str:
        value = value.strip().upper()
        if not value:
            raise ValueError("symbol must not be blank")
        return value

    @field_validator("submitted_at")
    @classmethod
    def submitted_at_is_utc(cls, value: datetime) -> datetime:
        return _require_utc(value)

    @model_validator(mode="after")
    def paper_only_and_time_ordered(self) -> "BrokerSubmissionRequest":
        if self.run_type is not RunType.PAPER:
            raise ValueError("ATS-022 broker submissions are PAPER-only")
        if self.submitted_at < self.intent.created_at:
            raise ValueError("submission cannot predate the order intent")
        if (
            self.intent.order_type in {OrderType.LIMIT, OrderType.STOP_LIMIT}
            and self.intent.limit_price is None
        ):
            raise ValueError("limit and stop-limit intents require limit_price")
        if (
            self.intent.order_type in {OrderType.STOP, OrderType.STOP_LIMIT}
            and self.intent.stop_price is None
        ):
            raise ValueError("stop and stop-limit intents require stop_price")
        return self


class BrokerSubmissionReceipt(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    submission_id: UUID
    idempotency_key: str
    run_type: RunType
    symbol: str
    intent_id: UUID
    adapter: str
    status: SubmissionStatus
    submitted_at: datetime
    fill: BrokerFill | None = None

    @field_validator("submitted_at")
    @classmethod
    def submitted_at_is_utc(cls, value: datetime) -> datetime:
        return _require_utc(value)

    @model_validator(mode="after")
    def fill_matches_status(self) -> "BrokerSubmissionReceipt":
        if self.status is SubmissionStatus.FILLED and self.fill is None:
            raise ValueError("filled submission requires a fill")
        if self.status is SubmissionStatus.ACCEPTED and self.fill is not None:
            raise ValueError("accepted submission cannot already contain a fill")
        if self.run_type is not RunType.PAPER:
            raise ValueError("ATS-022 receipts must remain PAPER")
        return self


class BrokerGateway(Protocol):
    """Replaceable execution boundary used by core application code."""

    def submit(self, request: BrokerSubmissionRequest) -> BrokerSubmissionReceipt: ...


class ExecutionService:
    """Core service depends only on the broker-neutral gateway."""

    def __init__(self, gateway: BrokerGateway) -> None:
        self._gateway = gateway

    def submit(self, request: BrokerSubmissionRequest) -> BrokerSubmissionReceipt:
        return self._gateway.submit(request)


class PaperBrokerAdapter:
    """Deterministic in-memory PAPER adapter. No credentials or network access."""

    adapter_name = "paper"

    def __init__(self) -> None:
        self._requests: dict[str, BrokerSubmissionRequest] = {}
        self._receipts: dict[str, BrokerSubmissionReceipt] = {}

    def submit(self, request: BrokerSubmissionRequest) -> BrokerSubmissionReceipt:
        existing_request = self._requests.get(request.idempotency_key)
        if existing_request is not None:
            if existing_request != request:
                raise ValueError("idempotency key was reused with a different request")
            return self._receipts[request.idempotency_key]

        fill_price = self._fill_price(request)
        receipt = BrokerSubmissionReceipt(
            submission_id=uuid5(
                NAMESPACE_URL,
                f"ats-paper:{request.idempotency_key}",
            ),
            idempotency_key=request.idempotency_key,
            run_type=RunType.PAPER,
            symbol=request.symbol,
            intent_id=request.intent.id,
            adapter=self.adapter_name,
            status=(
                SubmissionStatus.FILLED
                if fill_price is not None
                else SubmissionStatus.ACCEPTED
            ),
            submitted_at=request.submitted_at,
            fill=(
                BrokerFill(
                    quantity=request.intent.quantity,
                    price=fill_price,
                    fees=Decimal(0),
                    filled_at=request.submitted_at,
                )
                if fill_price is not None
                else None
            ),
        )
        self._requests[request.idempotency_key] = request
        self._receipts[request.idempotency_key] = receipt
        return receipt

    @staticmethod
    def _fill_price(request: BrokerSubmissionRequest) -> Decimal | None:
        intent = request.intent
        reference = request.reference_price

        if intent.order_type is OrderType.MARKET:
            return reference

        if intent.order_type is OrderType.LIMIT:
            assert intent.limit_price is not None
            if intent.action is OrderAction.BUY and reference <= intent.limit_price:
                return min(reference, intent.limit_price)
            if intent.action is OrderAction.SELL and reference >= intent.limit_price:
                return max(reference, intent.limit_price)
            return None

        if intent.order_type is OrderType.STOP:
            assert intent.stop_price is not None
            triggered = (
                reference >= intent.stop_price
                if intent.action is OrderAction.BUY
                else reference <= intent.stop_price
            )
            return reference if triggered else None

        assert intent.order_type is OrderType.STOP_LIMIT
        assert intent.stop_price is not None
        assert intent.limit_price is not None
        triggered = (
            reference >= intent.stop_price
            if intent.action is OrderAction.BUY
            else reference <= intent.stop_price
        )
        if not triggered:
            return None
        if intent.action is OrderAction.BUY and reference <= intent.limit_price:
            return reference
        if intent.action is OrderAction.SELL and reference >= intent.limit_price:
            return reference
        return None
