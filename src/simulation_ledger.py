"""Synthetic NLV-delta ledger for offline loss-accounting development only.

PNL entries include realized/marked P&L and fees as explicit signed deltas.
This is not a fill engine or a broker valuation adapter. Never pass these
synthetic contracts to a broker-backed authorization service.
"""

from datetime import datetime
from decimal import Decimal, localcontext
from enum import StrEnum
from fractions import Fraction
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from src.ats009_accounting import (
    ExactDecimal,
    ExternalFlowKind,
    ExternalFlowReconciliation,
    PeriodOpeningEquity,
    ReconciledExternalFlow,
    VerifiedAccountValuation,
    _utc_timestamp,
)
from src.domain.models import ImmutableModel

SOURCE = "SIMULATION-ONLY"


class EntryKind(StrEnum):
    PNL = "PNL"
    EXTERNAL_FLOW = "EXTERNAL_FLOW"


class SimulationEntry(ImmutableModel):
    event_id: str = Field(min_length=1, max_length=128)
    occurred_at: datetime
    kind: EntryKind
    amount: ExactDecimal

    @field_validator("occurred_at")
    @classmethod
    def utc(cls, value: datetime) -> datetime:
        return _utc_timestamp(value)

    @field_validator("amount")
    @classmethod
    def finite(cls, value: Decimal) -> Decimal:
        if not value.is_finite() or value == 0:
            raise ValueError("entry amount must be finite and nonzero")
        return value


class SimulationLedger(ImmutableModel):
    account_id: UUID
    opened_at: datetime
    opening_nlv: ExactDecimal
    entries: tuple[SimulationEntry, ...] = ()

    @field_validator("opened_at")
    @classmethod
    def utc(cls, value: datetime) -> datetime:
        return _utc_timestamp(value)

    @field_validator("opening_nlv")
    @classmethod
    def positive(cls, value: Decimal) -> Decimal:
        if not value.is_finite() or value <= 0:
            raise ValueError("opening NLV must be finite and positive")
        return value

    @model_validator(mode="after")
    def ordered_unique_entries(self) -> "SimulationLedger":
        seen: set[str] = set()
        last_at = self.opened_at
        for entry in self.entries:
            if (
                entry.event_id in seen
                or entry.occurred_at < last_at
                or entry.occurred_at <= self.opened_at
            ):
                raise ValueError("entries must be unique, ordered, and after opening")
            seen.add(entry.event_id)
            last_at = entry.occurred_at
        return self

    def append(self, entry: SimulationEntry) -> "SimulationLedger":
        entry = SimulationEntry.model_validate(entry.model_dump())
        for previous in self.entries:
            if previous.event_id == entry.event_id:
                if previous != entry:
                    raise ValueError("conflicting event identity")
                return self
        last_at = self.entries[-1].occurred_at if self.entries else self.opened_at
        if entry.occurred_at < last_at or entry.occurred_at <= self.opened_at:
            raise ValueError("entry must follow opening and preserve event order")
        return SimulationLedger(
            account_id=self.account_id,
            opened_at=self.opened_at,
            opening_nlv=self.opening_nlv,
            entries=(*self.entries, entry),
        )

    def valuation(self, as_of: datetime) -> VerifiedAccountValuation:
        as_of = _utc_timestamp(as_of)
        if as_of < self.opened_at:
            raise ValueError("valuation precedes opening")
        entries = tuple(e for e in self.entries if e.occurred_at <= as_of)
        total = Fraction(self.opening_nlv) + sum(
            (Fraction(e.amount) for e in entries), Fraction(0)
        )
        with localcontext() as context:
            context.prec = (
                len(str(abs(total.numerator))) + len(str(total.denominator)) + 2
            )
            nlv = Decimal(total.numerator) / Decimal(total.denominator)
        return VerifiedAccountValuation(
            account_id=self.account_id,
            source_id=SOURCE,
            source_epoch=SOURCE,
            source_sequence=len(entries) + 1,
            as_of=as_of,
            base_currency="USD",
            net_liquidation=nlv,
            complete=True,
            reconciled=True,
            provenance_ref=SOURCE,
        )

    def flows(
        self, opening: PeriodOpeningEquity, as_of: datetime
    ) -> ExternalFlowReconciliation:
        as_of = _utc_timestamp(as_of)
        if opening.account_id != self.account_id or opening.source_id != SOURCE:
            raise ValueError("simulation opening must belong to this ledger")
        if opening.opened_at < self.opened_at or as_of < opening.opened_at:
            raise ValueError("flow window is outside ledger history")
        entries = tuple(e for e in self.entries if e.occurred_at <= as_of)
        flows = tuple(
            ReconciledExternalFlow(
                account_id=self.account_id,
                flow_id=e.event_id,
                source_id=SOURCE,
                source_epoch=SOURCE,
                source_sequence=index,
                occurred_at=e.occurred_at,
                kind=(
                    ExternalFlowKind.CONTRIBUTION
                    if e.amount > 0
                    else ExternalFlowKind.WITHDRAWAL
                ),
                signed_amount=e.amount,
                base_currency="USD",
                provenance_ref=SOURCE,
            )
            for index, e in enumerate(entries, start=1)
            if e.kind is EntryKind.EXTERNAL_FLOW and e.occurred_at > opening.opened_at
        )
        return ExternalFlowReconciliation(
            account_id=self.account_id,
            period_kind=opening.period_kind,
            period_id=opening.period_id,
            window_start=opening.opened_at,
            as_of=as_of,
            base_currency="USD",
            source_id=SOURCE,
            source_epoch=SOURCE,
            source_sequence=len(entries) + 1,
            reconciliation_id=SOURCE,
            flows=flows,
            complete=True,
            reconciled=True,
            provenance_ref=SOURCE,
        )
