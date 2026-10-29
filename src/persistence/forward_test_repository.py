"""Persistence for ATS-031/033 forward-test campaign and run evidence."""

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.forward_test import ForwardTestRunEvidence
from src.forward_test_campaign import (
    ForwardTestCampaignManifest,
    campaign_manifest_digest,
)
from src.persistence.models import ForwardTestCampaignRecord, ForwardTestRunRecord
from src.persistence.repository import PersistenceConflict


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


class ForwardTestCampaignRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def save(self, manifest: ForwardTestCampaignManifest) -> str:
        digest = campaign_manifest_digest(manifest)
        existing = self._session.get(ForwardTestCampaignRecord, manifest.campaign_id)
        values = manifest.model_dump(mode="json")
        if existing is not None:
            if existing.manifest_digest != digest or existing.manifest_data != values:
                raise PersistenceConflict("forward-test campaign id already exists")
            return digest
        self._session.add(
            ForwardTestCampaignRecord(
                id=manifest.campaign_id,
                run_type=manifest.run_type.value,
                created_at=manifest.created_at,
                start_at=manifest.start_at,
                end_at=manifest.end_at,
                playbook_version=manifest.playbook_version,
                playbook_digest=manifest.playbook_digest,
                operational_policy_version=manifest.operational_policy_version,
                manifest_digest=digest,
                manifest_data=values,
            )
        )
        self._session.flush()
        return digest

    def get(self, campaign_id: UUID) -> ForwardTestCampaignManifest | None:
        row = self._session.get(ForwardTestCampaignRecord, campaign_id)
        if row is None:
            return None
        data = dict(row.manifest_data)
        data["created_at"] = _as_utc(row.created_at)
        data["start_at"] = _as_utc(row.start_at)
        data["end_at"] = _as_utc(row.end_at)
        manifest = ForwardTestCampaignManifest.model_validate(data)
        if campaign_manifest_digest(manifest) != row.manifest_digest:
            raise PersistenceConflict("forward-test campaign digest mismatch")
        return manifest


class ForwardTestRunRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def save(self, evidence: ForwardTestRunEvidence) -> None:
        if evidence.campaign_id is None or evidence.campaign_digest is None:
            raise PersistenceConflict("forward-test run requires campaign binding")
        campaign = self._session.get(ForwardTestCampaignRecord, evidence.campaign_id)
        if campaign is None:
            raise PersistenceConflict("forward-test campaign does not exist")
        if campaign.manifest_digest != evidence.campaign_digest:
            raise PersistenceConflict("forward-test campaign digest mismatch")

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
                campaign_id=evidence.campaign_id,
                campaign_digest=evidence.campaign_digest,
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
        return tuple(self._restore(row) for row in rows)

    def list_by_campaign(
        self,
        campaign_id: UUID,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> tuple[ForwardTestRunEvidence, ...]:
        statement = select(ForwardTestRunRecord).where(
            ForwardTestRunRecord.campaign_id == campaign_id
        )
        if start is not None:
            statement = statement.where(ForwardTestRunRecord.as_of >= start)
        if end is not None:
            statement = statement.where(ForwardTestRunRecord.as_of <= end)
        rows = self._session.scalars(
            statement.order_by(ForwardTestRunRecord.as_of, ForwardTestRunRecord.id)
        ).all()
        return tuple(self._restore(row) for row in rows)

    @staticmethod
    def _restore(row: ForwardTestRunRecord) -> ForwardTestRunEvidence:
        data = dict(row.run_data)
        data["as_of"] = _as_utc(row.as_of)
        return ForwardTestRunEvidence.model_validate(data)
