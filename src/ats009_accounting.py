"""ATS-009B immutable valuation contracts and exact loss accounting.

These structural contracts do not prove source authentication. Production
adapters, sticky-lock persistence, and authorization integration are separate
trust and persistence boundaries.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from fractions import Fraction
from typing import Annotated
from uuid import UUID

from pydantic import (
    BeforeValidator,
    Field,
    StrictBool,
    StrictInt,
    ValidationError,
    field_validator,
    model_validator,
)

from src.ats009_calendar import (
    EXCHANGE,
    NEW_YORK,
    PINNED_VERSION,
    SOURCE,
    ResolvedSession,
)
from src.domain.models import ImmutableModel


class PeriodKind(StrEnum):
    DAILY = "DAILY"
    WEEKLY = "WEEKLY"


ATS009_POLICY_VERSION = "ATS-009-v0.1"
RISK_UNIT_EQUITY_FRACTION = Fraction(1, 200)
DAILY_LOSS_LIMIT_R = Fraction(-3)
WEEKLY_LOSS_LIMIT_R = Fraction(-6)


def _exact_decimal(value: object) -> Decimal:
    if isinstance(value, (bool, float)):
        raise ValueError(  # noqa: TRY004 - Pydantic requires ValueError for validation errors.
            "financial amounts must not be supplied as floats or booleans"
        )
    if isinstance(value, Decimal):
        return value
    if isinstance(value, (str, int)):
        try:
            return Decimal(value)
        except (ValueError, ArithmeticError) as error:
            raise ValueError("financial amount is not a valid decimal") from error
    raise ValueError("financial amounts must be Decimal, string, or integer values")


ExactDecimal = Annotated[Decimal, BeforeValidator(_exact_decimal)]


def _utc_timestamp(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError("timestamp must be timezone-aware UTC")
    return value.astimezone(UTC)


def _nonblank(value: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError("value must not be blank")
    return normalized


class VerifiedAccountValuation(ImmutableModel):
    """Adapter-shaped current valuation; field names do not prove authenticity."""

    account_id: UUID
    source_id: str = Field(min_length=1, max_length=128)
    source_epoch: str = Field(min_length=1, max_length=128)
    source_sequence: StrictInt = Field(gt=0)
    as_of: datetime
    base_currency: str = Field(min_length=3, max_length=3)
    net_liquidation: ExactDecimal
    complete: StrictBool
    reconciled: StrictBool
    provenance_ref: str = Field(min_length=1, max_length=256)

    @field_validator("source_id", "source_epoch", "provenance_ref")
    @classmethod
    def required_text(cls, value: str) -> str:
        return _nonblank(value)

    @field_validator("as_of")
    @classmethod
    def timestamp_is_utc(cls, value: datetime) -> datetime:
        return _utc_timestamp(value)

    @field_validator("base_currency")
    @classmethod
    def normalize_currency(cls, value: str) -> str:
        currency = value.strip().upper()
        if len(currency) != 3 or not currency.isascii() or not currency.isalpha():
            raise ValueError("base_currency must be a three-letter currency code")
        return currency

    @field_validator("net_liquidation")
    @classmethod
    def nlv_is_finite(cls, value: Decimal) -> Decimal:
        if not value.is_finite():
            raise ValueError("net_liquidation must be finite")
        return value


class PeriodOpeningEquity(ImmutableModel):
    """Immutable daily or weekly opening NLV/equity facts for one period."""

    account_id: UUID
    period_kind: PeriodKind
    period_id: str = Field(min_length=1, max_length=128)
    calendar_version: str = Field(min_length=1, max_length=128)
    opened_at: datetime
    opening_equity: ExactDecimal
    opening_nlv: ExactDecimal
    base_currency: str = Field(min_length=3, max_length=3)
    source_id: str = Field(min_length=1, max_length=128)
    source_epoch: str = Field(min_length=1, max_length=128)
    source_sequence: StrictInt = Field(gt=0)
    immutable_version: StrictInt = Field(ge=1)
    complete: StrictBool
    reconciled: StrictBool
    provenance_ref: str = Field(min_length=1, max_length=256)

    @field_validator(
        "period_id", "calendar_version", "source_id", "source_epoch", "provenance_ref"
    )
    @classmethod
    def required_text(cls, value: str) -> str:
        return _nonblank(value)

    @field_validator("opened_at")
    @classmethod
    def timestamp_is_utc(cls, value: datetime) -> datetime:
        return _utc_timestamp(value)

    @field_validator("base_currency")
    @classmethod
    def normalize_currency(cls, value: str) -> str:
        currency = value.strip().upper()
        if len(currency) != 3 or not currency.isascii() or not currency.isalpha():
            raise ValueError("base_currency must be a three-letter currency code")
        return currency

    @field_validator("opening_equity")
    @classmethod
    def equity_is_positive(cls, value: Decimal) -> Decimal:
        if not value.is_finite() or value <= 0:
            raise ValueError("period-opening equity must be finite and positive")
        return value

    @field_validator("opening_nlv")
    @classmethod
    def opening_nlv_is_finite(cls, value: Decimal) -> Decimal:
        if not value.is_finite():
            raise ValueError("opening NLV must be finite")
        return value


class ExternalFlowKind(StrEnum):
    CONTRIBUTION = "CONTRIBUTION"
    WITHDRAWAL = "WITHDRAWAL"


class ReconciledExternalFlow(ImmutableModel):
    """A single, signed, reconciled external cash flow; fees are not flows."""

    account_id: UUID
    flow_id: str = Field(min_length=1, max_length=128)
    source_id: str = Field(min_length=1, max_length=128)
    source_epoch: str = Field(min_length=1, max_length=128)
    source_sequence: StrictInt = Field(gt=0)
    occurred_at: datetime
    kind: ExternalFlowKind
    signed_amount: ExactDecimal
    base_currency: str = Field(min_length=3, max_length=3)
    provenance_ref: str = Field(min_length=1, max_length=256)

    @field_validator("flow_id", "source_id", "source_epoch", "provenance_ref")
    @classmethod
    def required_text(cls, value: str) -> str:
        return _nonblank(value)

    @field_validator("occurred_at")
    @classmethod
    def timestamp_is_utc(cls, value: datetime) -> datetime:
        return _utc_timestamp(value)

    @field_validator("base_currency")
    @classmethod
    def normalize_currency(cls, value: str) -> str:
        currency = value.strip().upper()
        if len(currency) != 3 or not currency.isascii() or not currency.isalpha():
            raise ValueError("base_currency must be a three-letter currency code")
        return currency

    @field_validator("signed_amount")
    @classmethod
    def amount_matches_flow_kind(cls, value: Decimal, info: object) -> Decimal:
        if not value.is_finite():
            raise ValueError("external flow amount must be finite")
        kind = getattr(info, "data", {}).get("kind")
        if kind is ExternalFlowKind.CONTRIBUTION and value <= 0:
            raise ValueError("contributions must have a positive signed amount")
        if kind is ExternalFlowKind.WITHDRAWAL and value >= 0:
            raise ValueError("withdrawals must have a negative signed amount")
        return value


class ExternalFlowReconciliation(ImmutableModel):
    """Complete period-specific flow ledger through a source watermark."""

    account_id: UUID
    period_kind: PeriodKind
    period_id: str = Field(min_length=1, max_length=128)
    window_start: datetime
    as_of: datetime
    base_currency: str = Field(min_length=3, max_length=3)
    source_id: str = Field(min_length=1, max_length=128)
    source_epoch: str = Field(min_length=1, max_length=128)
    source_sequence: StrictInt = Field(gt=0)
    reconciliation_id: str = Field(min_length=1, max_length=128)
    flows: tuple[ReconciledExternalFlow, ...]
    complete: StrictBool
    reconciled: StrictBool
    provenance_ref: str = Field(min_length=1, max_length=256)

    @field_validator(
        "period_id", "source_id", "source_epoch", "reconciliation_id", "provenance_ref"
    )
    @classmethod
    def required_text(cls, value: str) -> str:
        return _nonblank(value)

    @field_validator("window_start", "as_of")
    @classmethod
    def timestamps_are_utc(cls, value: datetime) -> datetime:
        return _utc_timestamp(value)

    @field_validator("base_currency")
    @classmethod
    def normalize_currency(cls, value: str) -> str:
        currency = value.strip().upper()
        if len(currency) != 3 or not currency.isascii() or not currency.isalpha():
            raise ValueError("base_currency must be a three-letter currency code")
        return currency

    @model_validator(mode="after")
    def flows_are_unique_and_within_watermark(self) -> ExternalFlowReconciliation:
        if self.as_of < self.window_start:
            raise ValueError("flow reconciliation as_of precedes its window")
        flow_ids: set[str] = set()
        sequences: set[int] = set()
        previous_sequence = 0
        for flow in self.flows:
            if flow.account_id != self.account_id:
                raise ValueError("external flow account does not match reconciliation")
            if (
                flow.source_id != self.source_id
                or flow.source_epoch != self.source_epoch
            ):
                raise ValueError("external flow source does not match reconciliation")
            if flow.base_currency != self.base_currency:
                raise ValueError("external flow currency does not match reconciliation")
            if flow.source_sequence > self.source_sequence:
                raise ValueError(
                    "external flow exceeds reconciliation source watermark"
                )
            if not self.window_start <= flow.occurred_at <= self.as_of:
                raise ValueError("external flow is outside its reconciled window")
            if flow.flow_id in flow_ids or flow.source_sequence in sequences:
                raise ValueError("duplicate or conflicting external flow identity")
            if flow.source_sequence <= previous_sequence:
                raise ValueError("external flows are reordered by source sequence")
            flow_ids.add(flow.flow_id)
            sequences.add(flow.source_sequence)
            previous_sequence = flow.source_sequence
        return self


@dataclass(frozen=True, slots=True)
class LossAccountingEvaluation:
    """Pure threshold-crossing result; it does not represent a sticky lock."""

    account_id: UUID | None
    available: bool
    daily_net_pnl: Fraction | None = None
    weekly_net_pnl: Fraction | None = None
    daily_r: Fraction | None = None
    weekly_r: Fraction | None = None
    daily_threshold_crossed: bool = False
    weekly_threshold_crossed: bool = False
    unavailable_reasons: tuple[str, ...] = ()


def _unavailable(account_id: UUID | None, *reasons: str) -> LossAccountingEvaluation:
    return LossAccountingEvaluation(
        account_id=account_id,
        available=False,
        unavailable_reasons=tuple(sorted(set(reasons))),
    )


def _as_fraction(value: Decimal) -> Fraction:
    return Fraction(value)


def _revalidated[T](model: T, model_type: type[T]) -> T:
    return model_type.model_validate(model.model_dump(mode="python"))


def _calendar_state_is_valid(session: ResolvedSession) -> bool:
    if (
        session.exchange != EXCHANGE
        or not isinstance(session.calendar_version, str)
        or session.calendar_version != f"{SOURCE}:{PINNED_VERSION}:{EXCHANGE}"
        or not isinstance(session.session_id, str)
        or not isinstance(session.local_date, date)
        or isinstance(session.local_date, datetime)
        or session.session_id != f"{EXCHANGE}:{session.local_date.isoformat()}"
        or not isinstance(session.week_id, str)
        or not session.week_id.startswith(f"{EXCHANGE}:week:")
        or type(session.schedule_verified) is not bool
        or type(session.is_regular_session) is not bool
        or type(session.eligible_new_risk) is not bool
        or not session.schedule_verified
        or not session.is_regular_session
        or not session.eligible_new_risk
        or not isinstance(session.authority_revision, str)
        or not session.authority_revision.strip()
    ):
        return False
    week_prefix = f"{EXCHANGE}:week:"
    week_date = session.week_id.removeprefix(week_prefix)
    try:
        week_open = date.fromisoformat(week_date)
    except ValueError:
        return False
    monday = session.local_date - timedelta(days=session.local_date.weekday())
    if (
        week_open.isoformat() != week_date
        or not monday <= week_open <= session.local_date <= monday + timedelta(days=6)
    ):
        return False
    try:
        opening = _utc_timestamp(session.open_utc)
        closing = _utc_timestamp(session.close_utc)
        observed = _utc_timestamp(session.observed_at_utc)
    except (AttributeError, TypeError, ValueError):
        return False
    return (
        opening < closing
        and opening <= observed < closing
        and observed.astimezone(NEW_YORK).date() == session.local_date
        and session.is_regular_session is (opening <= observed < closing)
    )


def evaluate_loss_accounting(
    *,
    valuation: VerifiedAccountValuation,
    daily_opening: PeriodOpeningEquity,
    weekly_opening: PeriodOpeningEquity,
    daily_flows: ExternalFlowReconciliation,
    weekly_flows: ExternalFlowReconciliation,
    session: ResolvedSession,
    evaluated_at: datetime,
    max_valuation_age: timedelta,
) -> LossAccountingEvaluation:
    """Compute separate daily/weekly P&L and threshold crossings exactly.

    This is not an authorization result and does not persist sticky lock state.
    """
    account_id = getattr(valuation, "account_id", None)
    if not isinstance(valuation, VerifiedAccountValuation):
        return _unavailable(None, "INVALID_VALUATION_CONTRACT")
    if not isinstance(session, ResolvedSession) or not _calendar_state_is_valid(
        session
    ):
        return _unavailable(account_id, "INVALID_CALENDAR_CONTRACT")
    try:
        evaluated_at = _utc_timestamp(evaluated_at)
    except (AttributeError, TypeError, ValueError):
        return _unavailable(account_id, "INVALID_EVALUATION_TIME")
    if not isinstance(max_valuation_age, timedelta) or max_valuation_age <= timedelta(
        0
    ):
        return _unavailable(account_id, "INVALID_FRESHNESS_POLICY")
    try:
        valuation = _revalidated(valuation, VerifiedAccountValuation)
        daily_opening = _revalidated(daily_opening, PeriodOpeningEquity)
        weekly_opening = _revalidated(weekly_opening, PeriodOpeningEquity)
        daily_flows = _revalidated(daily_flows, ExternalFlowReconciliation)
        weekly_flows = _revalidated(weekly_flows, ExternalFlowReconciliation)
    except (ValidationError, AttributeError, TypeError):
        return _unavailable(account_id, "STRUCTURAL_VALIDATION_FAILED")

    reasons: list[str] = []
    if not valuation.complete or not valuation.reconciled:
        reasons.append("VALUATION_INCOMPLETE_OR_UNRECONCILED")
    for opening in (daily_opening, weekly_opening):
        if not opening.complete or not opening.reconciled:
            reasons.append("OPENING_INCOMPLETE_OR_UNRECONCILED")
        if opening.account_id != valuation.account_id:
            reasons.append("ACCOUNT_MISMATCH")
        if opening.base_currency != valuation.base_currency:
            reasons.append("CURRENCY_MISMATCH")
        if opening.source_id != valuation.source_id:
            reasons.append("VALUATION_SOURCE_MISMATCH")
        if opening.source_epoch != valuation.source_epoch:
            reasons.append("VALUATION_SOURCE_EPOCH_MISMATCH")
        if opening.source_sequence > valuation.source_sequence:
            reasons.append("VALUATION_SEQUENCE_REGRESSION")
        if opening.opened_at > valuation.as_of:
            reasons.append("OPENING_AFTER_VALUATION")

    if daily_opening.period_kind is not PeriodKind.DAILY:
        reasons.append("DAILY_OPENING_KIND_MISMATCH")
    if weekly_opening.period_kind is not PeriodKind.WEEKLY:
        reasons.append("WEEKLY_OPENING_KIND_MISMATCH")
    if daily_opening.period_id != session.session_id:
        reasons.append("DAILY_PERIOD_MISMATCH")
    if weekly_opening.period_id != session.week_id:
        reasons.append("WEEKLY_PERIOD_MISMATCH")
    if (
        daily_opening.calendar_version != session.calendar_version
        or weekly_opening.calendar_version != session.calendar_version
    ):
        reasons.append("CALENDAR_VERSION_MISMATCH")
    if not session.schedule_verified or not session.is_regular_session:
        reasons.append("CALENDAR_SESSION_UNVERIFIED_OR_OUTSIDE_SESSION")
    if session.exchange != "XNYS":
        reasons.append("UNSUPPORTED_EXCHANGE")
    if not session.eligible_new_risk:
        reasons.append("CALENDAR_SESSION_NOT_ELIGIBLE")
    if session.observed_at_utc != valuation.as_of:
        reasons.append("VALUATION_SESSION_TIME_MISMATCH")
    valuation_age = evaluated_at - valuation.as_of
    if valuation_age < timedelta(0) or valuation_age > max_valuation_age:
        reasons.append("VALUATION_STALE_OR_FROM_FUTURE")

    for reconciliation, opening in (
        (daily_flows, daily_opening),
        (weekly_flows, weekly_opening),
    ):
        if not reconciliation.complete or not reconciliation.reconciled:
            reasons.append("FLOW_RECONCILIATION_INCOMPLETE")
        if reconciliation.account_id != valuation.account_id:
            reasons.append("FLOW_ACCOUNT_MISMATCH")
        if reconciliation.period_kind is not opening.period_kind:
            reasons.append("FLOW_PERIOD_KIND_MISMATCH")
        if reconciliation.period_id != opening.period_id:
            reasons.append("FLOW_PERIOD_MISMATCH")
        if reconciliation.base_currency != valuation.base_currency:
            reasons.append("FLOW_CURRENCY_MISMATCH")
        if reconciliation.window_start != opening.opened_at:
            reasons.append("FLOW_WINDOW_START_MISMATCH")
        if reconciliation.as_of != valuation.as_of:
            reasons.append("FLOW_VALUATION_TIME_MISMATCH")

    if (
        daily_flows.source_id != weekly_flows.source_id
        or daily_flows.source_epoch != weekly_flows.source_epoch
        or daily_flows.source_sequence != weekly_flows.source_sequence
    ):
        reasons.append("FLOW_WATERMARK_MISMATCH")
    if daily_opening.opened_at < weekly_opening.opened_at:
        reasons.append("PERIOD_OPENING_ORDER_INVALID")

    weekly_by_id = {flow.flow_id: flow for flow in weekly_flows.flows}
    if any(weekly_by_id.get(flow.flow_id) != flow for flow in daily_flows.flows):
        reasons.append("DAILY_WEEKLY_FLOW_SET_CONFLICT")

    if reasons:
        return _unavailable(account_id, *reasons)

    daily_pnl = (
        _as_fraction(valuation.net_liquidation)
        - _as_fraction(daily_opening.opening_nlv)
        - sum(
            (_as_fraction(flow.signed_amount) for flow in daily_flows.flows), Fraction()
        )
    )
    weekly_pnl = (
        _as_fraction(valuation.net_liquidation)
        - _as_fraction(weekly_opening.opening_nlv)
        - sum(
            (_as_fraction(flow.signed_amount) for flow in weekly_flows.flows),
            Fraction(),
        )
    )
    daily_risk_unit = (
        _as_fraction(daily_opening.opening_equity) * RISK_UNIT_EQUITY_FRACTION
    )
    weekly_risk_unit = (
        _as_fraction(weekly_opening.opening_equity) * RISK_UNIT_EQUITY_FRACTION
    )
    daily_r = daily_pnl / daily_risk_unit
    weekly_r = weekly_pnl / weekly_risk_unit
    return LossAccountingEvaluation(
        account_id=account_id,
        available=True,
        daily_net_pnl=daily_pnl,
        weekly_net_pnl=weekly_pnl,
        daily_r=daily_r,
        weekly_r=weekly_r,
        daily_threshold_crossed=daily_pnl <= DAILY_LOSS_LIMIT_R * daily_risk_unit,
        weekly_threshold_crossed=weekly_pnl <= WEEKLY_LOSS_LIMIT_R * weekly_risk_unit,
    )
