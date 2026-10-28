"""ATS-032 deterministic PAPER forward-test daily scorecard."""

from collections import defaultdict
from datetime import date
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from src.agent_evaluation import AgentEvaluationSummary
from src.domain.models import JournalEntry
from src.forward_test import ForwardTestRunEvidence, ForwardTestRunStatus
from src.run_type import RunType

ZERO = Decimal(0)


class DenialCategory(StrEnum):
    HUMAN_REJECTION = "HUMAN_REJECTION"
    RISK = "RISK"
    OPERATIONAL_GUARD = "OPERATIONAL_GUARD"
    OTHER = "OTHER"


class PerformanceSlice(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    setup: str
    regime: int
    closed_trades: int = Field(ge=0)
    cumulative_r: Decimal
    expectancy_r: Decimal | None = None
    average_mae_r: Decimal | None = Field(default=None, ge=ZERO)
    average_mfe_r: Decimal | None = Field(default=None, ge=ZERO)
    adherence_observations: int = Field(ge=0)
    adherence_rate: float | None = Field(default=None, ge=0.0, le=1.0)


class AgentCalibrationSlice(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    arm: str
    observations: int = Field(ge=0)
    accuracy: float = Field(ge=0.0, le=1.0)
    mean_confidence: float = Field(ge=0.0, le=1.0)
    brier_score: float = Field(ge=0.0, le=1.0)
    calibration_gap: float = Field(ge=0.0, le=1.0)
    rmse: float = Field(ge=0.0, le=1.0)


class ForwardTestDailyScorecard(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    trading_day: date
    run_type: RunType = RunType.PAPER
    proposals: int = Field(ge=0)
    approvals: int = Field(ge=0)
    rejections: int = Field(ge=0)
    blocked_runs: int = Field(ge=0)
    risk_denials: int = Field(ge=0)
    guard_denials: int = Field(ge=0)
    other_denials: int = Field(ge=0)
    fills: int = Field(ge=0)
    closed_trades: int = Field(ge=0)
    cumulative_r: Decimal
    expectancy_r: Decimal | None = None
    max_drawdown_r: Decimal = Field(ge=ZERO)
    average_mae_r: Decimal | None = Field(default=None, ge=ZERO)
    average_mfe_r: Decimal | None = Field(default=None, ge=ZERO)
    adherence_observations: int = Field(ge=0)
    adherence_rate: float | None = Field(default=None, ge=0.0, le=1.0)
    by_setup_regime: tuple[PerformanceSlice, ...] = ()
    agent_calibration: tuple[AgentCalibrationSlice, ...] = ()


def _denial_category(evidence: ForwardTestRunEvidence) -> DenialCategory | None:
    if evidence.status is not ForwardTestRunStatus.BLOCKED:
        return None
    if evidence.approval_decision == "REJECT":
        return DenialCategory.HUMAN_REJECTION

    reason = (evidence.blocked_reason or "").casefold()
    if "fresh risk" in reason or "risk result" in reason or "risk check" in reason:
        return DenialCategory.RISK
    if any(
        token in reason
        for token in (
            "market data is stale",
            "order quantity",
            "order notional",
            "orders per day",
            "daily order",
            "operational",
        )
    ):
        return DenialCategory.OPERATIONAL_GUARD
    return DenialCategory.OTHER


def _paper_journals_through(
    journals: tuple[JournalEntry, ...],
    trading_day: date,
) -> tuple[JournalEntry, ...]:
    selected: list[JournalEntry] = []
    for entry in journals:
        if entry.result is None or entry.result.closed_at.date() > trading_day:
            continue
        if entry.result.run_type is not RunType.PAPER:
            raise ValueError(\n                "forward-test scorecard accepts PAPER journal outcomes only"\n            )
        selected.append(entry)
    return tuple(sorted(selected, key=lambda item: item.result.closed_at))


def _average(values: list[Decimal]) -> Decimal | None:
    if not values:
        return None
    return sum(values, ZERO) / Decimal(len(values))


def _adherence(entries: tuple[JournalEntry, ...]) -> tuple[int, float | None]:
    observed = [\n        entry.plan_followed for entry in entries if entry.plan_followed is not None\n    ]
    if not observed:
        return 0, None
    return len(observed), sum(bool(value) for value in observed) / len(observed)


def _max_drawdown(entries: tuple[JournalEntry, ...]) -> Decimal:
    equity = ZERO
    peak = ZERO
    max_drawdown = ZERO
    for entry in entries:
        assert entry.result is not None
        equity += entry.result.r_multiple
        peak = max(peak, equity)
        max_drawdown = max(max_drawdown, peak - equity)
    return max_drawdown


def _performance_slice(\n    entries: tuple[JournalEntry, ...], *, setup: str, regime: int\n) -> PerformanceSlice:
    results = [entry.result for entry in entries if entry.result is not None]
    total_r = sum((result.r_multiple for result in results), ZERO)
    maes = [result.mae_r for result in results if result.mae_r is not None]
    mfes = [result.mfe_r for result in results if result.mfe_r is not None]
    adherence_count, adherence_rate = _adherence(entries)
    count = len(results)
    return PerformanceSlice(
        setup=setup,
        regime=regime,
        closed_trades=count,
        cumulative_r=total_r,
        expectancy_r=total_r / Decimal(count) if count else None,
        average_mae_r=_average(maes),
        average_mfe_r=_average(mfes),
        adherence_observations=adherence_count,
        adherence_rate=adherence_rate,
    )


def _group_performance(\n    entries: tuple[JournalEntry, ...],\n) -> tuple[PerformanceSlice, ...]:
    grouped: dict[tuple[str, int], list[JournalEntry]] = defaultdict(list)
    for entry in entries:
        setup = entry.plan.setup_type.value
        regime = entry.plan.score_snapshot.regime_score
        grouped[(setup, regime)].append(entry)

    slices = [
        _performance_slice(tuple(group), setup=setup, regime=regime)
        for (setup, regime), group in grouped.items()
    ]
    return tuple(sorted(slices, key=lambda item: (item.setup, item.regime)))


def _latest_agent_calibration(
    evaluations: tuple[AgentEvaluationSummary, ...],
    trading_day: date,
) -> tuple[AgentCalibrationSlice, ...]:
    latest: dict[str, AgentEvaluationSummary] = {}
    for evaluation in evaluations:
        if evaluation.as_of.date() > trading_day:
            continue
        key = evaluation.arm.value
        current = latest.get(key)
        if current is None or evaluation.as_of > current.as_of:
            latest[key] = evaluation

    output = []
    for key in sorted(latest):
        calibration = latest[key].calibration
        output.append(
            AgentCalibrationSlice(
                arm=key,
                observations=calibration.observations,
                accuracy=calibration.accuracy,
                mean_confidence=calibration.mean_confidence,
                brier_score=calibration.brier_score,
                calibration_gap=calibration.calibration_gap,
                rmse=calibration.rmse,
            )
        )
    return tuple(output)


def build_forward_test_daily_scorecard(
    *,
    trading_day: date,
    run_evidence: tuple[ForwardTestRunEvidence, ...],
    journals: tuple[JournalEntry, ...],
    agent_evaluations: tuple[AgentEvaluationSummary, ...] = (),
) -> ForwardTestDailyScorecard:
    """Build one reproducible PAPER scorecard without mixing evidence domains."""

    for evidence in run_evidence:
        if evidence.run_type is not RunType.PAPER:
            raise ValueError("forward-test scorecard accepts PAPER run evidence only")

    daily_runs = tuple(
        evidence for evidence in run_evidence if evidence.as_of.date() == trading_day
    )
    history = _paper_journals_through(journals, trading_day)
    daily_closed = tuple(
        entry
        for entry in history
        if entry.result is not None and entry.result.closed_at.date() == trading_day
    )

    approvals = sum(e.approval_decision == "APPROVE" for e in daily_runs)
    rejections = sum(e.approval_decision == "REJECT" for e in daily_runs)
    blocked = tuple(e for e in daily_runs if e.status is ForwardTestRunStatus.BLOCKED)
    categories = [_denial_category(e) for e in blocked]

    results = [entry.result for entry in history if entry.result is not None]
    total_r = sum((result.r_multiple for result in results), ZERO)
    maes = [result.mae_r for result in results if result.mae_r is not None]
    mfes = [result.mfe_r for result in results if result.mfe_r is not None]
    adherence_count, adherence_rate = _adherence(history)

    return ForwardTestDailyScorecard(
        trading_day=trading_day,
        proposals=len(daily_runs),
        approvals=approvals,
        rejections=rejections,
        blocked_runs=len(blocked),
        risk_denials=sum(c is DenialCategory.RISK for c in categories),
        guard_denials=sum(c is DenialCategory.OPERATIONAL_GUARD for c in categories),
        other_denials=sum(c is DenialCategory.OTHER for c in categories),
        fills=sum(e.status is ForwardTestRunStatus.FILLED for e in daily_runs),
        closed_trades=len(daily_closed),
        cumulative_r=total_r,
        expectancy_r=total_r / Decimal(len(results)) if results else None,
        max_drawdown_r=_max_drawdown(history),
        average_mae_r=_average(maes),
        average_mfe_r=_average(mfes),
        adherence_observations=adherence_count,
        adherence_rate=adherence_rate,
        by_setup_regime=_group_performance(history),
        agent_calibration=_latest_agent_calibration(agent_evaluations, trading_day),
    )
