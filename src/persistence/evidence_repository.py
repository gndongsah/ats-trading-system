from datetime import datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from src.domain.evidence import EvidenceItem, SourceDocument
from src.domain.models import _require_utc
from src.persistence.models import EvidenceItemRecord, SourceDocumentRecord
from src.persistence.repository import PersistenceConflict


class EvidenceRepository:
    """Append-only persistence for versioned source documents and evidence."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def save_source(self, source: SourceDocument) -> None:
        existing = self._session.scalar(
            select(SourceDocumentRecord).where(
                SourceDocumentRecord.source_id == source.source_id,
                SourceDocumentRecord.version == source.version,
            )
        )
        values = source.model_dump(mode="json")
        if existing is not None:
            if existing.id != source.id or existing.source_data != values:
                raise PersistenceConflict(
                    f"source {source.source_id} version {source.version} already exists"
                )
            return
        latest_version = self._session.scalar(
            select(func.max(SourceDocumentRecord.version)).where(
                SourceDocumentRecord.source_id == source.source_id
            )
        )
        expected_version = (latest_version or 0) + 1
        if source.version != expected_version:
            raise PersistenceConflict(
                f"source {source.source_id} version must be {expected_version}"
            )
        self._session.add(
            SourceDocumentRecord(
                id=source.id,
                source_id=source.source_id,
                version=source.version,
                source_type=source.source_type,
                subject=source.subject,
                published_at=source.published_at,
                available_at=source.effective_available_at,
                ingested_at=source.effective_ingested_at,
                source_data=values,
            )
        )
        self._session.flush()

    def ingest_source(
        self,
        source: SourceDocument,
        *,
        ingested_at: datetime,
    ) -> SourceDocument:
        """Persist a first-seen source revision using the collector clock.

        The adapter/provider may supply publication and availability times but
        never the ingestion timestamp. Retries reuse the originally persisted
        ingestion time and are therefore idempotent.
        """
        if source.ingested_at is not None:
            raise ValueError("collector owns source ingested_at")
        ingested_at = _require_utc(ingested_at)
        existing = self.get_source_version(source.source_id, source.version)
        if existing is not None:
            expected = SourceDocument.model_validate(
                source.model_dump(mode="python")
                | {
                    "available_at": source.effective_available_at,
                    "ingested_at": existing.effective_ingested_at,
                }
            )
            if existing != expected:
                raise PersistenceConflict(
                    f"source {source.source_id} version {source.version} already exists"
                )
            return existing
        persisted = SourceDocument.model_validate(
            source.model_dump(mode="python")
            | {
                "available_at": source.effective_available_at,
                "ingested_at": ingested_at,
            }
        )
        self.save_source(persisted)
        return persisted

    def list_sources_as_of(
        self,
        *,
        as_of: datetime,
        subject: str | None = None,
        source_types: tuple[str, ...] = (),
    ) -> tuple[SourceDocument, ...]:
        """Return the latest then-known revision for each logical source ID."""
        as_of = _require_utc(as_of)
        statement = select(SourceDocumentRecord).where(
            SourceDocumentRecord.published_at <= as_of,
            SourceDocumentRecord.available_at <= as_of,
            SourceDocumentRecord.ingested_at <= as_of,
        )
        if subject is not None:
            normalized_subject = subject.strip()
            if not normalized_subject:
                raise ValueError("subject must not be blank")
            statement = statement.where(
                SourceDocumentRecord.subject == normalized_subject
            )
        if source_types:
            normalized_types = tuple(
                source_type.strip() for source_type in source_types
            )
            if any(not source_type for source_type in normalized_types):
                raise ValueError("source type must not be blank")
            statement = statement.where(
                SourceDocumentRecord.source_type.in_(normalized_types)
            )
        rows = self._session.scalars(
            statement.order_by(
                SourceDocumentRecord.source_id,
                SourceDocumentRecord.version.desc(),
            )
        ).all()
        selected: list[SourceDocument] = []
        seen: set[str] = set()
        for row in rows:
            if row.source_id in seen:
                continue
            selected.append(SourceDocument.model_validate(row.source_data))
            seen.add(row.source_id)
        return tuple(selected)

    def save_evidence(self, evidence: EvidenceItem) -> None:
        existing = self._session.get(
            EvidenceItemRecord, (evidence.id, evidence.version)
        )
        values = evidence.model_dump(mode="json")
        if existing is not None:
            if existing.evidence_data != values:
                raise PersistenceConflict(
                    f"evidence {evidence.id} version {evidence.version} already exists"
                )
            return
        latest_version = self._session.scalar(
            select(func.max(EvidenceItemRecord.version)).where(
                EvidenceItemRecord.id == evidence.id
            )
        )
        expected_version = (latest_version or 0) + 1
        if evidence.version != expected_version:
            raise PersistenceConflict(
                f"evidence {evidence.id} version must be {expected_version}"
            )
        self._session.add(
            EvidenceItemRecord(
                id=evidence.id,
                version=evidence.version,
                source_document_version_id=evidence.source_document_version_id,
                evidence_data=values,
            )
        )
        self._session.flush()

    def get_source(self, source_version_id: UUID) -> SourceDocument | None:
        record = self._session.get(SourceDocumentRecord, source_version_id)
        return (
            SourceDocument.model_validate(record.source_data)
            if record is not None
            else None
        )

    def get_source_version(self, source_id: str, version: int) -> SourceDocument | None:
        record = self._session.scalar(
            select(SourceDocumentRecord).where(
                SourceDocumentRecord.source_id == source_id,
                SourceDocumentRecord.version == version,
            )
        )
        return (
            SourceDocument.model_validate(record.source_data)
            if record is not None
            else None
        )

    def get_evidence(self, evidence_id: UUID, version: int) -> EvidenceItem | None:
        record = self._session.get(EvidenceItemRecord, (evidence_id, version))
        return (
            EvidenceItem.model_validate(record.evidence_data)
            if record is not None
            else None
        )

    def get_latest_evidence(self, evidence_id: UUID) -> EvidenceItem | None:
        record = self._session.scalar(
            select(EvidenceItemRecord)
            .where(EvidenceItemRecord.id == evidence_id)
            .order_by(EvidenceItemRecord.version.desc())
            .limit(1)
        )
        return (
            EvidenceItem.model_validate(record.evidence_data)
            if record is not None
            else None
        )
