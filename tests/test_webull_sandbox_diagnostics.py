"""Offline regression tests for ATS-009C4A Webull sandbox authentication diagnostics.

Synthetic data and a mock keyring only; no network and no real credentials.
"""

import importlib.util
import io
import logging
import traceback
from pathlib import Path

import pytest

from src.webull_sandbox_credentials import (
    compare_sources,
    credential_source,
    credential_status,
    load_sandbox_credentials,
)
from src.webull_sandbox_readonly import (
    SdkRequestFailure,
    TransportError,
    _build_sdk_trade_client,
    _call,
    classify_sdk_exception,
    silence_sdk_logging,
)

K, S = "WEBULL_APP_KEY", "WEBULL_APP_SECRET"
KR_KEY, KR_SECRET = "KR-SYNTH-KEY", "KR-SYNTH-SECRET"
ENV_KEY, ENV_SECRET = "ENV-SYNTH-KEY", "ENV-SYNTH-SECRET"
LEAK = "synthetic-leak-marker"
RUNNER = Path(r"C:\Users\ghamf\Downloads\webull_sandbox_auth_diagnostic.py")


class FakeKeyring:
    def __init__(self, values=None, error=None):
        self.values = values or {}
        self.error = error

    def get_password(self, service, name):
        assert service == "ATS-Webull-Sandbox"
        if self.error:
            raise self.error
        return self.values.get(name)


def keyring_pair(key=KR_KEY, secret=KR_SECRET):
    return FakeKeyring({K: key, S: secret})


def env_pair(key=ENV_KEY, secret=ENV_SECRET):
    return {K: key, S: secret}


class ServerException(Exception):  # same name and attributes as the SDK class
    def __init__(self, code="INVALID_APP_KEY", msg=LEAK, http_status=401, request_id="req"):
        super().__init__()
        self.error_code = code
        self.error_msg = msg
        self.http_status = http_status
        self.request_id = request_id

    def __str__(self):
        return f"HTTP Status: {self.http_status}, Code: {self.error_code}, Msg: {self.error_msg}"


# ---- exception classification ------------------------------------------------


def test_server_exception_reduced_to_status_and_code_only():
    failure = classify_sdk_exception("client initialization", ServerException())
    assert isinstance(failure, SdkRequestFailure) and isinstance(failure, TransportError)
    assert (failure.http_status, failure.error_code) == (401, "INVALID_APP_KEY")
    assert failure.error_type == "ServerException"
    assert LEAK not in str(failure) and LEAK not in repr(failure)


@pytest.mark.parametrize("status", [True, 99, 600, "401", None, 4.01])
def test_unreliable_http_status_is_unknown(status):
    assert classify_sdk_exception("op", ServerException(http_status=status)).http_status is None


@pytest.mark.parametrize("code", ["has space", f"{LEAK} detail", "x" * 65, "", None, 7, "a/b"])
def test_unsafe_error_code_is_dropped(code):
    failure = classify_sdk_exception("op", ServerException(code=code))
    assert failure.error_code is None
    assert LEAK not in str(failure)


def test_exception_without_attributes_keeps_legacy_message():
    failure = classify_sdk_exception("account list", RuntimeError(LEAK))
    assert str(failure) == "account list failed (RuntimeError)"


def test_call_wraps_sdk_exception_without_chain_or_text():
    def boom():
        raise ServerException()

    with pytest.raises(SdkRequestFailure) as error:
        _call("account list", boom)
    assert error.value.http_status == 401
    assert error.value.__cause__ is None and error.value.__suppress_context__ is True
    rendered = "".join(traceback.format_exception(error.value))
    assert LEAK not in rendered


# ---- client initialization (handshake) failures ------------------------------


def test_initialization_server_exception_is_sanitized(tmp_path, monkeypatch):
    sdk_exceptions = pytest.importorskip("webull.core.exception.exceptions")
    trade_module = pytest.importorskip("webull.trade.trade_client")
    monkeypatch.chdir(tmp_path)

    def fail_init(api_client):
        raise sdk_exceptions.ServerException("INVALID_APP_KEY", LEAK, 401, "req-1")

    monkeypatch.setattr(trade_module, "TradeClient", fail_init)
    key, secret = "synthetic-key", "synthetic-secret"
    with pytest.raises(SdkRequestFailure) as error:
        _build_sdk_trade_client(key, secret)

    failure = error.value
    assert failure.operation == "client initialization"
    assert (failure.http_status, failure.error_code) == (401, "INVALID_APP_KEY")
    assert failure.__cause__ is None
    rendered = "".join(traceback.format_exception(failure))
    for value in (LEAK, key, secret):
        assert value not in rendered


def test_real_sdk_failure_logging_path_is_silent(tmp_path, monkeypatch, capsys):
    client_module = pytest.importorskip("webull.core.client")
    monkeypatch.chdir(tmp_path)
    names = ("webull", "webull.core", "webull.core.http.response")
    saved = {
        n: (
            logging.getLogger(n).level,
            logging.getLogger(n).propagate,
            list(logging.getLogger(n).handlers),
            logging.getLogger(n).disabled,
        )
        for n in names
    }
    root_sink = io.StringIO()
    root_handler = logging.StreamHandler(root_sink)
    root_handler.setLevel(logging.DEBUG)
    logging.getLogger().addHandler(root_handler)

    class Request:
        def __init__(self):
            self.headers = {"x-signature": LEAK, "x-app-key": LEAK}

    try:
        api_client = client_module.ApiClient("synthetic-key", "synthetic-secret", "us")
        silence_sdk_logging(api_client)
        # Runs the SDK's real non-200 handling, which logs request details at ERROR.
        exception = api_client._get_server_exception(
            Request(),
            401,
            {},
            b'{"error_code": "INVALID_APP_KEY", "message": "synthetic-leak-marker"}',
            "api.sandbox.webull.com",
            "synthetic-string-to-sign",
        )
        failure = classify_sdk_exception("client initialization", exception)

        assert (failure.http_status, failure.error_code) == (401, "INVALID_APP_KEY")
        captured = capsys.readouterr()
        assert captured.out == "" and captured.err == ""
        assert root_sink.getvalue() == ""
        assert list(tmp_path.iterdir()) == []
    finally:
        logging.getLogger().removeHandler(root_handler)
        for n, (level, propagate, handlers, disabled) in saved.items():
            logger = logging.getLogger(n)
            logger.setLevel(level)
            logger.propagate = propagate
            logger.handlers[:] = handlers
            logger.disabled = disabled


# ---- credential source precedence, status and comparison ---------------------


def test_keyring_wins_when_both_sources_complete():
    assert credential_source(environ=env_pair(), keyring_module=keyring_pair()) == "keyring"
    creds = load_sandbox_credentials(environ=env_pair(), keyring_module=keyring_pair())
    assert creds.reveal() == (KR_KEY, KR_SECRET)


def test_environment_is_used_only_when_keyring_has_nothing():
    assert credential_source(environ=env_pair(), keyring_module=FakeKeyring()) == "environment"
    assert (
        credential_source(environ=env_pair(), keyring_module=FakeKeyring(error=RuntimeError(LEAK)))
        == "environment"
    )


def test_missing_status_when_nothing_or_partial_or_backend_down():
    assert credential_status(environ={}, keyring_module=FakeKeyring()) == "MISSING"
    assert credential_source(environ={}, keyring_module=FakeKeyring()) == "MISSING"
    assert credential_status(environ={}, keyring_module=FakeKeyring({K: KR_KEY})) == "MISSING"
    assert (
        credential_status(environ={}, keyring_module=FakeKeyring(error=RuntimeError(LEAK)))
        == "MISSING"
    )


def test_partial_keyring_never_falls_back_to_a_complete_environment_pair():
    assert credential_source(environ=env_pair(), keyring_module=FakeKeyring({K: KR_KEY})) == "MISSING"


def test_compare_sources_match_mismatch_unavailable():
    same = env_pair(KR_KEY, KR_SECRET)
    assert compare_sources(environ=same, keyring_module=keyring_pair()) == "MATCH"
    assert compare_sources(environ=env_pair(), keyring_module=keyring_pair()) == "MISMATCH"
    assert compare_sources(environ=env_pair(KR_KEY, "other-secret"), keyring_module=keyring_pair()) == "MISMATCH"
    assert compare_sources(environ=env_pair(), keyring_module=FakeKeyring()) == "UNAVAILABLE"
    assert compare_sources(environ={}, keyring_module=keyring_pair()) == "UNAVAILABLE"
    assert compare_sources(environ={K: ENV_KEY}, keyring_module=keyring_pair()) == "UNAVAILABLE"


# ---- diagnostic runner --------------------------------------------------------


@pytest.fixture(scope="module")
def runner():
    if not RUNNER.exists():
        pytest.skip("external diagnostic runner not present")
    spec = importlib.util.spec_from_file_location("auth_diag_under_test", RUNNER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Resp:
    def __init__(self, body, status=200):
        self.status_code = status
        self._body = body

    def json(self):
        return self._body


class Trade:
    def __init__(self, list_result=None, list_error=None):
        self.calls = []
        outer = self

        class Account:
            def get_account_list(self):
                outer.calls.append("account_list")
                if list_error:
                    raise list_error
                return list_result

            def get_account_balance(self, *a):
                raise AssertionError("balance must not be called")

            def get_account_position(self, *a):
                raise AssertionError("positions must not be called")

        class Activity:
            def list_activities(self, *a, **k):
                raise AssertionError("cash activities must not be called")

        self.account_v2 = Account()
        self.activity = Activity()


def test_runner_reports_init_failure_phase_status_and_code_only(runner, capsys):
    def build(key, secret):
        raise classify_sdk_exception("client initialization", ServerException())

    code = runner.run(environ=env_pair(), keyring_module=keyring_pair(), build_trade_client=build)
    out = capsys.readouterr().out

    assert code == 1
    assert "credential_source_used: keyring" in out
    assert "keyring_vs_environment_pair: MISMATCH" in out
    assert "failed_phase: client_initialization" in out
    assert "http_status: 401" in out and "error_code: INVALID_APP_KEY" in out
    for secret in (KR_KEY, KR_SECRET, ENV_KEY, ENV_SECRET, LEAK):
        assert secret not in out


def test_runner_reports_account_list_phase_failure_with_one_request(runner, capsys):
    trade = Trade(list_error=ServerException(code="FORBIDDEN", http_status=403))
    code = runner.run(
        environ={}, keyring_module=keyring_pair(), build_trade_client=lambda k, s: trade
    )
    out = capsys.readouterr().out

    assert code == 1
    assert trade.calls == ["account_list"]
    assert "failed_phase: account_list" in out
    assert "http_status: 403" in out and "error_code: FORBIDDEN" in out
    assert "keyring_vs_environment_pair: UNAVAILABLE" in out
    assert LEAK not in out


def test_runner_success_prints_count_only_and_no_ids(runner, capsys):
    accounts = [{"account_id": "FULL-ID-AAAA-0001"}, {"account_id": "FULL-ID-BBBB-0002"}]
    trade = Trade(list_result=Resp(accounts))
    code = runner.run(
        environ={}, keyring_module=keyring_pair(), build_trade_client=lambda k, s: trade
    )
    out = capsys.readouterr().out

    assert code == 0
    assert trade.calls == ["account_list"]
    assert "account_count: 2" in out and "result: PASS" in out
    assert "FULL-ID" not in out
    assert "sdk_files_written_to_scratch_cwd: 0" in out


def test_runner_blocks_without_credentials_or_with_debug(runner, capsys):
    built = []
    assert runner.run(environ={}, keyring_module=FakeKeyring(), build_trade_client=built.append) == 2
    assert runner.run(
        environ={**env_pair(), "WEBULL_API_DEBUG": "sdk"},
        keyring_module=keyring_pair(),
        build_trade_client=built.append,
    ) == 2
    out = capsys.readouterr().out
    assert built == []
    for secret in (KR_KEY, KR_SECRET, ENV_KEY, ENV_SECRET):
        assert secret not in out


def test_runner_source_has_no_other_endpoints_or_secret_output(runner):
    source = RUNNER.read_text(encoding="utf-8")
    for token in ("place_order", "order_v", "get_account_balance", "get_account_position", "list_activities", "set_password"):
        assert token not in source
