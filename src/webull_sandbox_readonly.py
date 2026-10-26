"""ATS-009C2 Webull sandbox read-only account adapter.

Isolation contract: this module exposes only three Webull operations (account
list, balance, positions). The official-SDK gateway holds just the SDK's
account API object, so nothing else on the SDK trade client is reachable from
here. It is NOT wired into ATS-009 enforcement or Layer 3 authorization, and it
never emits a ``VerifiedAccountValuation``: the trust gaps in
``SandboxAccountSnapshot.trust_gaps`` are unresolved by design.

All failures are fail-closed exceptions. Messages never contain credentials,
full account identifiers, or raw response bodies.
"""

from __future__ import annotations

import io
import logging
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Any, Protocol

SANDBOX_HOST = "api.sandbox.webull.com"
SANDBOX_REGION = "us"
APP_KEY_ENV = "WEBULL_APP_KEY"
APP_SECRET_ENV = "WEBULL_APP_SECRET"

_DECIMAL_PATTERN = re.compile(r"[+-]?\d+(\.\d+)?")
_SAFE_CODE_PATTERN = re.compile(r"[A-Za-z0-9_.\-]{1,64}")
_OPTIONAL_AMOUNT_FIELDS = (
    "market_value",
    "cash_balance",
    "settled_cash",
    "unsettled_cash",
    "buying_power",
    "option_buying_power",
    "night_trading_buying_power",
    "unrealized_profit_loss",
    "day_profit_loss",
)


class WebullReadOnlyError(Exception):
    """Base class for all fail-closed adapter errors."""


class ConfigurationError(WebullReadOnlyError):
    pass


class TransportError(WebullReadOnlyError):
    pass


class HttpFailure(WebullReadOnlyError):
    def __init__(self, operation: str, status_code: object) -> None:
        super().__init__(f"{operation} returned HTTP status {status_code!r}")
        self.operation = operation
        self.status_code = status_code


class SdkRequestFailure(TransportError):
    """SDK call failed; carries only a safe HTTP status and structured error code."""

    def __init__(
        self,
        operation: str,
        error_type: str,
        http_status: int | None = None,
        error_code: str | None = None,
    ) -> None:
        details = [error_type]
        if http_status is not None:
            details.append(f"http_status={http_status}")
        if error_code is not None:
            details.append(f"error_code={error_code}")
        super().__init__(f"{operation} failed ({', '.join(details)})")
        self.operation = operation
        self.error_type = error_type
        self.http_status = http_status
        self.error_code = error_code


class MalformedResponse(WebullReadOnlyError):
    pass


class AccountSelectionError(WebullReadOnlyError):
    pass


class CurrencyMismatch(WebullReadOnlyError):
    pass


class UnsupportedMultiCurrency(WebullReadOnlyError):
    pass


class TrustGap(StrEnum):
    BROKER_AS_OF_UNAVAILABLE = "BROKER_AS_OF_UNAVAILABLE"
    FRESHNESS_UNVERIFIED = "FRESHNESS_UNVERIFIED"
    SOURCE_ORDERING_UNVERIFIED = "SOURCE_ORDERING_UNVERIFIED"
    ACCOUNT_BINDING_UNVERIFIED = "ACCOUNT_BINDING_UNVERIFIED"
    POSITIONS_COMPLETENESS_UNVERIFIED = "POSITIONS_COMPLETENESS_UNVERIFIED"
    EXTERNAL_FLOWS_UNVERIFIED = "EXTERNAL_FLOWS_UNVERIFIED"
    FROZEN_DAILY_NLV_NOT_ESTABLISHED = "FROZEN_DAILY_NLV_NOT_ESTABLISHED"
    FROZEN_WEEKLY_NLV_NOT_ESTABLISHED = "FROZEN_WEEKLY_NLV_NOT_ESTABLISHED"
    OPENING_RESTATEMENT_UNVERIFIED = "OPENING_RESTATEMENT_UNVERIFIED"
    RECONCILIATION_NOT_PERFORMED = "RECONCILIATION_NOT_PERFORMED"


class ReadOnlyGateway(Protocol):
    """The only Webull surface this adapter may use.

    Each method returns the SDK response object (``status_code`` and
    ``json()``). Implementations must not expose any other broker operation.
    """

    def get_account_list(self) -> Any: ...

    def get_account_balance(self, account_id: str) -> Any: ...

    def get_account_position(self, account_id: str) -> Any: ...


class OfficialSdkGateway:
    """Delegates to the official SDK account API object and nothing else."""

    def __init__(self, account_api: Any) -> None:
        self._account_api = account_api

    def get_account_list(self) -> Any:
        return self._account_api.get_account_list()

    def get_account_balance(self, account_id: str) -> Any:
        return self._account_api.get_account_balance(account_id)

    def get_account_position(self, account_id: str) -> Any:
        return self._account_api.get_account_position(account_id)


def credentials_from_environment(
    environ: Mapping[str, str],
) -> tuple[str, str]:
    missing = [name for name in (APP_KEY_ENV, APP_SECRET_ENV) if not environ.get(name)]
    if missing:
        raise ConfigurationError(
            "missing required environment variables: " + ", ".join(missing)
        )
    return environ[APP_KEY_ENV], environ[APP_SECRET_ENV]


def _build_sdk_trade_client(app_key: str, app_secret: str) -> Any:
    if not app_key or not app_secret:
        raise ConfigurationError("credentials must be non-empty")
    from webull.core.client import ApiClient
    from webull.trade.trade_client import TradeClient

    api_client = ApiClient(app_key, app_secret, SANDBOX_REGION)
    api_client.add_endpoint(SANDBOX_REGION, SANDBOX_HOST)
    silence_sdk_logging(api_client)
    # TradeClient construction performs the SDK's config/token handshake, so a
    # server error can surface here, before any account request is made.
    try:
        return TradeClient(api_client)
    except Exception as error:  # noqa: BLE001 - SDK text is deliberately not chained.
        raise classify_sdk_exception("client initialization", error) from None


def build_official_sandbox_gateway(app_key: str, app_secret: str) -> OfficialSdkGateway:
    """Build the gateway using the official SDK, sandbox endpoint only.

    The SDK is imported lazily so offline tests do not need it. Only the
    account API object is retained from the SDK trade client.
    """
    return OfficialSdkGateway(_build_sdk_trade_client(app_key, app_secret).account_v2)


def silence_sdk_logging(api_client: Any) -> None:
    """Disable every SDK log sink that could carry secrets or raw responses.

    ``TradeClient`` installs an INFO file handler (relative to the working
    directory) and a stdout handler unless a logger is already registered on the
    client, and SDK error paths serialize request details. Registering a
    CRITICAL-only handler through the SDK's public API suppresses that default
    setup; propagation is cut so application handlers never receive SDK records.

    ``webull.core.http.response`` is separate: it owns a DEBUG stderr handler and
    dumps request headers (signature material) and raw response bodies when
    ``WEBULL_API_DEBUG=sdk``. It is disabled outright, so that variable cannot
    re-enable the dump.
    """
    import webull.core.http.response  # noqa: F401 - ensure its logger setup already ran

    api_client.set_stream_logger(log_level=logging.CRITICAL, stream=io.StringIO())
    for name in ("webull", "webull.core"):
        sdk_logger = logging.getLogger(name)
        sdk_logger.setLevel(logging.CRITICAL)
        sdk_logger.propagate = False
    response_logger = logging.getLogger("webull.core.http.response")
    response_logger.handlers[:] = [logging.NullHandler()]
    response_logger.setLevel(logging.CRITICAL)
    response_logger.propagate = False
    response_logger.disabled = True


@dataclass(frozen=True)
class AccountSummary:
    """Sanitized discovery row; never carries the full identifier."""

    account_type: str | None
    id_suffix: str


@dataclass(frozen=True)
class MonetaryField:
    value: Decimal
    currency: str
    source_path: str


@dataclass(frozen=True)
class PositionsEvidence:
    count: int
    empty_array_observed: bool
    # Entry schema is unverified; entries are counted, never interpreted.
    entries_parsed: bool = False
    complete: bool = False


@dataclass(frozen=True)
class SandboxAccountSnapshot:
    account_id: str = field(repr=False)
    id_suffix: str
    account_type: str | None
    net_liquidation: MonetaryField
    net_liquidation_cross_check_path: str
    currency: str
    optional_amounts: Mapping[str, MonetaryField]
    positions: PositionsEvidence
    observed_started_at: datetime
    observed_at: datetime
    # No authoritative broker valuation timestamp has been demonstrated.
    broker_as_of: datetime | None
    trust_gaps: frozenset[TrustGap]
    enforcement_eligible: bool = False


def _suffix(account_id: str) -> str:
    return "****" + account_id[-4:]


def _decimal(value: object, path: str) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise MalformedResponse(f"{path} must be a decimal string or integer")
    text = str(value).strip()
    if not _DECIMAL_PATTERN.fullmatch(text):
        raise MalformedResponse(f"{path} is not a valid plain decimal")
    try:
        parsed = Decimal(text)
    except InvalidOperation as error:
        raise MalformedResponse(f"{path} is not a valid decimal") from error
    if not parsed.is_finite():
        raise MalformedResponse(f"{path} must be finite")
    return parsed


def _currency(value: object, path: str) -> str:
    if not isinstance(value, str):
        raise MalformedResponse(f"{path} must be a string")
    code = value.strip().upper()
    if len(code) != 3 or not code.isascii() or not code.isalpha():
        raise MalformedResponse(f"{path} must be a three-letter currency code")
    return code


def _json_body(response: Any, operation: str) -> object:
    status = getattr(response, "status_code", None)
    if isinstance(status, bool) or not isinstance(status, int) or status != 200:
        raise HttpFailure(operation, status)
    try:
        return response.json()
    except Exception as error:  # noqa: BLE001 - SDK text is deliberately not chained.
        raise MalformedResponse(
            f"{operation} body is not valid JSON ({type(error).__name__})"
        ) from None


def _safe_status(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or not 100 <= value <= 599:
        return None
    return value


def _safe_code(value: object) -> str | None:
    if isinstance(value, str) and _SAFE_CODE_PATTERN.fullmatch(value):
        return value
    return None


def classify_sdk_exception(operation: str, error: BaseException) -> SdkRequestFailure:
    """Reduce an SDK exception to type, HTTP status and a structured code only.

    The SDK's exception text embeds request details and response bodies, so it
    is never read. Attributes are used only when they match strict shapes.
    """
    return SdkRequestFailure(
        operation,
        type(error).__name__,
        _safe_status(getattr(error, "http_status", None)),
        _safe_code(getattr(error, "error_code", None)),
    )


def _call(operation: str, func: Callable[[], Any]) -> Any:
    try:
        return func()
    except WebullReadOnlyError:
        raise
    except Exception as error:  # noqa: BLE001 - SDK text is deliberately not chained.
        raise classify_sdk_exception(operation, error) from None


def _unwrap_list(body: object, operation: str) -> list[object]:
    if isinstance(body, list):
        return body
    if (
        isinstance(body, dict)
        and set(body) == {"data"}
        and isinstance(body["data"], list)
    ):
        return body["data"]
    raise MalformedResponse(f"{operation} has an unexpected response shape")


def _unwrap_balance(body: object) -> dict[str, Any]:
    if isinstance(body, dict):
        if "total_net_liquidation_value" in body or "account_currency_assets" in body:
            return body
        if set(body) == {"data"} and isinstance(body["data"], dict):
            return body["data"]
    raise MalformedResponse("balance has an unexpected response shape")


def _fetch_accounts(gateway: ReadOnlyGateway) -> list[dict[str, Any]]:
    response = _call("account list", gateway.get_account_list)
    entries = _unwrap_list(_json_body(response, "account list"), "account list")
    accounts: list[dict[str, Any]] = []
    for entry in entries:
        if (
            not isinstance(entry, dict)
            or not isinstance(entry.get("account_id"), str)
            or not entry["account_id"].strip()
        ):
            raise MalformedResponse("account list entry is malformed")
        account_type = entry.get("account_type")
        if account_type is not None and not isinstance(account_type, str):
            raise MalformedResponse("account list entry type is malformed")
        accounts.append(entry)
    return accounts


@dataclass(frozen=True)
class AccountChoice:
    """A selectable API account. The full identifier is hidden from repr."""

    index: int
    account_type: str | None
    id_suffix: str
    account_id: str = field(repr=False)


def list_account_choices(
    gateway: ReadOnlyGateway, *, account_type: str | None = None
) -> tuple[AccountChoice, ...]:
    """Numbered choices for an operator to pick from; selects nothing itself.

    Identifiers returned more than once are excluded rather than guessed at.
    Numbering is the display order and carries no meaning beyond this listing.
    """
    entries = _fetch_accounts(gateway)
    counts: dict[str, int] = {}
    for entry in entries:
        counts[entry["account_id"]] = counts.get(entry["account_id"], 0) + 1
    unique = [
        entry
        for entry in entries
        if counts[entry["account_id"]] == 1
        and (account_type is None or entry.get("account_type") == account_type)
    ]
    return tuple(
        AccountChoice(
            index=position,
            account_type=entry.get("account_type"),
            id_suffix=_suffix(entry["account_id"]),
            account_id=entry["account_id"],
        )
        for position, entry in enumerate(unique, start=1)
    )


class WebullSandboxReadOnlyAdapter:
    def __init__(
        self,
        gateway: ReadOnlyGateway,
        *,
        selected_account_id: str,
        expected_currency: str | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        if (
            not isinstance(selected_account_id, str)
            or not selected_account_id.strip()
            or selected_account_id != selected_account_id.strip()
            or "*" in selected_account_id
            or any(char.isspace() for char in selected_account_id)
        ):
            raise ConfigurationError(
                "selected_account_id must be an explicit full account identifier"
            )
        self._gateway = gateway
        self._selected_account_id = selected_account_id
        self._expected_currency = (
            _currency(expected_currency, "expected_currency")
            if expected_currency is not None
            else None
        )
        self._clock = clock

    def _accounts(self) -> list[dict[str, Any]]:
        return _fetch_accounts(self._gateway)

    def discover_accounts(self) -> tuple[AccountSummary, ...]:
        """Sanitized listing for local comparison; selects nothing."""
        return tuple(
            AccountSummary(entry.get("account_type"), _suffix(entry["account_id"]))
            for entry in self._accounts()
        )

    def fetch_snapshot(self) -> SandboxAccountSnapshot:
        started_at = self._clock()
        matches = [
            entry
            for entry in self._accounts()
            if entry["account_id"] == self._selected_account_id
        ]
        if not matches:
            raise AccountSelectionError("selected account was not returned by broker")
        if len(matches) > 1:
            raise AccountSelectionError("selected account identifier is ambiguous")
        account_type = matches[0].get("account_type")

        balance_response = _call(
            "balance",
            lambda: self._gateway.get_account_balance(self._selected_account_id),
        )
        balance = _unwrap_balance(_json_body(balance_response, "balance"))
        nlv, currency, cross_check_path, optional = self._parse_balance(balance)

        positions_response = _call(
            "positions",
            lambda: self._gateway.get_account_position(self._selected_account_id),
        )
        entries = _unwrap_list(_json_body(positions_response, "positions"), "positions")
        if not all(isinstance(entry, dict) for entry in entries):
            raise MalformedResponse("positions entry is malformed")
        positions = PositionsEvidence(
            count=len(entries), empty_array_observed=not entries
        )

        observed_at = self._clock()
        for stamp in (started_at, observed_at):
            if stamp.tzinfo is None or stamp.utcoffset() is None:
                raise ConfigurationError("clock must return timezone-aware datetimes")
        return SandboxAccountSnapshot(
            account_id=self._selected_account_id,
            id_suffix=_suffix(self._selected_account_id),
            account_type=account_type,
            net_liquidation=nlv,
            net_liquidation_cross_check_path=cross_check_path,
            currency=currency,
            optional_amounts=optional,
            positions=positions,
            observed_started_at=started_at.astimezone(UTC),
            observed_at=observed_at.astimezone(UTC),
            broker_as_of=None,
            trust_gaps=frozenset(TrustGap),
        )

    def _parse_balance(
        self, balance: dict[str, Any]
    ) -> tuple[MonetaryField, str, str, dict[str, MonetaryField]]:
        assets = balance.get("account_currency_assets")
        if not isinstance(assets, list) or not assets:
            raise MalformedResponse("account_currency_assets must be a non-empty list")
        if len(assets) > 1:
            raise UnsupportedMultiCurrency(
                "multiple account_currency_assets entries are not aggregated"
            )
        item = assets[0]
        base = "balance.account_currency_assets[0]"
        if not isinstance(item, dict):
            raise MalformedResponse(f"{base} must be an object")

        currency = _currency(item.get("currency"), f"{base}.currency")
        if self._expected_currency is not None and currency != self._expected_currency:
            raise CurrencyMismatch("account currency differs from expected currency")
        if "total_asset_currency" in balance and (
            _currency(balance["total_asset_currency"], "balance.total_asset_currency")
            != currency
        ):
            raise CurrencyMismatch("total and asset currencies differ")

        total_path = "balance.total_net_liquidation_value"
        item_path = f"{base}.net_liquidation_value"
        total = _decimal(balance.get("total_net_liquidation_value"), total_path)
        item_nlv = _decimal(item.get("net_liquidation_value"), item_path)
        if total != item_nlv:
            raise MalformedResponse("net liquidation values disagree")
        if total <= 0:
            raise MalformedResponse("net liquidation value must be positive")

        optional = {
            name: MonetaryField(
                _decimal(item[name], f"{base}.{name}"), currency, f"{base}.{name}"
            )
            for name in _OPTIONAL_AMOUNT_FIELDS
            if name in item
        }
        return MonetaryField(total, currency, item_path), currency, total_path, optional
