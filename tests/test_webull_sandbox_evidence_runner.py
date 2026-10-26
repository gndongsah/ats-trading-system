from __future__ import annotations

from dataclasses import dataclass

from src.webull_sandbox_evidence import run, sanitized_shape

ENV = {
    "WEBULL_APP_KEY": "SYNTHETIC-APP-KEY",
    "WEBULL_APP_SECRET": "SYNTHETIC-APP-SECRET",
}
ACCOUNT_ID = "SYNTHETIC-FULL-ACCOUNT-ID"


@dataclass
class FakeResponse:
    payload: object
    status_code: int = 200

    def json(self):
        return self.payload


class FakeKeyring:
    def __init__(self, values=None):
        self.values = values or {}

    def get_password(self, service, name):
        return self.values.get(name)


class EmptyKeyring:
    def get_password(self, service, name):
        return None


class FakeAccountApi:
    def __init__(self):
        self.calls = []

    def get_account_list(self):
        self.calls.append(("account_list", None))
        return FakeResponse(
            {
                "data": [
                    {
                        "account_id": ACCOUNT_ID,
                        "account_type": "CASH",
                        "display_name": "Sensitive Name",
                    }
                ]
            }
        )

    def get_account_balance(self, account_id):
        self.calls.append(("balance", account_id))
        return FakeResponse(
            {
                "total_net_liquidation_value": "12345.67",
                "account_currency_assets": [
                    {"currency": "USD", "net_liquidation_value": "12345.67"}
                ],
                "as_of": "2026-10-22T10:00:00Z",
            }
        )

    def get_account_position(self, account_id):
        self.calls.append(("positions", account_id))
        return FakeResponse(
            {"data": [{"symbol": "SYNTH", "quantity": "1", "account_id": ACCOUNT_ID}]}
        )


class FakeActivityApi:
    def __init__(self, payloads):
        self.payloads = list(payloads)
        self.calls = []

    def list_activities(
        self,
        account_id,
        *,
        start_time=None,
        end_time=None,
        pagination_key=None,
    ):
        self.calls.append((account_id, start_time, end_time, pagination_key))
        return FakeResponse(self.payloads.pop(0))


class FakeTradeClient:
    def __init__(self, account_api, activity_api):
        self.account_v2 = account_api
        self.activity = activity_api


def test_sanitized_shape_reports_types_without_values():
    shape = sanitized_shape(
        {
            "account_id": ACCOUNT_ID,
            "account_type": "CASH",
            "balance": [{"currency": "USD", "amount": "123.45"}],
            "api_token": "DO-NOT-PRINT",
        }
    )
    rendered = str(shape)
    assert "account_type" in rendered
    assert "balance" in rendered
    assert "account_id" not in rendered
    assert "api_token" not in rendered
    assert ACCOUNT_ID not in rendered
    assert "123.45" not in rendered
    assert "DO-NOT-PRINT" not in rendered


def test_runner_collects_sanitized_read_only_shapes_and_cursor_metadata(capsys):
    account_api = FakeAccountApi()
    activity_api = FakeActivityApi(
        [
            {
                "data": [
                    {
                        "activity_id": "PRIVATE-EVENT-ID",
                        "activity_type": "UNVERIFIED-CATEGORY",
                        "amount": "100.00",
                        "currency": "USD",
                        "event_time": "2026-10-22T10:00:00Z",
                    }
                ],
                "pagination_key": "PRIVATE-CURSOR",
            },
            {"data": [], "pagination_key": None},
        ]
    )
    client = FakeTradeClient(account_api, activity_api)

    used_credentials = []

    def build_client(key, secret):
        used_credentials.append((key, secret))
        return client

    result = run(
        environ={},
        input_fn=lambda _: "1",
        isatty=True,
        keyring_module=FakeKeyring(
            {
                "WEBULL_APP_KEY": "SYNTHETIC-APP-KEY",
                "WEBULL_APP_SECRET": "SYNTHETIC-APP-SECRET",
            }
        ),
        build_trade_client=build_client,
    )

    output = capsys.readouterr().out
    assert result == 0
    assert "credential_source: KEYRING" in output
    assert used_credentials == [(ENV["WEBULL_APP_KEY"], ENV["WEBULL_APP_SECRET"])]
    assert "account_list_shape:" in output
    assert "balance_shape:" in output
    assert "positions_shape:" in output
    assert "cash_activities_page_1:" in output
    assert "cash_activities_page_2:" in output
    assert "cash_pagination_terminal_for_request_sequence: True" in output
    assert "cash_history_coverage: UNVERIFIED" in output
    assert "cash_category_and_sign_semantics: UNVERIFIED" in output
    assert "opening_equity: UNVERIFIED" in output
    assert "reconciliation: NOT_PERFORMED" in output
    assert "account_identity_binding: UNVERIFIED" in output
    assert "enforcement_eligible: False" in output
    for secret in (
        ENV["WEBULL_APP_KEY"],
        ENV["WEBULL_APP_SECRET"],
        ACCOUNT_ID,
        "PRIVATE-EVENT-ID",
        "PRIVATE-CURSOR",
        "UNVERIFIED-CATEGORY",
        "12345.67",
    ):
        assert secret not in output
    assert len(account_api.calls) == 3
    assert len(activity_api.calls) == 2
    assert activity_api.calls[0][1:] == (None, None, None)
    assert activity_api.calls[1][3] == "PRIVATE-CURSOR"


def test_runner_does_not_call_data_endpoints_if_account_selection_cancelled(capsys):
    account_api = FakeAccountApi()
    activity_api = FakeActivityApi([])
    client = FakeTradeClient(account_api, activity_api)

    result = run(
        environ=ENV,
        input_fn=lambda _: "",
        isatty=True,
        keyring_module=FakeKeyring(),
        build_trade_client=lambda key, secret: client,
    )

    output = capsys.readouterr().out
    assert result == 1
    assert account_api.calls == [("account_list", None)]
    assert activity_api.calls == []
    assert ACCOUNT_ID not in output
    assert ENV["WEBULL_APP_SECRET"] not in output


def test_runner_requires_interactive_selection(capsys):
    account_api = FakeAccountApi()
    activity_api = FakeActivityApi([])
    result = run(
        environ=ENV,
        isatty=False,
        keyring_module=FakeKeyring(),
        build_trade_client=lambda *_: FakeTradeClient(account_api, activity_api),
    )
    assert result == 1
    assert account_api.calls == [("account_list", None)]
    assert activity_api.calls == []
    assert "BLOCKED_NON_INTERACTIVE" in capsys.readouterr().out


def test_runner_rejects_no_cash_accounts_without_data_requests(capsys):
    account_api = FakeAccountApi()
    account_api.get_account_list = lambda: FakeResponse(
        {"data": [{"account_id": ACCOUNT_ID, "account_type": "MARGIN"}]}
    )
    activity_api = FakeActivityApi([])
    result = run(
        environ=ENV,
        input_fn=lambda _: "1",
        isatty=True,
        keyring_module=FakeKeyring(),
        build_trade_client=lambda *_: FakeTradeClient(account_api, activity_api),
    )
    assert result == 1
    assert activity_api.calls == []
    assert "NO_CASH_ACCOUNTS" in capsys.readouterr().out


def test_runner_blocks_without_credentials_before_client_creation(capsys):
    calls = []
    result = run(
        environ={},
        isatty=True,
        keyring_module=EmptyKeyring(),
        build_trade_client=lambda *_: calls.append("client"),
    )
    assert result == 2
    assert calls == []
    assert "credential_source" not in capsys.readouterr().out


def test_runner_blocks_sdk_debug_before_client_creation(capsys):
    calls = []
    result = run(
        environ={**ENV, "WEBULL_API_DEBUG": "sdk"},
        isatty=True,
        keyring_module=FakeKeyring(),
        build_trade_client=lambda *_: calls.append("client"),
    )
    assert result == 2
    assert calls == []
    assert "WEBULL_API_DEBUG" in capsys.readouterr().out


def test_runner_detects_repeated_cursor_without_emitting_cursor(capsys):
    account_api = FakeAccountApi()
    activity_api = FakeActivityApi(
        [
            {"data": [], "pagination_key": "REPEATED-CURSOR"},
            {"data": [], "pagination_key": "REPEATED-CURSOR"},
        ]
    )
    client = FakeTradeClient(account_api, activity_api)

    result = run(
        environ=ENV,
        input_fn=lambda _: "1",
        isatty=True,
        keyring_module=FakeKeyring(),
        build_trade_client=lambda *_: client,
    )

    output = capsys.readouterr().out
    assert result == 1
    assert "cash_pagination_stop_reason: REPEATED_CURSOR" in output
    assert "cash_pagination_terminal_for_request_sequence: False" in output
    assert "REPEATED-CURSOR" not in output


def test_runner_sanitizes_sdk_failure_output(capsys):
    private_marker = "PRIVATE-DETAIL-DO-NOT-PRINT"

    class ServerFailure(RuntimeError):
        http_status = 403
        error_code = "FORBIDDEN"

        def __str__(self):
            return private_marker

    account_api = FakeAccountApi()
    account_api.get_account_list = lambda: (_ for _ in ()).throw(ServerFailure())
    result = run(
        environ=ENV,
        isatty=True,
        keyring_module=FakeKeyring(),
        build_trade_client=lambda *_: FakeTradeClient(account_api, FakeActivityApi([])),
    )
    output = capsys.readouterr().out
    assert result == 1
    assert "error_type: SdkRequestFailure" in output
    assert "http_status: 403" in output
    assert "webull_error_code: FORBIDDEN" in output
    assert private_marker not in output
    assert ACCOUNT_ID not in output


def test_runner_source_is_machine_independent_and_has_no_order_calls():
    from pathlib import Path

    source = Path("src/webull_sandbox_evidence.py").read_text(encoding="utf-8")
    assert "C:\\Users\\" not in source
    assert "C:/Users/" not in source
    for token in ("place_order", "order_v", "submit_order"):
        assert token not in source


def test_paper_identifier_comparison_requires_unique_exact_match():
    from src.webull_sandbox_evidence import paper_identifier_match

    body = {"data": [{"account_id": ACCOUNT_ID, "account_number": "PAPER-1234"}]}
    assert (
        paper_identifier_match(body, ACCOUNT_ID, "PAPER-1234")
        == "EXACT_IDENTIFIER_MATCH_OBSERVED"
    )
    assert paper_identifier_match(body, ACCOUNT_ID, "1234") == "NO_UNIQUE_EXACT_MATCH"
    assert (
        paper_identifier_match(body, "another", "PAPER-1234")
        == "MATCHES_DIFFERENT_API_ACCOUNT"
    )
    assert (
        paper_identifier_match({"data": body["data"] * 2}, ACCOUNT_ID, "PAPER-1234")
        == "NO_UNIQUE_EXACT_MATCH"
    )
    assert (
        paper_identifier_match(body, ACCOUNT_ID, " PAPER-1234") == "INVALID_IDENTIFIER"
    )


def test_mapping_mismatch_stops_before_balance_and_never_prints_identifier(capsys):
    account = FakeAccountApi()
    status = run(
        environ=ENV,
        keyring_module=EmptyKeyring(),
        isatty=True,
        input_fn=lambda _: "1",
        paper_identifier_input=lambda: "PRIVATE-PAPER-ID",
        build_trade_client=lambda *_: FakeTradeClient(account, FakeActivityApi([])),
    )
    output = capsys.readouterr().out
    assert status == 1
    assert account.calls == [("account_list", None)]
    assert "PRIVATE-PAPER-ID" not in output
    assert "NO_UNIQUE_EXACT_MATCH" in output


def test_exact_mapping_observation_keeps_binding_and_enforcement_unverified(capsys):
    account = FakeAccountApi()
    status = run(
        environ=ENV,
        keyring_module=EmptyKeyring(),
        isatty=True,
        input_fn=lambda _: "1",
        paper_identifier_input=lambda: ACCOUNT_ID,
        build_trade_client=lambda *_: FakeTradeClient(
            account, FakeActivityApi([{"data": []}])
        ),
    )
    output = capsys.readouterr().out
    assert status == 0
    assert "EXACT_IDENTIFIER_MATCH_OBSERVED" in output
    assert ACCOUNT_ID not in output
    assert "account_identity_binding: UNVERIFIED" in output
    assert "enforcement_eligible: False" in output
