from dataclasses import replace

import pytest

from src.desktop_deposit_comparison import (
    compare_desktop_deposit,
    parse_desktop_deposit,
)
from src.webull_sandbox_evidence import run
from src.webull_sandbox_readonly import WebullReadOnlyError
from tests.test_cash_history_comparison import E, S, history
from tests.test_webull_sandbox_evidence_comparison import go, record
from tests.test_webull_sandbox_evidence_runner import ACCOUNT_ID

VALUES = ("250000.50", "2026-10-07", "USD", "ACH")


def test_unique_same_record_match_and_private_repr():
    expected = parse_desktop_deposit(VALUES)
    result = compare_desktop_deposit(history([record()]), expected)
    assert result["status"] == "UNIQUE_RECORD_MATCH_OBSERVED"
    assert result["matching_record_count"] == 1
    assert not result["accounting_authority"] and not result["enforcement_eligible"]
    for value in VALUES:
        assert value not in repr(expected)
        assert value not in str(result)


def test_distinct_ids_with_identical_values_are_ambiguous():
    result = compare_desktop_deposit(
        history([record("a"), record("b")]), parse_desktop_deposit(VALUES)
    )
    assert result["status"] == "AMBIGUOUS_MULTIPLE_MATCHES"
    assert result["matching_record_count"] == 2


def test_separate_partial_matches_are_not_one_matching_record():
    rows = [record("a", "1"), record("b", trade_date="2026-10-08")]
    assert (
        compare_desktop_deposit(history(rows), parse_desktop_deposit(VALUES))["status"]
        == "NO_EXACT_RECORD_MATCH"
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"activity_type": "WITHDRAW"},
        {"activity_sub_type": "WIRE"},
        {"trade_date": "2026-10-08"},
        {"amount": "-250000.50"},
    ],
)
def test_each_mismatched_field_prevents_match(changes):
    result = compare_desktop_deposit(
        history([record(**changes)]), parse_desktop_deposit(VALUES)
    )
    assert result["matching_record_count"] == 0


def test_opaque_business_time_not_interpreted():
    result = compare_desktop_deposit(
        history([record(biz_time="opaque other day")]), parse_desktop_deposit(VALUES)
    )
    assert result["matching_record_count"] == 1
    assert result["broker_time_semantics"] == "UNVERIFIED"


@pytest.mark.parametrize(
    "values",
    [
        ("", "2026-10-07", "USD", "ACH"),
        ("NaN", "2026-10-07", "USD", "ACH"),
        ("-1", "2026-10-07", "USD", "ACH"),
        ("0", "2026-10-07", "USD", "ACH"),
        ("1", "2026-02-30", "USD", "ACH"),
        ("1", "2026-10-07", "EUR", "ACH"),
        ("1", "2026-10-07", "USD", "guessed"),
    ],
)
def test_invalid_input_blocks_before_credentials_and_redacts(values, capsys):
    built = []
    status = run(
        environ={},
        start_time=S,
        end_time=E,
        paper_identifier_input=lambda: "unused",
        desktop_deposit_input=lambda: values,
        build_trade_client=lambda *_: built.append(True),
    )
    assert status == 2 and not built
    out = capsys.readouterr().out
    assert "INVALID_DESKTOP_DEPOSIT" in out
    assert "2026-10-07" not in out and "guessed" not in out


@pytest.mark.parametrize("with_window,with_identifier", [(False, True), (True, False)])
def test_required_context_blocks_before_prompts(with_window, with_identifier, capsys):
    prompts = []
    status = run(
        environ={},
        start_time=S if with_window else None,
        end_time=E if with_window else None,
        paper_identifier_input=(lambda: "unused") if with_identifier else None,
        desktop_deposit_input=lambda: prompts.append(True),
    )
    assert status == 2 and not prompts
    assert (
        "DESKTOP_COMPARISON_REQUIRES_WINDOW_AND_IDENTIFIER" in capsys.readouterr().out
    )


def test_incomplete_history_rejected():
    with pytest.raises(ValueError):
        compare_desktop_deposit(
            replace(history([record()]), pagination_complete=False),
            parse_desktop_deposit(VALUES),
        )


def test_all_blank_skips_and_partial_blank_rejected():
    assert parse_desktop_deposit(("", "", "", "")) is None
    assert compare_desktop_deposit(history([]), None)["status"] == "NOT_REQUESTED"
    with pytest.raises((ValueError, WebullReadOnlyError)):
        parse_desktop_deposit(("1", "", "", ""))


def test_runner_success_uses_hidden_input_and_sanitized_output(capsys):
    status, _, _ = go(
        [{"data": [record()]}],
        start_time="2026-10-01T00:00:00.000Z",
        end_time=E,
        paper_identifier_input=lambda: ACCOUNT_ID,
        desktop_deposit_input=lambda: VALUES,
    )
    out = capsys.readouterr().out
    assert status == 0 and "UNIQUE_RECORD_MATCH_OBSERVED" in out
    for private in (ACCOUNT_ID, VALUES[0], VALUES[1], "EVT-PRIVATE-1"):
        assert private not in out


def test_identifier_mismatch_prevents_cash_comparison(capsys):
    status, _, activity = go(
        [],
        start_time=S,
        end_time=E,
        paper_identifier_input=lambda: "wrong",
        desktop_deposit_input=lambda: VALUES,
    )
    assert status == 1 and activity.calls == []
    assert "desktop_deposit_comparison: NOT_COMPLETED" in capsys.readouterr().out


def test_later_protocol_failure_suppresses_desktop_match(capsys):
    from tests.test_cash_history_comparison import OPTIONS

    status, _, _ = go(
        [{"data": [record()]}, {"bad": []}],
        **OPTIONS,
        paper_identifier_input=lambda: ACCOUNT_ID,
        desktop_deposit_input=lambda: VALUES,
    )
    out = capsys.readouterr().out
    assert status == 1
    assert "desktop_deposit_comparison: NOT_COMPLETED" in out
    assert "UNIQUE_RECORD_MATCH_OBSERVED" not in out
