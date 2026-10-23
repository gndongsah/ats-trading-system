from dataclasses import replace
from decimal import Decimal

import pytest

from src.cash_history_comparison import compare_histories, comparison_windows
from src.webull_sandbox_cash_activities import (
    OfficialSdkCashActivitiesGateway,
    WebullSandboxCashActivitiesAdapter,
)
from tests.test_webull_sandbox_evidence_comparison import go, record
from tests.test_webull_sandbox_evidence_runner import ACCOUNT_ID, FakeActivityApi

S = "2026-10-07T00:00:00.000Z"
O = "2026-10-09T00:00:00.000Z"
E = "2026-10-10T00:00:00.000Z"
C = "2026-10-11T00:00:00.000Z"
OPTIONS = {
    "start_time": S,
    "end_time": E,
    "overlap_start_time": O,
    "enclosing_end_time": C,
}


def history(rows):
    return WebullSandboxCashActivitiesAdapter(
        OfficialSdkCashActivitiesGateway(FakeActivityApi([{"data": rows}])),
        selected_account_id=ACCOUNT_ID,
        start_time=S,
        end_time=E,
    ).fetch_history()


def test_protocol_same_account_four_windows_and_redaction(capsys):
    status, account, activity = go(
        [
            {"data": [record()]},
            {"data": [record()]},
            {"data": []},
            {"data": [record()]},
        ],
        **OPTIONS,
    )
    out = capsys.readouterr().out
    assert status == 0
    assert activity.calls == [
        (ACCOUNT_ID, a, b, None) for a, b in comparison_windows(S, E, O, C)
    ]
    assert sum(call[0] == "balance" for call in account.calls) == 1
    assert "'equal_parsed_event_sets': True" in out
    for secret in (ACCOUNT_ID, "EVT-PRIVATE-1", "250000.50", "20:14:17"):
        assert secret not in out
    assert "'history_coverage': 'UNVERIFIED'" in out
    assert "enforcement_eligible: False" in out


def test_same_counts_different_ids_are_detected():
    result = compare_histories(history([record("a")]), history([record("b")]))
    assert result["left_only_count"] == result["right_only_count"] == 1
    assert result["shared_id_count"] == 0


@pytest.mark.parametrize(
    "change",
    [
        {"amount": "1.01"},
        {"biz_time": "opaque-other"},
        {"trade_date": "2026-10-08"},
        {"market": "US"},
        {"symbol": "SECRET"},
        {"activity_sub_type": "WIRE"},
    ],
)
def test_changed_parsed_fields_detected(change):
    result = compare_histories(history([record()]), history([record(**change)]))
    assert result["changed_parsed_payload_count"] == 1


def test_order_observation_and_decimal_representation_do_not_change_event():
    left = history([record("a", "1.0"), record("b", "2")])
    right = history([record("b", "2.00"), record("a", "1.00")])
    assert left.events[0].amount == Decimal(1)
    assert compare_histories(left, right)["unchanged_parsed_payload_count"] == 2


def test_incomplete_and_duplicate_inputs_rejected():
    h = history([record()])
    for bad in (replace(h, pagination_complete=False), replace(h, events=h.events * 2)):
        with pytest.raises(ValueError):
            compare_histories(h, bad)


@pytest.mark.parametrize("position", [1, 2, 3])
def test_later_query_failure_cannot_report_complete(position, capsys):
    pages = [{"data": []} for _ in range(position)] + [
        {"data": [record(account="FOREIGN")]}
    ]
    status, _, _ = go(pages, **OPTIONS)
    out = capsys.readouterr().out
    assert status == 1
    assert "cash_history_comparison: NOT_COMPLETED" in out
    assert "cash_pagination_terminal_for_request_sequence: False" in out
    assert "FOREIGN" not in out


@pytest.mark.parametrize(
    "overlap,enclosing", [(None, C), (O, None), (S, C), (O, E), (E, C)]
)
def test_invalid_protocol_blocks_before_credentials(overlap, enclosing, capsys):
    from src.webull_sandbox_evidence import run

    built = []
    status = run(
        start_time=S,
        end_time=E,
        overlap_start_time=overlap,
        enclosing_end_time=enclosing,
        environ={},
        build_trade_client=lambda *_: built.append(True),
    )
    assert status == 2 and not built
    assert "INVALID_QUERY_WINDOW" in capsys.readouterr().out


def test_missing_enclosing_event_and_changed_overlap_are_reported(capsys):
    status, _, _ = go(
        [
            {"data": [record("a"), record("b")]},
            {"data": [record("b"), record("a")]},
            {"data": [record("a", "1.01")]},
            {"data": [record("a")]},
        ],
        **OPTIONS,
    )
    out = capsys.readouterr().out
    assert status == 0  # Completed observation; differences are not transport errors.
    assert "'left_only_count': 1" in out
    assert "'changed_parsed_payload_count': 1" in out


def test_each_query_restarts_cursor_and_deduplicates_pages(capsys):
    pages = []
    for _ in range(4):
        pages.extend(
            [
                {"data": [record()], "pagination_key": "PRIVATE-CURSOR"},
                {"data": [record()]},
            ]
        )
    status, _, activity = go(pages, **OPTIONS)
    out = capsys.readouterr().out
    assert status == 0
    assert [c[3] for c in activity.calls] == [None, "PRIVATE-CURSOR"] * 4
    assert "'equal_parsed_event_sets': True" in out
    assert "PRIVATE-CURSOR" not in out
