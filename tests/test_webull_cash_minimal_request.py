"""Offline tests for the minimal cash-activities request (ATS-009C3A). Synthetic only."""

from src import webull_sandbox_evidence
from src.webull_sandbox_cash_activities import (
    OfficialSdkCashActivitiesGateway,
    describe_response_shape,
)


class _Capture:
    def __init__(self):
        self.requests = []

    def get_response(self, request):
        self.requests.append(request)
        return {"ok": True}


def _activity_api(capture):
    from webull.trade.trade.activity import Activity

    return Activity(capture)


def test_minimal_request_has_only_account_id_and_v3_header():
    capture = _Capture()
    gateway = OfficialSdkCashActivitiesGateway(_activity_api(capture))
    gateway.list_cash_activities(
        "SYNTH-ACCT", start_time=None, end_time=None, pagination_key=None
    )

    (request,) = capture.requests
    assert request.get_query_params() == {"account_id": "SYNTH-ACCT"}
    for absent in ("start_time", "end_time", "activity_types", "pagination_key"):
        assert absent not in request.get_query_params()
    assert request.get_action_name() == "/trading/activities/cash-activities/list"
    assert request.get_headers() == {"x-version": "v3"}
    assert request.get_version() == "v3"
    assert request.get_method() == "GET"


def test_cursor_is_sent_only_when_present_and_empty_string_is_dropped():
    capture = _Capture()
    gateway = OfficialSdkCashActivitiesGateway(_activity_api(capture))
    gateway.list_cash_activities("A", start_time=None, end_time=None, pagination_key="")
    gateway.list_cash_activities(
        "A", start_time=None, end_time=None, pagination_key="c1"
    )
    assert "pagination_key" not in capture.requests[0].get_query_params()
    assert capture.requests[1].get_query_params()["pagination_key"] == "c1"


def test_shape_reports_names_and_types_only():
    body = {
        "data": [{"activity_id": "SECRET-1", "amount": "123.45"}],
        "pagination_key": "TOKEN",
    }
    shape = describe_response_shape(body)
    rendered = repr(shape)
    assert shape["activity_item_fields"] == {"activity_id": "str", "amount": "str"}
    assert shape["has_pagination_key"] is True
    assert shape["matches_observed_page_structure"] is True
    for value in ("SECRET-1", "123.45", "TOKEN"):
        assert value not in rendered


def test_shape_for_observed_empty_page():
    shape = describe_response_shape({"data": []})
    assert shape["activity_count"] == 0 and shape["has_pagination_key"] is False
    assert shape["matches_observed_page_structure"] is True


def test_shape_rejects_legacy_provisional_structures():
    assert describe_response_shape([1])["top_level_type"] == "list"
    for body in ({"activities": []}, {"data": {"activities": []}}):
        assert describe_response_shape(body)["matches_observed_page_structure"] is False


def test_unsafe_field_names_are_masked():
    shape = describe_response_shape({"bad name with SECRET": 1})
    assert "SECRET" not in repr(shape)


class _Keyring:
    def get_password(self, service, name):
        return {"WEBULL_APP_KEY": "k-synth", "WEBULL_APP_SECRET": "s-synth"}[name]


class _Resp:
    status_code = 200

    def __init__(self, body):
        self._body = body

    def json(self):
        return self._body


class _Trade:
    def __init__(self, body=None, error=None):
        self.calls = []
        outer = self

        class Account:
            def get_account_list(self):
                return _Resp(
                    [
                        {
                            "account_id": "FULLID-0AA8",
                            "account_type": "CASH",
                            "account_number": "x0AA8",
                        }
                    ]
                )

            def get_account_balance(self, account_id):
                return _Resp(
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
                return _Resp([])

        class Activity:
            def list_activities(self, account_id, **kwargs):
                outer.calls.append(kwargs)
                if error:
                    raise error
                return _Resp(body)

        self.account_v2 = Account()
        self.activity = Activity()


def test_runner_one_request_no_optional_params_and_no_values(capsys):
    trade = _Trade({"data": [{"activity_id": "TXN-SECRET", "amount": "9.99"}]})
    code = webull_sandbox_evidence.run(
        environ={},
        input_fn=lambda _: "1",
        isatty=True,
        build_trade_client=lambda k, s: trade,
        keyring_module=_Keyring(),
    )
    out = capsys.readouterr().out
    assert code == 0 and len(trade.calls) == 1
    assert trade.calls[0] == {
        "start_time": None,
        "end_time": None,
        "pagination_key": None,
    }
    assert "activity_id" in out and "UNVERIFIED" in out
    for leak in ("TXN-SECRET", "9.99", "FULLID"):
        assert leak not in out


def test_runner_417_reports_status_and_code_only(capsys):
    class ServerException(Exception):
        error_code = "OPENAPI_PARAM_ERR"
        http_status = 417
        error_msg = "private-detail"

    trade = _Trade(error=ServerException())
    code = webull_sandbox_evidence.run(
        environ={},
        input_fn=lambda _: "1",
        isatty=True,
        build_trade_client=lambda k, s: trade,
        keyring_module=_Keyring(),
    )
    out = capsys.readouterr().out
    assert code == 1 and len(trade.calls) == 1
    assert "http_status: 417" in out and "webull_error_code: OPENAPI_PARAM_ERR" in out
    assert "private-detail" not in out and "FULLID" not in out


def test_runner_cancel_makes_no_cash_request(capsys):
    trade = _Trade({"data": []})
    code = webull_sandbox_evidence.run(
        environ={},
        input_fn=lambda _: "",
        isatty=True,
        build_trade_client=lambda k, s: trade,
        keyring_module=_Keyring(),
    )
    assert code == 1 and trade.calls == []
    assert "CANCELLED" in capsys.readouterr().out
