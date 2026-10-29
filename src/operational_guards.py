"""ATS-025 fail-closed operational controls for PAPER execution."""

from datetime import datetime, timedelta
from decimal import Decimal
from typing import Protocol

import exchange_calendars as xcals
from pydantic import BaseModel, ConfigDict, Field, field_validator

from src.domain.models import _require_utc
from src.execution import BrokerSubmissionRequest


class MarketHoursChecker(Protocol):
    def is_open(self, at: datetime) -> bool: ...


class XnysMarketHours:
    """NYSE session check backed by exchange-calendars."""

    def __init__(self) -> None:
        self._calendar = xcals.get_calendar("XNYS")

    def is_open(self, at: datetime) -> bool:
        at = _require_utc(at)
        return bool(self._calendar.is_open_on_minute(at, ignore_breaks=True))


class OperationalGuardPolicy(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    version: str = Field(default="ops-v1", min_length=1, max_length=64)
    kill_switch_engaged: bool = False
    max_order_quantity: Decimal = Field(gt=0)
    max_order_notional: Decimal = Field(gt=0)
    max_orders_per_day: int = Field(gt=0, strict=True)
    max_market_data_age: timedelta = Field(gt=timedelta(0))


class OperationalGuardContext(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    evaluated_at: datetime
    market_data_observed_at: datetime
    orders_submitted_today: int = Field(ge=0, strict=True)

    @field_validator("evaluated_at", "market_data_observed_at")
    @classmethod
    def timestamps_are_utc(cls, value: datetime) -> datetime:
        return _require_utc(value)


class OperationalGuardViolation(PermissionError):
    """Raised when a pre-execution operational control fails closed."""


class OperationalGuard:
    def __init__(
        self,
        *,
        policy: OperationalGuardPolicy,
        market_hours: MarketHoursChecker | None = None,
    ) -> None:
        self._policy = policy
        self._market_hours = market_hours or XnysMarketHours()

    @property
    def policy_version(self) -> str:
        return self._policy.version

    def validate(
        self,
        *,
        request: BrokerSubmissionRequest,
        context: OperationalGuardContext,
    ) -> None:
        if context.evaluated_at != request.submitted_at:
            raise OperationalGuardViolation(
                "operational guards must evaluate at broker submission time"
            )
        if self._policy.kill_switch_engaged:
            raise OperationalGuardViolation("kill switch is engaged")

        if request.intent.quantity > self._policy.max_order_quantity:
            raise OperationalGuardViolation("order quantity exceeds configured cap")

        order_notional = request.intent.quantity * request.reference_price
        if order_notional > self._policy.max_order_notional:
            raise OperationalGuardViolation("order notional exceeds configured cap")

        if context.orders_submitted_today >= self._policy.max_orders_per_day:
            raise OperationalGuardViolation("daily order-count cap is reached")

        if not self._market_hours.is_open(context.evaluated_at):
            raise OperationalGuardViolation("market is closed for new submissions")

        if context.market_data_observed_at > context.evaluated_at:
            raise OperationalGuardViolation("market data timestamp is in the future")

        age = context.evaluated_at - context.market_data_observed_at
        if age > self._policy.max_market_data_age:
            raise OperationalGuardViolation("market data is stale")
