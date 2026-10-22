"""Synthetic offline tests for the Webull cash-activities evidence adapter."""

from datetime import UTC, datetime

import pytest

from src.webull_sandbox_cash_activities import (
    ConflictingActivity,
    IncompleteHistory,
    OfficialSdkCashActivitiesGateway,
    RepeatedCursor,
    UnverifiedRecordSchema,
    WebullSandboxCashActivitiesAdapter,
)
from src.webull_sandbox_readonly import MalformedResponse, TransportError

ACCOUNT = "SYNTH-ACCOUNT-CASH-0001"
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

    def list_cash_activities(
        self, account_id, *, start_time, end_time, pagination_key
    ):
        self.calls.append((account_id, start_time, end_time, pagination_key))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def event(
    event_id="evt-1",
    category="DEPOSIT",
    amount="25.50",
    currency="USD",
    event_time="2026-10-20T09:15:00-04:00",
):
    result = {
        "activity_id": event_id,
        "activity_type": category,
        "amount": amount,
        "currency": currency,
    }
    if event_time is not ...:
        result["event_time"] = event_time
    return result


def page(activities, pagination_key=...):
    body = {"data": activities}
    if pagination_key is not ...:
        body["pagination_key"] = pagination_key
    return FakeResponse(body)


def adapter(gateway, **kwargs):
    # Record-level tests exercise the provisional record schema explicitly.
    kwargs.setdefault("allow_provisional_record_schema", True)
    return WebullSandboxCashActivitiesAdapter(
        gateway,
        selected_account_id=ACCOUNT,
        clock=lambda: OBSERVED_AT,
        **kwargs,
    )


def test_cursor_pagination_records_sanitized_provenance_and_forwards_filters():
    gateway = FakeGateway(
        page([event()], "next-cursor"),
        page([event("evt-2", "DIVIDEND", "-1.25")]),
    )
    history = adapter(
        gateway,
        start_time="2026-10-01T00:00:00Z",
        end_time="2026-10-22T00:00:00Z",
    ).fetch_history()

    assert len(history.events) == 2
    assert len(history.pages) == 2
    assert history.pagination_complete is True
    assert gateway.calls == [
        (ACCOUNT, "2026-10-01T00:00:00Z", "2026-10-22T00:00:00Z", None),
        (ACCOUNT, "2026-10-01T00:00:00Z", "2026-10-22T00:00:00Z", "next-cursor"),
    ]
    first_provenance = history.events[0].pagination_provenance[0]
    assert first_provenance.page_number == 1
    assert first_provenance.requested_cursor_digest is None
    assert first_provenance.next_cursor_digest is not None
    assert "next-cursor" not in repr(history)


def test_identical_duplicate_is_deduplicated_with_both_page_provenances():
    same = event()
    history = adapter(
        FakeGateway(page([same], "next"), page([same]))
    ).fetch_history()

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
    with pytest.raises(IncompleteHistory,     match="SdkRequestFailure"):
        adapter(FakeGateway(page([event()], "next"), RuntimeError("private details"))).fetch_history()


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


def test_empty_data_page_is_accepted_but_is_not_proof_of_no_flows():
    gateway = FakeGateway(FakeResponse({"data": []}))
    history = adapter(gateway, allow_provisional_record_schema=False).fetch_history()

    assert history.events == ()
    assert history.pagination_complete is True
    assert len(history.pages) == 1 and history.pages[0].event_count == 0
    assert len(gateway.calls) == 1
    assert "EMPTY_RESULT_NOT_PROOF_OF_NO_FLOWS" in history.trust_flags
    assert "HISTORY_COVERAGE_UNVERIFIED" in history.trust_flags
    assert "RECORD_SCHEMA_UNVERIFIED" in history.trust_flags
    assert history.external_flows_verified is False
    assert history.enforcement_eligible is False


def test_empty_page_with_cursor_continues_and_empty_terminal_page_ends():
    gateway = FakeGateway(page([], "c1"), page([event()], "c2"), page([]))
    history = adapter(gateway).fetch_history()
    assert [p.event_count for p in history.pages] == [0, 1, 0]
    assert len(history.events) == 1 and history.pagination_complete is True


def test_nonempty_records_are_rejected_while_record_schema_is_unverified():
    with pytest.raises(UnverifiedRecordSchema) as error:
        adapter(
            FakeGateway(page([event("secret-id", amount="55.55")])),
            allow_provisional_record_schema=False,
        ).fetch_history()
    assert "secret-id" not in str(error.value) and "55.55" not in str(error.value)


def test_default_adapter_rejects_nonempty_records():
    with pytest.raises(UnverifiedRecordSchema):
        WebullSandboxCashActivitiesAdapter(
            FakeGateway(page([event()])), selected_account_id=ACCOUNT, clock=lambda: OBSERVED_AT
        ).fetch_history()


def test_failed_subsequent_page_after_empty_page_is_incomplete():
    with pytest.raises(IncompleteHistory):
        adapter(FakeGateway(page([], "c1"), FakeResponse({"data": []}, status_code=500))).fetch_history()


def test_legacy_provisional_response_shapes_are_no_longer_accepted():
    with pytest.raises(MalformedResponse):
        adapter(FakeGateway(FakeResponse({"activities": [event()]}))).fetch_history()


@pytest.mark.parametrize(
    "bad_event",
    [
        event(amount=1.5),
        event(amount=True),
        event(amount=" 1.00"),
        event(amount="NaN"),
        event(currency="US"),
        event(currency=" USD"),
        event(event_id=" "),
        event(category=" "),
        event(event_time="2026-10-20T09:15:00"),
        {**event(), "unexpected": "field"},
    ],
)
def test_malformed_event_fields_fail_closed(bad_event):
    with pytest.raises(MalformedResponse):
        adapter(FakeGateway(page([bad_event]))).fetch_history()


def test_missing_broker_timestamp_stays_missing_and_local_time_is_separate():
    history = adapter(FakeGateway(page([event(event_time=...)]))).fetch_history()
    evidence = history.events[0]

    assert evidence.broker_event_time is None
    assert evidence.observed_at == OBSERVED_AT


def test_unknown_category_is_preserved_without_classification_or_flow_sign():
    history = adapter(
        FakeGateway(page([event(category="BROKER_DEFINED_FUTURE_KIND")]))
    ).fetch_history()
    evidence = history.events[0]

    assert evidence.raw_category == "BROKER_DEFINED_FUTURE_KIND"
    assert evidence.classification is None
    assert evidence.classification_trusted is False
    assert evidence.external_flow_sign_trusted is False
    assert evidence.external_flow_eligible is False
    assert "ACTIVITY_CLASSIFICATION_UNVERIFIED" in evidence.trust_flags


@pytest.mark.parametrize(
    "category",
    ["DEPOSIT", "WITHDRAWAL", "DIVIDEND", "INTEREST", "FEE", "INTERNAL_TRANSFER"],
)
def test_cash_categories_remain_distinct_raw_evidence(category):
    history = adapter(FakeGateway(page([event(category=category)]))).fetch_history()

    assert history.events[0].raw_category == category
    assert history.events[0].classification is None


def test_empty_final_page_is_not_claimed_as_complete_history():
    history = adapter(FakeGateway(page([]))).fetch_history()

    assert history.events == ()
    assert history.pagination_complete is True
    assert "HISTORY_COVERAGE_UNVERIFIED" in history.trust_flags
    assert history.external_flows_verified is False
    assert history.enforcement_eligible is False


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
        start_time="start",
        end_time="end",
        pagination_key="cursor",
    )

    assert result == "response"
    assert api.calls == [
        (
            (ACCOUNT,),
            {
                "start_time": "start",
                "end_time": "end",
                "pagination_key": "cursor",
            },
        )
    ]
    assert {
        name for name in dir(gateway) if not name.startswith("_")
    } == {"list_cash_activities"}


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
