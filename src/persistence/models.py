from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import Uuid

from src.database import Base

JSON_DOCUMENT = JSON().with_variant(JSONB(), "postgresql")


class MarketDataRecord(Base):
    __tablename__ = "ats_market_data"
    __table_args__ = (
        CheckConstraint("revision >= 1", name="ck_ats_market_revision"),
        CheckConstraint("ingested_at >= available_at", name="ck_ats_market_clock"),
        Index(
            "ix_ats_market_replay",
            "source",
            "symbol",
            "stream",
            "event_time",
            "available_at",
            "ingested_at",
        ),
    )

    source: Mapped[str] = mapped_column(String(128), primary_key=True)
    symbol: Mapped[str] = mapped_column(String(32), primary_key=True)
    stream: Mapped[str] = mapped_column(String(32), primary_key=True)
    event_time: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), primary_key=True
    )
    revision: Mapped[int] = mapped_column(Integer, primary_key=True)
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    ingested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    event_data: Mapped[dict[str, Any]] = mapped_column(JSON_DOCUMENT, nullable=False)


class TradeRecord(Base):
    __tablename__ = "ats_trades"

    trade_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)


class ScoreSnapshotRecord(Base):
    __tablename__ = "ats_score_snapshots"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    decided_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    snapshot_data: Mapped[dict[str, Any]] = mapped_column(JSON_DOCUMENT, nullable=False)


class TradePlanRecord(Base):
    __tablename__ = "ats_trade_plans"
    __table_args__ = (
        UniqueConstraint("trade_id", "version", name="uq_ats_plan_trade_version"),
        CheckConstraint("version >= 1", name="ck_ats_plan_version_positive"),
        CheckConstraint(
            "(version = 1 AND supersedes_plan_id IS NULL) OR "
            "(version > 1 AND supersedes_plan_id IS NOT NULL)",
            name="ck_ats_plan_supersession_shape",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    trade_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("ats_trades.trade_id"), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    supersedes_plan_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), nullable=True
    )
    score_snapshot_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("ats_score_snapshots.id"),
        nullable=False,
    )
    planned_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    plan_data: Mapped[dict[str, Any]] = mapped_column(JSON_DOCUMENT, nullable=False)


class OrderIntentRecord(Base):
    __tablename__ = "ats_order_intents"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    trade_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("ats_trades.trade_id"), nullable=False
    )
    plan_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("ats_trade_plans.id"), nullable=False
    )
    order_data: Mapped[dict[str, Any]] = mapped_column(JSON_DOCUMENT, nullable=False)


class FillRecord(Base):
    __tablename__ = "ats_fills"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    trade_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("ats_trades.trade_id"), nullable=False
    )
    order_intent_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("ats_order_intents.id"), nullable=False
    )
    fill_data: Mapped[dict[str, Any]] = mapped_column(JSON_DOCUMENT, nullable=False)


class PositionSnapshotRecord(Base):
    __tablename__ = "ats_position_snapshots"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    trade_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("ats_trades.trade_id"), nullable=False
    )
    plan_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("ats_trade_plans.id"), nullable=False
    )
    snapshot_data: Mapped[dict[str, Any]] = mapped_column(JSON_DOCUMENT, nullable=False)


class TradeResultRecord(Base):
    __tablename__ = "ats_trade_results"
    __table_args__ = (
        UniqueConstraint("plan_id", name="uq_ats_result_plan"),
        CheckConstraint(
            "run_type IN ('LIVE', 'PAPER', 'REPLAY')",
            name="ck_ats_trade_results_run_type",
        ),
        Index("ix_ats_trade_results_run_type", "run_type"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    trade_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("ats_trades.trade_id"), nullable=False
    )
    plan_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("ats_trade_plans.id"), nullable=False
    )
    run_type: Mapped[str] = mapped_column(String(16), nullable=False, default="PAPER")
    result_data: Mapped[dict[str, Any]] = mapped_column(JSON_DOCUMENT, nullable=False)


class JournalEntryRecord(Base):
    __tablename__ = "ats_journal_entries"
    __table_args__ = (
        UniqueConstraint("trade_id", "revision", name="uq_ats_journal_trade_revision"),
        CheckConstraint("revision >= 1", name="ck_ats_journal_revision_positive"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    revision: Mapped[int] = mapped_column(Integer, primary_key=True)
    trade_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("ats_trades.trade_id"), nullable=False
    )
    plan_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("ats_trade_plans.id"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    plan_followed: Mapped[bool | None]
    mood: Mapped[str | None] = mapped_column(String(32), nullable=True)
    lesson: Mapped[str | None] = mapped_column(String, nullable=True)
    thesis: Mapped[str | None] = mapped_column(String, nullable=True)
    entry_data: Mapped[dict[str, Any]] = mapped_column(JSON_DOCUMENT, nullable=False)


class SourceDocumentRecord(Base):
    __tablename__ = "ats_source_documents"
    __table_args__ = (
        UniqueConstraint("source_id", "version", name="uq_ats_source_document_version"),
        CheckConstraint("version >= 1", name="ck_ats_source_document_version_positive"),
        CheckConstraint(
            "available_at >= published_at", name="ck_ats_source_available_clock"
        ),
        CheckConstraint(
            "ingested_at >= available_at", name="ck_ats_source_ingested_clock"
        ),
        Index(
            "ix_ats_source_replay",
            "source_type",
            "subject",
            "published_at",
            "available_at",
            "ingested_at",
            "source_id",
            "version",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    source_id: Mapped[str] = mapped_column(String(255), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    source_type: Mapped[str] = mapped_column(String(64), nullable=False)
    subject: Mapped[str] = mapped_column(String(255), nullable=False)
    published_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    ingested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    source_data: Mapped[dict[str, Any]] = mapped_column(JSON_DOCUMENT, nullable=False)


class EvidenceItemRecord(Base):
    __tablename__ = "ats_evidence_items"
    __table_args__ = (
        ForeignKeyConstraint(
            ["source_document_version_id"],
            ["ats_source_documents.id"],
            name="fk_ats_evidence_source_version",
        ),
        CheckConstraint("version >= 1", name="ck_ats_evidence_version_positive"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    version: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_document_version_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), nullable=False
    )
    evidence_data: Mapped[dict[str, Any]] = mapped_column(JSON_DOCUMENT, nullable=False)


class PortfolioSnapshotRecord(Base):
    __tablename__ = "ats_portfolio_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "account_id", "version", name="uq_ats_portfolio_snapshot_account_version"
        ),
        CheckConstraint("version >= 1", name="ck_ats_portfolio_snapshot_version"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    account_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    as_of: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    snapshot_data: Mapped[dict[str, Any]] = mapped_column(JSON_DOCUMENT, nullable=False)


class RiskReservationEventRecord(Base):
    __tablename__ = "ats_risk_reservation_events"
    __table_args__ = (
        UniqueConstraint(
            "reservation_id", "sequence", name="uq_ats_risk_reservation_sequence"
        ),
        UniqueConstraint(
            "account_id",
            "idempotency_key",
            name="uq_ats_risk_reservation_idempotency",
        ),
        Index(
            "ix_ats_risk_reservation_account",
            "account_id",
            "reservation_id",
            "sequence",
        ),
        CheckConstraint("sequence >= 1", name="ck_ats_risk_reservation_sequence"),
        CheckConstraint(
            "event_type IN ('reserved', 'fill', 'stop_confirmed', 'release')",
            name="ck_ats_risk_reservation_event_type",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    account_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    reservation_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    event_type: Mapped[str] = mapped_column(String(16), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    snapshot_version: Mapped[int] = mapped_column(Integer, nullable=False)
    plan_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("ats_trade_plans.id"), nullable=False
    )
    event_data: Mapped[dict[str, Any]] = mapped_column(JSON_DOCUMENT, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class LossValuationObservationRecord(Base):
    __tablename__ = "ats_loss_valuation_observations"
    __table_args__ = (
        UniqueConstraint(
            "account_id",
            "source_id",
            "source_epoch",
            "source_sequence",
            name="uq_ats_loss_valuation_source_sequence",
        ),
        CheckConstraint("source_sequence > 0", name="ck_ats_loss_valuation_sequence"),
        Index(
            "ix_ats_loss_valuation_account_as_of",
            "account_id",
            "as_of",
            "source_sequence",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    account_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    source_id: Mapped[str] = mapped_column(String(128), nullable=False)
    source_epoch: Mapped[str] = mapped_column(String(128), nullable=False)
    source_sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    as_of: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    base_currency: Mapped[str] = mapped_column(String(3), nullable=False)
    net_liquidation: Mapped[Decimal] = mapped_column(Numeric(), nullable=False)
    complete: Mapped[bool] = mapped_column(nullable=False)
    reconciled: Mapped[bool] = mapped_column(nullable=False)
    provenance_ref: Mapped[str] = mapped_column(String(256), nullable=False)
    digest: Mapped[str] = mapped_column(String(64), nullable=False)
    correction_of_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("ats_loss_valuation_observations.id"),
        nullable=True,
    )


class LossExternalFlowRecord(Base):
    __tablename__ = "ats_loss_external_flows"
    __table_args__ = (
        UniqueConstraint(
            "account_id",
            "source_id",
            "source_epoch",
            "source_sequence",
            name="uq_ats_loss_flow_source_sequence",
        ),
        UniqueConstraint(
            "account_id",
            "source_id",
            "source_epoch",
            "flow_id",
            name="uq_ats_loss_flow_identity",
        ),
        CheckConstraint("source_sequence > 0", name="ck_ats_loss_flow_sequence"),
        CheckConstraint(
            "flow_kind IN ('CONTRIBUTION', 'WITHDRAWAL')",
            name="ck_ats_loss_flow_kind",
        ),
        CheckConstraint(
            "(flow_kind = 'CONTRIBUTION' AND signed_amount > 0) OR "
            "(flow_kind = 'WITHDRAWAL' AND signed_amount < 0)",
            name="ck_ats_loss_flow_sign",
        ),
        Index(
            "ix_ats_loss_flow_account_time",
            "account_id",
            "occurred_at",
            "source_sequence",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    account_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    flow_id: Mapped[str] = mapped_column(String(128), nullable=False)
    source_id: Mapped[str] = mapped_column(String(128), nullable=False)
    source_epoch: Mapped[str] = mapped_column(String(128), nullable=False)
    source_sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    flow_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    signed_amount: Mapped[Decimal] = mapped_column(Numeric(), nullable=False)
    base_currency: Mapped[str] = mapped_column(String(3), nullable=False)
    provenance_ref: Mapped[str] = mapped_column(String(256), nullable=False)
    digest: Mapped[str] = mapped_column(String(64), nullable=False)
    correction_of_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("ats_loss_external_flows.id"),
        nullable=True,
    )


class LossPeriodOpeningRecord(Base):
    __tablename__ = "ats_loss_period_openings"
    __table_args__ = (
        UniqueConstraint(
            "account_id",
            "period_kind",
            "period_id",
            name="uq_ats_loss_opening_period",
        ),
        CheckConstraint(
            "period_kind IN ('DAILY', 'WEEKLY')", name="ck_ats_loss_opening_kind"
        ),
        CheckConstraint("immutable_version >= 1", name="ck_ats_loss_opening_version"),
        CheckConstraint("source_sequence > 0", name="ck_ats_loss_opening_sequence"),
        CheckConstraint("opening_equity > 0", name="ck_ats_loss_opening_equity"),
        Index(
            "ix_ats_loss_opening_account_period",
            "account_id",
            "period_kind",
            "period_id",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    account_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    period_kind: Mapped[str] = mapped_column(String(8), nullable=False)
    period_id: Mapped[str] = mapped_column(String(128), nullable=False)
    calendar_version: Mapped[str] = mapped_column(String(128), nullable=False)
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    opening_equity: Mapped[Decimal] = mapped_column(Numeric(), nullable=False)
    opening_nlv: Mapped[Decimal] = mapped_column(Numeric(), nullable=False)
    base_currency: Mapped[str] = mapped_column(String(3), nullable=False)
    source_id: Mapped[str] = mapped_column(String(128), nullable=False)
    source_epoch: Mapped[str] = mapped_column(String(128), nullable=False)
    source_sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    immutable_version: Mapped[int] = mapped_column(Integer, nullable=False)
    complete: Mapped[bool] = mapped_column(nullable=False)
    reconciled: Mapped[bool] = mapped_column(nullable=False)
    provenance_ref: Mapped[str] = mapped_column(String(256), nullable=False)
    digest: Mapped[str] = mapped_column(String(64), nullable=False)


class LossStateEventRecord(Base):
    __tablename__ = "ats_loss_state_events"
    __table_args__ = (
        UniqueConstraint(
            "account_id",
            "state_version",
            name="uq_ats_loss_state_account_version",
        ),
        UniqueConstraint(
            "account_id",
            "event_key",
            name="uq_ats_loss_state_event_key",
        ),
        CheckConstraint("state_version >= 1", name="ck_ats_loss_state_version"),
        CheckConstraint(
            "event_type IN ('OBSERVED', 'LOCKED', 'CORRECTION')",
            name="ck_ats_loss_state_event_type",
        ),
        Index(
            "ix_ats_loss_state_account_version",
            "account_id",
            "state_version",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    account_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    state_version: Mapped[int] = mapped_column(Integer, nullable=False)
    policy_version: Mapped[str] = mapped_column(String(64), nullable=False)
    event_type: Mapped[str] = mapped_column(String(16), nullable=False)
    event_key: Mapped[str] = mapped_column(String(256), nullable=False)
    daily_period_id: Mapped[str] = mapped_column(String(128), nullable=False)
    weekly_period_id: Mapped[str] = mapped_column(String(128), nullable=False)
    daily_locked: Mapped[bool] = mapped_column(nullable=False)
    weekly_locked: Mapped[bool] = mapped_column(nullable=False)
    valuation_observation_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("ats_loss_valuation_observations.id"),
        nullable=False,
    )
    daily_opening_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("ats_loss_period_openings.id"),
        nullable=False,
    )
    weekly_opening_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("ats_loss_period_openings.id"),
        nullable=False,
    )
    daily_pnl_numerator: Mapped[str] = mapped_column(String(4096), nullable=False)
    daily_pnl_denominator: Mapped[str] = mapped_column(String(4096), nullable=False)
    weekly_pnl_numerator: Mapped[str] = mapped_column(String(4096), nullable=False)
    weekly_pnl_denominator: Mapped[str] = mapped_column(String(4096), nullable=False)
    daily_r_numerator: Mapped[str] = mapped_column(String(4096), nullable=False)
    daily_r_denominator: Mapped[str] = mapped_column(String(4096), nullable=False)
    weekly_r_numerator: Mapped[str] = mapped_column(String(4096), nullable=False)
    weekly_r_denominator: Mapped[str] = mapped_column(String(4096), nullable=False)
    flow_evidence: Mapped[dict[str, Any]] = mapped_column(JSON_DOCUMENT, nullable=False)
    input_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    correction_of_state_version: Mapped[int | None] = mapped_column(
        Integer, nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class TriageExperimentRecord(Base):
    __tablename__ = "ats_triage_experiments"
    __table_args__ = (
        UniqueConstraint(
            "experiment_id",
            "case_id",
            "arm",
            name="uq_ats_triage_experiment_case_arm",
        ),
        CheckConstraint(
            "arm IN ('llm_only', 'llm_jev')",
            name="ck_ats_triage_experiment_arm",
        ),
        CheckConstraint(
            "probability >= 0 AND probability <= 1",
            name="ck_ats_triage_probability",
        ),
        Index(
            "ix_ats_triage_experiment_lookup",
            "experiment_id",
            "case_id",
            "arm",
            "observed_at",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    experiment_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    case_id: Mapped[str] = mapped_column(String(255), nullable=False)
    subject: Mapped[str] = mapped_column(String(255), nullable=False)
    arm: Mapped[str] = mapped_column(String(16), nullable=False)
    predicted_label: Mapped[str] = mapped_column(String(128), nullable=False)
    probability: Mapped[Decimal] = mapped_column(Numeric(8, 7), nullable=False)
    model_name: Mapped[str] = mapped_column(String(128), nullable=False)
    model_version: Mapped[str | None] = mapped_column(String(128), nullable=True)
    observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    outcome_label: Mapped[str | None] = mapped_column(String(128), nullable=True)
    outcome_resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    prediction_data: Mapped[dict[str, Any]] = mapped_column(
        JSON_DOCUMENT, nullable=False
    )


class AgentEvaluationRecord(Base):
    __tablename__ = "ats_agent_evaluations"
    __table_args__ = (
        CheckConstraint(
            "arm IN ('llm_only', 'llm_jev')",
            name="ck_ats_agent_evaluation_arm",
        ),
        Index(
            "ix_ats_agent_evaluation_lookup",
            "experiment_id",
            "arm",
            "positive_label",
            "as_of",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    experiment_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    arm: Mapped[str] = mapped_column(String(16), nullable=False)
    positive_label: Mapped[str] = mapped_column(String(128), nullable=False)
    evaluated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    as_of: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    observations: Mapped[int] = mapped_column(Integer, nullable=False)
    summary_data: Mapped[dict[str, Any]] = mapped_column(JSON_DOCUMENT, nullable=False)


class ForwardTestRunRecord(Base):
    __tablename__ = "ats_forward_test_runs"
    __table_args__ = (
        CheckConstraint(
            "run_type = 'PAPER'",
            name="ck_ats_forward_test_run_type",
        ),
        CheckConstraint(
            "status IN ('BLOCKED', 'ACCEPTED', 'FILLED')",
            name="ck_ats_forward_test_status",
        ),
        Index(
            "ix_ats_forward_test_as_of",
            "as_of",
            "status",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    proposal_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    run_type: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    as_of: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    playbook_version: Mapped[str] = mapped_column(String(64), nullable=False)
    playbook_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    run_data: Mapped[dict[str, Any]] = mapped_column(JSON_DOCUMENT, nullable=False)
