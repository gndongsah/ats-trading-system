"""F11 simulation-only opening capture; no broker adapter or runtime route.

Candidates and openings commit atomically. A post-commit model-clock receipt
makes them eligible for the simulation provider. Receipt failure leaves a
quarantined candidate, never a usable capture. This is not authentication.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Literal, Protocol
from uuid import NAMESPACE_URL, UUID, uuid5

from pydantic import Field, field_validator, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.ats009_accounting import (
    PeriodKind,
    PeriodOpeningEquity,
    _utc_timestamp,
)
from src.ats009_calendar import XNYSCalendar
from src.domain.evidence import SourceDocument
from src.domain.models import ImmutableModel
from src.persistence.evidence_repository import EvidenceRepository
from src.persistence.loss_state_provider import RepositoryLossLockProvider
from src.persistence.loss_state_repository import (
    LossStateConflict,
    LossStateRepository,
    _digest,
)
from src.persistence.models import (
    LossPeriodOpeningRecord,
    LossStateEventRecord,
    LossValuationObservationRecord,
    SourceDocumentRecord,
)
from src.simulation_ledger import SOURCE, SimulationLedger

CAPTURE_POLICY = "ATS-009F10-SIM-v0.1"


class CaptureUnavailable(ValueError):
    """No usable simulation opening; new risk must be denied."""


class ModelClock(Protocol):
    def now(self) -> datetime: ...
    def monotonic(self) -> float: ...


class SealedLedger(ImmutableModel):
    """Explicit complete harness cutoff; all same-time entries are included."""

    ledger: SimulationLedger
    cutoff: datetime
    complete: Literal[True]
    mode: Literal["CAPTURE", "HISTORICAL_REPLAY"] = "CAPTURE"

    @field_validator("cutoff")
    @classmethod
    def utc(cls, value: datetime) -> datetime:
        return _utc_timestamp(value)

    @model_validator(mode="after")
    def prefix_only(self):
        if self.cutoff < self.ledger.opened_at or any(
            e.occurred_at > self.cutoff for e in self.ledger.entries
        ):
            raise ValueError(
                "sealed ledger must contain only the complete cutoff prefix"
            )
        return self

    @property
    def digest(self) -> str:
        return _digest(self.model_dump())


def seal(
    ledger: SimulationLedger, cutoff: datetime, *, replay: bool = False
) -> SealedLedger:
    """Harness attests completeness; sealing cannot prove upstream ingestion."""
    ledger = SimulationLedger.model_validate(ledger.model_dump())
    return SealedLedger(
        ledger=SimulationLedger(
            account_id=ledger.account_id,
            opened_at=ledger.opened_at,
            opening_nlv=ledger.opening_nlv,
            entries=tuple(e for e in ledger.entries if e.occurred_at <= cutoff),
        ),
        cutoff=cutoff,
        complete=True,
        mode="HISTORICAL_REPLAY" if replay else "CAPTURE",
    )


class CaptureFacts(ImmutableModel):
    source: Literal["SIMULATION-ONLY"] = SOURCE
    policy: Literal["ATS-009F10-SIM-v0.1"] = CAPTURE_POLICY
    opening: PeriodOpeningEquity
    seal_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    watermark: int = Field(ge=1)
    authority_revision: str = Field(min_length=1)
    observed_at: datetime

    @field_validator("observed_at")
    @classmethod
    def utc(cls, value: datetime) -> datetime:
        return _utc_timestamp(value)

    @model_validator(mode="after")
    def simulation_only(self):
        o = self.opening
        if (
            o.source_id != SOURCE
            or o.source_epoch != SOURCE
            or o.provenance_ref != SOURCE
            or not o.complete
            or not o.reconciled
            or o.base_currency != "USD"
            or o.opening_equity != o.opening_nlv
            or o.source_sequence != self.watermark
            or not timedelta(0)
            <= self.observed_at - o.opened_at
            <= timedelta(seconds=10)
        ):
            raise ValueError("invalid simulation capture facts")
        return self


class CompletionReceipt(ImmutableModel):
    capture_id: UUID
    facts_digest: str
    completed_at: datetime
    elapsed_seconds: Decimal = Field(ge=0, allow_inf_nan=False)
    accepted: bool

    @field_validator("completed_at")
    @classmethod
    def utc(cls, value: datetime) -> datetime:
        return _utc_timestamp(value)


def _capture_key(account: UUID, kind: PeriodKind, period: str) -> str:
    return f"sim-opening:{account}:{kind.value}:{period}"


def _load(session: Session, key: str) -> SourceDocument | None:
    return EvidenceRepository(session).get_source_version(key, 1)


def _write(
    session: Session, key: str, kind: str, payload: dict, at: datetime
) -> SourceDocument:
    document = SourceDocument(
        id=uuid5(NAMESPACE_URL, key),
        source_id=key,
        source_type=kind,
        publisher=SOURCE,
        subject=SOURCE,
        published_at=at,
        observed_at=at,
        content_hash=_digest(payload),
        version_metadata={"payload": json.dumps(payload, sort_keys=True)},
    )
    EvidenceRepository(session).save_source(document)
    return document


def _payload(document: SourceDocument) -> dict:
    payload = json.loads(document.version_metadata["payload"])
    if document.publisher != SOURCE or document.content_hash != _digest(payload):
        raise CaptureUnavailable("invalid simulation evidence envelope")
    return payload


class SimulationOpeningCapture:
    """Owns transactions; only supplies evidence to simulation consumers."""

    def __init__(self, calendar: XNYSCalendar, clock: ModelClock):
        self.calendar, self.clock = calendar, clock

    def capture(
        self, factory: Callable[[], Session], sealed: SealedLedger, kind: PeriodKind
    ) -> PeriodOpeningEquity:
        sealed = SealedLedger.model_validate(sealed.model_dump())
        kind = PeriodKind(kind)
        if sealed.mode != "CAPTURE":
            raise CaptureUnavailable("historical replay cannot qualify as capture")
        start_mono = self.clock.monotonic()
        observed = _utc_timestamp(self.clock.now())
        resolved = self.calendar.resolve(sealed.cutoff)
        if (
            resolved is None
            or not resolved.schedule_verified
            or not resolved.authority_revision
        ):
            raise CaptureUnavailable("simulation schedule authority missing")
        if not resolved.authority_revision.startswith(SOURCE):
            raise CaptureUnavailable("explicit simulation schedule authority required")
        boundary = resolved.open_utc
        if kind is PeriodKind.WEEKLY:
            first_day = datetime.fromisoformat(
                resolved.week_id.rsplit(":", 1)[1]
            ).date()
            boundary = self.calendar.calendar.session_open(
                str(first_day)
            ).to_pydatetime()
        if sealed.cutoff != boundary:
            raise CaptureUnavailable("source cutoff is not the exact period opening")
        period = resolved.session_id if kind is PeriodKind.DAILY else resolved.week_id
        key = _capture_key(sealed.ledger.account_id, kind, period)
        valuation = sealed.ledger.valuation(boundary)
        opening = PeriodOpeningEquity(
            account_id=valuation.account_id,
            period_kind=kind,
            period_id=period,
            calendar_version=resolved.calendar_version,
            opened_at=boundary,
            opening_equity=valuation.net_liquidation,
            opening_nlv=valuation.net_liquidation,
            base_currency="USD",
            source_id=SOURCE,
            source_epoch=SOURCE,
            source_sequence=valuation.source_sequence,
            immutable_version=1,
            complete=True,
            reconciled=True,
            provenance_ref=SOURCE,
        )
        with factory.begin() as session:
            LossStateRepository._lock_account(session, opening.account_id)
            existing = _load(session, key)
            if existing:
                facts = CaptureFacts.model_validate(_payload(existing))
                if (
                    facts.opening != opening
                    or facts.seal_digest != sealed.digest
                    or facts.authority_revision != resolved.authority_revision
                ):
                    raise LossStateConflict("conflicting sealed capture replay")
                receipt = _load(session, f"sim-receipt:{existing.id}")
                if receipt:
                    _validated_receipt(receipt, existing, facts)
                    return facts.opening
            else:
                if not timedelta(0) <= observed - boundary <= timedelta(seconds=10):
                    raise CaptureUnavailable("capture observation outside window")
                facts = CaptureFacts(
                    opening=opening,
                    seal_digest=sealed.digest,
                    watermark=valuation.source_sequence,
                    authority_revision=resolved.authority_revision,
                    observed_at=observed,
                )
                LossStateRepository().append_opening(session, opening)
                existing = _write(
                    session,
                    key,
                    "SIM_OPENING_CANDIDATE",
                    facts.model_dump(mode="json"),
                    observed,
                )
        # Candidate/opening transaction has actually committed before this read.
        completed = _utc_timestamp(self.clock.now())
        elapsed = self.clock.monotonic() - start_mono
        accepted = (
            timedelta(0) <= completed - boundary <= timedelta(seconds=60)
            and completed >= observed
            and 0 <= elapsed <= 60
            and completed - observed >= timedelta(seconds=elapsed)
        )
        receipt = CompletionReceipt(
            capture_id=existing.id,
            facts_digest=existing.content_hash,
            completed_at=completed,
            elapsed_seconds=str(max(elapsed, 0)),
            accepted=accepted,
        )
        with factory.begin() as session:
            LossStateRepository._lock_account(session, opening.account_id)
            receipt_key = f"sim-receipt:{existing.id}"
            prior = _load(session, receipt_key)
            if prior:
                _validated_receipt(prior, existing, facts)
            else:
                _write(
                    session,
                    receipt_key,
                    "SIM_OPENING_RECEIPT",
                    receipt.model_dump(mode="json"),
                    max(completed, observed),
                )
        if not accepted and prior is None:
            raise CaptureUnavailable("capture commit completed outside deadline")
        return opening


def _validated_receipt(
    document: SourceDocument, candidate: SourceDocument, facts: CaptureFacts
) -> CompletionReceipt:
    receipt = CompletionReceipt.model_validate(_payload(document))
    if (
        not receipt.accepted
        or receipt.capture_id != candidate.id
        or receipt.facts_digest != candidate.content_hash
        or not facts.observed_at
        <= receipt.completed_at
        <= facts.opening.opened_at + timedelta(seconds=60)
        or receipt.elapsed_seconds > 60
    ):
        raise CaptureUnavailable("capture receipt is not usable")
    return receipt


def report_correction(
    session: Session,
    opening: PeriodOpeningEquity,
    *,
    correction_id: UUID,
    reason: str,
    candidate_nlv: str,
    discovered_at: datetime,
) -> None:
    """Append-only quarantine, never replaces an opening or clears a lock."""
    discovered_at = _utc_timestamp(discovered_at)
    LossStateRepository._lock_account(session, opening.account_id)
    key = _capture_key(opening.account_id, opening.period_kind, opening.period_id)
    original = _load(session, key)
    if not original or not reason.strip():
        raise CaptureUnavailable("correction needs an existing capture and reason")
    facts = CaptureFacts.model_validate(_payload(original))
    if facts.opening != opening or discovered_at < facts.observed_at:
        raise CaptureUnavailable("correction identity/time mismatch")
    value = Decimal(candidate_nlv)
    if not value.is_finite() or value <= 0:
        raise CaptureUnavailable("invalid proposed correction value")
    payload = {
        "capture_id": str(original.id),
        "original_digest": original.content_hash,
        "account_id": str(opening.account_id),
        "period_id": opening.period_id,
        "period_kind": opening.period_kind.value,
        "reason": reason.strip(),
        "candidate_nlv": str(value),
        "discovered_at": discovered_at.isoformat(),
        "policy": CAPTURE_POLICY,
        "review_status": "PENDING",
        "source": SOURCE,
    }
    _write(
        session,
        f"sim-correction:{opening.account_id}:{correction_id}",
        "SIM_OPENING_CORRECTION",
        payload,
        discovered_at,
    )


class SimulationCaptureLossProvider:
    """Explicit simulation-only provider; no broker/runtime wiring."""

    def __init__(self, calendar: XNYSCalendar):
        self.calendar = calendar

    def evaluate(self, account_id: UUID, at: datetime):
        return None  # a transaction is mandatory

    def evaluate_in_transaction(self, session: Session, account_id: UUID, at: datetime):
        at = _utc_timestamp(at)
        LossStateRepository._lock_account(session, account_id)
        resolved = self.calendar.resolve(at)
        if resolved is None or not resolved.eligible_new_risk:
            return None
        try:
            ids = set()
            opening_ids = {}
            for kind, period in (
                (PeriodKind.DAILY, resolved.session_id),
                (PeriodKind.WEEKLY, resolved.week_id),
            ):
                candidate = _load(session, _capture_key(account_id, kind, period))
                if candidate is None:
                    return None
                facts = CaptureFacts.model_validate(_payload(candidate))
                receipt = _load(session, f"sim-receipt:{candidate.id}")
                if receipt is None:
                    return None
                _validated_receipt(receipt, candidate, facts)
                if (
                    facts.opening.account_id != account_id
                    or facts.opening.period_kind != kind
                    or facts.opening.period_id != period
                    or facts.opening.calendar_version != resolved.calendar_version
                ):
                    return None
                row = session.scalar(
                    select(LossPeriodOpeningRecord).where(
                        LossPeriodOpeningRecord.account_id == account_id,
                        LossPeriodOpeningRecord.period_kind == kind.value,
                        LossPeriodOpeningRecord.period_id == period,
                    )
                )
                if row is None or row.digest != _digest(
                    facts.opening.model_dump(exclude={"complete", "reconciled"})
                ):
                    return None
                opening_ids[kind] = row.id
                ids.add(str(candidate.id))
            corrections = session.scalars(
                select(SourceDocumentRecord).where(
                    SourceDocumentRecord.source_id.like(
                        f"sim-correction:{account_id}:%"
                    )
                )
            )
            for record in corrections:
                correction = _payload(SourceDocument.model_validate(record.source_data))
                if correction["capture_id"] in ids:
                    return None
            state = RepositoryLossLockProvider(
                calendar=self.calendar
            ).evaluate_in_transaction(session, account_id, at)
            if state is None or not timedelta(0) <= at - state.as_of <= timedelta(
                seconds=30
            ):
                return None
            event = session.scalar(
                select(LossStateEventRecord).where(
                    LossStateEventRecord.account_id == account_id,
                    LossStateEventRecord.state_version == state.state_version,
                )
            )
            if (
                event is None
                or event.daily_opening_id != opening_ids[PeriodKind.DAILY]
                or event.weekly_opening_id != opening_ids[PeriodKind.WEEKLY]
            ):
                return None
            valuation = session.get(
                LossValuationObservationRecord, event.valuation_observation_id
            )
            if (
                valuation is None
                or valuation.source_id != SOURCE
                or valuation.source_epoch != SOURCE
                or valuation.provenance_ref != SOURCE
            ):
                return None
            return state
        except (ValueError, KeyError, TypeError, LossStateConflict):
            return None
