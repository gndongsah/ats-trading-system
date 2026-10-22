"""Offline tests for the ATS-009C2 Webull sandbox read-only adapter (synthetic data)."""

import io
import logging
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

import src.webull_sandbox_readonly as module
from src.webull_sandbox_readonly import (
    AccountSelectionError,
    ConfigurationError,
    CurrencyMismatch,
    HttpFailure,
    MalformedResponse,
    OfficialSdkGateway,
    TransportError,
    TrustGap,
    UnsupportedMultiCurrency,
    WebullSandboxReadOnlyAdapter,
    credentials_from_environment,
    list_account_choices,
    silence_sdk_logging,
)

ACCOUNT_A = "SYNTH-ACCOUNT-AAAA-0001"
ACCOUNT_B = "SYNTH-ACCOUNT-BBBB-0002"


class FakeResponse:
    def __init__(self, body, status_code=200, json_error=False):
        self.status_code = status_code
        self._body = body
        self._json_error = json_error

    def json(self):
        if self._json_error:
            raise ValueError("not json")
        return self._body


def good_balance(**overrides):
    body = {
        "total_net_liquidation_value": "10000.50",
        "account_currency_assets": [
            {
                "currency": "USD",
                "net_liquidation_value": "10000.50",
                "market_value": "0",
                "cash_balance": "10000.50",
                "buying_power": "10000.50",
                "unrealized_profit_loss": "0.00",
            }
        ],
    }
    body.update(overrides)
    return body


class FakeGateway:
    def __init__(self, accounts=None, balance=None, positions=None):
        self.accounts = (
            accounts
            if accounts is not None
            else FakeResponse(
                [
                    {"account_id": ACCOUNT_A, "account_type": "CASH"},
                    {"account_id": ACCOUNT_B, "account_type": "CASH"},
                ]
            )
        )
        self.balance = balance if balance is not None else FakeResponse(good_balance())
        self.positions = positions if positions is not None else FakeResponse([])
        self.calls = []

    def get_account_list(self):
        self.calls.append(("list",))
        return self._result(self.accounts)

    def get_account_balance(self, account_id):
        self.calls.append(("balance", account_id))
        return self._result(self.balance)

    def get_account_position(self, account_id):
        self.calls.append(("positions", account_id))
        return self._result(self.positions)

    @staticmethod
    def _result(value):
        if isinstance(value, Exception):
            raise value
        return value


def adapter(gateway, account_id=ACCOUNT_A, **kwargs):
    return WebullSandboxReadOnlyAdapter(
        gateway, selected_account_id=account_id, **kwargs
    )


def test_success_snapshot_preserves_exact_decimals_currency_and_provenance():
    gateway = FakeGateway()
    snapshot = adapter(gateway, expected_currency="usd").fetch_snapshot()

    assert snapshot.net_liquidation.value == Decimal("10000.50")
    assert isinstance(snapshot.net_liquidation.value, Decimal)
    assert snapshot.currency == "USD"
    assert snapshot.net_liquidation.source_path == (
        "balance.account_currency_assets[0].net_liquidation_value"
    )
    assert snapshot.net_liquidation_cross_check_path == (
        "balance.total_net_liquidation_value"
    )
    assert snapshot.optional_amounts["buying_power"].value == Decimal("10000.50")
    assert snapshot.optional_amounts["buying_power"].currency == "USD"
    assert "settled_cash" not in snapshot.optional_amounts
    assert snapshot.account_type == "CASH"
    assert snapshot.id_suffix == "****0001"
    assert gateway.calls == [
        ("list",),
        ("balance", ACCOUNT_A),
        ("positions", ACCOUNT_A),
    ]


def test_snapshot_repr_and_errors_do_not_leak_full_identifier():
    snapshot = adapter(FakeGateway()).fetch_snapshot()
    assert ACCOUNT_A not in repr(snapshot)

    with pytest.raises(AccountSelectionError) as error:
        adapter(FakeGateway(), account_id="SYNTH-MISSING-9999").fetch_snapshot()
    assert "SYNTH-MISSING-9999" not in str(error.value)


def test_observed_time_is_local_and_broker_as_of_is_never_invented():
    ticks = iter(
        [
            datetime(2026, 10, 21, 20, 0, 0, tzinfo=UTC),
            datetime(2026, 10, 21, 20, 0, 2, tzinfo=UTC),
        ]
    )
    body = good_balance(update_time="2026-10-21T19:00:00Z", as_of="x")
    snapshot = adapter(
        FakeGateway(balance=FakeResponse(body)), clock=lambda: next(ticks)
    ).fetch_snapshot()

    assert snapshot.observed_started_at < snapshot.observed_at
    assert snapshot.broker_as_of is None
    assert TrustGap.BROKER_AS_OF_UNAVAILABLE in snapshot.trust_gaps


def test_naive_clock_fails_closed():
    with pytest.raises(ConfigurationError):
        adapter(
            FakeGateway(), clock=lambda: datetime(2026, 10, 21, 12, 0)
        ).fetch_snapshot()


def test_all_trust_gaps_open_and_not_enforcement_eligible():
    snapshot = adapter(FakeGateway()).fetch_snapshot()
    assert snapshot.trust_gaps == frozenset(TrustGap)
    assert snapshot.enforcement_eligible is False
    assert {
        TrustGap.EXTERNAL_FLOWS_UNVERIFIED,
        TrustGap.FROZEN_DAILY_NLV_NOT_ESTABLISHED,
        TrustGap.FROZEN_WEEKLY_NLV_NOT_ESTABLISHED,
        TrustGap.SOURCE_ORDERING_UNVERIFIED,
        TrustGap.OPENING_RESTATEMENT_UNVERIFIED,
        TrustGap.ACCOUNT_BINDING_UNVERIFIED,
    } <= snapshot.trust_gaps


def test_empty_positions_are_recorded_but_not_complete():
    positions = adapter(FakeGateway()).fetch_snapshot().positions
    assert positions.count == 0
    assert positions.empty_array_observed is True
    assert positions.complete is False
    assert positions.entries_parsed is False


def test_nonempty_positions_counted_not_interpreted():
    gateway = FakeGateway(positions=FakeResponse({"data": [{"x": 1}, {"y": 2}]}))
    positions = adapter(gateway).fetch_snapshot().positions
    assert positions.count == 2
    assert positions.empty_array_observed is False
    assert positions.entries_parsed is False


def test_discover_accounts_is_sanitized_and_selects_nothing():
    gateway = FakeGateway()
    rows = adapter(gateway).discover_accounts()
    assert [(row.account_type, row.id_suffix) for row in rows] == [
        ("CASH", "****0001"),
        ("CASH", "****0002"),
    ]
    assert gateway.calls == [("list",)]


@pytest.mark.parametrize(
    "account_id",
    ["", "   ", "****0001", " " + ACCOUNT_A, ACCOUNT_A + " ", "SYNTH ACCOUNT", "0001"],
)
def test_account_selection_must_be_explicit_full_identifier(account_id):
    if account_id == "0001":
        with pytest.raises(AccountSelectionError):
            adapter(FakeGateway(), account_id=account_id).fetch_snapshot()
    else:
        with pytest.raises(ConfigurationError):
            adapter(FakeGateway(), account_id=account_id)


def test_no_auto_selection_by_suffix_order_or_type():
    gateway = FakeGateway()
    with pytest.raises(AccountSelectionError):
        adapter(gateway, account_id=ACCOUNT_A[-4:]).fetch_snapshot()
    assert gateway.calls == [("list",)]


def test_duplicate_selected_identifier_is_ambiguous():
    accounts = FakeResponse(
        [
            {"account_id": ACCOUNT_A, "account_type": "CASH"},
            {"account_id": ACCOUNT_A, "account_type": "MARGIN"},
        ]
    )
    gateway = FakeGateway(accounts=accounts)
    with pytest.raises(AccountSelectionError, match="ambiguous"):
        adapter(gateway).fetch_snapshot()
    assert gateway.calls == [("list",)]


@pytest.mark.parametrize(
    "accounts",
    [
        {"unexpected": []},
        "text",
        [None],
        [{"account_type": "CASH"}],
        [{"account_id": 123}],
        [{"account_id": "  "}],
        [{"account_id": ACCOUNT_A, "account_type": 5}],
    ],
)
def test_malformed_account_list_fails_closed(accounts):
    with pytest.raises(MalformedResponse):
        adapter(FakeGateway(accounts=FakeResponse(accounts))).fetch_snapshot()


@pytest.mark.parametrize("status", [401, 403, 500, None, True, "200"])
def test_http_failures_fail_closed_for_each_endpoint(status):
    for field in ("accounts", "balance", "positions"):
        gateway = FakeGateway()
        setattr(gateway, field, FakeResponse([], status_code=status))
        with pytest.raises(HttpFailure):
            adapter(gateway).fetch_snapshot()


def test_invalid_json_and_transport_exceptions_fail_closed():
    with pytest.raises(MalformedResponse):
        adapter(
            FakeGateway(balance=FakeResponse(None, json_error=True))
        ).fetch_snapshot()
    with pytest.raises(TransportError) as error:
        adapter(FakeGateway(positions=RuntimeError("secret detail"))).fetch_snapshot()
    assert "secret detail" not in str(error.value)


@pytest.mark.parametrize(
    "balance",
    [
        [],
        {"unrelated": 1},
        {"account_currency_assets": []},
        {"total_net_liquidation_value": "1", "account_currency_assets": "x"},
        {"total_net_liquidation_value": "1", "account_currency_assets": [None]},
        good_balance(total_net_liquidation_value=None),
        good_balance(total_net_liquidation_value=100.5),
        good_balance(total_net_liquidation_value=True),
        good_balance(total_net_liquidation_value="NaN"),
        good_balance(total_net_liquidation_value="Infinity"),
        good_balance(total_net_liquidation_value="1,000.00"),
        good_balance(total_net_liquidation_value="1_000"),
        good_balance(total_net_liquidation_value=""),
        good_balance(total_net_liquidation_value="0"),
        good_balance(total_net_liquidation_value="-5"),
        good_balance(total_net_liquidation_value="10000.51"),
    ],
)
def test_malformed_balance_fails_closed(balance):
    with pytest.raises(MalformedResponse):
        adapter(FakeGateway(balance=FakeResponse(balance))).fetch_snapshot()


@pytest.mark.parametrize("missing", ["currency", "net_liquidation_value"])
def test_missing_item_field_fails_closed(missing):
    body = good_balance()
    del body["account_currency_assets"][0][missing]
    with pytest.raises(MalformedResponse):
        adapter(FakeGateway(balance=FakeResponse(body))).fetch_snapshot()


@pytest.mark.parametrize("currency", ["", "US", "USDT", "12$", 5])
def test_invalid_currency_fails_closed(currency):
    body = good_balance()
    body["account_currency_assets"][0]["currency"] = currency
    with pytest.raises(MalformedResponse):
        adapter(FakeGateway(balance=FakeResponse(body))).fetch_snapshot()


def test_malformed_optional_amount_fails_closed():
    body = good_balance()
    body["account_currency_assets"][0]["buying_power"] = 12.5
    with pytest.raises(MalformedResponse):
        adapter(FakeGateway(balance=FakeResponse(body))).fetch_snapshot()


def test_currency_mismatches_fail_closed():
    with pytest.raises(CurrencyMismatch):
        adapter(FakeGateway(), expected_currency="HKD").fetch_snapshot()
    gateway = FakeGateway(
        balance=FakeResponse(good_balance(total_asset_currency="HKD"))
    )
    with pytest.raises(CurrencyMismatch):
        adapter(gateway).fetch_snapshot()


def test_multi_currency_is_rejected_not_aggregated():
    body = good_balance()
    body["account_currency_assets"].append(
        {"currency": "HKD", "net_liquidation_value": "5"}
    )
    with pytest.raises(UnsupportedMultiCurrency):
        adapter(FakeGateway(balance=FakeResponse(body))).fetch_snapshot()


def test_wrapped_data_shapes_accepted_and_positions_shape_validated():
    gateway = FakeGateway(
        accounts=FakeResponse({"data": [{"account_id": ACCOUNT_A}]}),
        balance=FakeResponse({"data": good_balance()}),
    )
    snapshot = adapter(gateway).fetch_snapshot()
    assert snapshot.account_type is None

    for bad in ({"data": "x"}, {"other": []}, [1], "x"):
        with pytest.raises(MalformedResponse):
            adapter(FakeGateway(positions=FakeResponse(bad))).fetch_snapshot()


def test_official_gateway_delegates_only_three_read_operations():
    class FakeAccountApi:
        def __init__(self):
            self.calls = []

        def get_account_list(self):
            self.calls.append("list")
            return "L"

        def get_account_balance(self, account_id):
            self.calls.append(("balance", account_id))
            return "B"

        def get_account_position(self, account_id):
            self.calls.append(("position", account_id))
            return "P"

        def place_order(self, *args):  # must stay unreachable through the gateway
            raise AssertionError("order operation reached")

    api = FakeAccountApi()
    gateway = OfficialSdkGateway(api)
    assert (
        gateway.get_account_list(),
        gateway.get_account_balance("id"),
        gateway.get_account_position("id"),
    ) == ("L", "B", "P")
    assert api.calls == ["list", ("balance", "id"), ("position", "id")]
    public = {name for name in dir(gateway) if not name.startswith("_")}
    assert public == {"get_account_list", "get_account_balance", "get_account_position"}


def test_module_has_no_order_capability_or_credential_output():
    source = Path(module.__file__).read_text(encoding="utf-8")
    for token in (
        "place_order",
        "cancel_order",
        "replace_order",
        "modify_order",
        "order_v2",
        "print(",
        "os.environ",
    ):
        assert token not in source
    assert module.SANDBOX_HOST == "api.sandbox.webull.com"


def test_credentials_from_environment_reports_names_only():
    assert credentials_from_environment(
        {"WEBULL_APP_KEY": "k", "WEBULL_APP_SECRET": "s"}
    ) == ("k", "s")
    with pytest.raises(ConfigurationError) as error:
        credentials_from_environment({"WEBULL_APP_KEY": "super-secret-value"})
    assert "WEBULL_APP_SECRET" in str(error.value)
    assert "super-secret-value" not in str(error.value)


def test_account_choices_are_numbered_masked_and_select_nothing():
    gateway = FakeGateway(
        accounts=FakeResponse(
            [
                {"account_id": ACCOUNT_A, "account_type": "CASH"},
                {"account_id": "OTHER-MARGIN-0003", "account_type": "MARGIN"},
                {"account_id": ACCOUNT_B, "account_type": "CASH"},
            ]
        )
    )
    choices = list_account_choices(gateway, account_type="CASH")

    assert [(c.index, c.account_type, c.id_suffix) for c in choices] == [
        (1, "CASH", "****0001"),
        (2, "CASH", "****0002"),
    ]
    assert choices[1].account_id == ACCOUNT_B
    assert ACCOUNT_A not in repr(choices) and ACCOUNT_B not in repr(choices)
    assert gateway.calls == [("list",)]


def test_account_choices_exclude_duplicate_identifiers_and_fail_closed():
    duplicated = FakeResponse(
        [
            {"account_id": ACCOUNT_A, "account_type": "CASH"},
            {"account_id": ACCOUNT_A, "account_type": "CASH"},
            {"account_id": ACCOUNT_B, "account_type": "CASH"},
        ]
    )
    choices = list_account_choices(FakeGateway(accounts=duplicated))
    assert [c.account_id for c in choices] == [ACCOUNT_B]

    with pytest.raises(HttpFailure):
        list_account_choices(FakeGateway(accounts=FakeResponse([], status_code=401)))
    with pytest.raises(MalformedResponse):
        list_account_choices(FakeGateway(accounts=FakeResponse({"bad": 1})))


def test_chosen_account_flows_to_adapter_unchanged():
    gateway = FakeGateway()
    chosen = list_account_choices(gateway)[1]
    snapshot = adapter(gateway, account_id=chosen.account_id).fetch_snapshot()
    assert snapshot.id_suffix == "****0002"
    assert ("balance", ACCOUNT_B) in gateway.calls


def test_sdk_exceptions_are_not_chained_into_tracebacks():
    gateway = FakeGateway(positions=RuntimeError("synthetic-secret-detail"))
    with pytest.raises(TransportError) as error:
        adapter(gateway).fetch_snapshot()
    assert error.value.__cause__ is None
    assert error.value.__suppress_context__ is True

    gateway = FakeGateway(balance=FakeResponse(None, json_error=True))
    with pytest.raises(MalformedResponse) as error:
        adapter(gateway).fetch_snapshot()
    assert error.value.__cause__ is None
    assert error.value.__suppress_context__ is True


def test_real_sdk_default_logging_is_suppressed(tmp_path, monkeypatch, capsys):
    client_module = pytest.importorskip("webull.core.client")
    trade_module = pytest.importorskip("webull.trade.trade_client")
    monkeypatch.chdir(tmp_path)
    names = ("webull", "webull.core")
    saved = {
        name: (
            logging.getLogger(name).level,
            logging.getLogger(name).propagate,
            list(logging.getLogger(name).handlers),
        )
        for name in names
    }
    root_sink = io.StringIO()
    root_handler = logging.StreamHandler(root_sink)
    root_handler.setLevel(logging.DEBUG)
    logging.getLogger().addHandler(root_handler)
    try:
        api_client = client_module.ApiClient("synthetic-key", "synthetic-secret", "us")
        silence_sdk_logging(api_client)
        # Runs the real SDK default-handler logic, which would otherwise create
        # webull_trade_sdk.log in the working directory and attach stdout.
        trade_module.TradeClient._init_logger(object(), api_client)
        for name in (
            "webull.core.client",
            "webull.core.http.initializer.client_initializer",
            "webull.core.http.initializer.token.token_manager",
        ):
            sdk_logger = logging.getLogger(name)
            for emit in (
                sdk_logger.debug,
                sdk_logger.info,
                sdk_logger.warning,
                sdk_logger.error,
            ):
                emit("synthetic-secret %s", ACCOUNT_A)
        captured = capsys.readouterr()
        assert captured.out == ""
        assert captured.err == ""
        assert root_sink.getvalue() == ""
        assert list(tmp_path.iterdir()) == []
        assert not any(
            isinstance(handler, logging.FileHandler)
            for handler in logging.getLogger("webull.core").handlers
        )
    finally:
        logging.getLogger().removeHandler(root_handler)
        for name, (level, propagate, handlers) in saved.items():
            sdk_logger = logging.getLogger(name)
            sdk_logger.setLevel(level)
            sdk_logger.propagate = propagate
            sdk_logger.handlers[:] = handlers
