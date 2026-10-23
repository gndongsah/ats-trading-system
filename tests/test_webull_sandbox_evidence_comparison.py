from __future__ import annotations

from decimal import Decimal

import pytest

from src.webull_sandbox_evidence import (
    compare_amount,
    parse_expected_amount,
    run,
)
from tests.test_webull_sandbox_evidence_runner import (
    ACCOUNT_ID,
    ENV,
    EmptyKeyring,
    FakeAccountApi,
    FakeActivityApi,
    FakeResponse,
    FakeTradeClient,
)

START = "2026-10-01T00:00:00.000Z"
END = "2026-10-22T23:59:59.999Z"
BALANCE = "1000000.00"
DEPOSIT = "250000.50"


class BalanceAccountApi(FakeAccountApi):
    def get_account_balance(self, account_id):
        self.calls.append(("balance", account_id))
        return FakeResponse(
            {
                "total_net_liquidation_value": BALANCE,
                "account_currency_assets": [
                    {
                        "currency": "USD",
                        "net_liquidation_value": BALANCE,
                        "cash_balance": BALANCE,
                    }
                ],
            }
        )


def record(identifier="EVT-PRIVATE-1", amount=DEPOSIT, account=ACCOUNT_ID, **extra):
    row = {
        "id": identifier,
        "account_id": account,
        "activity_type": "DEPOSIT",
        "activity_sub_type": "ACH",
        "currency": "USD",
        "trade_date": "2026-10-07",
        "net_amount": amount,
        "biz_time": "2026-10-07T20:14:17.000Z",
    }
    row.update(extra)
    return row


def go(payloads, **kwargs):
    account = kwargs.pop("account", None) or BalanceAccountApi()
    activity = FakeActivityApi(payloads)
    status = run(
        environ=ENV,
        keyring_module=EmptyKeyring(),
        isatty=True,
        input_fn=lambda _: "1",
        build_trade_client=lambda *_: FakeTradeClient(account, activity),
        **kwargs,
    )
    return status, account, activity


def test_amount_parsing_is_exact_and_strict():
    assert parse_expected_amount("1000000") == Decimal("1000000.00")
    assert parse_expected_amount("-5", allow_negative=True) == Decimal(-5)
    for bad in (
        "",
        "NaN",
        "Infinity",
        "1e6",
        "1,000",
        " 1",
        "+1",
        "1.",
        ".5",
        "$1",
        "1.123456789",
        "-1",
    ):
        with pytest.raises(ValueError):
            parse_expected_amount(bad)
    with pytest.raises(ValueError):
        parse_expected_amount(1000)  # type: ignore[arg-type]


def test_compare_amount_states():
    assert compare_amount(None, Decimal(1)) == "NOT_REQUESTED"
    assert compare_amount(Decimal(1), None) == "ACTUAL_UNAVAILABLE"
    assert compare_amount(Decimal("1.0"), Decimal("1.00")) == "MATCH"
    assert compare_amount(Decimal("1.01"), Decimal(1)) == "MISMATCH"


def test_balance_match_prints_status_only(capsys):
    status, _, _ = go(
        [{"data": []}],
        expected_nlv_input=lambda: "1000000",
        expected_cash_input=lambda: "1000000.0",
    )
    out = capsys.readouterr().out
    assert status == 0
    assert "balance_nlv_comparison: MATCH" in out
    assert "balance_cash_comparison: MATCH" in out
    assert "1000000" not in out
    assert "cash_query_window: DEFAULT_OMITTED" in out


def test_balance_mismatch_and_blank_skip(capsys):
    go(
        [{"data": []}],
        expected_nlv_input=lambda: "999999.99",
        expected_cash_input=lambda: "",
    )
    out = capsys.readouterr().out
    assert "balance_nlv_comparison: MISMATCH" in out
    assert "balance_cash_comparison: NOT_REQUESTED" in out
    assert "999999.99" not in out


@pytest.mark.parametrize("bad", ["NaN", "abc", "1e3", "Infinity", "1,000"])
def test_malformed_expected_values_block_before_any_api_call(bad, capsys):
    account = BalanceAccountApi()
    built = []
    status = run(
        environ=ENV,
        keyring_module=EmptyKeyring(),
        isatty=True,
        input_fn=lambda _: "1",
        expected_nlv_input=lambda: bad,
        build_trade_client=lambda *_: built.append(1),
    )
    out = capsys.readouterr().out
    assert status == 2
    assert built == [] and account.calls == []
    assert "MALFORMED_EXPECTED_AMOUNT" in out
    assert bad not in out


def test_explicit_window_is_forwarded_and_summarized(capsys):
    status, _, activity = go([{"data": [record()]}], start_time=START, end_time=END)
    out = capsys.readouterr().out
    assert status == 0
    assert activity.calls == [(ACCOUNT_ID, START, END, None)]
    assert "cash_parser: STRICT_DOCUMENTED_SCHEMA" in out
    assert "'event_count': 1" in out
    assert "DEPOSIT/ACH" in out
    assert "biz_time: RAW_UNVERIFIED_FORMAT_AND_TIMEZONE" in out
    assert "reversal_linkage: NOT_INFERRED" in out
    for private in ("EVT-PRIVATE-1", DEPOSIT, "20:14:17", ACCOUNT_ID):
        assert private not in out


@pytest.mark.parametrize(
    ("start", "end"),
    [
        (START, None),
        (None, END),
        ("2026-10-01", END),
        ("2025-12-31T00:00:00.000Z", "2026-01-01T00:00:00.000Z"),
        (END, START),
    ],
)
def test_invalid_windows_block_before_credentials(start, end, capsys):
    built = []
    status = run(
        environ=ENV,
        isatty=True,
        start_time=start,
        end_time=end,
        build_trade_client=lambda *_: built.append(1),
    )
    assert status == 2
    assert built == []
    assert "INVALID_QUERY_WINDOW" in capsys.readouterr().out


def test_expected_deposit_match_and_no_match(capsys):
    go([{"data": [record()]}], expected_deposit_input=lambda: "250000.5")
    out = capsys.readouterr().out
    assert "'expected_deposit': 'MATCH'" in out
    assert "'expected_deposit_matching_record_count': 1" in out
    assert "250000" not in out
    assert "external_flows_verified: False" in out
    assert "cash_category_and_sign_semantics: UNVERIFIED" in out

    go([{"data": [record()]}], expected_deposit_input=lambda: "1.00")
    assert "'expected_deposit': 'NO_MATCH'" in capsys.readouterr().out


def test_amount_match_on_non_deposit_record_is_not_a_deposit_match(capsys):
    other = record(activity_type="FEES", activity_sub_type="WIRE_FEE")
    go([{"data": [other]}], expected_deposit_input=lambda: DEPOSIT)
    assert "'expected_deposit': 'NO_MATCH'" in capsys.readouterr().out


def test_deposit_blank_skips_but_uses_strict_parser(capsys):
    go([{"data": [record()]}], expected_deposit_input=lambda: "")
    assert "'expected_deposit': 'NOT_REQUESTED'" in capsys.readouterr().out


def test_pagination_follows_cursor_and_redacts_it(capsys):
    status, _, activity = go(
        [
            {"data": [record()], "pagination_key": "PRIVATE-CURSOR"},
            {"data": [record("EVT-2", "1.00")]},
        ],
        start_time=START,
        end_time=END,
    )
    out = capsys.readouterr().out
    assert status == 0
    assert [c[3] for c in activity.calls] == [None, "PRIVATE-CURSOR"]
    assert "cash_activities_pages: 2" in out
    assert "PRIVATE-CURSOR" not in out


def test_repeated_cursor_fails_closed(capsys):
    status, _, _ = go(
        [
            {"data": [], "pagination_key": "SAME"},
            {"data": [], "pagination_key": "SAME"},
        ],
        start_time=START,
        end_time=END,
    )
    out = capsys.readouterr().out
    assert status == 1
    assert "error_type: RepeatedCursor" in out
    assert "SAME" not in out


def test_failed_later_page_is_incomplete_history(capsys):
    status, _, _ = go(
        [{"data": [], "pagination_key": "NEXT"}, {"unexpected": []}],
        start_time=START,
        end_time=END,
    )
    out = capsys.readouterr().out
    assert status == 1
    assert "error_type: IncompleteHistory" in out
    assert "cash_pagination_terminal_for_request_sequence: False" in out


def test_malformed_and_foreign_account_records_are_rejected(capsys):
    for payload in (
        {"data": [record(account="OTHER-PRIVATE-ACCOUNT")]},
        {"data": [record(amount="NaN")]},
        {"data": [record(unknown_field="x")]},
        {"data": "not-a-list"},
    ):
        status, _, _ = go([payload], start_time=START, end_time=END)
        out = capsys.readouterr().out
        assert status == 1
        assert "OTHER-PRIVATE-ACCOUNT" not in out
        assert "cash_pagination_terminal_for_request_sequence: False" in out


def test_page_limit_fails_closed(monkeypatch, capsys):
    monkeypatch.setattr("src.webull_sandbox_evidence._MAX_PAGES", 2)
    status, _, activity = go(
        [{"data": [], "pagination_key": f"C{i}"} for i in range(5)],
        start_time=START,
        end_time=END,
    )
    assert status == 1
    assert len(activity.calls) == 2
    assert "error_type: IncompleteHistory" in capsys.readouterr().out


def test_comparison_never_enables_trust_or_orders(capsys):
    go(
        [{"data": [record()]}],
        expected_nlv_input=lambda: BALANCE,
        expected_deposit_input=lambda: DEPOSIT,
        start_time=START,
        end_time=END,
    )
    out = capsys.readouterr().out
    assert "reconciliation: NOT_PERFORMED" in out
    assert "account_identity_binding: UNVERIFIED" in out
    assert "enforcement_eligible: False" in out
    assert "paper_live_order_submission: DISABLED" in out
