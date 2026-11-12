"""ATS-036E durable campaign activity ledger and notification outbox."""

from __future__ import annotations

import json
from urllib.request import Request, urlopen
from collections.abc import Callable
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from uuid import NAMESPACE_URL, UUID, uuid5

from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.domain.models import _require_utc
from src.forward_test_campaign import (
    ForwardTestCampaignManifest,
    campaign_manifest_digest,
)
from src.forward_test_scheduler import CampaignScheduledJob
from src.persistence.models import CampaignActivityEventRecord
from src.persistence.repository import PersistenceConflict


class CampaignActivityStatus(StrEnum):
    SCHEDULED = "SCHEDULED"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    HOLD = "HOLD"
    FAILED = "FAILED"
    MISSED = "MISSED"
    RETRY_EXHAUSTED = "RETRY_EXHAUSTED"

    @property
    def terminal(self) -> bool:
        return self in {
            CampaignActivityStatus.COMPLETED,
            CampaignActivityStatus.HOLD,
            CampaignActivityStatus.MISSED,
            CampaignActivityStatus.RETRY_EXHAUSTED,
        }


class CampaignActivityEvent(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: UUID
    campaign_id: UUID
    campaign_digest: str = Field(min_length=64, max_length=64)
    job_id: UUID
    job_type: str = Field(min_length=1, max_length=32)
    scheduled_at: datetime
    sequence: int = Field(ge=1)
    attempt: int = Field(ge=0)
    status: CampaignActivityStatus
    occurred_at: datetime
    reason_code: str | None = Field(default=None, max_length=128)
    payload: dict[str, object] = Field(default_factory=dict)

    _scheduled_utc = field_validator("scheduled_at")(_require_utc)
    _occurred_utc = field_validator("occurred_at")(_require_utc)


class CampaignActivityRepository:
    """Append-only transition history for one deterministic scheduled job."""

    def __init__(self, session: Session) -> None:
        self._session = session

    @staticmethod
    def job_id(campaign_id: UUID, job: CampaignScheduledJob) -> UUID:
        return uuid5(
            NAMESPACE_URL,
            f"ats-campaign-job:{campaign_id}:{job.kind.value}:{job.scheduled_at.isoformat()}",
        )

    def latest_for_job(
        self,
        *,
        campaign_id: UUID,
        job_id: UUID,
    ) -> CampaignActivityEvent | None:
        row = self._session.scalar(
            select(CampaignActivityEventRecord)
            .where(CampaignActivityEventRecord.campaign_id == campaign_id)
            .where(CampaignActivityEventRecord.job_id == job_id)
            .order_by(CampaignActivityEventRecord.sequence.desc())
            .limit(1)
        )
        return self._decode(row) if row is not None else None

    def list_for_campaign(
        self,
        campaign_id: UUID,
    ) -> tuple[CampaignActivityEvent, ...]:
        rows = self._session.scalars(
            select(CampaignActivityEventRecord)
            .where(CampaignActivityEventRecord.campaign_id == campaign_id)
            .order_by(
                CampaignActivityEventRecord.scheduled_at,
                CampaignActivityEventRecord.job_type,
                CampaignActivityEventRecord.sequence,
            )
        ).all()
        return tuple(self._decode(row) for row in rows)

    def append(
        self,
        *,
        campaign: ForwardTestCampaignManifest,
        job: CampaignScheduledJob,
        status: CampaignActivityStatus,
        occurred_at: datetime,
        attempt: int,
        reason_code: str | None = None,
        payload: dict[str, object] | None = None,
    ) -> CampaignActivityEvent:
        occurred_at = _require_utc(occurred_at)
        job_id = self.job_id(campaign.campaign_id, job)
        previous = self.latest_for_job(campaign_id=campaign.campaign_id, job_id=job_id)
        sequence = 1 if previous is None else previous.sequence + 1

        if previous is not None and previous.status.terminal:
            raise PersistenceConflict(
                f"campaign job {job_id} already has terminal status {previous.status.value}"
            )

        event = CampaignActivityEvent(
            id=uuid5(
                NAMESPACE_URL,
                f"ats-campaign-activity:{campaign.campaign_id}:{job_id}:{sequence}:{status.value}",
            ),
            campaign_id=campaign.campaign_id,
            campaign_digest=campaign_manifest_digest(campaign),
            job_id=job_id,
            job_type=job.kind.value,
            scheduled_at=job.scheduled_at,
            sequence=sequence,
            attempt=attempt,
            status=status,
            occurred_at=occurred_at,
            reason_code=reason_code,
            payload=payload or {},
        )
        self._session.add(
            CampaignActivityEventRecord(
                id=event.id,
                campaign_id=event.campaign_id,
                campaign_digest=event.campaign_digest,
                job_id=event.job_id,
                job_type=event.job_type,
                scheduled_at=event.scheduled_at,
                sequence=event.sequence,
                attempt=event.attempt,
                status=event.status.value,
                occurred_at=event.occurred_at,
                reason_code=event.reason_code,
                event_data=event.model_dump(mode="json"),
            )
        )
        self._session.flush()
        return event

    @staticmethod
    def _decode(row: CampaignActivityEventRecord) -> CampaignActivityEvent:
        return CampaignActivityEvent.model_validate(dict(row.event_data))


class CampaignNotification(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    campaign_id: UUID
    job_id: UUID
    job_type: str
    scheduled_at: datetime
    status: CampaignActivityStatus
    occurred_at: datetime
    reason_code: str | None = None
    attempt: int = 0
    payload: dict[str, object] = Field(default_factory=dict)


class CampaignNotifier:
    """Best-effort notification interface; failures never mutate campaign state."""

    def notify(self, notification: CampaignNotification) -> None:
        raise NotImplementedError


class NullCampaignNotifier(CampaignNotifier):
    def notify(self, notification: CampaignNotification) -> None:
        return None


class JsonLineCampaignNotifier(CampaignNotifier):
    """Durable local notification feed consumed by the future ATS dashboard."""

    def __init__(self, path: Path | str) -> None:
        self._path = Path(path)

    def notify(self, notification: CampaignNotification) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(notification.model_dump(mode="json"), sort_keys=True)
        with self._path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")


class WebhookCampaignNotifier(CampaignNotifier):
    """Optional generic JSON webhook transport."""

    def __init__(self, url: str, *, timeout_seconds: float = 5.0) -> None:
        if not url.startswith(("https://", "http://")):
            raise ValueError("notification webhook must be http(s)")
        self._url = url
        self._timeout_seconds = timeout_seconds

    def notify(self, notification: CampaignNotification) -> None:
        body = json.dumps(notification.model_dump(mode="json")).encode("utf-8")
        request = Request(
            self._url,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(request, timeout=self._timeout_seconds) as response:  # noqa: S310
            if not 200 <= response.status < 300:
                raise RuntimeError("notification webhook returned non-success status")


class NotificationFanout(CampaignNotifier):
    def __init__(self, *notifiers: CampaignNotifier) -> None:
        self._notifiers = notifiers

    def notify(self, notification: CampaignNotification) -> None:
        for notifier in self._notifiers:
            try:
                notifier.notify(notification)
            except Exception:
                # Notification transport must never alter campaign execution state.
                continue


def notification_from_event(event: CampaignActivityEvent) -> CampaignNotification:
    return CampaignNotification(
        campaign_id=event.campaign_id,
        job_id=event.job_id,
        job_type=event.job_type,
        scheduled_at=event.scheduled_at,
        status=event.status,
        occurred_at=event.occurred_at,
        reason_code=event.reason_code,
        attempt=event.attempt,
        payload=event.payload,
    )


def persist_then_notify(
    *,
    session: Session,
    campaign: ForwardTestCampaignManifest,
    job: CampaignScheduledJob,
    status: CampaignActivityStatus,
    occurred_at: datetime,
    attempt: int,
    notifier: CampaignNotifier,
    reason_code: str | None = None,
    payload: dict[str, object] | None = None,
) -> CampaignActivityEvent:
    repository = CampaignActivityRepository(session)
    event = repository.append(
        campaign=campaign,
        job=job,
        status=status,
        occurred_at=occurred_at,
        attempt=attempt,
        reason_code=reason_code,
        payload=payload,
    )
    session.commit()
    notifier.notify(notification_from_event(event))
    return event
