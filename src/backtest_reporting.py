"""Deterministic REPLAY-only reporting for ATS-016 backtest outcomes."""

from datetime import datetime
from decimal import ROUND_HALF_EVEN, Context, Decimal, localcontext
from hashlib import sha256

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictInt,
    field_validator,
    model_validator,
)

from src.backtest import BacktestTradeOutcome
from src.domain.models import Regime, _require_utc
from src.run_type import RunType
from src.swing_triggers import SwingTriggerType

ZERO = Decimal(0)
METRIC_DECIMAL_PLACES = 12
METRIC_QUANTUM = Decimal(1).scaleb(-METRIC_DECIMAL_PLACES)
METRIC_CONTEXT = Context(prec=80, rounding=ROUND_HALF_EVEN)


class ReportModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class BacktestReportingUnavailable(ValueError):
    """Backtest outcomes cannot be reported safely under the requested scope."""


class WalkForwardWindow(ReportModel):
    label: str = Field(min_length=1, max_length=128)
    start: datetime
    end: datetime

    @field_validator("label")
    @classmethod
    def label_is_trimmed(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("walk-forward window label must not be blank")
        return normalized

    @field_validator("start", "end")
    @classmethod
    def timestamps_are_utc(cls, value: datetime) -> datetime:
        return _require_utc(value)

    @model_validator(mode="after")
    def interval_is_forward(self) -> "WalkForwardWindow":
        if self.end <= self.start:
            raise ValueError("walk-forward window end must be after start")
        return self

    def contains(self, timestamp: datetime) -> bool:
        return self.start <= timestamp < self.end


class WalkForwardPlan(ReportModel):
    windows: tuple[WalkForwardWindow, ...]

    @model_validator(mode="after")
    def windows_are_ordered_non_overlapping(self) -> "WalkForwardPlan":
        if not self.windows:
            raise ValueError("walk-forward plan requires at least one window")
        labels = [window.label for window in self.windows]
        if len(set(labels)) != len(labels):
            raise ValueError("walk-forward window labels must be unique")
        for prior, current in zip(self.windows, self.windows[1:], strict=False):
            if current.start < prior.start:
                raise ValueError("walk-forward windows must be ordered by start time")
            if current.start < prior.end:
                raise ValueError("walk-forward windows must not overlap")
        return self


class PerformanceMetrics(ReportModel):
    trade_count: StrictInt = Field(ge=0)
    wins: StrictInt = Field(ge=0)
    losses: StrictInt = Field(ge=0)
    breakeven: StrictInt = Field(ge=0)
    win_rate: Decimal | None = Field(default=None, ge=ZERO, le=Decimal(1))
    cumulative_r: Decimal
    expectancy_r: Decimal | None = None
    max_drawdown_r: Decimal = Field(ge=ZERO)
    profit_factor: Decimal | None = Field(default=None, ge=ZERO)

    mae_count: StrictInt = Field(ge=0)
    average_mae_r: Decimal | None = Field(default=None, ge=ZERO)
    median_mae_r: Decimal | None = Field(default=None, ge=ZERO)

    mfe_count: StrictInt = Field(ge=0)
    average_mfe_r: Decimal | None = Field(default=None, ge=ZERO)
    median_mfe_r: Decimal | None = Field(default=None, ge=ZERO)

    @model_validator(mode="after")
    def counts_are_coherent(self) -> "PerformanceMetrics":
        if self.wins + self.losses + self.breakeven != self.trade_count:
            raise ValueError("win/loss/breakeven counts must equal trade count")
        if self.mae_count > self.trade_count or self.mfe_count > self.trade_count:
            raise ValueError("MAE/MFE coverage cannot exceed trade count")
        return self


class SetupPerformance(ReportModel):
    trigger_type: SwingTriggerType
    metrics: PerformanceMetrics


class RegimePerformance(ReportModel):
    regime: Regime
    metrics: PerformanceMetrics


class SetupRegimePerformance(ReportModel):
    trigger_type: SwingTriggerType
    regime: Regime
    metrics: PerformanceMetrics


class PerformanceBreakdown(ReportModel):
    overall: PerformanceMetrics
    by_setup: tuple[SetupPerformance, ...]
    by_regime: tuple[RegimePerformance, ...]
    by_setup_regime: tuple[SetupRegimePerformance, ...]


class WalkForwardWindowReport(ReportModel):
    window: WalkForwardWindow
    breakdown: PerformanceBreakdown


class BacktestPerformanceReport(ReportModel):
    overall: PerformanceBreakdown
    windows: tuple[WalkForwardWindowReport, ...]
    outcome_count: StrictInt = Field(ge=0)
    outcome_digests: tuple[str, ...]

    @property
    def digest(self) -> str:
        return sha256(self.model_dump_json().encode()).hexdigest()


def _quantize(value: Decimal) -> Decimal:
    with localcontext(METRIC_CONTEXT):
        return value.quantize(METRIC_QUANTUM)


def _divide(numerator: Decimal, denominator: int | Decimal) -> Decimal:
    with localcontext(METRIC_CONTEXT):
        value = numerator / Decimal(denominator)
        return value.quantize(METRIC_QUANTUM)


def _median(values: tuple[Decimal, ...]) -> Decimal | None:
    if not values:
        return None
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return _quantize(ordered[middle])
    return _divide(ordered[middle - 1] + ordered[middle], 2)


def _ordered_outcomes(
    outcomes: tuple[BacktestTradeOutcome, ...],
) -> tuple[BacktestTradeOutcome, ...]:
    digests = [outcome.request_digest for outcome in outcomes]
    if len(set(digests)) != len(digests):
        raise BacktestReportingUnavailable("DUPLICATE_OUTCOME_IDENTITY")

    for outcome in outcomes:
        _require_utc(outcome.decision_at)
        if outcome.trade_result.run_type is not RunType.REPLAY:
            raise BacktestReportingUnavailable("NON_REPLAY_OUTCOME")
        if outcome.trade_result.r_multiple != outcome.r_exact.decimal:
            raise BacktestReportingUnavailable("R_MULTIPLE_MISMATCH")
        if outcome.trade_result.realized_pnl != outcome.net_pnl:
            raise BacktestReportingUnavailable("REALIZED_PNL_MISMATCH")
        if outcome.trade_result.mae_r != (
            outcome.mae_exact.decimal if outcome.mae_exact is not None else None
        ):
            raise BacktestReportingUnavailable("MAE_MISMATCH")
        if outcome.trade_result.mfe_r != (
            outcome.mfe_exact.decimal if outcome.mfe_exact is not None else None
        ):
            raise BacktestReportingUnavailable("MFE_MISMATCH")

    return tuple(
        sorted(
            outcomes,
            key=lambda outcome: (outcome.decision_at, outcome.request_digest),
        )
    )


def _metrics(
    outcomes: tuple[BacktestTradeOutcome, ...],
) -> PerformanceMetrics:
    ordered = _ordered_outcomes(outcomes)
    r_values = tuple(outcome.trade_result.r_multiple for outcome in ordered)
    cumulative_r = sum(r_values, ZERO)
    trade_count = len(ordered)
    wins = sum(value > ZERO for value in r_values)
    losses = sum(value < ZERO for value in r_values)
    breakeven = trade_count - wins - losses

    win_rate = _divide(Decimal(wins), trade_count) if trade_count else None
    expectancy = _divide(cumulative_r, trade_count) if trade_count else None

    running = ZERO
    peak = ZERO
    max_drawdown = ZERO
    for value in r_values:
        running += value
        peak = max(peak, running)
        max_drawdown = max(max_drawdown, peak - running)

    positive_r = sum((value for value in r_values if value > ZERO), ZERO)
    negative_r = sum((value for value in r_values if value < ZERO), ZERO)
    profit_factor = _divide(positive_r, abs(negative_r)) if negative_r < ZERO else None

    mae_values = tuple(
        outcome.trade_result.mae_r
        for outcome in ordered
        if outcome.trade_result.mae_r is not None
    )
    mfe_values = tuple(
        outcome.trade_result.mfe_r
        for outcome in ordered
        if outcome.trade_result.mfe_r is not None
    )

    return PerformanceMetrics(
        trade_count=trade_count,
        wins=wins,
        losses=losses,
        breakeven=breakeven,
        win_rate=win_rate,
        cumulative_r=cumulative_r,
        expectancy_r=expectancy,
        max_drawdown_r=max_drawdown,
        profit_factor=profit_factor,
        mae_count=len(mae_values),
        average_mae_r=(
            _divide(sum(mae_values, ZERO), len(mae_values)) if mae_values else None
        ),
        median_mae_r=_median(mae_values),
        mfe_count=len(mfe_values),
        average_mfe_r=(
            _divide(sum(mfe_values, ZERO), len(mfe_values)) if mfe_values else None
        ),
        median_mfe_r=_median(mfe_values),
    )


def _breakdown(
    outcomes: tuple[BacktestTradeOutcome, ...],
) -> PerformanceBreakdown:
    ordered = _ordered_outcomes(outcomes)

    setup_types = sorted({outcome.trigger_type for outcome in ordered}, key=str)
    regimes = sorted({outcome.regime for outcome in ordered}, key=int)
    setup_regimes = sorted(
        {(outcome.trigger_type, outcome.regime) for outcome in ordered},
        key=lambda pair: (str(pair[0]), int(pair[1])),
    )

    return PerformanceBreakdown(
        overall=_metrics(ordered),
        by_setup=tuple(
            SetupPerformance(
                trigger_type=trigger_type,
                metrics=_metrics(
                    tuple(
                        outcome
                        for outcome in ordered
                        if outcome.trigger_type is trigger_type
                    )
                ),
            )
            for trigger_type in setup_types
        ),
        by_regime=tuple(
            RegimePerformance(
                regime=regime,
                metrics=_metrics(
                    tuple(outcome for outcome in ordered if outcome.regime is regime)
                ),
            )
            for regime in regimes
        ),
        by_setup_regime=tuple(
            SetupRegimePerformance(
                trigger_type=trigger_type,
                regime=regime,
                metrics=_metrics(
                    tuple(
                        outcome
                        for outcome in ordered
                        if outcome.trigger_type is trigger_type
                        and outcome.regime is regime
                    )
                ),
            )
            for trigger_type, regime in setup_regimes
        ),
    )


def build_backtest_performance_report(
    outcomes: tuple[BacktestTradeOutcome, ...],
    *,
    walk_forward: WalkForwardPlan | None = None,
) -> BacktestPerformanceReport:
    """Build deterministic REPLAY-only backtest metrics and walk-forward reports."""

    ordered = _ordered_outcomes(outcomes)
    window_reports: tuple[WalkForwardWindowReport, ...] = ()

    if walk_forward is not None:
        assignments: dict[str, list[BacktestTradeOutcome]] = {
            window.label: [] for window in walk_forward.windows
        }
        for outcome in ordered:
            matching = tuple(
                window
                for window in walk_forward.windows
                if window.contains(outcome.decision_at)
            )
            if len(matching) != 1:
                raise BacktestReportingUnavailable(
                    f"OUTCOME_WINDOW_ASSIGNMENT:{outcome.request_digest}"
                )
            assignments[matching[0].label].append(outcome)

        window_reports = tuple(
            WalkForwardWindowReport(
                window=window,
                breakdown=_breakdown(tuple(assignments[window.label])),
            )
            for window in walk_forward.windows
        )

    return BacktestPerformanceReport(
        overall=_breakdown(ordered),
        windows=window_reports,
        outcome_count=len(ordered),
        outcome_digests=tuple(outcome.request_digest for outcome in ordered),
    )
