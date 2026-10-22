"""Fail-closed, read-only evidence collection for Webull cash activities.

The SDK documents the request endpoint and cursor protocol, but Webull's
published Trading API account reference does not currently define this
endpoint's response schema or category/sign semantics. Parsed records are
therefore evidence only and are never enforcement eligible.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
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
_EVENT_FIELDS = frozenset(
    {"activity_id", "activity_type", "amount", "currency", "event_time"}
)


class CashActivitiesError(WebullReadOnlyError):
    """Base class for cash-activity evidence failures."""


class IncompleteHistory(CashActivitiesError):
    """Pagination stopped before the SDK-reported final page."""


class RepeatedCursor(CashActivitiesError):
    """The service returned a cursor that would repeat a page request."""


class ConflictingActivity(CashActivitiesError):
    """One event ID was returned with conflicting event content."""


class UnverifiedRecordSchema(CashActivitiesError):
    """A nonempty page was returned but the record schema is not verified."""


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
    amount: Decimal
    currency: str
    broker_event_time: datetime | None
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
            "BROKER_EVENT_TIME_SEMANTICS_UNVERIFIED",
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
            "RECORD_SCHEMA_UNVERIFIED",
            "EMPTY_RESULT_NOT_PROOF_OF_NO_FLOWS",
            "BROKER_EVENT_TIME_SEMANTICS_UNVERIFIED",
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
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise MalformedResponse(
            "cash activity amount must be a decimal string or integer"
        )
    text = str(value)
    if not _DECIMAL_PATTERN.fullmatch(text):
        raise MalformedResponse("cash activity amount is not a plain decimal")
    try:
        result = Decimal(text)
    except InvalidOperation:
        raise MalformedResponse("cash activity amount is not a valid decimal") from None
    if not result.is_finite():
        raise MalformedResponse("cash activity amount must be finite")
    return result


def _parse_event_time(value: object) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise MalformedResponse("cash activity event_time must be an ISO-8601 string")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        raise MalformedResponse("cash activity event_time is malformed") from None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise MalformedResponse("cash activity event_time must include a timezone")
    return parsed


def _parse_event(
    value: object,
    *,
    observed_at: datetime,
    provenance: PaginationProvenance,
) -> CashActivityEvidence:
    if not isinstance(value, Mapping):
        raise MalformedResponse("cash activity entry must be an object")
    unknown_fields = set(value) - _EVENT_FIELDS
    required_fields = {"activity_id", "activity_type", "amount", "currency"}
    if unknown_fields or not required_fields.issubset(value):
        raise MalformedResponse(
            "cash activity entry has an unsupported field structure"
        )

    event_id = value["activity_id"]
    category = value["activity_type"]
    currency = value["currency"]
    if (
        not isinstance(event_id, str)
        or not event_id.strip()
        or event_id != event_id.strip()
    ):
        raise MalformedResponse("cash activity activity_id must be a non-empty string")
    if not isinstance(category, str) or not category.strip():
        raise MalformedResponse(
            "cash activity activity_type must be a non-empty string"
        )
    if (
        not isinstance(currency, str)
        or len(currency) != 3
        or not currency.isascii()
        or not currency.isalpha()
    ):
        raise MalformedResponse("cash activity currency must be a three-letter code")
    return CashActivityEvidence(
        event_id=event_id,
        raw_category=category,
        amount=_parse_amount(value["amount"]),
        currency=currency.upper(),
        broker_event_time=_parse_event_time(value.get("event_time")),
        observed_at=observed_at,
        pagination_provenance=(provenance,),
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
    if cursor is not None and (
        not isinstance(cursor, str) or not cursor.strip() or cursor != cursor.strip()
    ):
        raise MalformedResponse("cash activities pagination_key is malformed")
    return activities, cursor


def _same_event(left: CashActivityEvidence, right: CashActivityEvidence) -> bool:
    return (
        left.raw_category == right.raw_category
        and left.amount == right.amount
        and left.currency == right.currency
        and left.broker_event_time == right.broker_event_time
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
        allow_provisional_record_schema: bool = False,
    ) -> None:
        if not isinstance(selected_account_id, str) or not selected_account_id.strip():
            raise ValueError("selected_account_id must be a non-empty string")
        if start_time is not None and (
            not isinstance(start_time, str) or not start_time.strip()
        ):
            raise ValueError("start_time must be a non-empty string when provided")
        if end_time is not None and (
            not isinstance(end_time, str) or not end_time.strip()
        ):
            raise ValueError("end_time must be a non-empty string when provided")
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
        self._allow_provisional = allow_provisional_record_schema
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

            if entries and not self._allow_provisional:
                raise UnverifiedRecordSchema(
                    "cash activities returned records but their schema is unverified; "
                    "records rejected (schema discovery pending)"
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
                        amount=previous.amount,
                        currency=previous.currency,
                        broker_event_time=previous.broker_event_time,
                        observed_at=previous.observed_at,
                        pagination_provenance=(
                            *previous.pagination_provenance,
                            *parsed.pagination_provenance,
                        ),
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
    "UnverifiedRecordSchema",
    "WebullSandboxCashActivitiesAdapter",
    "build_official_sandbox_cash_activities_gateway",
]
