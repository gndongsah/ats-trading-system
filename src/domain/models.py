from datetime import UTC, datetime, timedelta
from decimal import Decimal
from enum import Enum, IntEnum
from itertools import pairwise
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class ImmutableModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class TradingStyle(str, Enum):
    SCALP = "scalp"
    DAY = "day"
    SWING = "swing"
    POSITION = "position"


class TradeSide(str, Enum):
    LONG = "long"
    SHORT = "short"


class SetupType(str, Enum):
    BREAKOUT = "breakout"
    PULLBACK = "pullback"
    REVERSAL = "reversal"
    CONTINUATION = "continuation"
    MEAN_REVERSION = "mean_reversion"
    CATALYST = "catalyst"


class ValidationStage(str, Enum):
    SCREENING = "screening"
    RESEARCH = "research"
    SCORED = "scored"
    PLANNED = "planned"
    APPROVED = "approved"
    EXECUTED = "executed"
    CLOSED = "closed"


class Regime(IntEnum):
    RISK_ON = 1
    RISK_ON_NARROW = 2
    NEUTRAL_CHOP = 3
    RISK_OFF = 4
    CAPITULATION_REVERSAL = 5

    def risk_multiplier(
        self,
        *,
        side: "TradeSide | None" = None,
        explicit_multiplier: Decimal | None = None,
        special_setup: bool = False,
    ) -> Decimal | None:
        return resolve_regime_risk_multiplier(
            self,
            side=side,
            explicit_multiplier=explicit_multiplier,
            special_setup=special_setup,
        )


class Mood(str, Enum):
    CALM = "calm"
    CONFIDENT = "confident"
    CAUTIOUS = "cautious"
    FEARFUL = "fearful"
    EUPHORIC = "euphoric"


class EvidenceStatus(str, Enum):
    UNVERIFIED = "unverified"
    VERIFIED = "verified"
    REJECTED = "rejected"


def resolve_regime_risk_multiplier(
    regime: Regime,
    *,
    side: "TradeSide | None" = None,
    explicit_multiplier: Decimal | None = None,
    special_setup: bool = False,
) -> Decimal | None:
    """Return the playbook-defined risk multiplier for a regime.

    Regimes 1-3 have fixed multipliers. Regime 4 requires an explicit long-side
    policy decision within the allowed 0..0.25 range. Regime 5 is treated as a
    special-case regime that requires a separately authorized setup or no trade.
    """
    if regime == Regime.RISK_ON:
        return Decimal("1.00")
    if regime == Regime.RISK_ON_NARROW:
        return Decimal("0.75")
    if regime == Regime.NEUTRAL_CHOP:
        return Decimal("0.50")
    if regime == Regime.RISK_OFF:
        if side is not TradeSide.LONG:
            raise ValueError(
                "risk-off regime policy applies only to the long side and must be chosen explicitly"
            )
        if explicit_multiplier is None:
            raise ValueError(
                "risk-off regime requires an explicit long-side multiplier within 0..0.25"
            )
        if isinstance(explicit_multiplier, bool):
            raise ValueError(
                "risk-off multiplier must be a numeric value, not a boolean"
            )
        explicit = Decimal(explicit_multiplier)
        if not explicit.is_finite() or not Decimal(0) <= explicit <= Decimal("0.25"):
            raise ValueError("risk-off long-side multiplier must be between 0 and 0.25")
        return explicit
    if regime == Regime.CAPITULATION_REVERSAL:
        if not special_setup:
            raise ValueError(
                "capitulation/reversal requires a separately authorized special setup or no trade"
            )
        if explicit_multiplier is None:
            return None
        if isinstance(explicit_multiplier, bool):
            raise ValueError("special-setup multiplier must be numeric, not a boolean")
        explicit = Decimal(explicit_multiplier)
        if not explicit.is_finite() or explicit < 0:
            raise ValueError(
                "special-setup multiplier must be a finite nonnegative value"
            )
        return explicit
    raise ValueError(f"unsupported regime: {regime}")


class OrderAction(str, Enum):
    BUY = "buy"
    SELL = "sell"


class OrderType(str, Enum):
    MARKET = "market"
    LIMIT = "limit"
    STOP = "stop"
    STOP_LIMIT = "stop_limit"


def _require_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError("timestamp must be timezone-aware and in UTC")
    return value.astimezone(UTC)


class SecurityRef(ImmutableModel):
    id: UUID = Field(default_factory=uuid4)
    ticker: str = Field(min_length=1, max_length=32)
    asset_class: str = Field(default="equity", min_length=1)
    exchange: str | None = None
    sector: str | None = None
    industry: str | None = None

    @field_validator("ticker")
    @classmethod
    def normalize_ticker(cls, value: str) -> str:
        normalized = value.strip().upper()
        if not normalized:
            raise ValueError("ticker must not be blank")
        return normalized


class ThemeExposure(ImmutableModel):
    theme: str = Field(min_length=1)
    exposure: Decimal = Field(ge=Decimal(-1), le=Decimal(1))


class FactorExposure(ImmutableModel):
    factor: str = Field(min_length=1)
    exposure: Decimal = Field(ge=Decimal(-1), le=Decimal(1))


class EvidenceRef(ImmutableModel):
    id: UUID = Field(default_factory=uuid4)
    source: str = Field(min_length=1)
    source_url: str | None = None
    published_at: datetime | None = None
    observed_at: datetime
    metric: str | None = None
    excerpt: str | None = None
    confidence: Decimal = Field(ge=Decimal(0), le=Decimal(1))
    verification_status: EvidenceStatus = EvidenceStatus.UNVERIFIED

    @field_validator("published_at", "observed_at")
    @classmethod
    def timestamps_are_utc(cls, value: datetime | None) -> datetime | None:
        return _require_utc(value) if value is not None else None


class ScoreContribution(ImmutableModel):
    factor: str = Field(min_length=1)
    points: Decimal = Field(ge=Decimal(-100), le=Decimal(100))
    evidence: tuple[EvidenceRef, ...] = ()

    @model_validator(mode="after")
    def positive_points_require_evidence(self) -> "ScoreContribution":
        if self.points > 0 and not self.evidence:
            raise ValueError("positive score contributions require evidence")
        return self


class ScoreSnapshot(ImmutableModel):
    id: UUID = Field(default_factory=uuid4)
    decided_at: datetime
    market_opportunity: int = Field(ge=0, le=100)
    discovery: int = Field(ge=0, le=100)
    setup: int = Field(ge=0, le=100)
    regime_score: int = Field(ge=1, le=5)
    regime: Regime | None = None
    mood: Mood | None = None
    inflection_stage: int = Field(ge=1, le=9)
    contributions: tuple[ScoreContribution, ...] = ()

    @field_validator("decided_at")
    @classmethod
    def decision_time_is_utc(cls, value: datetime) -> datetime:
        return _require_utc(value)

    @model_validator(mode="after")
    def evidence_was_available_at_decision(self) -> "ScoreSnapshot":
        for contribution in self.contributions:
            for evidence in contribution.evidence:
                if evidence.observed_at > self.decided_at:
                    raise ValueError("evidence cannot be observed after the decision")
                if (
                    evidence.published_at is not None
                    and evidence.published_at > self.decided_at
                ):
                    raise ValueError("evidence cannot be published after the decision")
        return self


class RiskIntent(ImmutableModel):
    id: UUID = Field(default_factory=uuid4)
    stop_risk_budget: Decimal = Field(gt=0)
    portfolio_risk_fraction: Decimal | None = Field(
        default=None, ge=Decimal(0), le=Decimal(1)
    )
    rationale: str | None = None


class TradePlan(ImmutableModel):
    id: UUID = Field(default_factory=uuid4)
    trade_id: UUID = Field(default_factory=uuid4)
    version: int = Field(default=1, ge=1)
    supersedes_plan_id: UUID | None = None
    security: SecurityRef
    side: TradeSide
    trading_style: TradingStyle
    setup_type: SetupType
    entry: Decimal = Field(gt=0)
    stop: Decimal = Field(gt=0)
    target: Decimal = Field(gt=0)
    quantity: Decimal = Field(gt=0)
    planned_at: datetime
    score_snapshot: ScoreSnapshot
    risk_intent: RiskIntent
    theme_exposures: tuple[ThemeExposure, ...] = ()
    factor_exposures: tuple[FactorExposure, ...] = ()

    @field_validator("planned_at")
    @classmethod
    def planning_time_is_utc(cls, value: datetime) -> datetime:
        return _require_utc(value)

    @model_validator(mode="after")
    def validate_plan(self) -> "TradePlan":
        if self.version == 1 and self.supersedes_plan_id is not None:
            raise ValueError("the initial plan cannot supersede another plan")
        if self.version > 1 and self.supersedes_plan_id is None:
            raise ValueError("amended plans must identify the plan they supersede")
        if self.supersedes_plan_id == self.id:
            raise ValueError("a plan cannot supersede itself")
        if self.score_snapshot.decided_at > self.planned_at:
            raise ValueError("plan cannot predate its score snapshot")

        if self.side is TradeSide.LONG:
            if not self.stop < self.entry < self.target:
                raise ValueError("long plan requires stop < entry < target")
        elif not self.target < self.entry < self.stop:
            raise ValueError("short plan requires target < entry < stop")

        stop_risk = abs(self.entry - self.stop) * self.quantity
        if stop_risk > self.risk_intent.stop_risk_budget:
            raise ValueError("trade quantity exceeds the declared stop-risk budget")
        return self

    def validate_amendment_of(self, prior_plan: "TradePlan") -> None:
        if self.trade_id != prior_plan.trade_id:
            raise ValueError("amended plan must retain the prior plan's trade_id")
        if self.version != prior_plan.version + 1:
            raise ValueError(
                "amended plan version must immediately follow the prior plan"
            )
        if self.supersedes_plan_id != prior_plan.id:
            raise ValueError("amended plan must supersede the supplied prior plan")
        if self.planned_at < prior_plan.planned_at:
            raise ValueError("amended plan cannot predate the prior plan")


class OrderIntent(ImmutableModel):
    id: UUID = Field(default_factory=uuid4)
    plan_id: UUID
    action: OrderAction
    order_type: OrderType
    quantity: Decimal = Field(gt=0)
    limit_price: Decimal | None = Field(default=None, gt=0)
    stop_price: Decimal | None = Field(default=None, gt=0)
    created_at: datetime

    @field_validator("created_at")
    @classmethod
    def creation_time_is_utc(cls, value: datetime) -> datetime:
        return _require_utc(value)


class Fill(ImmutableModel):
    id: UUID = Field(default_factory=uuid4)
    order_intent_id: UUID
    filled_at: datetime
    quantity: Decimal = Field(gt=0)
    price: Decimal = Field(gt=0)
    fees: Decimal = Field(default=Decimal(0), ge=0)

    @field_validator("filled_at")
    @classmethod
    def fill_time_is_utc(cls, value: datetime) -> datetime:
        return _require_utc(value)


class PositionSnapshot(ImmutableModel):
    id: UUID = Field(default_factory=uuid4)
    plan_id: UUID
    as_of: datetime
    side: TradeSide
    quantity: Decimal = Field(ge=0)
    average_entry: Decimal = Field(gt=0)
    unrealized_pnl: Decimal

    @field_validator("as_of")
    @classmethod
    def snapshot_time_is_utc(cls, value: datetime) -> datetime:
        return _require_utc(value)


class TradeResult(ImmutableModel):
    id: UUID = Field(default_factory=uuid4)
    plan_id: UUID
    closed_at: datetime
    realized_pnl: Decimal
    r_multiple: Decimal
    outcome: str = Field(min_length=1)
    mae_r: Decimal | None = Field(default=None, ge=0)
    mfe_r: Decimal | None = Field(default=None, ge=0)
    excursion_source: str | None = None

    @field_validator("closed_at")
    @classmethod
    def close_time_is_utc(cls, value: datetime) -> datetime:
        return _require_utc(value)

    @field_validator("excursion_source")
    @classmethod
    def excursion_source_is_not_blank(cls, value: str | None) -> str | None:
        if value is None:
            return None
        source = value.strip()
        if not source:
            raise ValueError("excursion source must not be blank")
        return source

    @model_validator(mode="after")
    def excursions_retain_their_source(self) -> "TradeResult":
        if (self.mae_r is not None or self.mfe_r is not None) and (
            self.excursion_source is None
        ):
            raise ValueError("MAE/MFE observations require an excursion source")
        return self


class JournalEntry(ImmutableModel):
    id: UUID = Field(default_factory=uuid4)
    plan: TradePlan
    orders: tuple[OrderIntent, ...] = ()
    fills: tuple[Fill, ...] = ()
    position_snapshots: tuple[PositionSnapshot, ...] = ()
    result: TradeResult | None = None
    prior_plans: tuple[TradePlan, ...] = ()
    plan_followed: bool | None = None
    mood: Mood | None = None
    lesson: str | None = None
    thesis: str | None = None
    created_at: datetime

    @field_validator("created_at")
    @classmethod
    def journal_time_is_utc(cls, value: datetime) -> datetime:
        return _require_utc(value)

    @model_validator(mode="after")
    def validate_lifecycle_links(self) -> "JournalEntry":
        plans_by_id = {plan.id: plan for plan in (*self.prior_plans, self.plan)}
        if len(plans_by_id) != len(self.prior_plans) + 1:
            raise ValueError("journal plan history cannot repeat plan IDs")
        plans_by_version = {plan.version: plan for plan in plans_by_id.values()}
        if len(plans_by_version) != len(plans_by_id):
            raise ValueError("journal plan history cannot repeat plan versions")
        if any(plan.trade_id != self.plan.trade_id for plan in self.prior_plans):
            raise ValueError("journal plan history must retain the same trade_id")
        ordered_plans = sorted(plans_by_id.values(), key=lambda plan: plan.version)
        for prior_plan, amended_plan in pairwise(ordered_plans):
            amended_plan.validate_amendment_of(prior_plan)

        orders_by_id = {order.id: order for order in self.orders}
        if any(order.plan_id != self.plan.id for order in self.orders):
            raise ValueError("journal orders must reference the journal plan")
        for order in self.orders:
            if order.created_at < self.plan.planned_at:
                raise ValueError("journal orders cannot predate the plan")
        fill_totals: dict[UUID, Decimal] = {}
        for fill in self.fills:
            order = orders_by_id.get(fill.order_intent_id)
            if order is None:
                raise ValueError("journal fills must reference a journal order")
            if fill.filled_at < order.created_at:
                raise ValueError("journal fills cannot predate their order")
            fill_totals[order.id] = (
                fill_totals.get(order.id, Decimal(0)) + fill.quantity
            )
        if any(
            filled_quantity > orders_by_id[order_id].quantity
            for order_id, filled_quantity in fill_totals.items()
        ):
            raise ValueError("filled quantity cannot exceed the order quantity")
        if any(
            snapshot.plan_id != self.plan.id for snapshot in self.position_snapshots
        ):
            raise ValueError("position snapshots must reference the journal plan")
        if any(
            snapshot.as_of < self.plan.planned_at
            for snapshot in self.position_snapshots
        ):
            raise ValueError("position snapshots cannot predate the plan")
        if self.result is not None and self.result.plan_id != self.plan.id:
            raise ValueError("trade result must reference the journal plan")
        if self.created_at < self.plan.planned_at:
            raise ValueError("journal entry cannot predate its plan")
        latest_fill = max((fill.filled_at for fill in self.fills), default=None)
        if (
            self.result is not None
            and latest_fill
            and self.result.closed_at < latest_fill
        ):
            raise ValueError("trade result cannot predate the journal's latest fill")
        if self.created_at < max(
            (
                *[order.created_at for order in self.orders],
                *[fill.filled_at for fill in self.fills],
                *[snapshot.as_of for snapshot in self.position_snapshots],
                self.result.closed_at if self.result else self.plan.planned_at,
            )
        ):
            raise ValueError("journal entry cannot predate its lifecycle records")
        return self
