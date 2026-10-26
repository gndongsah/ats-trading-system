"""Run-type-safe outcome selection and summary analytics."""

from dataclasses import dataclass
from decimal import Decimal

from src.domain.models import TradeResult
from src.run_type import RunType, is_live_validation_run_type


@dataclass(frozen=True)
class OutcomeScope:
    """Explicit set of run types allowed into an analytics query."""

    run_types: frozenset[RunType]

    def __post_init__(self) -> None:
        if not self.run_types:
            raise ValueError("outcome scope requires at least one run type")

    @classmethod
    def live_only(cls) -> "OutcomeScope":
        return cls(frozenset({RunType.LIVE}))

    @classmethod
    def explicit(cls, *run_types: RunType) -> "OutcomeScope":
        if not run_types:
            raise ValueError("explicit outcome scope requires at least one run type")
        return cls(frozenset(run_types))


@dataclass(frozen=True)
class OutcomeSummary:
    """Minimal deterministic result summary for the selected evidence domain."""

    count: int
    total_r: Decimal
    expectancy_r: Decimal | None
    wins: int
    losses: int
    breakeven: int
    run_types: frozenset[RunType]


def eligible_for_live_validation(result: TradeResult) -> bool:
    """Only LIVE outcomes may contribute to live validation/promotion evidence."""

    return is_live_validation_run_type(result.run_type)


def live_validation_results(
    results: tuple[TradeResult, ...],
) -> tuple[TradeResult, ...]:
    """Return only outcomes eligible for live validation evidence."""

    return tuple(result for result in results if eligible_for_live_validation(result))


def summarize_outcomes(
    results: tuple[TradeResult, ...],
    *,
    scope: OutcomeScope | None = None,
) -> OutcomeSummary:
    """Summarize results without silently mixing evidence domains.

    Omitting scope is intentionally LIVE-only. PAPER and REPLAY outcomes are
    included only when the caller supplies an explicit scope containing them.
    """

    selected_scope = scope or OutcomeScope.live_only()
    selected = tuple(
        result for result in results if result.run_type in selected_scope.run_types
    )
    total_r = sum((result.r_multiple for result in selected), Decimal(0))
    count = len(selected)
    expectancy = total_r / Decimal(count) if count else None
    wins = sum(result.r_multiple > 0 for result in selected)
    losses = sum(result.r_multiple < 0 for result in selected)
    breakeven = count - wins - losses
    return OutcomeSummary(
        count=count,
        total_r=total_r,
        expectancy_r=expectancy,
        wins=wins,
        losses=losses,
        breakeven=breakeven,
        run_types=selected_scope.run_types,
    )
