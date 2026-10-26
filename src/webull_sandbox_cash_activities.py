"""Fail-closed, read-only evidence parsing for Webull cash activities.

The documented record fields are retained as evidence only. Account-flow
classification, reversal linkage, and broker-time interpretation remain
unverified and are never enforcement eligible.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Protocol

from src.webull_sandbox_readonly import (
    SANDBOX_HOST,
    HttpFailure,
    MalformedResponse,
    TransportError,
    WebullReadOnlyError,
    _build_sdk_trade_client,
    _call,
    _json_body,
)

_DECIMAL_PATTERN = re.compile(r"[+-]?\d+(\.\d+)?")
_QUERY_TIME_PATTERN = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z")
_TRADE_DATE_PATTERN = re.compile(r"\d{4}-\d{2}-\d{2}")
_ACTIVITY_SUBTYPES = {
    "TRADE": frozenset(
        {
            "BUY",
            "SELL",
            "BUY_CANCELLED",
            "SELL_CANCELLED",
            "PENNY_FOR_LOT",
            "FX_EXCHANGE",
        }
    ),
    "DEPOSIT": frozenset(
        {"WIRE", "ACH", "REVERSAL", "CHECK", "ACH_REVERSE", "INTERNAL_TRANSFER"}
    ),
    "WITHDRAW": frozenset({"WIRE", "ACH", "REVERSAL", "CHECK", "INTERNAL_TRANSFER"}),
    "FEES": frozenset(
        {
            "WIRE_FEE",
            "REVERSAL_FEE",
            "TRANSFER_ACATS",
            "CA_HANDLING_FEE",
            "ADR",
            "PAPER_STATEMENT_FEE",
            "PAPER_CONFIRM_FEE",
            "WRITE_OFF",
            "CHECK_FEE",
            "ADVISORY_FEE",
            "SUBSCRIPTION_FEE",
            "SERVICE_FEE",
            "ACH_REVERSE_FEE",
            "OTHER",
        }
    ),
    "TRANSFER": frozenset({"ACATS_IN", "ACATS_OUT", "INTERNAL_TRANSFER", "BANK_SWEEP"}),
    "DIVIDENDS": frozenset(
        {"INCOME", "PAYMENT_IN_LIEU", "CASH_IN_LIEU", "LP_DISTRIBUTION"}
    ),
    "TAX": frozenset(
        {
            "FOREIGN_TAX_WITHHELD",
            "US_TAX_WITHHOLDING",
            "IRA_FED_WITHHOLDING",
            "STATE_WITHHOLDING",
            "WITHHOLDING_TAX",
            "GP_TAX_WITHHELD",
            "OTHER",
        }
    ),
    "INTERESTS": frozenset(
        {
            "CREDIT",
            "DEBIT",
            "STOCK_BORROW_INTEREST",
            "SECURITIES_LENDING_INCOME",
            "ADJUSTMENT",
            "PAYMENT",
            "INTEREST_REBATE",
        }
    ),
    "CORPORATE_ACTION": frozenset(
        {
            "CASH_IN_LIEU",
            "REDEMPTION",
            "MISC_ADJUSTMENT",
            "MERGER",
            "RIGHTS_OFFERING",
            "IDENTIFIER_CHANGE",
            "REVERSE_SPLIT",
            "FORWARD_SPLIT",
            "SPIN_OFF",
            "CONVERSION",
            "LIQUIDATION",
        }
    ),
    "OPTION_EA": frozenset(
        {"CONTRACT_CLOSE", "OPTION_ASSIGNMENT", "OPTION_EXPIRATION", "OPTION_EXERCISE"}
    ),
    "JOURNAL": frozenset({"CASH_JOURNAL"}),
    "EC_SETTLEMENT": frozenset({"EC_EXPIRATION", "EC_PAYOUT"}),
    "OTHER": frozenset({"INCOME", "LENDING_REBATE", "DVP", "GRID_TRANSFER", "OTHER"}),
}
_REVERSAL_SUBTYPES = frozenset(
    {
        "REVERSAL",
        "ACH_REVERSE",
        "REVERSAL_FEE",
        "ACH_REVERSE_FEE",
        "BUY_CANCELLED",
        "SELL_CANCELLED",
    }
)
_EVENT_FIELDS = frozenset(
    {
        "id",
        "account_id",
        "activity_type",
        "activity_sub_type",
        "currency",
        "market",
        "symbol",
        "trade_date",
        "net_amount",
        "biz_time",
    }
)
_REQUIRED_EVENT_FIELDS = frozenset(
    {
        "id",
        "account_id",
        "activity_type",
        "activity_sub_type",
        "currency",
        "trade_date",
        "net_amount",
        "biz_time",
    }
)


class CashActivitiesError(WebullReadOnlyError):
    """Base class for cash-activity evidence failures."""


class IncompleteHistory(CashActivitiesError):
    """Pagination stopped before the SDK-reported final page."""


class RepeatedCursor(CashActivitiesError):
    """The service returned a cursor that would repeat a page request."""


class ConflictingActivity(CashActivitiesError):
    """One event ID was returned with conflicting event content."""


@dataclass(frozen=True)
class PaginationProvenance:
    page_number: int
    requested_cursor_digest: str | None
    next_cursor_digest: str | None
    event_index_on_page: int


@dataclass(frozen=True)
class CashActivityEvidence:
    event_id: str = field(repr=False)
    raw_category: str
    raw_subcategory: str
    amount: Decimal
    currency: str
    trade_date: date
    biz_time_raw: str
    market: str | None
    symbol: str | None
    account_match_verified: bool
    reversal_ambiguous: bool
    observed_at: datetime
    pagination_provenance: tuple[PaginationProvenance, ...]
    classification: None = None
    classification_trusted: bool = False
    external_flow_sign_trusted: bool = False
    external_flow_eligible: bool = False
    trust_flags: frozenset[str] = frozenset(
        {
            "ACTIVITY_CLASSIFICATION_UNVERIFIED",
            "EXTERNAL_FLOW_SIGN_UNVERIFIED",
            "BIZ_TIME_FORMAT_TIMEZONE_UNVERIFIED",
            "BROKER_ORDERING_UNVERIFIED",
            "ACCOUNT_BINDING_UNVERIFIED",
        }
    )


@dataclass(frozen=True)
class CashActivityPageEvidence:
    page_number: int
    requested_cursor_digest: str | None
    next_cursor_digest: str | None
    event_count: int


@dataclass(frozen=True)
class CashActivityHistory:
    events: tuple[CashActivityEvidence, ...]
    pages: tuple[CashActivityPageEvidence, ...]
    pagination_complete: bool
    history_window: tuple[str | None, str | None]
    account_binding_verified: bool = False
    external_flows_verified: bool = False
    enforcement_eligible: bool = False
    trust_flags: frozenset[str] = frozenset(
        {
            "ACCOUNT_BINDING_UNVERIFIED",
            "EXTERNAL_FLOW_CLASSIFICATION_UNVERIFIED",
            "EXTERNAL_FLOW_SIGN_UNVERIFIED",
            "HISTORY_COVERAGE_UNVERIFIED",
            "EMPTY_RESULT_NOT_PROOF_OF_NO_FLOWS",
            "BIZ_TIME_FORMAT_TIMEZONE_UNVERIFIED",
            "BROKER_ORDERING_UNVERIFIED",
            "RECONCILIATION_NOT_PERFORMED",
        }
    )


class CashActivitiesGateway(Protocol):
    def list_cash_activities(
        self,
        account_id: str,
        *,
        start_time: str | None,
        end_time: str | None,
        pagination_key: str | None,
    ) -> Any: ...


class OfficialSdkCashActivitiesGateway:
    """Expose only the SDK Activity.list_activities read operation."""

    def __init__(self, activity_api: Any) -> None:
        self._activity_api = activity_api

    def list_cash_activities(
        self,
        account_id: str,
        *,
        start_time: str | None,
        end_time: str | None,
        pagination_key: str | None,
    ) -> Any:
        return self._activity_api.list_activities(
            account_id,
            start_time=start_time,
            end_time=end_time,
            pagination_key=pagination_key,
        )


def build_official_sandbox_cash_activities_gateway(
    app_key: str, app_secret: str
) -> OfficialSdkCashActivitiesGateway:
    """Build an SDK v3 cash-activities gateway against the sandbox only."""
    trade_client = _build_sdk_trade_client(app_key, app_secret)
    return OfficialSdkCashActivitiesGateway(trade_client.activity)


def _cursor_digest(cursor: str | None) -> str | None:
    if cursor is None:
        return None
    return hashlib.sha256(cursor.encode("utf-8")).hexdigest()[:16]


def _parse_amount(value: object) -> Decimal:
    if not isinstance(value, str):
        raise MalformedResponse("cash activity net_amount must be a decimal string")
    if not _DECIMAL_PATTERN.fullmatch(value):
        raise MalformedResponse("cash activity net_amount is not a plain decimal")
    try:
        result = Decimal(value)
    except InvalidOperation:
        raise MalformedResponse("cash activity amount is not a valid decimal") from None
    if not result.is_finite():
        raise MalformedResponse("cash activity amount must be finite")
    return result


def _parse_trade_date(value: object) -> date:
    if not isinstance(value, str) or not _TRADE_DATE_PATTERN.fullmatch(value):
        raise MalformedResponse("cash activity trade_date must use yyyy-MM-dd")
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise MalformedResponse("cash activity trade_date is malformed") from None


def _validate_query_window(
    start_time: str | None, end_time: str | None
) -> tuple[str | None, str | None]:
    if (start_time is None) != (end_time is None):
        raise ValueError("start_time and end_time must be provided together")
    if start_time is None:
        return None, None

    parsed: list[datetime] = []
    for name, value in (("start_time", start_time), ("end_time", end_time)):
        if not isinstance(value, str) or not _QUERY_TIME_PATTERN.fullmatch(value):
            raise ValueError(f"{name} must use UTC format yyyy-MM-ddTHH:mm:ss.SSSZ")
        try:
            parsed.append(
                datetime.strptime(value[:-1] + "+0000", "%Y-%m-%dT%H:%M:%S.%f%z")
            )
        except ValueError:
            raise ValueError(f"{name} is not a valid UTC timestamp") from None
    if parsed[0].year != parsed[1].year:
        raise ValueError("cash activity query windows must remain within one year")
    if parsed[0] > parsed[1]:
        raise ValueError("start_time must not be after end_time")
    return start_time, end_time


def _parse_event(
    value: object,
    *,
    selected_account_id: str,
    observed_at: datetime,
    provenance: PaginationProvenance,
) -> CashActivityEvidence:
    if not isinstance(value, Mapping):
        raise MalformedResponse("cash activity entry must be an object")
    unknown_fields = set(value) - _EVENT_FIELDS
    if unknown_fields or not _REQUIRED_EVENT_FIELDS.issubset(value):
        raise MalformedResponse(
            "cash activity entry has an unsupported field structure"
        )

    event_id = value["id"]
    account_id = value["account_id"]
    category = value["activity_type"]
    subcategory = value["activity_sub_type"]
    currency = value["currency"]
    if (
        not isinstance(event_id, str)
        or not event_id.strip()
        or event_id != event_id.strip()
    ):
        raise MalformedResponse("cash activity id must be a non-empty string")
    if not isinstance(account_id, str) or not account_id.strip():
        raise MalformedResponse("cash activity account_id must be a non-empty string")
    if account_id != selected_account_id:
        raise MalformedResponse(
            "cash activity account_id does not match the selected account"
        )
    if not isinstance(category, str) or not category.strip():
        raise MalformedResponse(
            "cash activity activity_type must be a non-empty string"
        )
    if not isinstance(subcategory, str) or not subcategory.strip():
        raise MalformedResponse(
            "cash activity activity_sub_type must be a non-empty string"
        )
    if (
        category not in _ACTIVITY_SUBTYPES
        or subcategory not in _ACTIVITY_SUBTYPES[category]
    ):
        raise MalformedResponse("cash activity type/subtype is not documented")
    if not isinstance(currency, str) or currency != "USD":
        raise MalformedResponse("cash activity currency is not a documented value")

    market = value.get("market")
    if "market" in value and (not isinstance(market, str) or market != "US"):
        raise MalformedResponse("cash activity market is not a documented value")
    symbol = value.get("symbol")
    if "symbol" in value and (
        not isinstance(symbol, str) or not symbol or symbol != symbol.strip()
    ):
        raise MalformedResponse("cash activity symbol is malformed")
    biz_time = value["biz_time"]
    if not isinstance(biz_time, str) or not biz_time.strip():
        raise MalformedResponse("cash activity biz_time must be a non-empty string")

    reversal_ambiguous = subcategory in _REVERSAL_SUBTYPES
    trust_flags = {
        "ACTIVITY_CLASSIFICATION_UNVERIFIED",
        "EXTERNAL_FLOW_SIGN_UNVERIFIED",
        "BIZ_TIME_FORMAT_TIMEZONE_UNVERIFIED",
        "BROKER_ORDERING_UNVERIFIED",
        "ACCOUNT_BINDING_UNVERIFIED",
    }
    if reversal_ambiguous:
        trust_flags.add("REVERSAL_LINKAGE_UNVERIFIED")
    return CashActivityEvidence(
        event_id=event_id,
        raw_category=category,
        raw_subcategory=subcategory,
        amount=_parse_amount(value["net_amount"]),
        currency=currency,
        trade_date=_parse_trade_date(value["trade_date"]),
        biz_time_raw=biz_time,
        market=market,
        symbol=symbol,
        account_match_verified=True,
        reversal_ambiguous=reversal_ambiguous,
        observed_at=observed_at,
        pagination_provenance=(provenance,),
        trust_flags=frozenset(trust_flags),
    )


_SAFE_FIELD_NAME = re.compile(r"[A-Za-z0-9_]{1,48}")


def describe_response_shape(body: object) -> dict[str, object]:
    """Field names and type names only; never values."""

    def names(mapping: Mapping[object, object]) -> dict[str, str]:
        return {
            (
                key
                if isinstance(key, str) and _SAFE_FIELD_NAME.fullmatch(key)
                else "<unsafe-name>"
            ): (type(value).__name__)
            for key, value in list(mapping.items())[:40]
        }

    shape: dict[str, object] = {"top_level_type": type(body).__name__}
    if not isinstance(body, Mapping):
        return shape
    shape["top_level_fields"] = names(body)
    items = body.get("data")
    if isinstance(items, list):
        shape["activity_count"] = len(items)
        first = next((item for item in items if isinstance(item, Mapping)), None)
        if first is not None:
            shape["activity_item_fields"] = names(first)
    cursor = body.get("pagination_key")
    shape["has_pagination_key"] = isinstance(cursor, str) and bool(cursor)
    shape["matches_observed_page_structure"] = _matches_page_structure(body)
    return shape


def _matches_page_structure(body: object) -> bool:
    try:
        _parse_page(body)
    except MalformedResponse:
        return False
    return True


def _parse_page(body: object) -> tuple[list[object], str | None]:
    """Parse the observed v3 shape: {"data": [...], optional "pagination_key": str}."""
    if not isinstance(body, Mapping) or "data" not in body:
        raise MalformedResponse("cash activities response has an unsupported structure")
    if not set(body).issubset({"data", "pagination_key"}):
        raise MalformedResponse("cash activities response has an unsupported structure")
    activities = body["data"]
    if not isinstance(activities, list):
        raise MalformedResponse("cash activities data must be an array")

    cursor = body.get("pagination_key")
    if "pagination_key" in body and (
        not isinstance(cursor, str) or not cursor.strip() or cursor != cursor.strip()
    ):
        raise MalformedResponse("cash activities pagination_key is malformed")
    return activities, cursor


def _same_event(left: CashActivityEvidence, right: CashActivityEvidence) -> bool:
    return (
        left.raw_category == right.raw_category
        and left.raw_subcategory == right.raw_subcategory
        and left.amount == right.amount
        and left.currency == right.currency
        and left.trade_date == right.trade_date
        and left.biz_time_raw == right.biz_time_raw
        and left.market == right.market
        and left.symbol == right.symbol
    )


class WebullSandboxCashActivitiesAdapter:
    def __init__(
        self,
        gateway: CashActivitiesGateway,
        *,
        selected_account_id: str,
        start_time: str | None = None,
        end_time: str | None = None,
        max_pages: int = 1000,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if not isinstance(selected_account_id, str) or not selected_account_id.strip():
            raise ValueError("selected_account_id must be a non-empty string")
        start_time, end_time = _validate_query_window(start_time, end_time)
        if (
            isinstance(max_pages, bool)
            or not isinstance(max_pages, int)
            or max_pages < 1
        ):
            raise ValueError("max_pages must be a positive integer")
        self._gateway = gateway
        self._account_id = selected_account_id.strip()
        self._start_time = start_time.strip() if start_time is not None else None
        self._end_time = end_time.strip() if end_time is not None else None
        self._max_pages = max_pages
        self._clock = clock or (lambda: datetime.now(UTC))

    def fetch_history(self) -> CashActivityHistory:
        cursor: str | None = None
        requested_cursors: set[str] = set()
        events: dict[str, CashActivityEvidence] = {}
        pages: list[CashActivityPageEvidence] = []

        for page_number in range(1, self._max_pages + 1):
            if cursor is not None and cursor in requested_cursors:
                raise RepeatedCursor("cash activities pagination repeated a cursor")
            if cursor is not None:
                requested_cursors.add(cursor)

            try:
                response = _call(
                    "cash activities",
                    lambda requested_cursor=cursor: self._gateway.list_cash_activities(
                        self._account_id,
                        start_time=self._start_time,
                        end_time=self._end_time,
                        pagination_key=requested_cursor,
                    ),
                )
                body = _json_body(response, "cash activities")
                entries, next_cursor = _parse_page(body)
            except (HttpFailure, MalformedResponse, TransportError) as error:
                if page_number == 1:
                    raise
                raise IncompleteHistory(
                    f"cash activity pagination stopped before the final page "
                    f"({type(error).__name__})"
                ) from None

            if next_cursor is not None and (
                next_cursor == cursor or next_cursor in requested_cursors
            ):
                raise RepeatedCursor("cash activities pagination repeated a cursor")

            observed_at = self._clock()
            if observed_at.tzinfo is None or observed_at.utcoffset() is None:
                raise CashActivitiesError(
                    "local observed_at clock must be timezone-aware"
                )
            provenance = PaginationProvenance(
                page_number=page_number,
                requested_cursor_digest=_cursor_digest(cursor),
                next_cursor_digest=_cursor_digest(next_cursor),
                event_index_on_page=0,
            )
            page = CashActivityPageEvidence(
                page_number=page_number,
                requested_cursor_digest=provenance.requested_cursor_digest,
                next_cursor_digest=provenance.next_cursor_digest,
                event_count=len(entries),
            )

            for index, entry in enumerate(entries, start=1):
                event_provenance = PaginationProvenance(
                    page_number=page_number,
                    requested_cursor_digest=provenance.requested_cursor_digest,
                    next_cursor_digest=provenance.next_cursor_digest,
                    event_index_on_page=index,
                )
                parsed = _parse_event(
                    entry,
                    selected_account_id=self._account_id,
                    observed_at=observed_at,
                    provenance=event_provenance,
                )
                previous = events.get(parsed.event_id)
                if previous is None:
                    events[parsed.event_id] = parsed
                elif not _same_event(previous, parsed):
                    raise ConflictingActivity(
                        "cash activities contained conflicting records for an event ID"
                    )
                else:
                    events[parsed.event_id] = CashActivityEvidence(
                        event_id=previous.event_id,
                        raw_category=previous.raw_category,
                        raw_subcategory=previous.raw_subcategory,
                        amount=previous.amount,
                        currency=previous.currency,
                        trade_date=previous.trade_date,
                        biz_time_raw=previous.biz_time_raw,
                        market=previous.market,
                        symbol=previous.symbol,
                        account_match_verified=previous.account_match_verified,
                        reversal_ambiguous=previous.reversal_ambiguous,
                        observed_at=previous.observed_at,
                        pagination_provenance=(
                            *previous.pagination_provenance,
                            *parsed.pagination_provenance,
                        ),
                        trust_flags=previous.trust_flags,
                    )

            pages.append(page)
            if next_cursor is None:
                return CashActivityHistory(
                    events=tuple(events.values()),
                    pages=tuple(pages),
                    pagination_complete=True,
                    history_window=(self._start_time, self._end_time),
                )
            cursor = next_cursor

        raise IncompleteHistory(
            f"cash activity pagination exceeded the configured {self._max_pages} page limit"
        )


__all__ = [
    "SANDBOX_HOST",
    "CashActivitiesError",
    "CashActivityEvidence",
    "CashActivityHistory",
    "CashActivityPageEvidence",
    "ConflictingActivity",
    "IncompleteHistory",
    "OfficialSdkCashActivitiesGateway",
    "PaginationProvenance",
    "RepeatedCursor",
    "WebullSandboxCashActivitiesAdapter",
    "build_official_sandbox_cash_activities_gateway",
]
