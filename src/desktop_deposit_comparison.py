"""Private operator-observed deposit comparison; no accounting authority."""

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from src.webull_sandbox_cash_activities import (
    _ACTIVITY_SUBTYPES,
    CashActivityHistory,
    _parse_amount,
    _parse_trade_date,
)


@dataclass(frozen=True, repr=False)
class DesktopDeposit:
    amount: Decimal = field(repr=False)
    displayed_date: date = field(repr=False)
    currency: str = field(repr=False)
    subtype: str = field(repr=False)


def parse_desktop_deposit(values: tuple[str, str, str, str]) -> DesktopDeposit | None:
    """All blank skips; otherwise all four exact, unambiguous values required."""
    if len(values) != 4 or any(not isinstance(value, str) for value in values):
        raise ValueError("invalid desktop deposit input")
    if all(value == "" for value in values):
        return None
    amount, displayed_date, currency, subtype = values
    # Parser errors are emitted as a fixed input-error code by the caller.
    parsed_amount = _parse_amount(amount)
    parsed_date = _parse_trade_date(displayed_date)
    if (
        parsed_amount <= 0
        or currency != "USD"
        or subtype not in _ACTIVITY_SUBTYPES["DEPOSIT"]
    ):
        raise ValueError("invalid desktop deposit input")
    return DesktopDeposit(parsed_amount, parsed_date, currency, subtype)


def compare_desktop_deposit(
    history: CashActivityHistory, expected: DesktopDeposit | None
) -> dict:
    """Match all entered fields on the same record; never infer a timezone."""
    if expected is None:
        return {"status": "NOT_REQUESTED", "accounting_authority": False}
    if not history.pagination_complete:
        raise ValueError("desktop comparison requires terminal request sequence")
    matches = [
        event
        for event in history.events
        if event.raw_category == "DEPOSIT"
        and event.amount == expected.amount
        and event.trade_date == expected.displayed_date
        and event.currency == expected.currency
        and event.raw_subcategory == expected.subtype
    ]
    return {
        "status": "UNIQUE_RECORD_MATCH_OBSERVED"
        if len(matches) == 1
        else ("AMBIGUOUS_MULTIPLE_MATCHES" if matches else "NO_EXACT_RECORD_MATCH"),
        "matching_record_count": len(matches),
        "matching_reversal_ambiguous_count": sum(
            event.reversal_ambiguous for event in matches
        ),
        "date_comparison": "DISPLAYED_DATE_TO_TRADE_DATE_ONLY",
        "broker_time_semantics": "UNVERIFIED",
        "history_coverage": "UNVERIFIED",
        "accounting_authority": False,
        "enforcement_eligible": False,
    }
