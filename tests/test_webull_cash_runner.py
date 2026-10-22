"""Offline synthetic tests for the external cash-activities validation runner."""

import importlib.util
import os
from pathlib import Path

import pytest

RUNNER = Path(r"C:\Users\ghamf\Downloads\webull_sandbox_cash_activities_validation.py")
pytestmark = pytest.mark.skipif(
    not RUNNER.exists(), reason="external runner not present"
)

FULL_ID_A = "SYNTH-FULL-ACCOUNT-AAAA-0001"
FULL_ID_B = "SYNTH-FULL-ACCOUNT-BBBB-0002"
ENV = {
    "WEBULL_APP_KEY": "synthetic-key-value",
    "WEBULL_APP_SECRET": "synthetic-secret-value",
    "WEBULL_CASH_ACTIVITIES_START_TIME": "2026-10-01T00:00:00Z",
    "WEBULL_CASH_ACTIVITIES_END_TIME": "2026-10-22T00:00:00Z",
}


@pytest.fixture(scope="module")
def runner():
    spec = importlib.util.spec_from_file_location("cash_runner_under_test", RUNNER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Resp:
    def __init__(self, body, status=200):
        self.status_code = status
        self._body = body

    def json(self):
        return self._body


class FakeTradeClient:
    def __init__(self, activity_pages=None, accounts=None):
        self.calls = []
        outer = self

        class AccountApi:
            def get_account_list(self):
                outer.calls.append("account_list")
                return Resp(
                    accounts
                    if accounts is not None
                    else [
                        {"account_id": FULL_ID_A, "account_type": "CASH"},
                        {"account_id": "SYNTH-MARGIN-0003", "account_type": "MARGIN"},
                        {"account_id": FULL_ID_B, "account_type": "CASH"},
                    ]
                )

            def get_account_balance(self, *a):
                raise AssertionError("balance must not be called")

            def get_account_position(self, *a):
                raise AssertionError("positions must not be called")

        class ActivityApi:
            def list_activities(self, account_id, **kwargs):
                outer.calls.append(("activities", account_id, kwargs["pagination_key"]))
                return Resp((activity_pages or [{"data": []}]).pop(0))

            def get_activities(self, *a, **k):
                raise AssertionError("deprecated endpoint must not be called")

        self.account_v2 = AccountApi()
        self.activity = ActivityApi()


class EmptyKeyring:
    def get_password(self, service, name):
        return None


def go(runner, trade, env=None, answer="2", isatty=True):
    prompts = []

    def fake_input(prompt):
        prompts.append(prompt)
        return answer

    code = runner.run(
        environ=ENV if env is None else env,
        input_fn=fake_input,
        isatty=isatty,
        build_trade_client=lambda key, secret: trade,
        keyring_module=EmptyKeyring(),
    )
    return code, prompts


def test_selection_passes_full_id_internally_and_never_prints_it(runner, capsys):
    trade = FakeTradeClient([{"data": []}])
    code, _ = go(runner, trade, answer="2")
    out = capsys.readouterr().out

    assert code == 0
    assert ("activities", FULL_ID_B, None) in trade.calls
    assert "[1] CASH ****0001" in out and "[2] CASH ****0002" in out
    assert "MARGIN" not in out
    for secret in (FULL_ID_A, FULL_ID_B, "synthetic-secret-value"):
        assert secret not in out
    assert "unique_events_received: 0" in out
    assert "NOT evidence of complete history" in out
    assert "pagination_terminal_for_this_request_sequence: True" in out
    assert "RECORD_SCHEMA_UNVERIFIED" in out
    assert "account_binding: UNVERIFIED" in out
    assert "independent API sandbox account" in out
    assert "enforcement_eligible: False" in out
    assert "sdk_files_written_to_cwd: 0" in out
    assert FULL_ID_B not in os.environ.values()


def test_nonempty_records_fail_closed_without_printing_contents(runner, capsys):
    trade = FakeTradeClient(
        [{"data": [{"activity_id": "evt-secret-1", "amount": "123.45"}]}]
    )
    code, _ = go(runner, trade, answer="2")
    out = capsys.readouterr().out

    assert code == 1
    assert "UnverifiedRecordSchema" in out
    assert "validation_outcome: NOT_COMPLETED" in out
    for secret in ("evt-secret-1", "123.45", FULL_ID_B):
        assert secret not in out


def test_no_window_sends_account_only_request(runner, capsys):
    seen = []
    trade = FakeTradeClient([{"data": []}])
    trade.activity.list_activities = lambda account_id, **kw: (
        seen.append(kw) or Resp({"data": []})
    )
    env = {k: v for k, v in ENV.items() if "CASH_ACTIVITIES" not in k}
    code, _ = go(runner, trade, env=env, answer="2")
    assert code == 0
    assert seen == [{"start_time": None, "end_time": None, "pagination_key": None}]
    assert "account-only" in capsys.readouterr().out


@pytest.mark.parametrize("answer", ["", "0", "3", "x", "1.5", "-1", " ", "²"])
def test_cancel_or_malformed_selection_makes_no_activity_request(
    runner, capsys, answer
):
    trade = FakeTradeClient()
    code, _ = go(runner, trade, answer=answer)
    out = capsys.readouterr().out

    assert code == 1
    assert trade.calls == ["account_list"]
    assert "validation_outcome: CANCELLED" in out
    assert FULL_ID_A not in out


def test_non_interactive_terminal_blocks_without_activity_request(runner, capsys):
    trade = FakeTradeClient()
    code, prompts = go(runner, trade, isatty=False)

    assert code == 1
    assert prompts == []
    assert trade.calls == ["account_list"]
    assert "interactive terminal required" in capsys.readouterr().out


def test_no_cash_accounts_is_cancelled_cleanly(runner, capsys):
    trade = FakeTradeClient(
        accounts=[{"account_id": "X-MARGIN-1", "account_type": "MARGIN"}]
    )
    code, _ = go(runner, trade)

    assert code == 1
    assert trade.calls == ["account_list"]
    assert "no selectable CASH accounts" in capsys.readouterr().out


@pytest.mark.parametrize(
    "missing",
    [
        "WEBULL_APP_KEY",
        "WEBULL_APP_SECRET",
        "WEBULL_CASH_ACTIVITIES_START_TIME",
        "WEBULL_CASH_ACTIVITIES_END_TIME",
    ],
)
def test_missing_inputs_block_before_any_client_is_built(runner, capsys, missing):
    env = {k: v for k, v in ENV.items() if k != missing}
    built = []
    code = runner.run(
        environ=env,
        input_fn=lambda p: "1",
        isatty=True,
        build_trade_client=lambda *a: built.append(a),
        keyring_module=EmptyKeyring(),
    )
    out = capsys.readouterr().out

    assert code == 2
    assert built == []
    assert "blocked:" in out
    assert "synthetic-secret-value" not in out and "synthetic-key-value" not in out


@pytest.mark.parametrize(
    "overrides",
    [
        {"WEBULL_CASH_ACTIVITIES_START_TIME": "2026-10-01T00:00:00"},
        {"WEBULL_CASH_ACTIVITIES_START_TIME": "not-a-time"},
        {"WEBULL_CASH_ACTIVITIES_END_TIME": "2026-09-01T00:00:00Z"},
    ],
)
def test_invalid_window_blocks_before_any_client_is_built(runner, overrides):
    built = []
    code = runner.run(
        environ={**ENV, **overrides},
        input_fn=lambda p: "1",
        isatty=True,
        build_trade_client=lambda *a: built.append(a),
        keyring_module=EmptyKeyring(),
    )
    assert code == 2 and built == []


def test_debug_flag_blocks(runner):
    built = []
    code = runner.run(
        environ={**ENV, "WEBULL_API_DEBUG": "sdk"},
        keyring_module=EmptyKeyring(),
        build_trade_client=lambda *a: built.append(a),
    )
    assert code == 2 and built == []


def test_sdk_failure_output_is_sanitized(runner, capsys):
    class Boom(FakeTradeClient):
        def __init__(self):
            super().__init__()
            self.account_v2.get_account_list = self._raise

        @staticmethod
        def _raise():
            raise RuntimeError("synthetic-secret-detail " + FULL_ID_A)

    code, _ = go(runner, Boom())
    out = capsys.readouterr().out

    assert code == 1
    assert "synthetic-secret-detail" not in out and FULL_ID_A not in out
    assert "validation_outcome: NOT_COMPLETED" in out


def test_cash_endpoint_http_failure_is_sanitized(runner, capsys):
    trade = FakeTradeClient()
    trade.activity.list_activities = lambda account_id, **kw: Resp(
        {"secret": FULL_ID_B}, 403
    )
    code, _ = go(runner, trade)
    out = capsys.readouterr().out

    assert code == 1
    assert "http_status: 403" in out
    assert FULL_ID_B not in out


def test_keyring_credentials_are_used_and_never_printed(runner, capsys):
    class Store:
        def get_password(self, service, name):
            assert service == "ATS-Webull-Sandbox"
            return {
                "WEBULL_APP_KEY": "kr-key-value",
                "WEBULL_APP_SECRET": "kr-secret-value",
            }[name]

    built = []
    trade = FakeTradeClient()
    window = {k: v for k, v in ENV.items() if "CASH_ACTIVITIES" in k}
    code = runner.run(
        environ=window,
        input_fn=lambda p: "",
        isatty=True,
        build_trade_client=lambda key, secret: built.append((key, secret)) or trade,
        keyring_module=Store(),
    )
    out = capsys.readouterr().out
    assert code == 1  # cancelled at selection
    assert built == [("kr-key-value", "kr-secret-value")]
    assert "kr-key-value" not in out and "kr-secret-value" not in out


def test_runner_source_has_no_order_env_or_id_output(runner):
    source = RUNNER.read_text(encoding="utf-8")
    for token in (
        "place_order",
        "order_v",
        "cancel_order",
        "WEBULL_SANDBOX_ACCOUNT_ID",
        "os.environ[",
    ):
        assert token not in source
    assert "print(selected.account_id" not in source
