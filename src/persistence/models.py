from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import Uuid

from src.database import Base

JSON_DOCUMENT = JSON().with_variant(JSONB(), "postgresql")


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
    __table_args__ = (UniqueConstraint("plan_id", name="uq_ats_result_plan"),)

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    trade_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("ats_trades.trade_id"), nullable=False
    )
    plan_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("ats_trade_plans.id"), nullable=False
    )
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
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    source_id: Mapped[str] = mapped_column(String(255), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
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
