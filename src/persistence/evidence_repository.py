from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from src.domain.evidence import EvidenceItem, SourceDocument
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
                source_data=values,
            )
        )
        self._session.flush()

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
