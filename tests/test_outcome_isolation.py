from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest

from src.domain.models import TradeResult
from src.outcome_analytics import (
    OutcomeScope,
    eligible_for_live_validation,
    live_validation_results,
    summarize_outcomes,
)
from src.run_type import RunType

NOW = datetime(2026, 10, 26, 22, 0, tzinfo=UTC)
PLAN_ID = uuid4()


def result(run_type: RunType, r_multiple: str) -> TradeResult:
    return TradeResult(
        plan_id=PLAN_ID,
        run_type=run_type,
        closed_at=NOW,
        realized_pnl=Decimal(100),
        r_multiple=Decimal(r_multiple),
        outcome="test",
    )


def test_trade_result_defaults_to_non_live_paper_mode_for_legacy_callers():
    legacy = TradeResult(
        plan_id=PLAN_ID,
        closed_at=NOW,
        realized_pnl=Decimal(0),
        r_multiple=Decimal(0),
        outcome="legacy",
    )
    assert legacy.run_type is RunType.PAPER
    assert not eligible_for_live_validation(legacy)


def test_live_validation_excludes_paper_and_replay():
    live = result(RunType.LIVE, "2")
    paper = result(RunType.PAPER, "20")
    replay = result(RunType.REPLAY, "200")

    assert eligible_for_live_validation(live)
    assert not eligible_for_live_validation(paper)
    assert not eligible_for_live_validation(replay)
    assert live_validation_results((paper, replay, live)) == (live,)


def test_default_analytics_scope_is_live_only():
    results = (
        result(RunType.LIVE, "2"),
        result(RunType.LIVE, "-1"),
        result(RunType.PAPER, "9"),
        result(RunType.REPLAY, "90"),
    )

    summary = summarize_outcomes(results)

    assert summary.count == 2
    assert summary.total_r == Decimal(1)
    assert summary.expectancy_r == Decimal("0.5")
    assert summary.wins == 1
    assert summary.losses == 1
    assert summary.run_types == frozenset({RunType.LIVE})


def test_mixed_run_analytics_require_explicit_scope():
    results = (
        result(RunType.LIVE, "2"),
        result(RunType.PAPER, "4"),
        result(RunType.REPLAY, "6"),
    )

    live_and_paper = summarize_outcomes(
        results,
        scope=OutcomeScope.explicit(RunType.LIVE, RunType.PAPER),
    )
    all_modes = summarize_outcomes(
        results,
        scope=OutcomeScope.explicit(*tuple(RunType)),
    )

    assert live_and_paper.count == 2
    assert live_and_paper.expectancy_r == Decimal(3)
    assert live_and_paper.run_types == frozenset({RunType.LIVE, RunType.PAPER})
    assert all_modes.count == 3
    assert all_modes.expectancy_r == Decimal(4)


def test_empty_explicit_scope_is_rejected():
    with pytest.raises(ValueError, match="at least one run type"):
        OutcomeScope.explicit()


def test_empty_live_summary_has_no_expectancy():
    summary = summarize_outcomes((result(RunType.REPLAY, "10"),))
    assert summary.count == 0
    assert summary.total_r == Decimal(0)
    assert summary.expectancy_r is None
