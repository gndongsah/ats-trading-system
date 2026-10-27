"""Deterministic AI-free historical backtest harness."""

from datetime import datetime
from decimal import (
    ROUND_HALF_EVEN,
    Context,
    Decimal,
    localcontext,
)
from enum import StrEnum
from fractions import Fraction
from hashlib import sha256
from uuid import NAMESPACE_URL, UUID, uuid5

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictInt,
    field_validator,
    model_validator,
)

from src.domain.models import TradeResult, TradeSide, _require_utc
from src.historical_replay import HistoricalReplayContext
from src.risk import SizingDecision, size_long_equity_position
from src.run_type import RunType
from src.swing_triggers import (
    SwingTriggerActionability,
    SwingTriggerResult,
)

ZERO = Decimal(0)
TEN_THOUSAND = Fraction(10_000)
R_DECIMAL_PLACES = 12
R_QUANTUM = Decimal(1).scaleb(-R_DECIMAL_PLACES)
R_CONTEXT = Context(prec=80, rounding=ROUND_HALF_EVEN)


class BacktestModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class BacktestUnavailable(ValueError):
    """Backtest cannot proceed safely with the supplied deterministic inputs."""


class BacktestCostModel(BacktestModel):
    """Explicit deterministic round-trip cost assumptions."""

    entry_slippage_bps: Decimal = Field(ge=ZERO, allow_inf_nan=False)
    exit_slippage_bps: Decimal = Field(ge=ZERO, allow_inf_nan=False)
    fee_per_share_per_side: Decimal = Field(ge=ZERO, allow_inf_nan=False)
    fixed_fee_per_side: Decimal = Field(ge=ZERO, allow_inf_nan=False)


class BacktestSizingInputs(BacktestModel):
    """Accepted risk-layer inputs used to size one long-equity backtest trade."""

    equity: Decimal = Field(gt=ZERO, allow_inf_nan=False)
    risk_fraction: Decimal = Field(gt=ZERO, le=Decimal(1), allow_inf_nan=False)
    available_buying_power: Decimal = Field(ge=ZERO, allow_inf_nan=False)
    explicit_regime_multiplier: Decimal | None = Field(
        default=None,
        gt=ZERO,
        allow_inf_nan=False,
    )


class HistoricalExitObservation(BacktestModel):
    """Post-entry historical observation used only to score the outcome."""

    source: str = Field(min_length=1, max_length=128)
    exit_at: datetime
    exit_price: Decimal = Field(gt=ZERO, allow_inf_nan=False)
    period_low: Decimal | None = Field(default=None, gt=ZERO, allow_inf_nan=False)
    period_high: Decimal | None = Field(default=None, gt=ZERO, allow_inf_nan=False)

    @field_validator("source")
    @classmethod
    def source_is_trimmed(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("exit observation source must not be blank")
        return normalized

    @field_validator("exit_at")
    @classmethod
    def exit_time_is_utc(cls, value: datetime) -> datetime:
        return _require_utc(value)

    @model_validator(mode="after")
    def excursion_range_is_coherent(self) -> "HistoricalExitObservation":
        if (self.period_low is None) != (self.period_high is None):
            raise ValueError("period low and high must be supplied together")
        if self.period_low is not None and self.period_high is not None:
            if self.period_low > self.period_high:
                raise ValueError("period low cannot exceed period high")
            if not self.period_low <= self.exit_price <= self.period_high:
                raise ValueError("exit price must lie within the excursion range")
        return self


class ExactRatio(BacktestModel):
    numerator: int
    denominator: StrictInt = Field(gt=0)
    decimal: Decimal

    @classmethod
    def from_fraction(cls, value: Fraction) -> "ExactRatio":
        return cls(
            numerator=value.numerator,
            denominator=value.denominator,
            decimal=_ratio_decimal(value),
        )


class BacktestSizingSnapshot(BacktestModel):
    decision: str
    reason: str
    quantity: StrictInt = Field(gt=0)
    adjusted_risk_budget: Decimal = Field(gt=ZERO, allow_inf_nan=False)
    effective_risk_fraction: Decimal = Field(gt=ZERO, allow_inf_nan=False)
    actual_stop_risk_exposure: Decimal = Field(gt=ZERO, allow_inf_nan=False)
    entry_notional: Decimal = Field(gt=ZERO, allow_inf_nan=False)


class BacktestTradeOutcome(BacktestModel):
    symbol: str = Field(pattern=r"^[A-Z][A-Z0-9.-]{0,31}$")
    context_digest: str = Field(min_length=64, max_length=64)
    request_digest: str = Field(min_length=64, max_length=64)
    sizing: BacktestSizingSnapshot
    planned_entry: Decimal = Field(gt=ZERO, allow_inf_nan=False)
    planned_invalidation: Decimal = Field(gt=ZERO, allow_inf_nan=False)
    entry_fill: Decimal = Field(gt=ZERO, allow_inf_nan=False)
    exit_fill: Decimal = Field(gt=ZERO, allow_inf_nan=False)
    gross_pnl: Decimal
    total_fees: Decimal = Field(ge=ZERO, allow_inf_nan=False)
    net_pnl: Decimal
    initial_risk: Decimal = Field(gt=ZERO, allow_inf_nan=False)
    r_exact: ExactRatio
    mae_exact: ExactRatio | None = None
    mfe_exact: ExactRatio | None = None
    trade_result: TradeResult

    @property
    def digest(self) -> str:
        return sha256(self.model_dump_json().encode()).hexdigest()


class BacktestOutcomeLabel(StrEnum):
    WIN = "win"
    LOSS = "loss"
    FLAT = "flat"


def _exact_decimal(value: Fraction) -> Decimal:
    """Convert a terminating Fraction to Decimal without ambient context."""

    denominator = value.denominator
    twos = fives = 0
    while denominator % 2 == 0:
        denominator //= 2
        twos += 1
    while denominator % 5 == 0:
        denominator //= 5
        fives += 1
    if denominator != 1:
        raise ValueError("exact value is not representable as a finite Decimal")

    scale = max(twos, fives)
    scaled = value.numerator * (2 ** (scale - twos)) * (5 ** (scale - fives))
    digits = tuple(int(digit) for digit in str(abs(scaled)))
    return Decimal((int(scaled < 0), digits, -scale))


def _ratio_decimal(value: Fraction) -> Decimal:
    """Render a ratio deterministically while preserving its exact fraction."""

    with localcontext(R_CONTEXT):
        raw = Decimal(value.numerator) / Decimal(value.denominator)
        return raw.quantize(R_QUANTUM)


def _slipped_price(
    reference: Decimal,
    *,
    bps: Decimal,
    adverse_direction: int,
) -> Decimal:
    factor = TEN_THOUSAND + Fraction(adverse_direction) * Fraction(bps)
    return _exact_decimal(Fraction(reference) * factor / TEN_THOUSAND)


def _trade_identity(
    *,
    symbol: str,
    context: HistoricalReplayContext,
    trigger: SwingTriggerResult,
    sizing: BacktestSizingInputs,
    costs: BacktestCostModel,
    exit_observation: HistoricalExitObservation,
) -> tuple[str, UUID, UUID]:
    payload = (
        f"{symbol}|{context.digest}|"
        f"{trigger.model_dump_json()}|{sizing.model_dump_json()}|"
        f"{costs.model_dump_json()}|{exit_observation.model_dump_json()}"
    )
    digest = sha256(payload.encode()).hexdigest()
    plan_id = uuid5(NAMESPACE_URL, f"ats-backtest-plan:{digest}")
    result_id = uuid5(NAMESPACE_URL, f"ats-backtest-result:{digest}")
    return digest, plan_id, result_id


def _require_symbol_in_context(
    symbol: str,
    context: HistoricalReplayContext,
) -> None:
    if not any(
        observation.event.symbol == symbol
        for observation in context.market.observations
    ):
        raise BacktestUnavailable(f"SYMBOL_NOT_IN_REPLAY_CONTEXT:{symbol}")


def _outcome_label(net_pnl: Decimal) -> BacktestOutcomeLabel:
    if net_pnl > ZERO:
        return BacktestOutcomeLabel.WIN
    if net_pnl < ZERO:
        return BacktestOutcomeLabel.LOSS
    return BacktestOutcomeLabel.FLAT


class DeterministicBacktestHarness:
    """Run one historical long-equity trade without AI or broker execution."""

    def run(
        self,
        *,
        symbol: str,
        context: HistoricalReplayContext,
        trigger: SwingTriggerResult,
        sizing: BacktestSizingInputs,
        costs: BacktestCostModel,
        exit_observation: HistoricalExitObservation,
    ) -> BacktestTradeOutcome:
        symbol = symbol.strip().upper()
        if not symbol:
            raise ValueError("symbol must not be blank")
        if context.as_of != trigger.as_of:
            raise BacktestUnavailable("CONTEXT_TRIGGER_TIMESTAMP_MISMATCH")
        if exit_observation.exit_at <= context.as_of:
            raise BacktestUnavailable("EXIT_MUST_FOLLOW_DECISION_TIME")
        _require_symbol_in_context(symbol, context)

        if trigger.actionability is not SwingTriggerActionability.ACTIONABLE:
            raise BacktestUnavailable("TRIGGER_NOT_ACTIONABLE")
        if trigger.side is not TradeSide.LONG:
            raise BacktestUnavailable("UNSUPPORTED_SIDE:SHORT")

        regime = trigger.setup_score.regime.regime
        try:
            sizing_result = size_long_equity_position(
                equity=sizing.equity,
                risk_fraction=sizing.risk_fraction,
                entry=trigger.trigger_level,
                stop=trigger.invalidation_level,
                available_buying_power=sizing.available_buying_power,
                regime=regime,
                side=trigger.side,
                instrument_type="equity",
                leverage=Decimal(1),
                fractional_shares=False,
                special_setup=False,
                explicit_regime_multiplier=sizing.explicit_regime_multiplier,
            )
        except (TypeError, ValueError) as error:
            raise BacktestUnavailable(f"SIZING_INPUT:{error}") from error
        if (
            sizing_result.decision is SizingDecision.DENY
            or sizing_result.quantity <= 0
        ):
            raise BacktestUnavailable(f"SIZING:{sizing_result.reason.value}")

        quantity = sizing_result.quantity
        entry_fill = _slipped_price(
            trigger.trigger_level,
            bps=costs.entry_slippage_bps,
            adverse_direction=1,
        )
        exit_fill = _slipped_price(
            exit_observation.exit_price,
            bps=costs.exit_slippage_bps,
            adverse_direction=-1,
        )

        gross_fraction = (Fraction(exit_fill) - Fraction(entry_fill)) * quantity
        fee_fraction = Fraction(2) * (
            Fraction(costs.fee_per_share_per_side) * quantity
            + Fraction(costs.fixed_fee_per_side)
        )
        net_fraction = gross_fraction - fee_fraction
        initial_risk_fraction = (
            Fraction(trigger.trigger_level) - Fraction(trigger.invalidation_level)
        ) * quantity

        gross_pnl = _exact_decimal(gross_fraction)
        total_fees = _exact_decimal(fee_fraction)
        net_pnl = _exact_decimal(net_fraction)
        initial_risk = _exact_decimal(initial_risk_fraction)
        r_ratio = ExactRatio.from_fraction(net_fraction / initial_risk_fraction)

        mae_ratio = None
        mfe_ratio = None
        excursion_source = None
        if (
            exit_observation.period_low is not None
            and exit_observation.period_high is not None
        ):
            adverse = max(
                Fraction(0),
                (Fraction(entry_fill) - Fraction(exit_observation.period_low))
                * quantity,
            )
            favorable = max(
                Fraction(0),
                (Fraction(exit_observation.period_high) - Fraction(entry_fill))
                * quantity,
            )
            mae_ratio = ExactRatio.from_fraction(adverse / initial_risk_fraction)
            mfe_ratio = ExactRatio.from_fraction(favorable / initial_risk_fraction)
            excursion_source = exit_observation.source

        request_digest, plan_id, result_id = _trade_identity(
            symbol=symbol,
            context=context,
            trigger=trigger,
            sizing=sizing,
            costs=costs,
            exit_observation=exit_observation,
        )
        trade_result = TradeResult(
            id=result_id,
            plan_id=plan_id,
            run_type=RunType.REPLAY,
            closed_at=exit_observation.exit_at,
            realized_pnl=net_pnl,
            r_multiple=r_ratio.decimal,
            outcome=_outcome_label(net_pnl).value,
            mae_r=mae_ratio.decimal if mae_ratio is not None else None,
            mfe_r=mfe_ratio.decimal if mfe_ratio is not None else None,
            excursion_source=excursion_source,
        )

        assert sizing_result.adjusted_risk_budget is not None
        assert sizing_result.effective_risk_fraction is not None
        assert sizing_result.actual_stop_risk_exposure is not None
        assert sizing_result.entry_notional is not None

        return BacktestTradeOutcome(
            symbol=symbol,
            context_digest=context.digest,
            request_digest=request_digest,
            sizing=BacktestSizingSnapshot(
                decision=sizing_result.decision.value,
                reason=sizing_result.reason.value,
                quantity=quantity,
                adjusted_risk_budget=sizing_result.adjusted_risk_budget,
                effective_risk_fraction=sizing_result.effective_risk_fraction,
                actual_stop_risk_exposure=sizing_result.actual_stop_risk_exposure,
                entry_notional=sizing_result.entry_notional,
            ),
            planned_entry=trigger.trigger_level,
            planned_invalidation=trigger.invalidation_level,
            entry_fill=entry_fill,
            exit_fill=exit_fill,
            gross_pnl=gross_pnl,
            total_fees=total_fees,
            net_pnl=net_pnl,
            initial_risk=initial_risk,
            r_exact=r_ratio,
            mae_exact=mae_ratio,
            mfe_exact=mfe_ratio,
            trade_result=trade_result,
        )
