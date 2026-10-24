"""Exact portfolio-level risk accounting for whole-share long equities."""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from fractions import Fraction
from types import MappingProxyType
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    field_validator,
    model_validator,
)

ZERO = Decimal(0)
ONE = Decimal(1)


class RiskDecision(StrEnum):
    ALLOW = "ALLOW"
    RESIZE = "RESIZE"
    DENY = "DENY"


class RiskReason(StrEnum):
    WITHIN_LIMITS = "WITHIN_LIMITS"
    PORTFOLIO_HEAT_LIMIT = "PORTFOLIO_HEAT_LIMIT"
    CORRELATED_HEAT_LIMIT = "CORRELATED_HEAT_LIMIT"
    THEME_CONCENTRATION_LIMIT = "THEME_CONCENTRATION_LIMIT"
    PORTFOLIO_STATE_STALE = "PORTFOLIO_STATE_STALE"
    PORTFOLIO_STATE_INCOMPLETE = "PORTFOLIO_STATE_INCOMPLETE"
    PORTFOLIO_STATE_CONFLICT = "PORTFOLIO_STATE_CONFLICT"
    STOP_MISSING = "STOP_MISSING"
    STOP_NOT_CONFIRMED = "STOP_NOT_CONFIRMED"
    FACTOR_CLASSIFICATION_MISSING = "FACTOR_CLASSIFICATION_MISSING"
    TAXONOMY_VERSION_MISMATCH = "TAXONOMY_VERSION_MISMATCH"
    POLICY_NOT_CONFIGURED = "POLICY_NOT_CONFIGURED"
    ZERO_ADMISSIBLE_QUANTITY = "ZERO_ADMISSIBLE_QUANTITY"


class RiskModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class PositionExposure(RiskModel):
    ticker: str = Field(min_length=1, max_length=32)
    quantity: int = Field(gt=0, strict=True)
    average_entry: Decimal = Field(gt=ZERO, allow_inf_nan=False)
    active_stop: Decimal | None = Field(default=None, gt=ZERO, allow_inf_nan=False)
    stop_confirmed: StrictBool

    @field_validator("ticker")
    @classmethod
    def normalize_ticker(cls, value: str) -> str:
        normalized = value.strip().upper()
        if not normalized:
            raise ValueError("ticker must not be blank")
        return normalized


class PendingExposure(RiskModel):
    ticker: str = Field(min_length=1, max_length=32)
    remaining_quantity: int = Field(gt=0, strict=True)
    entry: Decimal = Field(gt=ZERO, allow_inf_nan=False)
    stop: Decimal = Field(gt=ZERO, allow_inf_nan=False)
    status_confirmed: StrictBool

    @field_validator("ticker")
    @classmethod
    def normalize_ticker(cls, value: str) -> str:
        normalized = value.strip().upper()
        if not normalized:
            raise ValueError("ticker must not be blank")
        return normalized

    @model_validator(mode="after")
    def long_order_has_protective_stop_below_entry(self) -> "PendingExposure":
        if self.entry <= self.stop:
            raise ValueError("pending long order requires entry > stop")
        return self


class PortfolioSnapshot(RiskModel):
    account_id: UUID
    version: int = Field(ge=1, strict=True)
    as_of: datetime
    policy_version: str = Field(min_length=1)
    taxonomy_version: str = Field(min_length=1)
    equity: Decimal = Field(gt=ZERO, allow_inf_nan=False)
    positions: tuple[PositionExposure, ...]
    pending_orders: tuple[PendingExposure, ...]
    positions_complete: bool
    orders_complete: bool
    ats_managed_exposure_excluded: StrictBool

    @field_validator("as_of")
    @classmethod
    def as_of_is_utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() != timedelta(0):
            raise ValueError("portfolio snapshot timestamp must be UTC-aware")
        return value.astimezone(UTC)


class RiskClassification(RiskModel):
    theme: str = Field(min_length=1)
    factors: tuple[str, ...] = Field(min_length=1)

    @field_validator("theme")
    @classmethod
    def normalize_theme(cls, value: str) -> str:
        normalized = value.strip().upper()
        if not normalized:
            raise ValueError("theme must not be blank")
        return normalized

    @field_validator("factors")
    @classmethod
    def normalize_factors(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(sorted({factor.strip().upper() for factor in value}))
        if not normalized or any(not factor for factor in normalized):
            raise ValueError("at least one nonblank factor is required")
        return normalized


@dataclass(frozen=True)
class RiskTaxonomy:
    version: str
    classifications: Mapping[str, RiskClassification]

    def __post_init__(self) -> None:
        if not self.version.strip():
            raise ValueError("taxonomy version must not be blank")
        normalized = {
            ticker.strip().upper(): classification
            for ticker, classification in self.classifications.items()
        }
        if any(not ticker for ticker in normalized):
            raise ValueError("taxonomy tickers must not be blank")
        if any(
            not isinstance(classification, RiskClassification)
            for classification in normalized.values()
        ):
            raise TypeError("taxonomy values must be RiskClassification instances")
        object.__setattr__(self, "classifications", MappingProxyType(normalized))

    def classify(self, ticker: str) -> RiskClassification | None:
        return self.classifications.get(ticker.strip().upper())


@dataclass(frozen=True)
class PortfolioRiskPolicy:
    version: str
    reference_fraction: Decimal = Decimal("0.005")
    total_cap_r: Decimal = Decimal(4)
    correlated_cap_r: Decimal = Decimal(2)
    theme_concentration: Decimal = Decimal("0.15")
    snapshot_max_age: timedelta | None = None

    def __post_init__(self) -> None:
        if not self.version.strip():
            raise ValueError("policy version must not be blank")
        for name in (
            "reference_fraction",
            "total_cap_r",
            "correlated_cap_r",
            "theme_concentration",
        ):
            value = getattr(self, name)
            if not value.is_finite() or value <= ZERO:
                raise ValueError(f"{name} must be finite and greater than zero")
        if (
            self.reference_fraction != Decimal("0.005")
            or self.total_cap_r != Decimal(4)
            or self.correlated_cap_r != Decimal(2)
            or self.theme_concentration != Decimal("0.15")
        ):
            raise ValueError(
                "portfolio risk limits must match the approved v0.1 policy"
            )
        if self.snapshot_max_age is not None and self.snapshot_max_age <= timedelta(0):
            raise ValueError("snapshot_max_age must be positive")

    @property
    def is_operationally_configured(self) -> bool:
        return self.snapshot_max_age is not None


@dataclass(frozen=True)
class CandidateExposure:
    ticker: str
    requested_quantity: int
    entry: Decimal
    stop: Decimal

    def __post_init__(self) -> None:
        if not self.ticker.strip():
            raise ValueError("candidate ticker must not be blank")
        if (
            not isinstance(self.requested_quantity, int)
            or isinstance(self.requested_quantity, bool)
            or self.requested_quantity <= 0
        ):
            raise ValueError("requested_quantity must be positive")
        if self.entry <= self.stop or self.stop <= ZERO:
            raise ValueError("candidate requires entry > stop > 0")
        if not self.entry.is_finite() or not self.stop.is_finite():
            raise ValueError("candidate prices must be finite")


@dataclass(frozen=True)
class FilledExposure:
    quantity: int
    price: Decimal
    active_stop: Decimal | None = None

    def __post_init__(self) -> None:
        if (
            not isinstance(self.quantity, int)
            or isinstance(self.quantity, bool)
            or self.quantity <= 0
        ):
            raise ValueError("filled quantity must be positive")
        if not self.price.is_finite() or self.price <= ZERO:
            raise ValueError("fill price must be finite and positive")
        if self.active_stop is not None and (
            not self.active_stop.is_finite() or self.active_stop <= ZERO
        ):
            raise ValueError("confirmed active stop must be finite and positive")


@dataclass(frozen=True)
class ReservationExposure:
    reservation_id: UUID
    ticker: str
    remaining_quantity: int
    entry: Decimal
    stop: Decimal
    fills: tuple[FilledExposure, ...] = ()

    def __post_init__(self) -> None:
        if (
            not isinstance(self.remaining_quantity, int)
            or isinstance(self.remaining_quantity, bool)
            or self.remaining_quantity < 0
        ):
            raise ValueError("reservation quantities cannot be negative")
        if self.remaining_quantity + sum(fill.quantity for fill in self.fills) == 0:
            raise ValueError("empty reservation exposure is not active")
        if self.entry <= self.stop or self.stop <= ZERO:
            raise ValueError("reservation requires entry > stop > 0")
        if not self.entry.is_finite() or not self.stop.is_finite():
            raise ValueError("reservation prices must be finite")


@dataclass(frozen=True)
class PortfolioRiskResult:
    decision: RiskDecision
    quantity: int
    reason: RiskReason
    binding_constraints: tuple[RiskReason, ...] = ()


def _fraction(value: Decimal) -> Fraction:
    return Fraction(value)


def _floor(value: Fraction) -> int:
    return value.numerator // value.denominator


def assess_portfolio_risk(
    *,
    snapshot: PortfolioSnapshot,
    candidate: CandidateExposure,
    reservations: tuple[ReservationExposure, ...],
    taxonomy: RiskTaxonomy,
    policy: PortfolioRiskPolicy,
    now: datetime,
) -> PortfolioRiskResult:
    """Calculate a maximum admissible quantity from a coherent account snapshot."""
    if not policy.is_operationally_configured:
        return _deny(RiskReason.POLICY_NOT_CONFIGURED)
    if snapshot.policy_version != policy.version:
        return _deny(RiskReason.POLICY_NOT_CONFIGURED)
    if snapshot.taxonomy_version != taxonomy.version:
        return _deny(RiskReason.TAXONOMY_VERSION_MISMATCH)
    if not snapshot.ats_managed_exposure_excluded:
        return _deny(RiskReason.PORTFOLIO_STATE_CONFLICT)
    if not snapshot.positions_complete or not snapshot.orders_complete:
        return _deny(RiskReason.PORTFOLIO_STATE_INCOMPLETE)
    if now.tzinfo is None or now.utcoffset() != timedelta(0):
        raise ValueError("evaluation timestamp must be UTC-aware")
    age = now.astimezone(UTC) - snapshot.as_of
    if age < timedelta(0) or age > policy.snapshot_max_age:
        return _deny(RiskReason.PORTFOLIO_STATE_STALE)

    candidate_classification = taxonomy.classify(candidate.ticker)
    if candidate_classification is None:
        return _deny(RiskReason.FACTOR_CLASSIFICATION_MISSING)

    equity = _fraction(snapshot.equity)
    total_limit = (
        equity * _fraction(policy.reference_fraction) * _fraction(policy.total_cap_r)
    )
    factor_limit = (
        equity
        * _fraction(policy.reference_fraction)
        * _fraction(policy.correlated_cap_r)
    )
    theme_limit = equity * _fraction(policy.theme_concentration)

    total_risk = Fraction(0)
    factor_risk: dict[str, Fraction] = {}
    theme_risk: dict[str, Fraction] = {}
    theme_notional: dict[str, Fraction] = {}

    for position in snapshot.positions:
        classification = taxonomy.classify(position.ticker)
        if classification is None:
            return _deny(RiskReason.FACTOR_CLASSIFICATION_MISSING)
        if position.active_stop is None:
            return _deny(RiskReason.STOP_MISSING)
        if not position.stop_confirmed:
            return _deny(RiskReason.STOP_NOT_CONFIRMED)
        risk = position.quantity * max(
            Fraction(0),
            _fraction(position.average_entry) - _fraction(position.active_stop),
        )
        total_risk += risk
        _add_factor_risk(factor_risk, classification.factors, risk)
        theme_risk[classification.theme] = (
            theme_risk.get(classification.theme, Fraction(0)) + risk
        )
        theme_notional[classification.theme] = theme_notional.get(
            classification.theme, Fraction(0)
        ) + position.quantity * _fraction(position.average_entry)

    for pending in snapshot.pending_orders:
        classification = taxonomy.classify(pending.ticker)
        if classification is None:
            return _deny(RiskReason.FACTOR_CLASSIFICATION_MISSING)
        if not pending.status_confirmed:
            return _deny(RiskReason.PORTFOLIO_STATE_CONFLICT)
        risk = pending.remaining_quantity * max(
            Fraction(0), _fraction(pending.entry) - _fraction(pending.stop)
        )
        total_risk += risk
        _add_factor_risk(factor_risk, classification.factors, risk)
        theme_risk[classification.theme] = (
            theme_risk.get(classification.theme, Fraction(0)) + risk
        )
        theme_notional[classification.theme] = theme_notional.get(
            classification.theme, Fraction(0)
        ) + pending.remaining_quantity * _fraction(pending.entry)

    for reservation in reservations:
        classification = taxonomy.classify(reservation.ticker)
        if classification is None:
            return _deny(RiskReason.FACTOR_CLASSIFICATION_MISSING)
        if any(fill.active_stop is None for fill in reservation.fills):
            return _deny(RiskReason.STOP_NOT_CONFIRMED)
        filled_risk = sum(
            (
                fill.quantity
                * max(
                    Fraction(0),
                    _fraction(fill.price) - _fraction(fill.active_stop),
                )
                for fill in reservation.fills
            ),
            Fraction(0),
        )
        remaining_risk = reservation.remaining_quantity * (
            _fraction(reservation.entry) - _fraction(reservation.stop)
        )
        risk = filled_risk + remaining_risk
        total_risk += risk
        _add_factor_risk(factor_risk, classification.factors, risk)
        theme_risk[classification.theme] = (
            theme_risk.get(classification.theme, Fraction(0)) + risk
        )
        notional = sum(
            (fill.quantity * _fraction(fill.price) for fill in reservation.fills),
            Fraction(0),
        ) + (reservation.remaining_quantity * _fraction(reservation.entry))
        theme_notional[classification.theme] = (
            theme_notional.get(classification.theme, Fraction(0)) + notional
        )

    if any(notional > theme_limit for notional in theme_notional.values()):
        return _deny(RiskReason.THEME_CONCENTRATION_LIMIT)

    stop_distance = _fraction(candidate.entry) - _fraction(candidate.stop)
    candidate_notional = _fraction(candidate.entry)
    boundaries: list[tuple[RiskReason, int]] = [
        (
            RiskReason.PORTFOLIO_HEAT_LIMIT,
            _floor((total_limit - total_risk) / stop_distance),
        ),
        (
            RiskReason.THEME_CONCENTRATION_LIMIT,
            _floor(
                (
                    theme_limit
                    - theme_notional.get(candidate_classification.theme, Fraction(0))
                )
                / candidate_notional
            ),
        ),
    ]
    boundaries.append(
        (
            RiskReason.CORRELATED_HEAT_LIMIT,
            _floor(
                (
                    factor_limit
                    - theme_risk.get(candidate_classification.theme, Fraction(0))
                )
                / stop_distance
            ),
        )
    )
    for factor in candidate_classification.factors:
        boundaries.append(
            (
                RiskReason.CORRELATED_HEAT_LIMIT,
                _floor(
                    (factor_limit - factor_risk.get(factor, Fraction(0)))
                    / stop_distance
                ),
            )
        )

    quantity = max(
        0, min(candidate.requested_quantity, *(bound for _, bound in boundaries))
    )
    if quantity == candidate.requested_quantity:
        return PortfolioRiskResult(
            RiskDecision.ALLOW, quantity, RiskReason.WITHIN_LIMITS
        )
    binding = tuple(
        dict.fromkeys(reason for reason, bound in boundaries if bound <= quantity)
    )
    if quantity == 0:
        return PortfolioRiskResult(
            RiskDecision.DENY,
            0,
            binding[0] if binding else RiskReason.ZERO_ADMISSIBLE_QUANTITY,
            binding or (RiskReason.ZERO_ADMISSIBLE_QUANTITY,),
        )
    return PortfolioRiskResult(
        RiskDecision.RESIZE,
        quantity,
        binding[0] if binding else RiskReason.ZERO_ADMISSIBLE_QUANTITY,
        binding,
    )


def _add_factor_risk(
    totals: dict[str, Fraction], factors: tuple[str, ...], risk: Fraction
) -> None:
    for factor in factors:
        totals[factor] = totals.get(factor, Fraction(0)) + risk


def _deny(reason: RiskReason) -> PortfolioRiskResult:
    return PortfolioRiskResult(RiskDecision.DENY, 0, reason, (reason,))
