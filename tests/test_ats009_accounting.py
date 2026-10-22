from datetime import UTC, datetime, timedelta
from decimal import (
    ROUND_DOWN,
    ROUND_HALF_EVEN,
    ROUND_UP,
    Decimal,
    localcontext,
)
from fractions import Fraction
from uuid import UUID

import pytest
from pydantic import ValidationError

from src.ats009_accounting import (
    ExternalFlowKind,
    ExternalFlowReconciliation,
    PeriodKind,
    PeriodOpeningEquity,
    ReconciledExternalFlow,
    VerifiedAccountValuation,
    evaluate_loss_accounting,
)
from src.ats009_calendar import ResolvedSession

ACCOUNT_ID = UUID("00000000-0000-0000-0000-000000000009")
CALENDAR_VERSION = "exchange-calendars:4.13.1:XNYS"
WEEK_ID = "XNYS:week:2026-10-19"
SESSION_ID = "XNYS:2026-10-20"
WEEK_OPEN = datetime(2026, 10, 19, 13, 30, tzinfo=UTC)
DAY_OPEN = datetime(2026, 10, 20, 13, 30, tzinfo=UTC)
AS_OF = datetime(2026, 10, 20, 14, 0, tzinfo=UTC)


def _session() -> ResolvedSession:
    return ResolvedSession(
        exchange="XNYS",
        calendar_version=CALENDAR_VERSION,
        session_id=SESSION_ID,
        week_id=WEEK_ID,
        local_date=datetime(2026, 10, 20).date(),
        open_utc=datetime(2026, 10, 20, 13, 30, tzinfo=UTC),
        close_utc=datetime(2026, 10, 20, 20, 0, tzinfo=UTC),
        observed_at_utc=AS_OF,
        is_regular_session=True,
        schedule_verified=True,
        authority_revision="TEST-ONLY-REVISION",
        eligible_new_risk=True,
    )


def _valuation(
    nlv: str = "100000",
    *,
    account_id: UUID = ACCOUNT_ID,
    sequence: int = 10,
    complete: bool = True,
    reconciled: bool = True,
    currency: str = "USD",
) -> VerifiedAccountValuation:
    return VerifiedAccountValuation(
        account_id=account_id,
        source_id="account-feed",
        source_epoch="epoch-1",
        source_sequence=sequence,
        as_of=AS_OF,
        base_currency=currency,
        net_liquidation=Decimal(nlv),
        complete=complete,
        reconciled=reconciled,
        provenance_ref="TEST-ONLY-VALUATION",
    )


def _opening(
    kind: PeriodKind,
    *,
    equity: str | None = None,
    nlv: str | None = None,
    account_id: UUID = ACCOUNT_ID,
    complete: bool = True,
    reconciled: bool = True,
    currency: str = "USD",
    sequence: int = 1,
) -> PeriodOpeningEquity:
    is_daily = kind is PeriodKind.DAILY
    return PeriodOpeningEquity(
        account_id=account_id,
        period_kind=kind,
        period_id=SESSION_ID if is_daily else WEEK_ID,
        calendar_version=CALENDAR_VERSION,
        opened_at=DAY_OPEN if is_daily else WEEK_OPEN,
        opening_equity=Decimal(equity or ("100000" if is_daily else "120000")),
        opening_nlv=Decimal(nlv or "100000"),
        base_currency=currency,
        source_id="account-feed",
        source_epoch="epoch-1",
        source_sequence=sequence,
        immutable_version=1,
        complete=complete,
        reconciled=reconciled,
        provenance_ref=f"TEST-ONLY-{kind.value}-OPENING",
    )


def _flow(
    flow_id: str,
    sequence: int,
    amount: str,
    *,
    kind: ExternalFlowKind = ExternalFlowKind.CONTRIBUTION,
    occurred_at: datetime = datetime(2026, 10, 20, 13, 45, tzinfo=UTC),
) -> ReconciledExternalFlow:
    return ReconciledExternalFlow(
        account_id=ACCOUNT_ID,
        flow_id=flow_id,
        source_id="cash-ledger",
        source_epoch="flow-epoch-1",
        source_sequence=sequence,
        occurred_at=occurred_at,
        kind=kind,
        signed_amount=Decimal(amount),
        base_currency="USD",
        provenance_ref=f"TEST-ONLY-{flow_id}",
    )


def _flow_reconciliation(
    kind: PeriodKind,
    flows: tuple[ReconciledExternalFlow, ...] = (),
    *,
    complete: bool = True,
    reconciled: bool = True,
    source_sequence: int = 100,
    duplicate_bypass: bool = False,
) -> ExternalFlowReconciliation:
    is_daily = kind is PeriodKind.DAILY
    values = {
        "account_id": ACCOUNT_ID,
        "period_kind": kind,
        "period_id": SESSION_ID if is_daily else WEEK_ID,
        "window_start": DAY_OPEN if is_daily else WEEK_OPEN,
        "as_of": AS_OF,
        "base_currency": "USD",
        "source_id": "cash-ledger",
        "source_epoch": "flow-epoch-1",
        "source_sequence": source_sequence,
        "reconciliation_id": f"TEST-ONLY-{kind.value}-RECON",
        "flows": flows,
        "complete": complete,
        "reconciled": reconciled,
        "provenance_ref": "TEST-ONLY-FLOW-RECONCILIATION",
    }
    if duplicate_bypass:
        return ExternalFlowReconciliation.model_construct(**values)
    return ExternalFlowReconciliation(**values)


def _evaluate(
    *,
    nlv: str = "100000",
    daily_equity: str = "100000",
    weekly_equity: str = "120000",
    daily_opening_nlv: str = "100000",
    weekly_opening_nlv: str = "100000",
    daily_flows: tuple[ReconciledExternalFlow, ...] = (),
    weekly_flows: tuple[ReconciledExternalFlow, ...] = (),
    valuation: VerifiedAccountValuation | None = None,
    day_opening: PeriodOpeningEquity | None = None,
    week_opening: PeriodOpeningEquity | None = None,
    day_flow_state: ExternalFlowReconciliation | None = None,
    week_flow_state: ExternalFlowReconciliation | None = None,
    session: ResolvedSession | None = None,
    evaluated_at: datetime = AS_OF,
    max_valuation_age: timedelta = timedelta(minutes=5),
):
    return evaluate_loss_accounting(
        valuation=valuation or _valuation(nlv),
        daily_opening=day_opening
        or _opening(PeriodKind.DAILY, equity=daily_equity, nlv=daily_opening_nlv),
        weekly_opening=week_opening
        or _opening(PeriodKind.WEEKLY, equity=weekly_equity, nlv=weekly_opening_nlv),
        daily_flows=day_flow_state
        or _flow_reconciliation(PeriodKind.DAILY, daily_flows),
        weekly_flows=week_flow_state
        or _flow_reconciliation(PeriodKind.WEEKLY, weekly_flows),
        session=session or _session(),
        evaluated_at=evaluated_at,
        max_valuation_age=max_valuation_age,
    )


@pytest.mark.parametrize(
    ("fixture_id", "nlv", "daily_crossed", "weekly_crossed"),
    [
        ("no_loss", "100000", False, False),
        ("daily_exact_boundary", "98500", True, False),
        ("daily_just_inside", "98500.00000000000000000001", False, False),
        ("daily_just_outside", "98499.99999999999999999999", True, False),
    ],
)
def test_mapped_specification_oracle_examples(
    fixture_id: str, nlv: str, daily_crossed: bool, weekly_crossed: bool
) -> None:
    result = _evaluate(nlv=nlv)

    assert result.available, fixture_id
    assert result.daily_threshold_crossed is daily_crossed
    assert result.weekly_threshold_crossed is weekly_crossed


def test_mapped_weekly_exact_boundary_fixture_with_daily_pnl_at_zero() -> None:
    result = _evaluate(
        nlv="97000",
        daily_opening_nlv="97000",
        weekly_opening_nlv="100000",
        weekly_equity="100000",
    )

    assert result.available
    assert not result.daily_threshold_crossed
    assert result.weekly_threshold_crossed
    assert result.weekly_r == Fraction(-6)


def test_weekly_loss_just_inside_inclusive_boundary_does_not_cross() -> None:
    result = _evaluate(
        nlv="96400.01",
        daily_opening_nlv="96400.01",
        weekly_opening_nlv="100000",
        weekly_equity="120000",
    )

    assert result.available
    assert not result.daily_threshold_crossed
    assert not result.weekly_threshold_crossed


def test_deposit_and_withdrawal_are_excluded_from_net_pnl() -> None:
    deposit = _flow("deposit-1", 1, "1000")
    withdrawal = _flow(
        "withdrawal-1",
        2,
        "-500",
        kind=ExternalFlowKind.WITHDRAWAL,
    )
    result = _evaluate(
        nlv="100100",
        daily_flows=(deposit, withdrawal),
        weekly_flows=(deposit, withdrawal),
    )

    assert result.available
    assert result.daily_net_pnl == Fraction(-400)
    assert result.weekly_net_pnl == Fraction(-400)


def test_cash_flows_are_signed_and_period_scoped() -> None:
    prior_week_deposit = _flow(
        "prior-week-deposit",
        1,
        "2000",
        occurred_at=datetime(2026, 10, 19, 14, 0, tzinfo=UTC),
    )
    today_withdrawal = _flow(
        "today-withdrawal",
        2,
        "-300",
        kind=ExternalFlowKind.WITHDRAWAL,
    )
    result = _evaluate(
        nlv="99700",
        daily_flows=(today_withdrawal,),
        weekly_flows=(prior_week_deposit, today_withdrawal),
    )

    assert result.available
    assert result.daily_net_pnl == Fraction(0)
    assert result.weekly_net_pnl == Fraction(-2000)


def test_fee_already_reflected_in_nlv_is_counted_once() -> None:
    # No fee is separately subtracted: the NLV change already contains it.
    result = _evaluate(nlv="99990")

    assert result.available
    assert result.daily_net_pnl == Fraction(-10)
    assert result.weekly_net_pnl == Fraction(-10)


def test_daily_and_weekly_use_independent_frozen_equity_denominators() -> None:
    result = _evaluate(
        nlv="98500",
        daily_equity="100000",
        weekly_equity="200000",
        daily_opening_nlv="100000",
        weekly_opening_nlv="100000",
    )

    assert result.available
    assert result.daily_r == Fraction(-3)
    assert result.weekly_r == Fraction(-3, 2)
    assert result.daily_threshold_crossed
    assert not result.weekly_threshold_crossed


def test_fractional_cent_threshold_is_compared_exactly() -> None:
    result = _evaluate(nlv="98499.99999999999999999999")

    assert result.available
    assert result.daily_net_pnl == Fraction(Decimal("-1500.00000000000000000001"))
    assert result.daily_threshold_crossed


@pytest.mark.parametrize(
    ("precision", "rounding"),
    [
        (2, ROUND_DOWN),
        (6, ROUND_UP),
        (28, ROUND_HALF_EVEN),
        (50, ROUND_DOWN),
        (50, ROUND_UP),
    ],
)
def test_exact_results_are_independent_of_decimal_context(
    precision: int, rounding: str
) -> None:
    with localcontext() as context:
        context.prec = precision
        context.rounding = rounding
        result = _evaluate(
            nlv="98499.99999999999999999999",
            daily_equity="100000.00000000000000000001",
        )

    assert result.available
    assert result.daily_net_pnl == Fraction(Decimal("-1500.00000000000000000001"))
    assert result.daily_r == (
        Fraction(Decimal("-1500.00000000000000000001"))
        / (Fraction(Decimal("100000.00000000000000000001")) / 200)
    )
    assert result.daily_threshold_crossed


@pytest.mark.parametrize(
    ("equity", "expected_error"),
    [("0", "positive"), ("-1", "positive"), ("NaN", "finite")],
)
def test_invalid_opening_equity_rejected(equity: str, expected_error: str) -> None:
    with pytest.raises(ValidationError, match=expected_error):
        _opening(PeriodKind.DAILY, equity=equity)


def test_invalid_opening_equity_bypassing_model_validation_is_unavailable() -> None:
    invalid_opening = _opening(PeriodKind.DAILY).model_copy(
        update={"opening_equity": Decimal(0)}
    )
    result = _evaluate(day_opening=invalid_opening)

    assert not result.available
    assert "STRUCTURAL_VALIDATION_FAILED" in result.unavailable_reasons


def test_float_financial_input_is_rejected_to_preserve_exactness() -> None:
    with pytest.raises(ValidationError, match="floats"):
        VerifiedAccountValuation(
            account_id=ACCOUNT_ID,
            source_id="account-feed",
            source_epoch="epoch-1",
            source_sequence=10,
            as_of=AS_OF,
            base_currency="USD",
            net_liquidation=100000.1,
            complete=True,
            reconciled=True,
            provenance_ref="TEST-ONLY",
        )


def test_duplicate_external_flow_identity_is_rejected() -> None:
    duplicate = _flow("duplicate", 1, "100")
    with pytest.raises(ValidationError, match="duplicate or conflicting"):
        _flow_reconciliation(
            PeriodKind.DAILY,
            (duplicate, duplicate),
        )


def test_conflicting_duplicate_flow_is_unavailable_even_if_model_bypassed() -> None:
    first = _flow("same-id", 1, "100")
    conflicting = _flow("same-id", 2, "200")
    flow_state = _flow_reconciliation(
        PeriodKind.DAILY,
        (first, conflicting),
        duplicate_bypass=True,
    )
    result = _evaluate(
        nlv="100000",
        day_flow_state=flow_state,
        week_flow_state=_flow_reconciliation(
            PeriodKind.WEEKLY,
            (first, conflicting),
            duplicate_bypass=True,
        ),
    )

    assert not result.available
    assert "STRUCTURAL_VALIDATION_FAILED" in result.unavailable_reasons


@pytest.mark.parametrize(
    ("flow_kind", "amount"),
    [
        (ExternalFlowKind.CONTRIBUTION, "-1"),
        (ExternalFlowKind.WITHDRAWAL, "1"),
    ],
)
def test_flow_sign_must_match_kind(flow_kind: ExternalFlowKind, amount: str) -> None:
    with pytest.raises(ValidationError, match="signed amount"):
        _flow("wrong-sign", 1, amount, kind=flow_kind)


@pytest.mark.parametrize(
    "overrides",
    [
        {"complete": False},
        {"reconciled": False},
    ],
)
def test_incomplete_or_unreconciled_valuation_is_unavailable(
    overrides: dict[str, bool],
) -> None:
    result = _evaluate(valuation=_valuation(**overrides))

    assert not result.available
    assert "VALUATION_INCOMPLETE_OR_UNRECONCILED" in result.unavailable_reasons


def test_incomplete_opening_or_flow_reconciliation_is_unavailable() -> None:
    result = _evaluate(
        day_opening=_opening(PeriodKind.DAILY, complete=False),
    )
    assert not result.available
    assert "OPENING_INCOMPLETE_OR_UNRECONCILED" in result.unavailable_reasons

    result = _evaluate(
        day_flow_state=_flow_reconciliation(PeriodKind.DAILY, reconciled=False),
    )
    assert not result.available
    assert "FLOW_RECONCILIATION_INCOMPLETE" in result.unavailable_reasons


@pytest.mark.parametrize(
    "evaluated_at",
    [
        AS_OF + timedelta(minutes=6),
        AS_OF - timedelta(seconds=1),
    ],
)
def test_stale_or_future_valuation_is_unavailable(evaluated_at: datetime) -> None:
    result = _evaluate(evaluated_at=evaluated_at)

    assert not result.available
    assert "VALUATION_STALE_OR_FROM_FUTURE" in result.unavailable_reasons


def test_invalid_freshness_policy_is_unavailable() -> None:
    result = _evaluate(max_valuation_age=timedelta(0))

    assert not result.available
    assert "INVALID_FRESHNESS_POLICY" in result.unavailable_reasons


def test_reordered_external_flows_are_rejected() -> None:
    earlier_sequence_later = _flow("flow-2", 2, "20")
    later_sequence_first = _flow(
        "flow-1",
        1,
        "10",
        occurred_at=datetime(2026, 10, 20, 13, 40, tzinfo=UTC),
    )
    with pytest.raises(ValidationError, match="reordered"):
        _flow_reconciliation(
            PeriodKind.DAILY,
            (earlier_sequence_later, later_sequence_first),
        )


def test_flow_outside_window_or_beyond_watermark_is_rejected() -> None:
    after_as_of = _flow(
        "future-flow",
        1,
        "10",
        occurred_at=datetime(2026, 10, 20, 14, 1, tzinfo=UTC),
    )
    with pytest.raises(ValidationError, match="outside"):
        _flow_reconciliation(PeriodKind.DAILY, (after_as_of,))

    beyond_watermark = _flow("future-sequence", 11, "10")
    with pytest.raises(ValidationError, match="watermark"):
        _flow_reconciliation(
            PeriodKind.DAILY,
            (beyond_watermark,),
            source_sequence=10,
        )


@pytest.mark.parametrize(
    "session",
    [
        ResolvedSession(
            exchange="XNYS",
            calendar_version=CALENDAR_VERSION,
            session_id=SESSION_ID,
            week_id=WEEK_ID,
            local_date=datetime(2026, 10, 20).date(),
            open_utc=datetime(2026, 10, 20, 13, 30, tzinfo=UTC),
            close_utc=datetime(2026, 10, 20, 20, 0, tzinfo=UTC),
            observed_at_utc=AS_OF,
            is_regular_session=False,
            schedule_verified=True,
            authority_revision="TEST-ONLY",
            eligible_new_risk=False,
        ),
        ResolvedSession(
            exchange="XNYS",
            calendar_version=CALENDAR_VERSION,
            session_id=SESSION_ID,
            week_id=WEEK_ID,
            local_date=datetime(2026, 10, 20).date(),
            open_utc=datetime(2026, 10, 20, 13, 30, tzinfo=UTC),
            close_utc=datetime(2026, 10, 20, 20, 0, tzinfo=UTC),
            observed_at_utc=AS_OF,
            is_regular_session=True,
            schedule_verified=False,
            authority_revision=None,
            eligible_new_risk=False,
        ),
    ],
)
def test_unverified_or_non_regular_calendar_state_is_unavailable(
    session: ResolvedSession,
) -> None:
    result = _evaluate(session=session)

    assert not result.available


def test_calendar_period_or_version_mismatch_is_unavailable() -> None:
    result = _evaluate(
        day_opening=_opening(
            PeriodKind.DAILY,
            # A stale session identity must not be substituted for the current one.
        ).model_copy(update={"period_id": "XNYS:2026-10-19"}),
    )

    assert not result.available
    assert "DAILY_PERIOD_MISMATCH" in result.unavailable_reasons


def test_malformed_calendar_week_identity_is_unavailable() -> None:
    invalid_session = _session()
    object.__setattr__(invalid_session, "week_id", "XNYS:week:not-a-date")
    result = _evaluate(session=invalid_session)

    assert not result.available
    assert "INVALID_CALENDAR_CONTRACT" in result.unavailable_reasons


def test_account_currency_source_and_sequence_mismatches_are_unavailable() -> None:
    result = _evaluate(
        valuation=_valuation(sequence=1),
        day_opening=_opening(PeriodKind.DAILY, sequence=2),
    )

    assert not result.available
    assert "VALUATION_SEQUENCE_REGRESSION" in result.unavailable_reasons

    result = _evaluate(
        valuation=_valuation(currency="CAD"),
    )
    assert not result.available
    assert "CURRENCY_MISMATCH" in result.unavailable_reasons


def test_account_source_epoch_and_opening_order_conflicts_are_unavailable() -> None:
    result = _evaluate(
        valuation=_valuation(account_id=UUID("00000000-0000-0000-0000-000000000010"))
    )
    assert not result.available
    assert "ACCOUNT_MISMATCH" in result.unavailable_reasons

    stale_epoch_opening = _opening(PeriodKind.DAILY).model_copy(
        update={"source_epoch": "old-epoch"}
    )
    result = _evaluate(day_opening=stale_epoch_opening)
    assert not result.available
    assert "VALUATION_SOURCE_EPOCH_MISMATCH" in result.unavailable_reasons

    after_valuation_opening = _opening(PeriodKind.DAILY).model_copy(
        update={"opened_at": AS_OF + timedelta(seconds=1)}
    )
    result = _evaluate(day_opening=after_valuation_opening)
    assert not result.available
    assert "OPENING_AFTER_VALUATION" in result.unavailable_reasons


def test_daily_flow_set_must_be_a_subset_of_weekly_flow_set() -> None:
    daily_flow = _flow("daily-only", 1, "50")
    result = _evaluate(
        daily_flows=(daily_flow,),
        weekly_flows=(),
    )

    assert not result.available
    assert "DAILY_WEEKLY_FLOW_SET_CONFLICT" in result.unavailable_reasons


def test_contracts_are_immutable_and_provenance_is_not_authentication() -> None:
    valuation = _valuation()
    with pytest.raises(ValidationError):
        valuation.net_liquidation = Decimal(1)  # type: ignore[misc]

    # Constructible provenance labels and flags are structural assertions only.
    result = _evaluate(valuation=valuation)
    assert result.available
    assert not hasattr(result, "authenticated")
    assert not hasattr(result, "sticky_locked")
