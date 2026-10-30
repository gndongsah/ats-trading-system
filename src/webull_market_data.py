"""ATS-036A read-only Webull market-data adapter and probe.

This module deliberately exposes market-data reads only. It never constructs a
trade client, account client, order request, or broker submission path.

Webull's documented stock bar time is preserved as the provider event timestamp.
available_at is the local collector time at which the completed-bar response was
received. This is conservative for point-in-time replay: information cannot
become replay-visible before collection.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Callable, Protocol

from src.market_data import Bar, MarketEvent
from src.webull_sandbox_credentials import load_sandbox_credentials
from src.webull_sandbox_readonly import (
    SANDBOX_HOST,
    SANDBOX_REGION,
    ConfigurationError,
    HttpFailure,
    MalformedResponse,
    classify_sdk_exception,
    silence_sdk_logging,
)

SOURCE = "webull-openapi-sandbox-v1"


class MarketDataGateway(Protocol):
    def get_snapshot(self, *, symbols: list[str], category: str) -> Any: ...

    def get_history_bar(
        self,
        *,
        symbol: str,
        category: str,
        timespan: str,
        count: str,
        real_time_required: bool,
        trading_sessions: str,
    ) -> Any: ...


@dataclass(frozen=True)
class SnapshotEvidence:
    symbol: str
    price: Decimal
    last_trade_time: datetime | None
    observed_at: datetime


@dataclass(frozen=True)
class MarketDataProbeResult:
    snapshot: SnapshotEvidence
    event: MarketEvent


class OfficialSdkMarketDataGateway:
    """Narrow wrapper around the official SDK market-data object."""

    def __init__(self, market_data: Any) -> None:
        self._market_data = market_data

    def get_snapshot(self, *, symbols: list[str], category: str) -> Any:
        return self._market_data.get_snapshot(symbols=symbols, category=category)

    def get_history_bar(
        self,
        *,
        symbol: str,
        category: str,
        timespan: str,
        count: str,
        real_time_required: bool,
        trading_sessions: str,
    ) -> Any:
        return self._market_data.get_history_bar(
            symbol=symbol,
            category=category,
            timespan=timespan,
            count=count,
            real_time_required=real_time_required,
            trading_sessions=trading_sessions,
        )


def build_official_sandbox_market_data_gateway(
    app_key: str, app_secret: str
) -> OfficialSdkMarketDataGateway:
    if not app_key or not app_secret:
        raise ConfigurationError("credentials must be non-empty")

    from webull.core.client import ApiClient
    from webull.data.data_client import DataClient

    api_client = ApiClient(app_key, app_secret, SANDBOX_REGION)
    api_client.add_endpoint(SANDBOX_REGION, SANDBOX_HOST)
    silence_sdk_logging(api_client)
    try:
        client = DataClient(api_client)
    except Exception as error:  # noqa: BLE001 - SDK text may include request details.
        raise classify_sdk_exception("market-data client initialization", error) from None
    return OfficialSdkMarketDataGateway(client.market_data)


def _response_json(response: Any, operation: str) -> object:
    status = getattr(response, "status_code", None)
    if isinstance(status, bool) or not isinstance(status, int) or status != 200:
        raise HttpFailure(operation, status)
    try:
        return response.json()
    except Exception as error:  # noqa: BLE001
        raise MalformedResponse(
            f"{operation} body is not valid JSON ({type(error).__name__})"
        ) from None


def _decimal(value: object, field: str) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        raise MalformedResponse(f"{field} must be numeric")
    try:
        parsed = Decimal(str(value))
    except InvalidOperation:
        raise MalformedResponse(f"{field} must be numeric") from None
    if not parsed.is_finite() or parsed <= 0:
        raise MalformedResponse(f"{field} must be positive and finite")
    return parsed


def _volume(value: object) -> int:
    if isinstance(value, bool):
        raise MalformedResponse("volume must be a non-negative integer")
    text = str(value)
    if not text.isascii() or not text.isdigit():
        raise MalformedResponse("volume must be a non-negative integer")
    return int(text)


def _timestamp(value: object, field: str) -> datetime:
    if isinstance(value, bool):
        raise MalformedResponse(f"{field} timestamp is malformed")
    if isinstance(value, (int, float)):
        raw = float(value)
        seconds = raw / 1000 if raw >= 10_000_000_000 else raw
        try:
            return datetime.fromtimestamp(seconds, tz=UTC)
        except (OverflowError, OSError, ValueError):
            raise MalformedResponse(f"{field} timestamp is malformed") from None
    if not isinstance(value, str) or not value.strip():
        raise MalformedResponse(f"{field} timestamp is malformed")
    text = value.strip()
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        raise MalformedResponse(f"{field} timestamp is malformed") from None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise MalformedResponse(f"{field} timestamp must include timezone")
    return parsed.astimezone(UTC)


def _snapshot_rows(body: object) -> list[dict[str, Any]]:
    if isinstance(body, dict) and set(body) == {"data"}:
        body = body["data"]
    if not isinstance(body, list) or not all(isinstance(row, dict) for row in body):
        raise MalformedResponse("snapshot has an unexpected response shape")
    return body


def _shape(value: object) -> str:
    if isinstance(value, dict):
        keys = ",".join(sorted(str(key) for key in value.keys()))
        return f"dict(keys=[{keys}])"
    if isinstance(value, list):
        item_type = type(value[0]).__name__ if value else "empty"
        return f"list(len={len(value)},first={item_type})"
    return type(value).__name__


def _bar_rows(body: object, symbol: str) -> list[dict[str, Any]]:
    if isinstance(body, dict) and set(body) == {"data"}:
        body = body["data"]
    elif isinstance(body, dict) and set(body) == {"result"}:
        body = body["result"]
    if not isinstance(body, list) or not all(isinstance(row, dict) for row in body):
        raise MalformedResponse(
        "bar response has an unexpected response shape: " + _shape(body)
    )
    matches = [row for row in body if row.get("symbol") == symbol]
    if len(matches) != 1:
        raise MalformedResponse("bar response must contain exactly one requested bar")
    return matches


class WebullMarketDataAdapter:
    def __init__(
        self,
        gateway: MarketDataGateway,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        source: str = SOURCE,
    ) -> None:
        self._gateway = gateway
        self._clock = clock
        self._source = source

    def fetch_snapshot(self, symbol: str, *, category: str) -> SnapshotEvidence:
        observed_at = self._clock()
        if observed_at.tzinfo is None or observed_at.utcoffset() is None:
            raise ConfigurationError("clock must return timezone-aware datetimes")
        try:
            response = self._gateway.get_snapshot(symbols=[symbol], category=category)
        except Exception as error:  # noqa: BLE001
            if isinstance(error, (HttpFailure, MalformedResponse)):
                raise
            raise classify_sdk_exception("stock snapshot", error) from None
        rows = _snapshot_rows(_response_json(response, "stock snapshot"))
        matches = [row for row in rows if row.get("symbol") == symbol]
        if len(matches) != 1:
            raise MalformedResponse("snapshot must contain exactly one requested symbol")
        row = matches[0]
        trade_time = row.get("last_trade_time")
        return SnapshotEvidence(
            symbol=symbol,
            price=_decimal(row.get("price"), "snapshot.price"),
            last_trade_time=(
                _timestamp(trade_time, "snapshot.last_trade_time")
                if trade_time not in (None, "")
                else None
            ),
            observed_at=observed_at.astimezone(UTC),
        )

    def fetch_completed_1m_bar(self, symbol: str, *, category: str) -> MarketEvent:
        observed_at = self._clock()
        if observed_at.tzinfo is None or observed_at.utcoffset() is None:
            raise ConfigurationError("clock must return timezone-aware datetimes")
        try:
            response = self._gateway.get_history_bar(
                symbol=symbol,
                category=category,
                timespan="M1",
                count="1",
                real_time_required=True,
                trading_sessions="RTH",
            )
        except Exception as error:  # noqa: BLE001
            if isinstance(error, (HttpFailure, MalformedResponse)):
                raise
            raise classify_sdk_exception("stock completed 1m bar", error) from None

        bars = _bar_rows(_response_json(response, "stock completed 1m bar"), symbol)
        row = bars[0]
        provider_time = _timestamp(row.get("time"), "bar.time")
        available_at = observed_at.astimezone(UTC)
        if provider_time > available_at:
            raise MalformedResponse("bar timestamp cannot be in the collector future")
        return MarketEvent(
            source=self._source,
            symbol=symbol,
            event_time=provider_time,
            available_at=available_at,
            revision=1,
            payload=Bar(
                interval="1m",
                open=_decimal(row.get("open"), "bar.open"),
                high=_decimal(row.get("high"), "bar.high"),
                low=_decimal(row.get("low"), "bar.low"),
                close=_decimal(row.get("close"), "bar.close"),
                volume=_volume(row.get("volume")),
            ),
        )

    def probe(self, symbol: str, *, category: str) -> MarketDataProbeResult:
        return MarketDataProbeResult(
            snapshot=self.fetch_snapshot(symbol, category=category),
            event=self.fetch_completed_1m_bar(symbol, category=category),
        )


def build_keyring_sandbox_adapter() -> WebullMarketDataAdapter:
    credentials = load_sandbox_credentials(allow_environment_fallback=False)
    app_key, app_secret = credentials.reveal()
    return WebullMarketDataAdapter(
        build_official_sandbox_market_data_gateway(app_key, app_secret)
    )


def main() -> int:
    adapter = build_keyring_sandbox_adapter()
    try:
        result = adapter.probe("SPY", category="US_ETF")
    except Exception as error:  # noqa: BLE001
        print("market_data_probe: FAIL")
        print(f"error_type: {type(error).__name__}")
        if isinstance(error, MalformedResponse):
            print(f"validation_reason: {error}")
        return 1

    print("market_data_probe: PASS")
    print("symbol: SPY")
    print(f"snapshot_price: {result.snapshot.price}")
    print(
        "snapshot_last_trade_time: "
        + (
            result.snapshot.last_trade_time.isoformat()
            if result.snapshot.last_trade_time is not None
            else "UNAVAILABLE"
        )
    )
    print(f"bar_event_time: {result.event.event_time.isoformat()}")
    print(f"bar_available_at: {result.event.available_at.isoformat()}")
    print(f"bar_close: {result.event.payload.close}")
    print(f"bar_volume: {result.event.payload.volume}")
    print("broker_order_submission: DISABLED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
