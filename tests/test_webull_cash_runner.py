"""Portable offline tests for the in-repository Webull read-only runner."""

from dataclasses import dataclass

from src.webull_sandbox_evidence import run

ACCOUNT_ID_A = "SYNTHETIC-ACCOUNT-A-1001"
ACCOUNT_ID_B = "SYNTHETIC-ACCOUNT-B-2002"


@dataclass
class Response:
    body: object
    status_code: int = 200

    def json(self):
        return self.body


class Keyring:
    def get_password(self, service, name):
        return {
            "WEBULL_APP_KEY": "synthetic-key",
            "WEBULL_APP_SECRET": "synthetic-secret",
        }[name]


class EmptyKeyring:
    def get_password(self, service, name):
        return None


class TradeClient:
    def __init__(self, activity_pages=None, account_error=None):
        self.calls = []
        outer = self

        class AccountApi:
            def get_account_list(self):
                outer.calls.append(("account_list", None))
                if account_error:
                    raise account_error
                return Response(
                    {
                        "data": [
                            {
                                "account_id": ACCOUNT_ID_A,
                                "account_type": "CASH",
                                "account_label": "Synthetic one",
                            },
                            {
                                "account_id": ACCOUNT_ID_B,
                                "account_type": "CASH",
                                "account_label": "Synthetic two",
                            },
                        ]
                    }
                )

            def get_account_balance(self, account_id):
                outer.calls.append(("balance", account_id))
                return Response(
                    {
                        "total_net_liquidation_value": "100.00",
                        "account_currency_assets": [
                            {
                                "currency": "USD",
                                "net_liquidation_value": "100.00",
                            }
                        ],
                    }
                )

            def get_account_position(self, account_id):
                outer.calls.append(("positions", account_id))
                return Response([])

        class ActivityApi:
            def __init__(self):
                self.pages = list(activity_pages or [{"data": []}])

            def list_activities(self, account_id, **kwargs):
                outer.calls.append(("activities", account_id, kwargs))
                return Response(self.pages.pop(0))

        self.account_v2 = AccountApi()
        self.activity = ActivityApi()


def test_repository_runner_is_portable_and_read_only(capsys):
    trade = TradeClient(
        [
            {
                "data": [{"activity_id": "PRIVATE", "amount": "9.99"}],
                "pagination_key": "PRIVATE-CURSOR",
            },
            {"data": []},
        ]
    )
    result = run(
        environ={},
        input_fn=lambda _: "2",
        isatty=True,
        build_trade_client=lambda *_: trade,
        keyring_module=Keyring(),
    )

    output = capsys.readouterr().out
    assert result == 0
    assert "credential_source: KEYRING" in output
    assert "account_list_shape:" in output
    assert "balance_shape:" in output
    assert "positions_shape:" in output
    assert "cash_activities_page_1:" in output
    assert "cash_activities_page_2:" in output
    assert "cash_pagination_terminal_for_request_sequence: True" in output
    assert "account_identity_binding: UNVERIFIED" in output
    assert "cash_history_coverage: UNVERIFIED" in output
    assert "enforcement_eligible: False" in output
    for sensitive in (
        ACCOUNT_ID_A,
        ACCOUNT_ID_B,
        "synthetic-key",
        "synthetic-secret",
        "PRIVATE",
        "PRIVATE-CURSOR",
        "9.99",
    ):
        assert sensitive not in output
    assert [call[0] for call in trade.calls] == [
        "account_list",
        "balance",
        "positions",
        "activities",
        "activities",
    ]
    assert trade.calls[1][1] == ACCOUNT_ID_B
    assert trade.calls[3][2] == {
        "start_time": None,
        "end_time": None,
        "pagination_key": None,
    }


def test_cancel_blocks_balance_positions_and_activity_requests(capsys):
    trade = TradeClient()
    result = run(
        environ={},
        input_fn=lambda _: "",
        isatty=True,
        build_trade_client=lambda *_: trade,
        keyring_module=Keyring(),
    )
    assert result == 1
    assert [call[0] for call in trade.calls] == ["account_list"]
    output = capsys.readouterr().out
    assert ACCOUNT_ID_A not in output
    assert ACCOUNT_ID_B not in output


def test_malformed_selection_blocks_followup_endpoints(capsys):
    trade = TradeClient()
    result = run(
        environ={},
        input_fn=lambda _: "99",
        isatty=True,
        build_trade_client=lambda *_: trade,
        keyring_module=Keyring(),
    )
    assert result == 1
    assert [call[0] for call in trade.calls] == ["account_list"]
    assert "CANCELLED_OR_INVALID" in capsys.readouterr().out


def test_debug_flag_and_missing_credentials_block_client_creation(capsys):
    built = []
    for environ, keyring in (
        ({}, EmptyKeyring()),
        ({"WEBULL_API_DEBUG": "sdk"}, Keyring()),
    ):
        result = run(
            environ=environ,
            isatty=True,
            build_trade_client=lambda *_: built.append(True),
            keyring_module=keyring,
        )
        assert result == 2
    assert not built
    output = capsys.readouterr().out
    assert "synthetic-secret" not in output
