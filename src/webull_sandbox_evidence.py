"""Credential-free executable for sanitized Webull sandbox schema discovery.

Run with ``python -m src.webull_sandbox_evidence``. Credentials are loaded
through the sandbox keyring provider and are never included in output.
Only account-list, balance, positions, and cash-activity reads are exposed.
"""

from __future__ import annotations

import argparse
import getpass
import os
import re
import shutil
import sys
import tempfile
from collections.abc import Callable, Mapping
from decimal import Decimal
from typing import Any

from src.webull_sandbox_cash_activities import (
    OfficialSdkCashActivitiesGateway,
    WebullSandboxCashActivitiesAdapter,
    _validate_query_window,
)
from src.webull_sandbox_credentials import (
    SOURCE_ENVIRONMENT,
    SOURCE_KEYRING,
    load_sandbox_credentials,
)
from src.webull_sandbox_readonly import (
    HttpFailure,
    MalformedResponse,
    SdkRequestFailure,
    WebullReadOnlyError,
    WebullSandboxReadOnlyAdapter,
    _build_sdk_trade_client,
    _call,
    _json_body,
    list_account_choices,
)

_SAFE_KEY = re.compile(r"[A-Za-z][A-Za-z0-9_]{0,47}\Z")
_PRIVATE_KEY = re.compile(
    r"(secret|token|password|credential|signature|account.?id|"
    r"app.?key|api.?key|email|phone|address|full.?name|session|authorization)",
    re.IGNORECASE,
)
_MAX_SHAPE_DEPTH = 3
_MAX_SHAPE_FIELDS = 40
_MAX_PAGES = 100


def _type_name(value: object) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, dict):
        return "object"
    if isinstance(value, list):
        return "array"
    if isinstance(value, str):
        return "string"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    return type(value).__name__


def sanitized_shape(value: object, *, depth: int = 0) -> object:
    """Describe structure only, redacting risky keys and every value."""
    if isinstance(value, Mapping):
        fields: dict[str, object] = {}
        hidden = 0
        for key, item in list(value.items())[:_MAX_SHAPE_FIELDS]:
            if (
                not isinstance(key, str)
                or not _SAFE_KEY.fullmatch(key)
                or _PRIVATE_KEY.search(key)
            ):
                hidden += 1
                continue
            if depth >= _MAX_SHAPE_DEPTH:
                fields[key] = _type_name(item)
            else:
                fields[key] = sanitized_shape(item, depth=depth + 1)
        result: dict[str, object] = {
            "type": "object",
            "field_count": len(value),
            "fields": fields,
        }
        if hidden:
            result["redacted_field_count"] = hidden
        if len(value) > _MAX_SHAPE_FIELDS:
            result["omitted_field_count"] = len(value) - _MAX_SHAPE_FIELDS
        return result
    if isinstance(value, list):
        result = {"type": "array", "item_count": len(value)}
        if value:
            result["first_item"] = sanitized_shape(value[0], depth=depth + 1)
        return result
    return _type_name(value)


class _CapturingAccountGateway:
    """Cache account enumeration and retain only sanitized response shapes."""

    def __init__(self, account_api: Any) -> None:
        self._api = account_api
        self._account_response: Any | None = None
        self.shapes: dict[str, object] = {}
        self.statuses: dict[str, int | str] = {}

    def _request(self, label: str, call: Callable[[], Any]) -> Any:
        response = _call(label, call)
        status = getattr(response, "status_code", None)
        self.statuses[label] = status if isinstance(status, int) else "UNKNOWN"
        try:
            self.shapes[label] = sanitized_shape(response.json())
        except (ValueError, TypeError):
            self.shapes[label] = {"body": "INVALID_JSON"}
        return response

    def get_account_list(self) -> Any:
        if self._account_response is None:
            self._account_response = self._request(
                "account_list", self._api.get_account_list
            )
        return self._account_response

    def get_account_balance(self, account_id: str) -> Any:
        return self._request(
            "balance", lambda: self._api.get_account_balance(account_id)
        )

    def get_account_position(self, account_id: str) -> Any:
        return self._request(
            "positions", lambda: self._api.get_account_position(account_id)
        )


class _CapturingCashGateway:
    def __init__(self, gateway: OfficialSdkCashActivitiesGateway) -> None:
        self._gateway = gateway
        self.pages: list[dict[str, object]] = []

    def list_cash_activities(
        self,
        account_id: str,
        *,
        start_time: str | None,
        end_time: str | None,
        pagination_key: str | None,
    ) -> Any:
        response = _call(
            "cash activities",
            lambda: self._gateway.list_cash_activities(
                account_id,
                start_time=start_time,
                end_time=end_time,
                pagination_key=pagination_key,
            ),
        )
        body = _json_body(response, "cash activities")
        if not isinstance(body, Mapping) or "data" not in body:
            raise MalformedResponse(
                "cash activities response has an unsupported structure"
            )
        if not set(body).issubset({"data", "pagination_key"}):
            raise MalformedResponse(
                "cash activities response has an unsupported structure"
            )
        records = body["data"]
        if not isinstance(records, list):
            raise MalformedResponse("cash activities data must be an array")
        cursor = body.get("pagination_key")
        if cursor is not None and (
            not isinstance(cursor, str)
            or not cursor.strip()
            or cursor != cursor.strip()
        ):
            raise MalformedResponse("cash activities pagination_key is malformed")
        self.pages.append(
            {
                "http_status": getattr(response, "status_code", "UNKNOWN"),
                "shape": sanitized_shape(body),
                "record_count": len(records),
                "pagination_key_present": cursor is not None,
                "pagination_key_type": _type_name(cursor),
            }
        )
        return response


def _choose(choices: tuple[Any, ...], input_fn: Callable[[str], str], isatty: bool):
    if not choices:
        print("account_selection: NO_CASH_ACCOUNTS")
        return None
    print("API CASH accounts (masked):")
    for choice in choices:
        print(f"  [{choice.index}] {choice.account_type} {choice.id_suffix}")
    if not isatty:
        print("account_selection: BLOCKED_NON_INTERACTIVE")
        return None
    answer = input_fn("Select number (blank to cancel): ").strip()
    if (
        not answer.isascii()
        or not answer.isdigit()
        or not 1 <= int(answer) <= len(choices)
    ):
        print("account_selection: CANCELLED_OR_INVALID")
        return None
    return choices[int(answer) - 1]


def paper_identifier_match(body: object, selected_id: str, identifier: str) -> str:
    """Compare exact identifiers only; suffixes and labels cannot prove binding."""
    if not identifier or identifier != identifier.strip():
        return "INVALID_IDENTIFIER"
    entries = body.get("data") if isinstance(body, Mapping) else body
    if not isinstance(entries, list):
        return "UNSUPPORTED_RESPONSE"
    matches = [
        row
        for row in entries
        if isinstance(row, Mapping)
        and any(row.get(key) == identifier for key in ("account_id", "account_number"))
    ]
    if len(matches) != 1:
        return "NO_UNIQUE_EXACT_MATCH"
    if matches[0].get("account_id") != selected_id:
        return "MATCHES_DIFFERENT_API_ACCOUNT"
    return "EXACT_IDENTIFIER_MATCH_OBSERVED"


_EXPECTED_DECIMAL = re.compile(r"-?(0|[1-9]\d{0,14})(\.\d{1,8})?\Z")


def parse_expected_amount(text: object, *, allow_negative: bool = False) -> Decimal:
    """Parse operator-typed money without echoing or accepting ambiguous forms."""
    if not isinstance(text, str) or not _EXPECTED_DECIMAL.fullmatch(text):
        raise ValueError("expected amount is malformed")
    value = Decimal(text)
    if not value.is_finite() or (value < 0 and not allow_negative):
        raise ValueError("expected amount is malformed")
    return value


def _prompt_optional_amount(
    reader: Callable[[], str] | None, *, allow_negative: bool = False
) -> Decimal | None:
    """Return None when skipped; ValueError when entered but malformed."""
    if reader is None:
        return None
    text = reader()
    if text == "":
        return None
    return parse_expected_amount(text, allow_negative=allow_negative)


def compare_amount(expected: Decimal | None, actual: Decimal | None) -> str:
    if expected is None:
        return "NOT_REQUESTED"
    if actual is None:
        return "ACTUAL_UNAVAILABLE"
    return "MATCH" if expected == actual else "MISMATCH"


def summarize_cash_history(history: Any, expected_deposit: Decimal | None) -> dict:
    """Counts and documented category labels only; no ids, amounts or times."""
    by_type: dict[str, int] = {}
    for event in history.events:
        key = f"{event.raw_category}/{event.raw_subcategory}"
        by_type[key] = by_type.get(key, 0) + 1
    if expected_deposit is None:
        deposit = "NOT_REQUESTED"
        matches = 0
    else:
        matches = sum(
            1
            for event in history.events
            if event.raw_category == "DEPOSIT" and event.amount == expected_deposit
        )
        deposit = "MATCH" if matches else "NO_MATCH"
    return {
        "event_count": len(history.events),
        "reversal_ambiguous_count": sum(
            1 for event in history.events if event.reversal_ambiguous
        ),
        "type_subtype_counts": dict(sorted(by_type.items())),
        "expected_deposit": deposit,
        "expected_deposit_matching_record_count": matches,
    }


def _print_shapes(
    statuses: Mapping[str, int | str], shapes: Mapping[str, object]
) -> None:
    for label in ("account_list", "balance", "positions"):
        print(f"{label}_http:", statuses.get(label, "NOT_REQUESTED"))
        if label in shapes:
            print(f"{label}_shape:", shapes[label])


def run(
    *,
    environ: Mapping[str, str] | None = None,
    input_fn: Callable[[str], str] = input,
    isatty: bool | None = None,
    build_trade_client: Callable[[str, str], Any] | None = None,
    keyring_module: Any | None = None,
    paper_identifier_input: Callable[[], str] | None = None,
    start_time: str | None = None,
    end_time: str | None = None,
    expected_nlv_input: Callable[[], str] | None = None,
    expected_cash_input: Callable[[], str] | None = None,
    expected_deposit_input: Callable[[], str] | None = None,
) -> int:
    environ = os.environ if environ is None else environ
    isatty = sys.stdin is not None and sys.stdin.isatty() if isatty is None else isatty
    if environ.get("WEBULL_API_DEBUG"):
        print("result: BLOCKED (WEBULL_API_DEBUG must be unset)")
        return 2
    # Validate every operator input before credentials or the network are used.
    try:
        window = _validate_query_window(start_time, end_time)
    except ValueError as error:
        print("result: BLOCKED")
        print("input_error: INVALID_QUERY_WINDOW")
        print("detail:", error)
        return 2
    try:
        expected_nlv = _prompt_optional_amount(expected_nlv_input, allow_negative=True)
        expected_cash = _prompt_optional_amount(
            expected_cash_input, allow_negative=True
        )
        expected_deposit = _prompt_optional_amount(expected_deposit_input)
    except ValueError:
        print("result: BLOCKED")
        print("input_error: MALFORMED_EXPECTED_AMOUNT")
        return 2
    strict_cash = window != (None, None) or expected_deposit_input is not None
    cash_summary: dict | None = None
    nlv_status = cash_status = "NOT_REQUESTED"
    try:
        credentials = load_sandbox_credentials(
            environ=environ, keyring_module=keyring_module
        )
    except WebullReadOnlyError as error:
        print("result: BLOCKED")
        print("error_type:", type(error).__name__)
        return 2

    source_label = {
        SOURCE_KEYRING: "KEYRING",
        SOURCE_ENVIRONMENT: "ENVIRONMENT_FALLBACK",
    }.get(credentials.source, "UNKNOWN")
    builder = build_trade_client or _build_sdk_trade_client
    previous_cwd = os.getcwd()
    scratch = tempfile.mkdtemp(prefix="webull_f2_evidence_")
    os.chdir(scratch)
    account_gateway: _CapturingAccountGateway | None = None
    cash_gateway: _CapturingCashGateway | None = None
    selected = None
    snapshot = None
    mapping_status = "NOT_REQUESTED"
    cash_complete = False
    cash_stop_reason = "NOT_STARTED"
    try:
        trade_client = builder(*credentials.reveal())
        account_gateway = _CapturingAccountGateway(trade_client.account_v2)
        choices = list_account_choices(account_gateway, account_type="CASH")
        selected = _choose(choices, input_fn, bool(isatty))
        if selected is None:
            cash_stop_reason = "ACCOUNT_NOT_SELECTED"
        else:
            if paper_identifier_input is not None:
                mapping_status = paper_identifier_match(
                    _json_body(account_gateway.get_account_list(), "account list"),
                    selected.account_id,
                    paper_identifier_input(),
                )
                if mapping_status != "EXACT_IDENTIFIER_MATCH_OBSERVED":
                    raise MalformedResponse("paper identifier comparison failed")
            snapshot = WebullSandboxReadOnlyAdapter(
                account_gateway,
                selected_account_id=selected.account_id,
                expected_currency="USD",
            ).fetch_snapshot()
            cash_gateway = _CapturingCashGateway(
                OfficialSdkCashActivitiesGateway(trade_client.activity)
            )
            nlv_status = compare_amount(expected_nlv, snapshot.net_liquidation.value)
            cash_field = snapshot.optional_amounts.get("cash_balance")
            cash_status = compare_amount(
                expected_cash, cash_field.value if cash_field else None
            )
            cursor: str | None = None
            seen: set[str] = set()
            if strict_cash:
                history = WebullSandboxCashActivitiesAdapter(
                    cash_gateway,
                    selected_account_id=selected.account_id,
                    start_time=window[0],
                    end_time=window[1],
                    max_pages=_MAX_PAGES,
                ).fetch_history()
                cash_summary = summarize_cash_history(history, expected_deposit)
                cash_complete = history.pagination_complete
                cash_stop_reason = "TERMINAL_CURSOR"
            for _ in range(0 if strict_cash else _MAX_PAGES):
                response = cash_gateway.list_cash_activities(
                    selected.account_id,
                    start_time=None,
                    end_time=None,
                    pagination_key=cursor,
                )
                body = _json_body(response, "cash activities")
                next_cursor = body.get("pagination_key")
                if next_cursor is None:
                    cash_complete = True
                    cash_stop_reason = "TERMINAL_CURSOR"
                    break
                if next_cursor == cursor or next_cursor in seen:
                    cash_stop_reason = "REPEATED_CURSOR"
                    break
                if cursor is not None:
                    seen.add(cursor)
                cursor = next_cursor
            else:
                if not strict_cash:
                    cash_stop_reason = "PAGE_LIMIT"
    except WebullReadOnlyError as error:
        cash_stop_reason = type(error).__name__
        print("request_result: FAIL")
        print("error_type:", type(error).__name__)
        if isinstance(error, SdkRequestFailure):
            print(
                "http_status:",
                error.http_status if error.http_status is not None else "UNKNOWN",
            )
            print(
                "webull_error_code:",
                error.error_code if error.error_code is not None else "UNKNOWN",
            )
        elif isinstance(error, HttpFailure):
            print("operation:", error.operation)
            print("http_status:", error.status_code)
    except Exception as error:  # noqa: BLE001 - only the exception type is emitted.
        cash_stop_reason = type(error).__name__
        print("request_result: FAIL")
        print("error_type:", type(error).__name__)
    finally:
        leftovers = len(os.listdir(scratch))
        os.chdir(previous_cwd)
        shutil.rmtree(scratch)

    print("paper_account_identifier_comparison:", mapping_status)
    print("credential_source:", source_label)
    print("sandbox_host: api.sandbox.webull.com")
    print("sdk_files_written_to_scratch:", leftovers)
    if account_gateway is not None:
        _print_shapes(account_gateway.statuses, account_gateway.shapes)
    if selected is None:
        print("validation_outcome: NOT_COMPLETED")
        return 1
    print(
        "selected_account:", selected.index, selected.account_type, selected.id_suffix
    )
    if snapshot is not None:
        print("balance_currency:", snapshot.currency)
        print("positions_count:", snapshot.positions.count)
        print("positions_empty_array:", snapshot.positions.empty_array_observed)
        print("broker_timestamp: NOT_ESTABLISHED")
    if cash_gateway is not None:
        for index, page in enumerate(cash_gateway.pages, start=1):
            print(f"cash_activities_page_{index}:", page)
    print("cash_activities_pages:", len(cash_gateway.pages) if cash_gateway else 0)
    print("cash_query_window:", window if window != (None, None) else "DEFAULT_OMITTED")
    print("balance_nlv_comparison:", nlv_status)
    print("balance_cash_comparison:", cash_status)
    if cash_summary is not None:
        print("cash_activity_summary:", cash_summary)
        print("cash_parser: STRICT_DOCUMENTED_SCHEMA")
        print("biz_time: RAW_UNVERIFIED_FORMAT_AND_TIMEZONE")
        print("reversal_linkage: NOT_INFERRED")
    print("cash_pagination_terminal_for_request_sequence:", cash_complete)
    print("cash_pagination_stop_reason:", cash_stop_reason)
    print("cash_history_coverage: UNVERIFIED (empty or matching results are not proof)")
    print("cash_category_and_sign_semantics: UNVERIFIED")
    print(
        "account_identity_binding: UNVERIFIED (API sandbox account is not linked to desktop)"
    )
    print("opening_equity: UNVERIFIED (no authenticated frozen period opening)")
    print("reconciliation: NOT_PERFORMED")
    print("positions_completeness: UNVERIFIED")
    print("external_flows_verified: False")
    print("enforcement_eligible: False")
    print("paper_live_order_submission: DISABLED")
    print(
        "validation_outcome:",
        "READ_ONLY_SHAPES_COLLECTED" if snapshot else "INCOMPLETE",
    )
    return 0 if snapshot is not None and cash_complete else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--map-paper-account", action="store_true")
    parser.add_argument("--start-time", help="UTC yyyy-MM-ddTHH:mm:ss.SSSZ")
    parser.add_argument("--end-time", help="UTC yyyy-MM-ddTHH:mm:ss.SSSZ")
    parser.add_argument(
        "--compare-balance",
        action="store_true",
        help="hidden prompts for expected desktop NLV and cash balance",
    )
    parser.add_argument(
        "--check-deposit",
        action="store_true",
        help="hidden prompt for an expected deposit amount",
    )
    args = parser.parse_args()

    def hidden(label: str) -> Callable[[], str]:
        return lambda: getpass.getpass(f"{label} (hidden, blank to skip): ")

    return run(
        paper_identifier_input=(
            lambda: getpass.getpass("Desktop paper account identifier (hidden): ")
        )
        if args.map_paper_account
        else None,
        start_time=args.start_time,
        end_time=args.end_time,
        expected_nlv_input=hidden("Expected desktop NLV")
        if args.compare_balance
        else None,
        expected_cash_input=hidden("Expected desktop cash")
        if args.compare_balance
        else None,
        expected_deposit_input=hidden("Expected deposit amount")
        if args.check_deposit
        else None,
    )


if __name__ == "__main__":
    sys.exit(main())
