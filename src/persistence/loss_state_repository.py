"""Append-only PostgreSQL persistence for ATS-009 loss-state evidence.

This repository is persistence infrastructure, not a trusted provider. Its
inputs are structurally validated contracts and must not be treated as
authenticated production observations.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from fractions import Fraction
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from src.ats009_accounting import (
    ATS009_POLICY_VERSION,
    LossAccountingEvaluation,
    PeriodKind,
    PeriodOpeningEquity,
    ReconciledExternalFlow,
    VerifiedAccountValuation,
)
from src.persistence.models import (
    LossExternalFlowRecord,
    LossPeriodOpeningRecord,
    LossStateEventRecord,
    LossValuationObservationRecord,
)


class LossStateConflict(RuntimeError):
    """Input replay, source watermark, or durable state conflicts."""


class LossStateEventType(StrEnum):
    OBSERVED = "OBSERVED"
    LOCKED = "LOCKED"
    CORRECTION = "CORRECTION"


@dataclass(frozen=True)
class StoredEvidence:
    record_id: UUID
    digest: str
    replayed: bool


@dataclass(frozen=True)
class LossStateProjection:
    account_id: UUID
    state_version: int
    active_daily_period_id: str
    active_weekly_period_id: str
    daily_locked: bool
    weekly_locked: bool


def _decimal_text(value: Decimal) -> str:
    """Render a finite Decimal without context-sensitive normalization."""
    if not value.is_finite():
        raise ValueError("non-finite Decimal cannot be persisted")
    sign, digits, exponent = value.as_tuple()
    digit_list = list(digits)
    if not any(digit_list):
        return "0"
    while len(digit_list) > 1 and digit_list[-1] == 0:
        digit_list.pop()
        exponent += 1
    coefficient = "".join(str(digit) for digit in digit_list)
    if exponent >= 0:
        rendered = coefficient + ("0" * exponent)
    else:
        split = len(coefficient) + exponent
        if split > 0:
            rendered = f"{coefficient[:split]}.{coefficient[split:]}"
        else:
            rendered = f"0.{('0' * -split)}{coefficient}"
    return f"-{rendered}" if sign else rendered


def _fraction_parts(value: Fraction) -> tuple[str, str]:
    return str(value.numerator), str(value.denominator)


def _canonical(value: Any) -> Any:
    if isinstance(value, Decimal):
        return _decimal_text(value)
    if isinstance(value, Fraction):
        return {
            "numerator": str(value.numerator),
            "denominator": str(value.denominator),
        }
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() != timedelta(0):
            raise ValueError("digest timestamps must be timezone-aware UTC")
        return value.astimezone(UTC).isoformat(timespec="microseconds")
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, StrEnum):
        return value.value
    if isinstance(value, dict):
        return {str(key): _canonical(item) for key, item in sorted(value.items())}
    if isinstance(value, (tuple, list)):
        return [_canonical(item) for item in value]
    if value is None or isinstance(value, (str, int, bool)):
        return value
    raise TypeError(f"unsupported canonical digest value: {type(value).__name__}")


def _digest(payload: dict[str, Any]) -> str:
    encoded = json.dumps(
        _canonical(payload),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("ascii")
    return hashlib.sha256(encoded).hexdigest()


class LossStateRepository:
    """Append-only loss evidence writer and rebuildable lock projection."""

    def append_valuation(
        self,
        session: Session,
        valuation: VerifiedAccountValuation,
        *,
        correction_of_id: UUID | None = None,
    ) -> StoredEvidence:
        self._lock_account(session, valuation.account_id)
        digest = _digest(
            {
                "account_id": valuation.account_id,
                "source_id": valuation.source_id,
                "source_epoch": valuation.source_epoch,
                "source_sequence": valuation.source_sequence,
                "as_of": valuation.as_of,
                "base_currency": valuation.base_currency,
                "net_liquidation": valuation.net_liquidation,
                "complete": valuation.complete,
                "reconciled": valuation.reconciled,
                "provenance_ref": valuation.provenance_ref,
                "correction_of_id": correction_of_id,
            }
        )
        identity = (
            LossValuationObservationRecord.account_id == valuation.account_id,
            LossValuationObservationRecord.source_id == valuation.source_id,
            LossValuationObservationRecord.source_epoch == valuation.source_epoch,
            LossValuationObservationRecord.source_sequence == valuation.source_sequence,
        )
        existing = session.scalar(
            select(LossValuationObservationRecord).where(*identity)
        )
        if existing is not None:
            return self._existing_evidence(existing.id, existing.digest, digest)
        self._require_next_sequence(
            session,
            LossValuationObservationRecord,
            valuation.account_id,
            valuation.source_id,
            valuation.source_epoch,
            valuation.source_sequence,
        )
        if correction_of_id is not None:
            self._require_owned_record(
                session,
                LossValuationObservationRecord,
                correction_of_id,
                valuation.account_id,
            )
        record = LossValuationObservationRecord(
            id=uuid4(),
            account_id=valuation.account_id,
            source_id=valuation.source_id,
            source_epoch=valuation.source_epoch,
            source_sequence=valuation.source_sequence,
            as_of=valuation.as_of,
            base_currency=valuation.base_currency,
            net_liquidation=valuation.net_liquidation,
            complete=valuation.complete,
            reconciled=valuation.reconciled,
            provenance_ref=valuation.provenance_ref,
            digest=digest,
            correction_of_id=correction_of_id,
        )
        session.add(record)
        session.flush()
        return StoredEvidence(record.id, digest, False)

    def append_external_flow(
        self,
        session: Session,
        flow: ReconciledExternalFlow,
        *,
        correction_of_id: UUID | None = None,
    ) -> StoredEvidence:
        self._lock_account(session, flow.account_id)
        digest = _digest(
            {
                "account_id": flow.account_id,
                "flow_id": flow.flow_id,
                "source_id": flow.source_id,
                "source_epoch": flow.source_epoch,
                "source_sequence": flow.source_sequence,
                "occurred_at": flow.occurred_at,
                "flow_kind": flow.kind,
                "signed_amount": flow.signed_amount,
                "base_currency": flow.base_currency,
                "provenance_ref": flow.provenance_ref,
                "correction_of_id": correction_of_id,
            }
        )
        identity = (
            LossExternalFlowRecord.account_id == flow.account_id,
            LossExternalFlowRecord.source_id == flow.source_id,
            LossExternalFlowRecord.source_epoch == flow.source_epoch,
            LossExternalFlowRecord.source_sequence == flow.source_sequence,
        )
        existing = session.scalar(select(LossExternalFlowRecord).where(*identity))
        if existing is not None:
            return self._existing_evidence(existing.id, existing.digest, digest)
        prior_flow = session.scalar(
            select(LossExternalFlowRecord).where(
                LossExternalFlowRecord.account_id == flow.account_id,
                LossExternalFlowRecord.source_id == flow.source_id,
                LossExternalFlowRecord.source_epoch == flow.source_epoch,
                LossExternalFlowRecord.flow_id == flow.flow_id,
            )
        )
        if prior_flow is not None:
            return self._existing_evidence(prior_flow.id, prior_flow.digest, digest)
        self._require_next_sequence(
            session,
            LossExternalFlowRecord,
            flow.account_id,
            flow.source_id,
            flow.source_epoch,
            flow.source_sequence,
        )
        if correction_of_id is not None:
            self._require_owned_record(
                session, LossExternalFlowRecord, correction_of_id, flow.account_id
            )
        record = LossExternalFlowRecord(
            id=uuid4(),
            account_id=flow.account_id,
            flow_id=flow.flow_id,
            source_id=flow.source_id,
            source_epoch=flow.source_epoch,
            source_sequence=flow.source_sequence,
            occurred_at=flow.occurred_at,
            flow_kind=flow.kind.value,
            signed_amount=flow.signed_amount,
            base_currency=flow.base_currency,
            provenance_ref=flow.provenance_ref,
            digest=digest,
            correction_of_id=correction_of_id,
        )
        session.add(record)
        session.flush()
        return StoredEvidence(record.id, digest, False)

    def append_opening(
        self, session: Session, opening: PeriodOpeningEquity
    ) -> StoredEvidence:
        self._lock_account(session, opening.account_id)
        if not opening.complete or not opening.reconciled:
            raise LossStateConflict(
                "incomplete or unreconciled opening cannot be stored"
            )
        self._validate_calendar_opening(opening)
        digest = _digest(
            {
                "account_id": opening.account_id,
                "period_kind": opening.period_kind,
                "period_id": opening.period_id,
                "calendar_version": opening.calendar_version,
                "opened_at": opening.opened_at,
                "opening_equity": opening.opening_equity,
                "opening_nlv": opening.opening_nlv,
                "base_currency": opening.base_currency,
                "source_id": opening.source_id,
                "source_epoch": opening.source_epoch,
                "source_sequence": opening.source_sequence,
                "immutable_version": opening.immutable_version,
                "provenance_ref": opening.provenance_ref,
            }
        )
        identity = (
            LossPeriodOpeningRecord.account_id == opening.account_id,
            LossPeriodOpeningRecord.period_kind == opening.period_kind.value,
            LossPeriodOpeningRecord.period_id == opening.period_id,
        )
        existing = session.scalar(select(LossPeriodOpeningRecord).where(*identity))
        if existing is not None:
            return self._existing_evidence(existing.id, existing.digest, digest)
        record = LossPeriodOpeningRecord(
            id=uuid4(),
            account_id=opening.account_id,
            period_kind=opening.period_kind.value,
            period_id=opening.period_id,
            calendar_version=opening.calendar_version,
            opened_at=opening.opened_at,
            opening_equity=opening.opening_equity,
            opening_nlv=opening.opening_nlv,
            base_currency=opening.base_currency,
            source_id=opening.source_id,
            source_epoch=opening.source_epoch,
            source_sequence=opening.source_sequence,
            immutable_version=opening.immutable_version,
            complete=opening.complete,
            reconciled=opening.reconciled,
            provenance_ref=opening.provenance_ref,
            digest=digest,
        )
        session.add(record)
        session.flush()
        return StoredEvidence(record.id, digest, False)

    def append_evaluation(
        self,
        session: Session,
        *,
        evaluation: LossAccountingEvaluation,
        valuation_id: UUID,
        daily_opening_id: UUID,
        weekly_opening_id: UUID,
        daily_flow_ids: tuple[UUID, ...],
        weekly_flow_ids: tuple[UUID, ...],
        daily_period_id: str,
        weekly_period_id: str,
        input_digest: str,
        created_at: datetime,
        correction_of_state_version: int | None = None,
    ) -> tuple[LossStateEventRecord, ...]:
        account_id = evaluation.account_id
        if account_id is None:
            raise LossStateConflict("evaluation has no account identity")
        self._lock_account(session, account_id)
        if (
            not evaluation.available
            or evaluation.daily_net_pnl is None
            or evaluation.weekly_net_pnl is None
            or evaluation.daily_r is None
            or evaluation.weekly_r is None
        ):
            raise LossStateConflict("unavailable evaluation cannot create loss events")
        if (
            created_at.tzinfo is None
            or created_at.utcoffset() != timedelta(0)
            or len(input_digest) != 64
            or any(character not in "0123456789abcdef" for character in input_digest)
        ):
            raise LossStateConflict("event timestamp or input digest is invalid")

        valuation = self._require_owned_record(
            session, LossValuationObservationRecord, valuation_id, account_id
        )
        daily_opening = self._require_owned_record(
            session, LossPeriodOpeningRecord, daily_opening_id, account_id
        )
        weekly_opening = self._require_owned_record(
            session, LossPeriodOpeningRecord, weekly_opening_id, account_id
        )
        self._validate_opening(daily_opening, PeriodKind.DAILY, daily_period_id)
        self._validate_opening(weekly_opening, PeriodKind.WEEKLY, weekly_period_id)
        if not valuation.complete or not valuation.reconciled:
            raise LossStateConflict("valuation is not complete and reconciled")
        if (
            valuation.as_of < daily_opening.opened_at
            or valuation.as_of < weekly_opening.opened_at
        ):
            raise LossStateConflict("valuation precedes a period opening")
        for opening in (daily_opening, weekly_opening):
            if (
                opening.source_id != valuation.source_id
                or opening.source_epoch != valuation.source_epoch
                or opening.source_sequence > valuation.source_sequence
                or opening.base_currency != valuation.base_currency
            ):
                raise LossStateConflict(
                    "valuation and opening source watermarks conflict"
                )
        daily_flows = self._require_flow_evidence(session, account_id, daily_flow_ids)
        weekly_flows = self._require_flow_evidence(session, account_id, weekly_flow_ids)
        weekly_flows_by_id = {flow.id: flow.digest for flow in weekly_flows}
        if any(weekly_flows_by_id.get(flow.id) != flow.digest for flow in daily_flows):
            raise LossStateConflict(
                "daily flow evidence is not included in weekly evidence"
            )
        flow_evidence = {
            "daily": [
                {"id": str(flow.id), "digest": flow.digest} for flow in daily_flows
            ],
            "weekly": [
                {"id": str(flow.id), "digest": flow.digest} for flow in weekly_flows
            ],
        }

        prior = self.rebuild_projection(
            session,
            account_id=account_id,
            active_daily_period_id=daily_period_id,
            active_weekly_period_id=weekly_period_id,
        )
        daily_locked = evaluation.daily_threshold_crossed or (
            prior.active_daily_period_id == daily_period_id and prior.daily_locked
        )
        weekly_locked = evaluation.weekly_threshold_crossed or (
            prior.active_weekly_period_id == weekly_period_id and prior.weekly_locked
        )
        common_digest = _digest(
            {
                "input_digest": input_digest,
                "policy_version": ATS009_POLICY_VERSION,
                "valuation_digest": valuation.digest,
                "daily_opening_digest": daily_opening.digest,
                "weekly_opening_digest": weekly_opening.digest,
                "flow_evidence": flow_evidence,
                "daily_period_id": daily_period_id,
                "weekly_period_id": weekly_period_id,
                "daily_net_pnl": evaluation.daily_net_pnl,
                "weekly_net_pnl": evaluation.weekly_net_pnl,
                "daily_r": evaluation.daily_r,
                "weekly_r": evaluation.weekly_r,
                "daily_locked": daily_locked,
                "weekly_locked": weekly_locked,
                "correction_of_state_version": correction_of_state_version,
            }
        )
        observed_key = f"observation:{common_digest}"
        existing_observed = self._existing_event(session, account_id, observed_key)
        if existing_observed is not None:
            if existing_observed.input_digest != common_digest:
                raise LossStateConflict("conflicting state observation replay")
            return (existing_observed,)

        event_type = (
            LossStateEventType.CORRECTION
            if correction_of_state_version is not None
            else LossStateEventType.OBSERVED
        )
        if correction_of_state_version is not None:
            self._require_state_version(
                session, account_id, correction_of_state_version
            )
        observed = self._new_state_event(
            session,
            account_id=account_id,
            event_type=event_type,
            event_key=observed_key,
            daily_period_id=daily_period_id,
            weekly_period_id=weekly_period_id,
            daily_locked=daily_locked,
            weekly_locked=weekly_locked,
            valuation_id=valuation_id,
            daily_opening_id=daily_opening_id,
            weekly_opening_id=weekly_opening_id,
            evaluation=evaluation,
            flow_evidence=flow_evidence,
            input_digest=common_digest,
            created_at=created_at,
            correction_of_state_version=correction_of_state_version,
        )
        events = [observed]

        newly_locked_daily = evaluation.daily_threshold_crossed and not (
            prior.active_daily_period_id == daily_period_id and prior.daily_locked
        )
        newly_locked_weekly = evaluation.weekly_threshold_crossed and not (
            prior.active_weekly_period_id == weekly_period_id and prior.weekly_locked
        )
        if newly_locked_daily or newly_locked_weekly:
            lock_key = f"lock:{daily_period_id}:{weekly_period_id}:{common_digest}"
            locked = self._new_state_event(
                session,
                account_id=account_id,
                event_type=LossStateEventType.LOCKED,
                event_key=lock_key,
                daily_period_id=daily_period_id,
                weekly_period_id=weekly_period_id,
                daily_locked=daily_locked,
                weekly_locked=weekly_locked,
                valuation_id=valuation_id,
                daily_opening_id=daily_opening_id,
                weekly_opening_id=weekly_opening_id,
                evaluation=evaluation,
                flow_evidence=flow_evidence,
                input_digest=common_digest,
                created_at=created_at,
                correction_of_state_version=correction_of_state_version,
            )
            events.append(locked)
        session.flush()
        return tuple(events)

    def append_correction(
        self,
        session: Session,
        **kwargs: Any,
    ) -> tuple[LossStateEventRecord, ...]:
        """Append correction evidence; projections retain all previously set locks."""
        if kwargs.get("correction_of_state_version") is None:
            raise LossStateConflict(
                "correction must identify the state version corrected"
            )
        return self.append_evaluation(session, **kwargs)

    def rebuild_projection(
        self,
        session: Session,
        *,
        account_id: UUID,
        active_daily_period_id: str,
        active_weekly_period_id: str,
    ) -> LossStateProjection:
        self._lock_account(session, account_id)
        events = session.scalars(
            select(LossStateEventRecord)
            .where(LossStateEventRecord.account_id == account_id)
            .order_by(LossStateEventRecord.state_version)
        ).all()
        openings = session.scalars(
            select(LossPeriodOpeningRecord).where(
                LossPeriodOpeningRecord.account_id == account_id,
                (
                    (LossPeriodOpeningRecord.period_kind == PeriodKind.DAILY.value)
                    & (LossPeriodOpeningRecord.period_id == active_daily_period_id)
                )
                | (
                    (LossPeriodOpeningRecord.period_kind == PeriodKind.WEEKLY.value)
                    & (LossPeriodOpeningRecord.period_id == active_weekly_period_id)
                ),
            )
        ).all()
        present_openings = {
            (opening.period_kind, opening.period_id): opening for opening in openings
        }
        if any(
            key not in present_openings
            or not present_openings[key].complete
            or not present_openings[key].reconciled
            for key in (
                (PeriodKind.DAILY.value, active_daily_period_id),
                (PeriodKind.WEEKLY.value, active_weekly_period_id),
            )
        ):
            raise LossStateConflict(
                "valid daily and weekly opening evidence is required"
            )
        daily_locked_periods: set[str] = set()
        weekly_locked_periods: set[str] = set()
        expected_version = 1
        for event in events:
            if event.state_version != expected_version:
                raise LossStateConflict("loss-state event versions are not contiguous")
            expected_version += 1
            if event.daily_locked:
                daily_locked_periods.add(event.daily_period_id)
            if event.weekly_locked:
                weekly_locked_periods.add(event.weekly_period_id)
        return LossStateProjection(
            account_id=account_id,
            state_version=expected_version - 1,
            active_daily_period_id=active_daily_period_id,
            active_weekly_period_id=active_weekly_period_id,
            daily_locked=active_daily_period_id in daily_locked_periods,
            weekly_locked=active_weekly_period_id in weekly_locked_periods,
        )

    @staticmethod
    def _existing_evidence(
        record_id: UUID, existing_digest: str, incoming_digest: str
    ) -> StoredEvidence:
        if existing_digest != incoming_digest:
            raise LossStateConflict(
                "conflicting replay for an existing source identity"
            )
        return StoredEvidence(record_id, existing_digest, True)

    @staticmethod
    def _require_next_sequence(
        session: Session,
        record_type: type[LossValuationObservationRecord | LossExternalFlowRecord],
        account_id: UUID,
        source_id: str,
        source_epoch: str,
        sequence: int,
    ) -> None:
        latest = session.scalar(
            select(func.max(record_type.source_sequence)).where(
                record_type.account_id == account_id,
                record_type.source_id == source_id,
                record_type.source_epoch == source_epoch,
            )
        )
        if latest is not None and sequence <= latest:
            raise LossStateConflict("source sequence regressed")

    @staticmethod
    def _require_owned_record(
        session: Session,
        record_type: type[Any],
        record_id: UUID,
        account_id: UUID,
    ) -> Any:
        record = session.scalar(
            select(record_type).where(
                record_type.id == record_id,
                record_type.account_id == account_id,
            )
        )
        if record is None:
            raise LossStateConflict(
                "referenced evidence is missing or belongs to another account"
            )
        return record

    @staticmethod
    def _validate_calendar_opening(opening: PeriodOpeningEquity) -> None:
        from src.ats009_calendar import XNYSCalendar

        resolved = XNYSCalendar().resolve(opening.opened_at)
        if resolved is None or resolved.calendar_version != opening.calendar_version:
            raise LossStateConflict("opening calendar identity is unsupported")
        if opening.period_kind is PeriodKind.DAILY:
            expected_period_id = resolved.session_id
        elif opening.period_kind is PeriodKind.WEEKLY:
            expected_period_id = resolved.week_id
        if opening.period_id != expected_period_id:
            raise LossStateConflict(
                "opening period ID does not match the XNYS calendar"
            )

    @staticmethod
    def _validate_opening(
        record: LossPeriodOpeningRecord, kind: PeriodKind, period_id: str
    ) -> None:
        if (
            record.period_kind != kind.value
            or record.period_id != period_id
            or not record.complete
            or not record.reconciled
            or record.opening_equity <= 0
            or record.calendar_version != "exchange-calendars:4.13.1:XNYS"
        ):
            raise LossStateConflict("valid period-opening evidence is required")

    @staticmethod
    def _existing_event(
        session: Session, account_id: UUID, event_key: str
    ) -> LossStateEventRecord | None:
        return session.scalar(
            select(LossStateEventRecord).where(
                LossStateEventRecord.account_id == account_id,
                LossStateEventRecord.event_key == event_key,
            )
        )

    def _new_state_event(
        self,
        session: Session,
        *,
        account_id: UUID,
        event_type: LossStateEventType,
        event_key: str,
        daily_period_id: str,
        weekly_period_id: str,
        daily_locked: bool,
        weekly_locked: bool,
        valuation_id: UUID,
        daily_opening_id: UUID,
        weekly_opening_id: UUID,
        evaluation: LossAccountingEvaluation,
        flow_evidence: dict[str, Any],
        input_digest: str,
        created_at: datetime,
        correction_of_state_version: int | None,
    ) -> LossStateEventRecord:
        latest_version = session.scalar(
            select(func.max(LossStateEventRecord.state_version)).where(
                LossStateEventRecord.account_id == account_id
            )
        )
        next_version = (latest_version or 0) + 1
        daily_pnl_num, daily_pnl_den = _fraction_parts(evaluation.daily_net_pnl)
        weekly_pnl_num, weekly_pnl_den = _fraction_parts(evaluation.weekly_net_pnl)
        daily_r_num, daily_r_den = _fraction_parts(evaluation.daily_r)
        weekly_r_num, weekly_r_den = _fraction_parts(evaluation.weekly_r)
        event = LossStateEventRecord(
            id=uuid4(),
            account_id=account_id,
            state_version=next_version,
            policy_version=ATS009_POLICY_VERSION,
            event_type=event_type.value,
            event_key=event_key,
            daily_period_id=daily_period_id,
            weekly_period_id=weekly_period_id,
            daily_locked=daily_locked,
            weekly_locked=weekly_locked,
            valuation_observation_id=valuation_id,
            daily_opening_id=daily_opening_id,
            weekly_opening_id=weekly_opening_id,
            daily_pnl_numerator=daily_pnl_num,
            daily_pnl_denominator=daily_pnl_den,
            weekly_pnl_numerator=weekly_pnl_num,
            weekly_pnl_denominator=weekly_pnl_den,
            daily_r_numerator=daily_r_num,
            daily_r_denominator=daily_r_den,
            weekly_r_numerator=weekly_r_num,
            weekly_r_denominator=weekly_r_den,
            flow_evidence=flow_evidence,
            input_digest=input_digest,
            correction_of_state_version=correction_of_state_version,
            created_at=created_at.astimezone(UTC),
        )
        session.add(event)
        session.flush()
        return event

    @staticmethod
    def _require_flow_evidence(
        session: Session, account_id: UUID, flow_ids: tuple[UUID, ...]
    ) -> tuple[LossExternalFlowRecord, ...]:
        if len(flow_ids) != len(set(flow_ids)):
            raise LossStateConflict("duplicate flow evidence reference")
        records: list[LossExternalFlowRecord] = []
        for flow_id in flow_ids:
            record = LossStateRepository._require_owned_record(
                session, LossExternalFlowRecord, flow_id, account_id
            )
            records.append(record)
        return tuple(records)

    @staticmethod
    def _require_state_version(
        session: Session, account_id: UUID, state_version: int
    ) -> LossStateEventRecord:
        event = session.scalar(
            select(LossStateEventRecord).where(
                LossStateEventRecord.account_id == account_id,
                LossStateEventRecord.state_version == state_version,
            )
        )
        if event is None:
            raise LossStateConflict("correction target state version does not exist")
        return event

    @staticmethod
    def _lock_account(session: Session, account_id: UUID) -> None:
        if not session.in_transaction():
            raise RuntimeError("loss-state writes require a caller-owned transaction")
        if session.get_bind().dialect.name != "postgresql":
            raise RuntimeError("ATS-009 loss-state persistence requires PostgreSQL")
        isolation = session.scalar(text("SHOW transaction_isolation"))
        if isolation != "read committed":
            raise RuntimeError("ATS-009 writes require READ COMMITTED isolation")
        session.execute(
            select(
                func.pg_advisory_xact_lock(func.hashtextextended(str(account_id), 0))
            )
        )
