from datetime import UTC, datetime, timedelta
from decimal import Decimal, localcontext
from uuid import UUID

import pytest
from pydantic import ValidationError

from src.simulation_ledger import EntryKind, SimulationEntry, SimulationLedger

AT = datetime(2026, 10, 19, 13, 30, tzinfo=UTC)


def ledger():
    return SimulationLedger(account_id=UUID(int=9), opened_at=AT, opening_nlv="100000")


def event(event_id="pnl", amount="-1500", seconds=1):
    return SimulationEntry(
        event_id=event_id,
        occurred_at=AT + timedelta(seconds=seconds),
        kind=EntryKind.PNL,
        amount=amount,
    )


def test_replay_is_idempotent_and_conflicting_identity_fails():
    original = ledger()
    updated = original.append(event())
    assert updated.append(event()) is updated
    assert original.entries == ()
    assert updated.valuation(AT).net_liquidation == Decimal(100000)
    assert updated.valuation(AT + timedelta(seconds=2)).net_liquidation == Decimal(
        98500
    )
    with pytest.raises(ValueError, match="conflicting"):
        updated.append(event(amount="-1400"))


def test_fees_and_recovery_are_explicit_pnl_deltas():
    updated = (
        ledger()
        .append(event())
        .append(event("fee", "-1", 2))
        .append(event("recovery", "1501", 3))
    )
    assert updated.valuation(AT + timedelta(seconds=2)).net_liquidation == Decimal(
        98499
    )
    assert updated.valuation(AT + timedelta(seconds=3)).net_liquidation == Decimal(
        100000
    )


def test_order_and_direct_constructor_cannot_bypass_ledger_invariants():
    with pytest.raises(ValueError):
        ledger().append(event(seconds=0))
    with pytest.raises(ValueError):
        ledger().append(event(seconds=3)).append(event("earlier", seconds=2))
    with pytest.raises(ValidationError):
        SimulationLedger(
            account_id=UUID(int=9),
            opened_at=AT,
            opening_nlv="100000",
            entries=(event(), event()),
        )


@pytest.mark.parametrize("amount", ["NaN", "Infinity", "0", 1.25, True])
def test_invalid_amounts_rejected(amount):
    with pytest.raises(ValidationError):
        event(amount=amount)


def test_exact_under_low_decimal_precision():
    with localcontext() as ctx:
        ctx.prec = 2
        updated = ledger().append(event(amount="-0.00000001"))
        assert updated.valuation(AT + timedelta(seconds=1)).net_liquidation == Decimal(
            "99999.99999999"
        )
