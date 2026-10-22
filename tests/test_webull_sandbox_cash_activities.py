"""Synthetic offline tests for the documented Webull cash-activities schema."""

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from src.webull_sandbox_cash_activities import (
    ConflictingActivity,
    IncompleteHistory,
    OfficialSdkCashActivitiesGateway,
    RepeatedCursor,
    WebullSandboxCashActivitiesAdapter,
)
from src.webull_sandbox_readonly import MalformedResponse, TransportError

ACCOUNT = "SYNTH-ACCOUNT-CASH-0001"
OTHER_ACCOUNT = "SYNTH-ACCOUNT-CASH-0002"
OBSERVED_AT = datetime(2026, 10, 22, 12, 0, tzinfo=UTC)


class FakeResponse:
    def __init__(self, body, status_code=200, json_error=False):
        self.status_code = status_code
        self.body = body
        self.json_error = json_error

    def json(self):
        if self.json_error:
            raise ValueError("synthetic response detail")
        return self.body


class FakeGateway:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def list_cash_activities(self, account_id, *, start_time, end_time, pagination_key):
        self.calls.append((account_id, start_time, end_time, pagination_key))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def event(
    event_id="evt-1",
    account_id=ACCOUNT,
    category="DEPOSIT",
    subcategory="ACH",
    amount="25.50",
    currency="USD",
    trade_date="2026-10-20",
    biz_time="unverified broker time",
    **optional,
):
    result = {
        "id": event_id,
        "account_id": account_id,
        "activity_type": category,
        "activity_sub_type": subcategory,
        "currency": currency,
        "trade_date": trade_date,
        "net_amount": amount,
        "biz_time": biz_time,
    }
    result.update(optional)
    return result


def page(activities, pagination_key=...):
    body = {"data": activities}
    if pagination_key is not ...:
        body["pagination_key"] = pagination_key
    return FakeResponse(body)


def adapter(gateway, **kwargs):
    return WebullSandboxCashActivitiesAdapter(
        gateway,
        selected_account_id=ACCOUNT,
        clock=lambda: OBSERVED_AT,
        **kwargs,
    )


def test_cursor_pagination_parses_official_fields_and_forwards_same_year_window():
    gateway = FakeGateway(
        page([event()], "next-cursor"),
        page(
            [event("evt-2", category="DIVIDENDS", subcategory="INCOME", amount="-1.25")]
        ),
    )
    start_time = "2026-10-01T00:00:00.000Z"
    end_time = "2026-10-22T23:59:59.999Z"
    history = adapter(
        gateway,
        start_time=start_time,
        end_time=end_time,
    ).fetch_history()

    assert len(history.events) == 2
    assert history.pages and len(history.pages) == 2
    assert history.pagination_complete is True
    assert gateway.calls == [
        (ACCOUNT, start_time, end_time, None),
        (ACCOUNT, start_time, end_time, "next-cursor"),
    ]
    first_provenance = history.events[0].pagination_provenance[0]
    assert first_provenance.page_number == 1
    assert first_provenance.requested_cursor_digest is None
    assert first_provenance.next_cursor_digest is not None
    assert "next-cursor" not in repr(history)


@pytest.mark.parametrize(
    ("start_time", "end_time"),
    [
        ("2025-12-31T23:59:59.999Z", "2026-01-01T00:00:00.000Z"),
        ("2026-10-01T00:00:00Z", "2026-10-22T23:59:59.999Z"),
        ("2026-02-30T00:00:00.000Z", "2026-03-01T00:00:00.000Z"),
        ("2026-10-22T23:59:59.999Z", "2026-10-01T00:00:00.000Z"),
    ],
)
def test_query_rejects_cross_year_invalid_or_reversed_windows(start_time, end_time):
    with pytest.raises(ValueError):
        adapter(FakeGateway(), start_time=start_time, end_time=end_time)


def test_query_requires_both_dates_or_neither():
    with pytest.raises(ValueError, match="provided together"):
        adapter(FakeGateway(), start_time="2026-10-01T00:00:00.000Z")

    history = adapter(FakeGateway(page([]))).fetch_history()
    assert history.history_window == (None, None)


def test_identical_duplicate_is_deduplicated_with_both_page_provenances():
    same = event()
    history = adapter(FakeGateway(page([same], "next"), page([same]))).fetch_history()

    assert len(history.events) == 1
    assert [p.page_number for p in history.events[0].pagination_provenance] == [1, 2]


def test_conflicting_same_id_correction_is_rejected_not_guessed():
    with pytest.raises(ConflictingActivity):
        adapter(
            FakeGateway(
                page([event(amount="10.00")], "next"),
                page([event(amount="12.00")]),
            )
        ).fetch_history()


def test_missing_later_page_fails_without_returning_partial_history():
    with pytest.raises(IncompleteHistory, match="SdkRequestFailure"):
        adapter(
            FakeGateway(page([event()], "next"), RuntimeError("private details"))
        ).fetch_history()


def test_http_failure_after_first_page_is_incomplete_and_sanitized():
    with pytest.raises(IncompleteHistory, match="HttpFailure") as error:
        adapter(
            FakeGateway(page([event()], "next"), FakeResponse({}, status_code=503))
        ).fetch_history()
    assert error.value.__cause__ is None
    assert error.value.__suppress_context__ is True
    assert "private" not in str(error.value)


def test_repeated_cursor_is_rejected_before_replaying_page():
    with pytest.raises(RepeatedCursor):
        adapter(
            FakeGateway(page([event()], "loop"), page([event("evt-2")], "loop"))
        ).fetch_history()


@pytest.mark.parametrize(
    "response",
    [
        FakeResponse([]),
        FakeResponse({}),
        FakeResponse({"items": []}),
        FakeResponse({"activities": []}),
        FakeResponse({"data": {}}),
        FakeResponse({"data": None}),
        FakeResponse({"data": "x"}),
        FakeResponse({"data": {"activities": []}}),
        FakeResponse({"data": [], "pagination_key": None}),
        FakeResponse({"data": [], "pagination_key": ""}),
        FakeResponse({"data": [], "pagination_key": "  "}),
        FakeResponse({"data": [], "pagination_key": " pad"}),
        FakeResponse({"data": [], "pagination_key": 7}),
        FakeResponse({"data": [], "unexpected": True}),
    ],
)
def test_unsupported_or_malformed_page_structures_fail_closed(response):
    with pytest.raises(MalformedResponse):
        adapter(FakeGateway(response)).fetch_history()


def test_empty_data_page_is_not_proof_of_no_flows_or_complete_history():
    gateway = FakeGateway(FakeResponse({"data": []}))
    history = adapter(gateway).fetch_history()

    assert history.events == ()
    assert history.pagination_complete is True
    assert len(history.pages) == 1 and history.pages[0].event_count == 0
    assert len(gateway.calls) == 1
    assert "EMPTY_RESULT_NOT_PROOF_OF_NO_FLOWS" in history.trust_flags
    assert "HISTORY_COVERAGE_UNVERIFIED" in history.trust_flags
    assert "RECORD_SCHEMA_UNVERIFIED" not in history.trust_flags
    assert history.external_flows_verified is False
    assert history.enforcement_eligible is False


def test_empty_page_with_cursor_continues_and_empty_terminal_page_ends():
    gateway = FakeGateway(page([], "c1"), page([event()], "c2"), page([]))
    history = adapter(gateway).fetch_history()

    assert [p.event_count for p in history.pages] == [0, 1, 0]
    assert len(history.events) == 1 and history.pagination_complete is True


def test_failed_subsequent_page_after_empty_page_is_incomplete():
    with pytest.raises(IncompleteHistory):
        adapter(
            FakeGateway(page([], "c1"), FakeResponse({"data": []}, status_code=500))
        ).fetch_history()


def test_documented_record_fields_match_selected_account_and_keep_biz_time_raw():
    raw_biz_time = "proprietary timestamp value, timezone unknown"
    history = adapter(
        FakeGateway(page([event(biz_time=raw_biz_time, market="US", symbol="XYZ")]))
    ).fetch_history()
    evidence = history.events[0]

    assert evidence.account_match_verified is True
    assert evidence.raw_category == "DEPOSIT"
    assert evidence.raw_subcategory == "ACH"
    assert evidence.amount == Decimal("25.50")
    assert evidence.currency == "USD"
    assert evidence.trade_date.isoformat() == "2026-10-20"
    assert evidence.biz_time_raw == raw_biz_time
    assert evidence.market == "US"
    assert evidence.symbol == "XYZ"
    assert evidence.classification is None
    assert evidence.external_flow_sign_trusted is False
    assert "BIZ_TIME_FORMAT_TIMEZONE_UNVERIFIED" in evidence.trust_flags
    assert ACCOUNT not in repr(history)
    assert "evt-1" not in repr(history)


def test_mismatched_record_account_is_rejected_without_disclosing_account_ids():
    with pytest.raises(MalformedResponse) as error:
        adapter(FakeGateway(page([event(account_id=OTHER_ACCOUNT)]))).fetch_history()
    assert OTHER_ACCOUNT not in str(error.value)
    assert ACCOUNT not in str(error.value)


@pytest.mark.parametrize("amount", ["0", "-0.005", "1234567890.123456", "+1.25"])
def test_net_amount_accepts_finite_plain_decimal_strings(amount):
    history = adapter(FakeGateway(page([event(amount=amount)]))).fetch_history()
    assert history.events[0].amount == Decimal(amount)


@pytest.mark.parametrize(
    "amount", [1, 1.25, True, " 1.00", "1.00 ", "1e3", "NaN", "Infinity"]
)
def test_malformed_decimal_values_are_rejected(amount):
    with pytest.raises(MalformedResponse):
        adapter(FakeGateway(page([event(amount=amount)]))).fetch_history()


@pytest.mark.parametrize(
    ("category", "subcategory"),
    [
        ("TRADE", "BUY"),
        ("DEPOSIT", "ACH"),
        ("WITHDRAW", "WIRE"),
        ("FEES", "WIRE_FEE"),
        ("TRANSFER", "ACATS_IN"),
        ("DIVIDENDS", "INCOME"),
        ("TAX", "FOREIGN_TAX_WITHHELD"),
        ("INTERESTS", "CREDIT"),
        ("CORPORATE_ACTION", "MERGER"),
        ("OPTION_EA", "OPTION_EXERCISE"),
        ("JOURNAL", "CASH_JOURNAL"),
        ("EC_SETTLEMENT", "EC_PAYOUT"),
        ("OTHER", "OTHER"),
    ],
)
def test_documented_activity_category_pairs_are_retained_without_classification(
    category, subcategory
):
    evidence = (
        adapter(FakeGateway(page([event(category=category, subcategory=subcategory)])))
        .fetch_history()
        .events[0]
    )

    assert evidence.raw_category == category
    assert evidence.raw_subcategory == subcategory
    assert evidence.classification is None
    assert evidence.classification_trusted is False
    assert evidence.external_flow_eligible is False


@pytest.mark.parametrize(
    ("category", "subcategory"),
    [
        ("UNKNOWN", "OTHER"),
        ("DEPOSIT", "CREDIT"),
        ("WITHDRAW", "ACH_REVERSE"),
        ("DIVIDENDS", "ACH"),
        ("FEES", "OTHER_UNKNOWN"),
    ],
)
def test_undocumented_category_or_type_subtype_pair_is_rejected(category, subcategory):
    with pytest.raises(MalformedResponse):
        adapter(
            FakeGateway(page([event(category=category, subcategory=subcategory)]))
        ).fetch_history()


@pytest.mark.parametrize(
    ("category", "subcategory"),
    [
        ("DEPOSIT", "REVERSAL"),
        ("DEPOSIT", "ACH_REVERSE"),
        ("FEES", "REVERSAL_FEE"),
        ("TRADE", "BUY_CANCELLED"),
    ],
)
def test_reversal_records_remain_ambiguous_without_linkage_inference(
    category, subcategory
):
    evidence = (
        adapter(FakeGateway(page([event(category=category, subcategory=subcategory)])))
        .fetch_history()
        .events[0]
    )

    assert evidence.reversal_ambiguous is True
    assert evidence.classification is None
    assert evidence.external_flow_sign_trusted is False
    assert evidence.external_flow_eligible is False
    assert "REVERSAL_LINKAGE_UNVERIFIED" in evidence.trust_flags


@pytest.mark.parametrize(
    "bad_event",
    [
        "not-an-object",
        {**event(), "unknown_field": "unexpected"},
        {key: value for key, value in event().items() if key != "id"},
        event(event_id=" "),
        event(account_id=""),
        event(category=""),
        event(subcategory=""),
        event(currency="EUR"),
        event(trade_date="2026-02-30"),
        event(trade_date="2026-2-3"),
        event(biz_time=""),
        event(biz_time=None),
        event(market=None),
        event(market="CA"),
        event(symbol=None),
        event(symbol=" XYZ "),
    ],
)
def test_malformed_record_fields_fail_closed(bad_event):
    with pytest.raises(MalformedResponse):
        adapter(FakeGateway(page([bad_event]))).fetch_history()


def test_page_limit_prevents_unbounded_cursor_walk():
    with pytest.raises(IncompleteHistory, match="page limit"):
        adapter(
            FakeGateway(page([], "one"), page([], "two")),
            max_pages=1,
        ).fetch_history()


def test_sdk_gateway_exposes_only_list_activities_and_passes_cursor():
    class ActivityApi:
        def __init__(self):
            self.calls = []

        def list_activities(self, *args, **kwargs):
            self.calls.append((args, kwargs))
            return "response"

        def get_activities(self, *args, **kwargs):
            raise AssertionError("deprecated endpoint must not be called")

    api = ActivityApi()
    gateway = OfficialSdkCashActivitiesGateway(api)
    result = gateway.list_cash_activities(
        ACCOUNT,
        start_time="2026-10-01T00:00:00.000Z",
        end_time="2026-10-22T00:00:00.000Z",
        pagination_key="cursor",
    )

    assert result == "response"
    assert api.calls == [
        (
            (ACCOUNT,),
            {
                "start_time": "2026-10-01T00:00:00.000Z",
                "end_time": "2026-10-22T00:00:00.000Z",
                "pagination_key": "cursor",
            },
        )
    ]
    assert {name for name in dir(gateway) if not name.startswith("_")} == {
        "list_cash_activities"
    }


def test_initial_transport_and_invalid_json_fail_without_sdk_text():
    with pytest.raises(TransportError) as transport:
        adapter(FakeGateway(RuntimeError("sensitive SDK message"))).fetch_history()
    assert transport.value.__cause__ is None
    assert "sensitive" not in str(transport.value)

    with pytest.raises(MalformedResponse) as malformed:
        adapter(FakeGateway(FakeResponse(None, json_error=True))).fetch_history()
    assert malformed.value.__cause__ is None
    assert "synthetic response detail" not in str(malformed.value)


def test_history_and_event_remain_ineligible_for_ats009():
    history = adapter(FakeGateway(page([event()]))).fetch_history()

    assert history.account_binding_verified is False
    assert history.external_flows_verified is False
    assert history.enforcement_eligible is False
    assert history.events[0].external_flow_eligible is False
