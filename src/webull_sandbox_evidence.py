"""Credential-free executable for sanitized Webull sandbox schema discovery.

Run with ``python -m src.webull_sandbox_evidence``. Credentials are loaded
through the sandbox keyring provider and are never included in output.
Only account-list, balance, positions, and cash-activity reads are exposed.
"""

from __future__ import annotations

import os
import re
import shutil
import sys
import tempfile
from collections.abc import Callable, Mapping
from typing import Any

from src.webull_sandbox_cash_activities import OfficialSdkCashActivitiesGateway
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
) -> int:
    environ = os.environ if environ is None else environ
    isatty = sys.stdin is not None and sys.stdin.isatty() if isatty is None else isatty
    if environ.get("WEBULL_API_DEBUG"):
        print("result: BLOCKED (WEBULL_API_DEBUG must be unset)")
        return 2
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
            snapshot = WebullSandboxReadOnlyAdapter(
                account_gateway,
                selected_account_id=selected.account_id,
                expected_currency="USD",
            ).fetch_snapshot()
            cash_gateway = _CapturingCashGateway(
                OfficialSdkCashActivitiesGateway(trade_client.activity)
            )
            cursor: str | None = None
            seen: set[str] = set()
            for _ in range(_MAX_PAGES):
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
    print("cash_pagination_terminal_for_request_sequence:", cash_complete)
    print("cash_pagination_stop_reason:", cash_stop_reason)
    print("cash_history_coverage: UNVERIFIED (account-only default window)")
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
    return run()


if __name__ == "__main__":
    sys.exit(main())
