"""Layer 3 authorization and durable portfolio-risk reservations.

This module records authorization state only. Broker execution is deliberately
unavailable until the portfolio and ATS-009 gates are production-ready.
"""

from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from fractions import Fraction
from hashlib import sha256
from typing import Literal, Protocol, runtime_checkable
from uuid import UUID, uuid4

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    field_validator,
    model_validator,
)
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from src.domain.models import TradePlan, TradeSide
from src.persistence.models import (
    PortfolioSnapshotRecord,
    RiskReservationEventRecord,
    TradePlanRecord,
)
from src.persistence.transaction_retry import run_in_transaction
from src.portfolio import (
    CandidateExposure,
    FilledExposure,
    PortfolioRiskPolicy,
    PortfolioRiskResult,
    PortfolioSnapshot,
    ReservationExposure,
    RiskDecision,
    RiskTaxonomy,
    assess_portfolio_risk,
)

BROKER_EXECUTION_ENABLED = False


class Layer3Reason(StrEnum):
    RESERVED = "RESERVED"
    PRINCIPAL_UNAVAILABLE = "PRINCIPAL_UNAVAILABLE"
    SNAPSHOT_SOURCE_UNAVAILABLE = "SNAPSHOT_SOURCE_UNAVAILABLE"
    BROKER_EVENT_SOURCE_UNAVAILABLE = "BROKER_EVENT_SOURCE_UNAVAILABLE"
    PLAN_NOT_FOUND = "PLAN_NOT_FOUND"
    PLAN_ACCOUNT_MISMATCH = "PLAN_ACCOUNT_MISMATCH"
    PLAN_ALREADY_RESERVED = "PLAN_ALREADY_RESERVED"
    SNAPSHOT_UNAVAILABLE = "SNAPSHOT_UNAVAILABLE"
    SNAPSHOT_VERSION_CONFLICT = "SNAPSHOT_VERSION_CONFLICT"
    ATS009_UNAVAILABLE = "ATS009_UNAVAILABLE"
    ATS009_POLICY_UNRESOLVED = "ATS009_POLICY_UNRESOLVED"
    ATS009_LOSS_LOCKED = "ATS009_LOSS_LOCKED"
    LOSS_STATE_VERSION_CONFLICT = "LOSS_STATE_VERSION_CONFLICT"
    PORTFOLIO_RISK_DENIED = "PORTFOLIO_RISK_DENIED"


class LossLockState(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    account_id: UUID
    as_of: datetime
    policy_version: str | None = None
    session_id: str | None = None
    week_id: str | None = None
    daily_loss_r: Decimal | None = Field(default=None, allow_inf_nan=False)
    weekly_loss_r: Decimal | None = Field(default=None, allow_inf_nan=False)
    daily_limit_r: Decimal | None = Field(default=None, allow_inf_nan=False)
    weekly_limit_r: Decimal | None = Field(default=None, allow_inf_nan=False)
    # Set only by a repository-backed provider that read them under the account lock.
    state_version: StrictInt | None = Field(default=None, ge=1)
    daily_locked: StrictBool | None = None
    weekly_locked: StrictBool | None = None

    @field_validator("as_of")
    @classmethod
    def timestamp_is_utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() != timedelta(0):
            raise ValueError("ATS-009 timestamp must be timezone-aware UTC")
        return value.astimezone(UTC)

    @property
    def is_complete(self) -> bool:
        return (
            bool(self.policy_version)
            and bool(self.session_id)
            and bool(self.week_id)
            and self.daily_loss_r is not None
            and self.weekly_loss_r is not None
            and self.daily_limit_r is not None
            and self.weekly_limit_r is not None
        )

    def blocks_risk_increase(self) -> bool:
        if not self.is_complete:
            raise ValueError("ATS-009 loss-lock state is incomplete")
        assert self.daily_loss_r is not None
        assert self.weekly_loss_r is not None
        assert self.daily_limit_r is not None
        assert self.weekly_limit_r is not None
        if self.daily_limit_r != Decimal(-3):
            raise ValueError("ATS-009 daily loss lock must be configured at -3R")
        if self.weekly_limit_r != Decimal(-6):
            raise ValueError("ATS-009 weekly loss lock must be configured at -6R")
        sticky = self.daily_locked is True or self.weekly_locked is True
        return (
            sticky
            or Fraction(self.daily_loss_r) <= Fraction(self.daily_limit_r)
            or Fraction(self.weekly_loss_r) <= Fraction(self.weekly_limit_r)
        )


class LossLockProvider(Protocol):
    def evaluate(self, account_id: UUID, at: datetime) -> LossLockState | None: ...


@runtime_checkable
class TransactionalLossLockProvider(Protocol):
    """Provider that reads sticky state inside the caller's account-locked transaction."""

    def evaluate_in_transaction(
        self, session: Session, account_id: UUID, at: datetime
    ) -> LossLockState | None: ...


class AuthenticatedAccountPrincipal(BaseModel):
    """Identity supplied by the application's trusted authentication adapter."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    account_id: UUID
    subject: str = Field(min_length=1)
    authorization_id: str = Field(min_length=1)


class AccountPrincipalProvider(Protocol):
    """Resolve the account principal from trusted request/runtime context."""

    def current_principal(self) -> AuthenticatedAccountPrincipal | None: ...


class PortfolioSnapshotVerifier(Protocol):
    """Verify authoritative account, positions, orders, freshness and exclusions."""

    def verify_snapshot(
        self,
        snapshot: PortfolioSnapshot,
        principal: AuthenticatedAccountPrincipal,
    ) -> PortfolioSnapshot | None: ...


class BrokerOrderStatus(StrEnum):
    ACTIVE = "ACTIVE"
    CANCELLED = "CANCELLED"
    FILLED = "FILLED"


class BrokerFillEvent(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    fill_id: str = Field(min_length=1, max_length=128)
    quantity: StrictInt = Field(gt=0)
    price: Decimal = Field(gt=0, allow_inf_nan=False)


class BrokerOrderUpdate(BaseModel):
    """Untrusted broker payload; it must be verified by the configured adapter."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    event_id: str = Field(min_length=1, max_length=128)
    account_id: UUID
    order_attempt_id: UUID
    broker_order_id: str = Field(min_length=1, max_length=128)
    sequence: StrictInt = Field(gt=0)
    status: BrokerOrderStatus
    authorized_quantity: StrictInt = Field(gt=0)
    cumulative_filled_quantity: StrictInt = Field(ge=0)
    fills: tuple[BrokerFillEvent, ...]


class BrokerStopConfirmation(BaseModel):
    """Untrusted stop event; it must be verified by the configured adapter."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    event_id: str = Field(min_length=1, max_length=128)
    account_id: UUID
    order_attempt_id: UUID
    broker_order_id: str = Field(min_length=1, max_length=128)
    sequence: StrictInt = Field(gt=0)
    covered_fill_ids: tuple[str, ...]
    active_stop: Decimal = Field(gt=0, allow_inf_nan=False)


class VerifiedBrokerOrderUpdate(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    event: BrokerOrderUpdate
    verification_id: str = Field(min_length=1, max_length=255)


class VerifiedBrokerStopConfirmation(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    event: BrokerStopConfirmation
    verification_id: str = Field(min_length=1, max_length=255)


class BrokerEventVerifier(Protocol):
    """Authenticate source, account, order attempt, quantity, fills and sequence."""

    def verify_order_update(
        self,
        event: BrokerOrderUpdate,
        principal: AuthenticatedAccountPrincipal,
    ) -> VerifiedBrokerOrderUpdate | None: ...

    def verify_stop_confirmation(
        self,
        event: BrokerStopConfirmation,
        principal: AuthenticatedAccountPrincipal,
    ) -> VerifiedBrokerStopConfirmation | None: ...


class ReservationOrderStatus(StrEnum):
    NEW = "NEW"
    ACTIVE = "ACTIVE"
    CANCELLED = "CANCELLED"
    FILLED = "FILLED"


class ReservationState(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    account_id: UUID
    reservation_id: UUID
    plan_id: UUID
    order_attempt_id: UUID
    idempotency_key: str = Field(min_length=1, max_length=120)
    principal_subject: str = Field(min_length=1)
    authorization_id: str = Field(min_length=1)
    ticker: str
    entry: Decimal
    stop: Decimal
    requested_quantity: int = Field(gt=0, strict=True)
    remaining_quantity: int = Field(ge=0, strict=True)
    fills: tuple[FilledExposure, ...] = ()
    broker_fill_ids: tuple[str, ...] = ()
    broker_order_id: str | None = None
    broker_event_sequence: int = Field(default=0, ge=0, strict=True)
    order_status: ReservationOrderStatus = ReservationOrderStatus.NEW
    policy_version: str
    taxonomy_version: str
    created_at: datetime

    @field_validator("created_at")
    @classmethod
    def timestamp_is_utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() != timedelta(0):
            raise ValueError("reservation timestamp must be timezone-aware UTC")
        return value.astimezone(UTC)

    @field_validator("ticker")
    @classmethod
    def normalize_ticker(cls, value: str) -> str:
        return value.strip().upper()

    @model_validator(mode="after")
    def reservation_state_is_reconciled(self) -> "ReservationState":
        filled_quantity = sum(fill.quantity for fill in self.fills)
        if len(self.broker_fill_ids) != len(self.fills):
            raise ValueError("reservation broker fill IDs do not reconcile")
        if len(set(self.broker_fill_ids)) != len(self.broker_fill_ids):
            raise ValueError("reservation broker fill IDs must be unique")
        if self.order_status is ReservationOrderStatus.NEW:
            if (
                self.broker_order_id is not None
                or self.broker_event_sequence != 0
                or filled_quantity != 0
                or self.remaining_quantity != self.requested_quantity
            ):
                raise ValueError("new reservation state is inconsistent")
        elif not self.broker_order_id or self.broker_event_sequence < 1:
            raise ValueError("broker reservation state is missing order identity")
        elif self.order_status is ReservationOrderStatus.ACTIVE:
            if filled_quantity + self.remaining_quantity != self.requested_quantity:
                raise ValueError("active reservation quantities do not reconcile")
        elif self.order_status is ReservationOrderStatus.CANCELLED:
            if (
                self.remaining_quantity != 0
                or filled_quantity > self.requested_quantity
            ):
                raise ValueError("cancelled reservation quantities do not reconcile")
        elif self.order_status is ReservationOrderStatus.FILLED and (
            self.remaining_quantity != 0 or filled_quantity != self.requested_quantity
        ):
            raise ValueError("filled reservation quantities do not reconcile")
        return self

    def exposure(self) -> ReservationExposure | None:
        if sum(fill.quantity for fill in self.fills) + self.remaining_quantity > (
            self.requested_quantity
        ):
            raise ValueError("reservation exposure exceeds its authorized quantity")
        if self.remaining_quantity == 0 and not self.fills:
            return None
        return ReservationExposure(
            reservation_id=self.reservation_id,
            ticker=self.ticker,
            remaining_quantity=self.remaining_quantity,
            entry=self.entry,
            stop=self.stop,
            fills=self.fills,
        )


def _require_execution_disabled(decision: "Layer3Decision") -> "Layer3Decision":
    if decision.execution_enabled is not False:
        raise ValueError("Layer 3 decisions can never enable broker execution")
    return decision


class Layer3Decision(BaseModel):
    """Authorization result; execution is never enabled by this model.

    ``Literal[False]`` rejects ``True`` on validation, ``model_validate`` and JSON
    deserialization. ``model_copy`` and ``model_construct`` skip validation by
    design, so both are overridden to re-check. This is a regression guard against
    accidental misuse, not a security boundary: ``object.__setattr__``, direct
    ``__dict__`` writes or subclassing can still forge a value in-process, which is
    why the service re-checks every decision at its boundary and why
    ``submit_order_to_broker`` independently refuses to run.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    decision: RiskDecision
    quantity: int
    reason: str
    reservation_id: UUID | None = None
    execution_enabled: Literal[False] = False
    replayed: bool = False

    @classmethod
    def model_construct(cls, _fields_set=None, **values):  # type: ignore[override]
        return _require_execution_disabled(
            super().model_construct(_fields_set, **values)
        )

    def copy(self, *, include=None, exclude=None, update=None, deep=False):
        """Reject the deprecated validation-bypassing copy API."""
        raise TypeError("Layer3Decision.copy is unsupported; use model_copy")

    def model_copy(self, *, update=None, deep: bool = False):  # type: ignore[override]
        data = self.model_dump() | dict(update or {})
        if data.get("execution_enabled") is not False:
            raise ValueError("Layer 3 decisions can never enable broker execution")
        return type(self).model_validate(data)


class ExecutionDisabledError(RuntimeError):
    pass


class Layer3AuthorizationService:
    """The only supported service for reserving capacity for a TradePlan."""

    def __init__(
        self,
        *,
        policy: PortfolioRiskPolicy,
        taxonomy: RiskTaxonomy,
        loss_locks: LossLockProvider | None,
        principal_provider: AccountPrincipalProvider | None = None,
        snapshot_verifier: PortfolioSnapshotVerifier | None = None,
        broker_event_verifier: BrokerEventVerifier | None = None,
        ats009_policy_version: str | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._policy = policy
        self._taxonomy = taxonomy
        self._loss_locks = loss_locks
        self._principal_provider = principal_provider
        self._snapshot_verifier = snapshot_verifier
        self._broker_event_verifier = broker_event_verifier
        self._ats009_policy_version = ats009_policy_version
        self._clock = clock

    def publish_snapshot(self, session: Session, snapshot: PortfolioSnapshot) -> None:
        self._require_transaction(session)
        principal = self._current_principal()
        if principal is None:
            raise PermissionError("trusted account principal is unavailable")
        if self._snapshot_verifier is None:
            raise PermissionError("trusted portfolio snapshot source is unavailable")
        if snapshot.account_id != principal.account_id:
            raise PermissionError(
                "snapshot account does not match authenticated account"
            )
        verified_snapshot = self._snapshot_verifier.verify_snapshot(snapshot, principal)
        if verified_snapshot is None or verified_snapshot != snapshot:
            raise PermissionError("portfolio snapshot authentication failed")
        self._lock_account(session, principal.account_id)
        latest = session.scalar(
            select(PortfolioSnapshotRecord)
            .where(PortfolioSnapshotRecord.account_id == principal.account_id)
            .order_by(PortfolioSnapshotRecord.version.desc())
            .limit(1)
        )
        if latest is not None and snapshot.version <= latest.version:
            raise ValueError("portfolio snapshot version must increase monotonically")
        session.add(
            PortfolioSnapshotRecord(
                id=uuid4(),
                account_id=principal.account_id,
                version=snapshot.version,
                as_of=snapshot.as_of,
                snapshot_data=snapshot.model_dump(mode="json"),
            )
        )
        session.flush()

    def authorize_with_retry(
        self,
        session_factory: Callable[[], Session],
        *,
        plan_id: UUID,
        idempotency_key: str,
        max_attempts: int = 3,
    ) -> Layer3Decision:
        """Database-only, non-enforcing boundary; returns only after commit.

        Providers must be local evidence adapters; broker I/O is forbidden in
        this scope. Retry the entire transaction with the original identity.
        No API route or order submission is enabled by this method.
        """
        return run_in_transaction(
            session_factory,
            lambda session: self.authorize(
                session, plan_id=plan_id, idempotency_key=idempotency_key
            ),
            max_attempts=max_attempts,
        )

    def authorize(
        self,
        session: Session,
        *,
        plan_id: UUID,
        idempotency_key: str,
    ) -> Layer3Decision:
        """Atomically evaluate ATS-009 and portfolio risk, then reserve capacity."""
        self._require_transaction(session)
        principal = self._current_principal()
        if principal is None:
            return self._deny(Layer3Reason.PRINCIPAL_UNAVAILABLE)
        if self._snapshot_verifier is None:
            return self._deny(Layer3Reason.SNAPSHOT_SOURCE_UNAVAILABLE)
        account_id = principal.account_id
        idempotency_key = idempotency_key.strip()
        if not idempotency_key or len(idempotency_key) > 120:
            raise ValueError("idempotency_key must contain 1 to 120 characters")
        now = self._clock()
        self._require_utc(now)
        self._lock_account(session, account_id)

        prior = session.scalar(
            select(RiskReservationEventRecord).where(
                RiskReservationEventRecord.account_id == account_id,
                RiskReservationEventRecord.idempotency_key
                == f"reserve:{idempotency_key}",
            )
        )

        plan_record = session.get(TradePlanRecord, plan_id)
        if plan_record is None:
            return self._deny(Layer3Reason.PLAN_NOT_FOUND)
        plan = TradePlan.model_validate(plan_record.plan_data)
        if plan.id != plan_id or plan.security.asset_class.casefold() != "equity":
            return self._deny(Layer3Reason.PLAN_NOT_FOUND)
        if plan.account_id != account_id:
            return self._deny(Layer3Reason.PLAN_ACCOUNT_MISMATCH)
        if plan.side is not TradeSide.LONG:
            return self._deny(Layer3Reason.PORTFOLIO_RISK_DENIED)
        latest_plan_version = session.scalar(
            select(func.max(TradePlanRecord.version)).where(
                TradePlanRecord.trade_id == plan.trade_id
            )
        )
        if latest_plan_version != plan.version:
            return self._deny(Layer3Reason.PLAN_NOT_FOUND)

        if prior is not None:
            if prior.event_type != "reserved" or prior.plan_id != plan_id:
                raise ValueError("idempotency key was already used for another action")
            if prior.snapshot_version != self._latest_snapshot_version(
                session, account_id
            ):
                return self._deny(Layer3Reason.SNAPSHOT_VERSION_CONFLICT)
            if not self._loss_binding_is_current(
                session, account_id, now, prior.event_data
            ):
                return self._deny(Layer3Reason.LOSS_STATE_VERSION_CONFLICT)
            return Layer3Decision.model_validate(
                prior.event_data["decision"]
            ).model_copy(update={"replayed": True, "execution_enabled": False})

        prior_plan_reservation = session.scalar(
            select(RiskReservationEventRecord.id)
            .where(
                RiskReservationEventRecord.account_id == account_id,
                RiskReservationEventRecord.plan_id == plan_id,
                RiskReservationEventRecord.event_type == "reserved",
            )
            .limit(1)
        )
        if prior_plan_reservation is not None:
            return self._deny(Layer3Reason.PLAN_ALREADY_RESERVED)

        plan_quantity = Fraction(plan.quantity)
        if plan_quantity.denominator != 1 or plan_quantity <= 0:
            return self._deny(Layer3Reason.PORTFOLIO_RISK_DENIED)

        snapshot_record = session.scalar(
            select(PortfolioSnapshotRecord)
            .where(PortfolioSnapshotRecord.account_id == account_id)
            .order_by(PortfolioSnapshotRecord.version.desc())
            .limit(1)
        )
        if snapshot_record is None:
            return self._deny(Layer3Reason.SNAPSHOT_UNAVAILABLE)
        if snapshot_record.account_id != account_id:
            return self._deny(Layer3Reason.SNAPSHOT_VERSION_CONFLICT)
        snapshot = PortfolioSnapshot.model_validate(snapshot_record.snapshot_data)
        if snapshot.account_id != account_id:
            return self._deny(Layer3Reason.SNAPSHOT_VERSION_CONFLICT)
        verified_snapshot = self._snapshot_verifier.verify_snapshot(snapshot, principal)
        if verified_snapshot is None or verified_snapshot != snapshot:
            return self._deny(Layer3Reason.SNAPSHOT_SOURCE_UNAVAILABLE)

        if self._loss_locks is None:
            return self._deny(Layer3Reason.ATS009_UNAVAILABLE)
        loss_state = self._read_loss_state(session, account_id, now)
        if loss_state is None or loss_state.account_id != account_id:
            return self._deny(Layer3Reason.ATS009_UNAVAILABLE)
        if (
            self._ats009_policy_version is None
            or loss_state.policy_version != self._ats009_policy_version
            or not loss_state.is_complete
            or loss_state.as_of.tzinfo is None
            or loss_state.as_of.utcoffset() != timedelta(0)
            or loss_state.as_of > now
            or (
                self._policy.snapshot_max_age is not None
                and now - loss_state.as_of > self._policy.snapshot_max_age
            )
        ):
            return self._deny(Layer3Reason.ATS009_POLICY_UNRESOLVED)
        loss_binding: dict[str, object] | None = None
        if isinstance(self._loss_locks, TransactionalLossLockProvider):
            if (
                loss_state.state_version is None
                or loss_state.daily_locked is None
                or loss_state.weekly_locked is None
            ):
                return self._deny(Layer3Reason.ATS009_POLICY_UNRESOLVED)
            loss_binding = {
                "version": loss_state.state_version,
                "policy_version": loss_state.policy_version,
            }
        try:
            if loss_state.blocks_risk_increase():
                return self._deny(Layer3Reason.ATS009_LOSS_LOCKED)
        except ValueError:
            return self._deny(Layer3Reason.ATS009_POLICY_UNRESOLVED)

        latest_states = self._latest_reservations(session, account_id)
        exposures = []
        for state in latest_states:
            if (
                state.policy_version != self._policy.version
                or state.taxonomy_version != self._taxonomy.version
            ):
                return self._deny(Layer3Reason.SNAPSHOT_VERSION_CONFLICT)
            exposure = state.exposure()
            if exposure is not None:
                if state.plan_id == plan_id:
                    return self._deny(Layer3Reason.PLAN_ALREADY_RESERVED)
                exposures.append(exposure)

        result = assess_portfolio_risk(
            snapshot=snapshot,
            candidate=CandidateExposure(
                ticker=plan.security.ticker,
                requested_quantity=int(plan_quantity),
                entry=plan.entry,
                stop=plan.stop,
            ),
            reservations=tuple(exposures),
            taxonomy=self._taxonomy,
            policy=self._policy,
            now=now,
        )
        if result.decision is RiskDecision.DENY:
            return self._deny(Layer3Reason.PORTFOLIO_RISK_DENIED, result=result)

        reservation_id = uuid4()
        state = ReservationState(
            account_id=account_id,
            reservation_id=reservation_id,
            plan_id=plan_id,
            order_attempt_id=uuid4(),
            idempotency_key=idempotency_key,
            principal_subject=principal.subject,
            authorization_id=principal.authorization_id,
            ticker=plan.security.ticker,
            entry=plan.entry,
            stop=plan.stop,
            requested_quantity=result.quantity,
            remaining_quantity=result.quantity,
            policy_version=self._policy.version,
            taxonomy_version=self._taxonomy.version,
            created_at=now,
        )
        decision = Layer3Decision(
            decision=result.decision,
            quantity=result.quantity,
            reason=result.reason.value,
            reservation_id=reservation_id,
        )
        _require_execution_disabled(decision)
        if loss_binding is not None and not self._loss_binding_is_current(
            session, account_id, now, {"loss_state": loss_binding}
        ):
            return self._deny(Layer3Reason.LOSS_STATE_VERSION_CONFLICT)
        self._append_event(
            session,
            state=state,
            event_type="reserved",
            idempotency_key=f"reserve:{idempotency_key}",
            snapshot_version=snapshot.version,
            event_data={
                "reservation": state.model_dump(mode="json"),
                "decision": decision.model_dump(mode="json"),
                **({"loss_state": loss_binding} if loss_binding is not None else {}),
            },
        )
        return decision

    def _read_loss_state(
        self, session: Session, account_id: UUID, now: datetime
    ) -> LossLockState | None:
        provider = self._loss_locks
        if isinstance(provider, TransactionalLossLockProvider):
            return provider.evaluate_in_transaction(session, account_id, now)
        return provider.evaluate(account_id, now)

    def _loss_binding_is_current(
        self,
        session: Session,
        account_id: UUID,
        now: datetime,
        event_data: dict[str, object],
    ) -> bool:
        binding = event_data.get("loss_state")
        provider = self._loss_locks
        if binding is None:
            # Legacy decisions cannot prove freshness under a transactional
            # provider, or when the evidence provider is unavailable.
            return provider is not None and not isinstance(
                provider, TransactionalLossLockProvider
            )
        if not isinstance(provider, TransactionalLossLockProvider) or not isinstance(
            binding, dict
        ):
            return False
        current = provider.evaluate_in_transaction(session, account_id, now)
        return (
            current is not None
            and current.state_version == binding.get("version")
            and current.policy_version == binding.get("policy_version")
        )

    def process_broker_order_update(
        self,
        session: Session,
        *,
        reservation_id: UUID,
        event: BrokerOrderUpdate,
    ) -> ReservationState:
        self._require_transaction(session)
        principal = self._require_principal()
        if self._broker_event_verifier is None:
            raise PermissionError("trusted broker event source is unavailable")
        self._lock_account(session, principal.account_id)
        latest = self._reservation_state(session, principal.account_id, reservation_id)
        if latest is None:
            raise ValueError("reservation does not exist")
        verified = self._broker_event_verifier.verify_order_update(event, principal)
        if verified is None or verified.event != event or not verified.verification_id:
            raise PermissionError("broker order event authentication failed")
        self._validate_order_event_identity(event, latest, principal)

        event_key = self._broker_event_key("order", event.event_id)
        existing = self._existing_broker_event(session, principal.account_id, event_key)
        event_payload = event.model_dump(mode="json")
        if existing is not None:
            if (
                existing.reservation_id != reservation_id
                or existing.event_data.get("broker_event") != event_payload
            ):
                raise ValueError("broker event ID was reused with conflicting data")
            return latest
        if latest.order_status in (
            ReservationOrderStatus.CANCELLED,
            ReservationOrderStatus.FILLED,
        ):
            raise ValueError("broker order update arrived after a terminal event")
        if event.sequence <= latest.broker_event_sequence:
            raise ValueError("broker order event is stale or reordered")
        if event.authorized_quantity != latest.requested_quantity:
            raise ValueError("broker order quantity differs from approved quantity")

        fill_ids = tuple(fill.fill_id for fill in event.fills)
        if len(set(fill_ids)) != len(fill_ids):
            raise ValueError("broker event contains duplicate fill IDs")
        cumulative = sum(fill.quantity for fill in event.fills)
        if cumulative != event.cumulative_filled_quantity:
            raise ValueError("broker cumulative fill quantity does not reconcile")
        if cumulative > latest.requested_quantity:
            raise ValueError("broker fills exceed the approved quantity")
        if fill_ids[: len(latest.broker_fill_ids)] != latest.broker_fill_ids:
            raise ValueError("broker cumulative fills do not extend prior fills")
        for index, prior_fill in enumerate(latest.fills):
            incoming = event.fills[index]
            if (
                prior_fill.quantity != incoming.quantity
                or prior_fill.price != incoming.price
            ):
                raise ValueError("broker event altered a previously recorded fill")
        if event.status is BrokerOrderStatus.ACTIVE:
            if event.broker_order_id == "" or cumulative >= latest.requested_quantity:
                raise ValueError("active order status conflicts with cumulative fills")
        elif (
            event.status is BrokerOrderStatus.FILLED
            and cumulative != latest.requested_quantity
        ):
            raise ValueError("filled order does not match approved quantity")

        existing_stops = {
            fill_id: latest.fills[index].active_stop
            for index, fill_id in enumerate(latest.broker_fill_ids)
        }
        fills = tuple(
            FilledExposure(
                quantity=fill.quantity,
                price=fill.price,
                active_stop=existing_stops.get(fill.fill_id),
            )
            for fill in event.fills
        )
        remaining = (
            0
            if event.status is BrokerOrderStatus.CANCELLED
            else latest.requested_quantity - cumulative
        )
        updated = latest.model_copy(
            update={
                "fills": fills,
                "broker_fill_ids": fill_ids,
                "remaining_quantity": remaining,
                "broker_order_id": event.broker_order_id,
                "broker_event_sequence": event.sequence,
                "order_status": ReservationOrderStatus(event.status.value),
            }
        )
        self._append_event(
            session,
            state=updated,
            event_type=(
                "release" if event.status is BrokerOrderStatus.CANCELLED else "fill"
            ),
            idempotency_key=event_key,
            snapshot_version=self._latest_snapshot_version(
                session, principal.account_id
            ),
            event_data={
                "reservation": updated.model_dump(mode="json"),
                "broker_event": event_payload,
                "verification_id": verified.verification_id,
            },
        )
        return updated

    def confirm_filled_stops(
        self,
        session: Session,
        *,
        reservation_id: UUID,
        event: BrokerStopConfirmation,
    ) -> ReservationState:
        self._require_transaction(session)
        principal = self._require_principal()
        if self._broker_event_verifier is None:
            raise PermissionError("trusted broker event source is unavailable")
        self._lock_account(session, principal.account_id)
        latest = self._reservation_state(session, principal.account_id, reservation_id)
        if latest is None:
            raise ValueError("reservation does not exist")
        verified = self._broker_event_verifier.verify_stop_confirmation(
            event, principal
        )
        if verified is None or verified.event != event or not verified.verification_id:
            raise PermissionError("broker stop event authentication failed")
        if (
            event.account_id != principal.account_id
            or event.order_attempt_id != latest.order_attempt_id
        ):
            raise PermissionError("broker stop event does not match reservation")
        event_key = self._broker_event_key("stop", event.event_id)
        existing = self._existing_broker_event(session, principal.account_id, event_key)
        event_payload = event.model_dump(mode="json")
        if existing is not None:
            if (
                existing.reservation_id != reservation_id
                or existing.event_data.get("broker_event") != event_payload
            ):
                raise ValueError(
                    "broker stop event ID was reused with conflicting data"
                )
            return latest
        if event.sequence <= latest.broker_event_sequence:
            raise ValueError("broker stop event is stale or reordered")
        if not latest.fills:
            raise ValueError("cannot confirm a protective stop before any fill")
        if event.covered_fill_ids != latest.broker_fill_ids:
            raise ValueError(
                "stop confirmation does not cover current cumulative fills"
            )
        if any(
            fill.active_stop is not None and event.active_stop < fill.active_stop
            for fill in latest.fills
        ):
            raise ValueError("stop loosening requires a separately approved policy")

        updated = latest.model_copy(
            update={
                "fills": tuple(
                    replace(fill, active_stop=event.active_stop)
                    for fill in latest.fills
                ),
                "broker_event_sequence": event.sequence,
            }
        )
        self._append_event(
            session,
            state=updated,
            event_type="stop_confirmed",
            idempotency_key=event_key,
            snapshot_version=self._latest_snapshot_version(
                session, principal.account_id
            ),
            event_data={
                "reservation": updated.model_dump(mode="json"),
                "broker_event": event_payload,
                "verification_id": verified.verification_id,
            },
        )
        return updated

    def _current_principal(self) -> AuthenticatedAccountPrincipal | None:
        if self._principal_provider is None:
            return None
        principal = self._principal_provider.current_principal()
        if principal is None:
            return None
        if not principal.subject.strip() or not principal.authorization_id.strip():
            return None
        return principal

    def _require_principal(self) -> AuthenticatedAccountPrincipal:
        principal = self._current_principal()
        if principal is None:
            raise PermissionError("trusted account principal is unavailable")
        return principal

    @staticmethod
    def _validate_order_event_identity(
        event: BrokerOrderUpdate,
        state: ReservationState,
        principal: AuthenticatedAccountPrincipal,
    ) -> None:
        if (
            event.account_id != principal.account_id
            or event.account_id != state.account_id
            or event.order_attempt_id != state.order_attempt_id
        ):
            raise PermissionError("broker order event does not match reservation")
        if state.broker_order_id is not None and (
            event.broker_order_id != state.broker_order_id
        ):
            raise PermissionError("broker order identity changed for reservation")

    @staticmethod
    def _broker_event_key(event_type: str, event_id: str) -> str:
        digest = sha256(f"{event_type}:{event_id}".encode()).hexdigest()
        return f"broker:{digest}"

    @staticmethod
    def _existing_broker_event(
        session: Session, account_id: UUID, idempotency_key: str
    ) -> RiskReservationEventRecord | None:
        return session.scalar(
            select(RiskReservationEventRecord).where(
                RiskReservationEventRecord.account_id == account_id,
                RiskReservationEventRecord.idempotency_key == idempotency_key,
            )
        )

    @staticmethod
    def _deny(
        reason: Layer3Reason, result: PortfolioRiskResult | None = None
    ) -> Layer3Decision:
        return Layer3Decision(
            decision=RiskDecision.DENY,
            quantity=0,
            reason=(result.reason.value if result is not None else reason.value),
        )

    @staticmethod
    def _require_transaction(session: Session) -> None:
        if not session.in_transaction():
            raise RuntimeError("Layer 3 writes require a caller-owned transaction")

    @staticmethod
    def _require_utc(value: datetime) -> None:
        if value.tzinfo is None or value.utcoffset() != timedelta(0):
            raise ValueError("Layer 3 timestamps must be timezone-aware UTC")

    @staticmethod
    def _lock_account(session: Session, account_id: UUID) -> None:
        dialect = session.get_bind().dialect.name
        if dialect == "postgresql":
            isolation_level = session.scalar(text("SHOW transaction_isolation"))
            if isolation_level != "read committed":
                raise RuntimeError(
                    "PostgreSQL risk transactions require READ COMMITTED isolation"
                )
            session.execute(
                select(
                    func.pg_advisory_xact_lock(
                        func.hashtextextended(str(account_id), 0)
                    )
                )
            )
        elif dialect == "sqlite":
            transaction = session.get_transaction()
            if transaction is None:
                raise RuntimeError("SQLite risk locks require an active transaction")
            lock_state = session.info.get("ats_risk_account_locks")
            if (
                lock_state is not None
                and lock_state[0] is transaction
                and account_id in lock_state[1]
            ):
                return
            connection = session.connection()
            if connection.connection.driver_connection.in_transaction:
                raise RuntimeError(
                    "SQLite account lock must be the first database operation "
                    "in the transaction"
                )
            connection.exec_driver_sql("BEGIN IMMEDIATE")
            if lock_state is None or lock_state[0] is not transaction:
                lock_state = (transaction, set())
                session.info["ats_risk_account_locks"] = lock_state
            lock_state[1].add(account_id)
        else:
            raise RuntimeError(f"unsupported transactional risk store: {dialect}")

    @staticmethod
    def _latest_snapshot_version(session: Session, account_id: UUID) -> int:
        version = session.scalar(
            select(func.max(PortfolioSnapshotRecord.version)).where(
                PortfolioSnapshotRecord.account_id == account_id
            )
        )
        if version is None:
            raise ValueError("portfolio snapshot is unavailable")
        return version

    @staticmethod
    def _latest_reservations(
        session: Session, account_id: UUID
    ) -> tuple[ReservationState, ...]:
        rows = session.scalars(
            select(RiskReservationEventRecord)
            .where(RiskReservationEventRecord.account_id == account_id)
            .order_by(
                RiskReservationEventRecord.reservation_id,
                RiskReservationEventRecord.sequence,
            )
        ).all()
        latest: dict[UUID, RiskReservationEventRecord] = {}
        for row in rows:
            latest[row.reservation_id] = row
        return tuple(
            ReservationState.model_validate(row.event_data["reservation"])
            for row in latest.values()
        )

    @staticmethod
    def _reservation_state(
        session: Session, account_id: UUID, reservation_id: UUID
    ) -> ReservationState | None:
        row = session.scalar(
            select(RiskReservationEventRecord)
            .where(
                RiskReservationEventRecord.account_id == account_id,
                RiskReservationEventRecord.reservation_id == reservation_id,
            )
            .order_by(RiskReservationEventRecord.sequence.desc())
            .limit(1)
        )
        if row is None:
            return None
        return ReservationState.model_validate(row.event_data["reservation"])

    @staticmethod
    def _append_event(
        session: Session,
        *,
        state: ReservationState,
        event_type: str,
        idempotency_key: str,
        snapshot_version: int,
        event_data: dict[str, object],
    ) -> None:
        sequence = (
            session.scalar(
                select(func.max(RiskReservationEventRecord.sequence)).where(
                    RiskReservationEventRecord.reservation_id == state.reservation_id
                )
            )
            or 0
        ) + 1
        session.add(
            RiskReservationEventRecord(
                id=uuid4(),
                account_id=state.account_id,
                reservation_id=state.reservation_id,
                sequence=sequence,
                event_type=event_type,
                idempotency_key=idempotency_key,
                snapshot_version=snapshot_version,
                plan_id=state.plan_id,
                event_data=event_data,
                created_at=datetime.now(UTC),
            )
        )
        session.flush()


def submit_order_to_broker(*_args: object, **_kwargs: object) -> None:
    raise ExecutionDisabledError(
        "broker execution is disabled pending portfolio-risk and ATS-009 readiness"
    )
