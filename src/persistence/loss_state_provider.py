"""Repository-backed sticky loss-state projection (non-enforcing).

The provider only projects durable evidence. Evidence written by synthetic or
unauthenticated inputs stays unauthenticated; this module grants no trading
authority and Layer 3 remains disabled for order submission.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import ROUND_FLOOR, Decimal
from fractions import Fraction
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.ats009_accounting import ATS009_POLICY_VERSION
from src.ats009_calendar import XNYSCalendar
from src.layer3 import LossLockState
from src.persistence.loss_state_repository import (
    LossStateConflict,
    LossStateRepository,
)
from src.persistence.models import (
    LossStateEventRecord,
    LossValuationObservationRecord,
)

_R_QUANTUM = Decimal("0.00000001")


def _floor_decimal(numerator: str, denominator: str) -> Decimal:
    value = Fraction(int(numerator), int(denominator))
    exact = Decimal(value.numerator) / Decimal(value.denominator)
    # Floor keeps the displayed loss at or below the exact value (conservative).
    return exact.quantize(_R_QUANTUM, rounding=ROUND_FLOOR)


class RepositoryLossLockProvider:
    """Reads sticky lock state under the caller's PostgreSQL account lock."""

    def __init__(
        self,
        repository: LossStateRepository | None = None,
        calendar: XNYSCalendar | None = None,
        policy_version: str = ATS009_POLICY_VERSION,
    ) -> None:
        self._repository = repository or LossStateRepository()
        self._calendar = calendar or XNYSCalendar()
        self._policy_version = policy_version

    def evaluate(self, account_id: UUID, at: datetime) -> LossLockState | None:
        # No session means no durable read; fail closed.
        return None

    def evaluate_in_transaction(
        self, session: Session, account_id: UUID, at: datetime
    ) -> LossLockState | None:
        resolved = self._calendar.resolve(at)
        if resolved is None:
            return None
        try:
            projection = self._repository.rebuild_projection(
                session,
                account_id=account_id,
                active_daily_period_id=resolved.session_id,
                active_weekly_period_id=resolved.week_id,
            )
        except LossStateConflict:
            return None
        if projection.state_version < 1:
            return None
        event = session.scalar(
            select(LossStateEventRecord).where(
                LossStateEventRecord.account_id == account_id,
                LossStateEventRecord.state_version == projection.state_version,
            )
        )
        if (
            event is None
            or event.policy_version != self._policy_version
            or event.daily_period_id != resolved.session_id
            or event.weekly_period_id != resolved.week_id
        ):
            return None
        observation = session.get(
            LossValuationObservationRecord, event.valuation_observation_id
        )
        if observation is None:
            return None
        return LossLockState(
            account_id=account_id,
            as_of=observation.as_of.astimezone(UTC),
            policy_version=event.policy_version,
            session_id=resolved.session_id,
            week_id=resolved.week_id,
            daily_loss_r=_floor_decimal(
                event.daily_r_numerator, event.daily_r_denominator
            ),
            weekly_loss_r=_floor_decimal(
                event.weekly_r_numerator, event.weekly_r_denominator
            ),
            daily_limit_r=Decimal(-3),
            weekly_limit_r=Decimal(-6),
            state_version=projection.state_version,
            daily_locked=projection.daily_locked,
            weekly_locked=projection.weekly_locked,
        )
