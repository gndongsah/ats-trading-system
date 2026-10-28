"""Persistence for ATS-031 forward-test run evidence."""

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.forward_test import ForwardTestRunEvidence
from src.persistence.models import ForwardTestRunRecord
from src.persistence.repository import PersistenceConflict


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


class ForwardTestRunRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def save(self, evidence: ForwardTestRunEvidence) -> None:
        existing = self._session.get(ForwardTestRunRecord, evidence.run_id)
        values = evidence.model_dump(mode="json")
        if existing is not None:
            if existing.run_data != values:
                raise PersistenceConflict("forward-test run id already exists")
            return
        self._session.add(
            ForwardTestRunRecord(
                id=evidence.run_id,
                proposal_id=evidence.proposal_id,
                run_type=evidence.run_type.value,
                status=evidence.status.value,
                as_of=evidence.as_of,
                playbook_version=evidence.playbook_version,
                playbook_digest=evidence.playbook_digest,
                run_data=values,
            )
        )
        self._session.flush()

    def get(self, run_id: UUID) -> ForwardTestRunEvidence | None:
        row = self._session.get(ForwardTestRunRecord, run_id)
        if row is None:
            return None
        data = dict(row.run_data)
        data["as_of"] = _as_utc(row.as_of)
        return ForwardTestRunEvidence.model_validate(data)

    def list_between(
        self,
        *,
        start: datetime,
        end: datetime,
    ) -> tuple[ForwardTestRunEvidence, ...]:
        rows = self._session.scalars(
            select(ForwardTestRunRecord)
            .where(ForwardTestRunRecord.as_of >= start)
            .where(ForwardTestRunRecord.as_of <= end)
            .order_by(ForwardTestRunRecord.as_of, ForwardTestRunRecord.id)
        ).all()
        results = []
        for row in rows:
            data = dict(row.run_data)
            data["as_of"] = _as_utc(row.as_of)
            results.append(ForwardTestRunEvidence.model_validate(data))
        return tuple(results)
