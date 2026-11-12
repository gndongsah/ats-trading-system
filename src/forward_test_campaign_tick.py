"""ATS-036D owner-machine campaign activation tick.

A Windows scheduled task invokes this module repeatedly. Each tick reloads the
already-persisted campaign, derives due XNYS jobs, and executes each artifact
once. Market windows are market-data health/collection boundaries only; this
module has no broker client and cannot create or approve trade proposals.
"""

from __future__ import annotations

import argparse
import json
import os
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session, sessionmaker

from src.database import create_database_engine
from src.forward_test_activity import (
    CampaignActivityRepository,
    CampaignActivityStatus,
    CampaignNotifier,
    JsonLineCampaignNotifier,
    NotificationFanout,
    NullCampaignNotifier,
    WebhookCampaignNotifier,
    notification_from_event,
)
from src.forward_test_campaign_ops import (
    CampaignArtifactKind,
    CampaignArtifactRepository,
    CampaignOperationsService,
)
from src.forward_test_runtime import DurableWebullPaperMarketRuntime
from src.forward_test_scheduler import (
    CampaignJobKind,
    CampaignScheduledJob,
    CampaignScheduler,
)
from src.market_data import Observation
from src.webull_market_data import build_keyring_sandbox_adapter

DEFAULT_CAMPAIGN_ID = UUID("e00289e2-4e14-4819-a493-de93263c79ad")
DEFAULT_SYMBOL = "SPY"
DEFAULT_CATEGORY = "US_ETF"
DAY1_ACTIVATION_KEY = "day1"
MARKET_WINDOW_GRACE = timedelta(minutes=5)
MAX_MARKET_WINDOW_ATTEMPTS = 2


class MarketCollector(Protocol):
    def collect(
        self,
        *,
        symbol: str,
        category: str,
        as_of: datetime | None = None,
    ) -> Observation: ...


class CampaignTickDisposition(StrEnum):
    PASS = "PASS"
    NOOP = "NOOP"
    HOLD = "HOLD"


class CampaignTickResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    disposition: CampaignTickDisposition
    campaign_id: UUID
    evaluated_at: datetime
    activated: bool
    executed_jobs: tuple[str, ...] = ()
    reason: str | None = None
    broker_order_submission: str = "DISABLED"
    live_execution_used: bool = False
    proposal_creation_used: bool = False


class CampaignTickRunner:
    """Idempotently execute due campaign jobs against one frozen manifest."""

    def __init__(
        self,
        *,
        service: CampaignOperationsService,
        artifacts: CampaignArtifactRepository,
        scheduler: CampaignScheduler,
        market_collector: MarketCollector,
        session_factory: sessionmaker[Session] | None = None,
        notifier: CampaignNotifier | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._service = service
        self._artifacts = artifacts
        self._scheduler = scheduler
        self._market_collector = market_collector
        self._session_factory = session_factory
        self._notifier = NotificationFanout(notifier or NullCampaignNotifier())
        self._clock = clock

    def tick(
        self,
        *,
        campaign_id: UUID,
        symbol: str = DEFAULT_SYMBOL,
        category: str = DEFAULT_CATEGORY,
    ) -> CampaignTickResult:
        now = self._utc(self._clock())
        campaign = self._service.load_campaign(campaign_id)
        activation = self._artifacts.load(
            campaign_id=campaign_id,
            kind=CampaignArtifactKind.ACTIVATION,
            key=DAY1_ACTIVATION_KEY,
        )

        if now < campaign.start_at:
            return self._result(
                campaign_id=campaign_id,
                now=now,
                activated=activation is not None,
                disposition=CampaignTickDisposition.NOOP,
                reason="CAMPAIGN_NOT_STARTED",
            )
        if now > campaign.end_at:
            return self._result(
                campaign_id=campaign_id,
                now=now,
                activated=activation is not None,
                disposition=CampaignTickDisposition.NOOP,
                reason="CAMPAIGN_ENDED",
            )
        if activation is None and now.date() != campaign.start_at.date():
            return self._result(
                campaign_id=campaign_id,
                now=now,
                activated=False,
                disposition=CampaignTickDisposition.HOLD,
                reason="DAY1_NOT_ACTIVATED",
            )
        if not self._scheduler.is_trading_day(now.date()):
            return self._result(
                campaign_id=campaign_id,
                now=now,
                activated=activation is not None,
                disposition=CampaignTickDisposition.NOOP,
                reason="XNYS_CLOSED",
            )

        jobs = self._scheduler.jobs_for_day(
            campaign=campaign,
            trading_day=now.date(),
        )
        market_jobs = tuple(
            job for job in jobs if job.kind is CampaignJobKind.MARKET_WINDOW
        )
        if not market_jobs:
            return self._result(
                campaign_id=campaign_id,
                now=now,
                activated=activation is not None,
                disposition=CampaignTickDisposition.NOOP,
                reason="NO_CAMPAIGN_JOBS_TODAY",
            )

        self._seed_and_reconcile_activity(
            campaign=campaign,
            jobs=jobs,
            now=now,
        )

        first_window = market_jobs[0]
        if activation is None and now > first_window.scheduled_at + MARKET_WINDOW_GRACE:
            return self._result(
                campaign_id=campaign_id,
                now=now,
                activated=False,
                disposition=CampaignTickDisposition.HOLD,
                reason="DAY1_FIRST_MARKET_WINDOW_MISSED",
            )

        executed: list[str] = []
        for job in jobs:
            if job.scheduled_at > now:
                continue
            if job.kind is CampaignJobKind.MARKET_WINDOW:
                if now > job.scheduled_at + MARKET_WINDOW_GRACE:
                    continue
                key = self._job_key(job)
                if (
                    self._artifacts.load(
                        campaign_id=campaign_id,
                        kind=CampaignArtifactKind.MARKET_WINDOW,
                        key=key,
                    )
                    is not None
                ):
                    continue
                latest = self._latest_activity(campaign_id, job)
                attempt = 1 if latest is None else max(1, latest.attempt + 1)
                self._append_activity(
                    campaign=campaign,
                    job=job,
                    status=CampaignActivityStatus.RUNNING,
                    occurred_at=now,
                    attempt=attempt,
                    reason_code=None,
                    payload={"artifact_key": key},
                )
                try:
                    observation = self._market_collector.collect(
                        symbol=symbol,
                        category=category,
                    )
                except Exception as error:  # noqa: BLE001 - sanitize provider failure.
                    terminal = attempt >= MAX_MARKET_WINDOW_ATTEMPTS
                    self._append_activity(
                        campaign=campaign,
                        job=job,
                        status=(
                            CampaignActivityStatus.RETRY_EXHAUSTED
                            if terminal
                            else CampaignActivityStatus.FAILED
                        ),
                        occurred_at=self._utc(self._clock()),
                        attempt=attempt,
                        reason_code=type(error).__name__,
                        payload={"artifact_key": key},
                    )
                    continue
                payload = {
                    "scheduled_at": job.scheduled_at.isoformat(),
                    "trading_day": job.trading_day.isoformat(),
                    "source": observation.event.source,
                    "symbol": observation.event.symbol,
                    "revision": observation.event.revision,
                    "event_time": observation.event.event_time.isoformat(),
                    "available_at": observation.event.available_at.isoformat(),
                    "ingested_at": observation.ingested_at.isoformat(),
                    "broker_order_submission": "DISABLED",
                    "live_execution_used": False,
                    "proposal_creation_used": False,
                }
                completed_at = max(self._utc(self._clock()), observation.ingested_at)
                self._artifacts.save(
                    kind=CampaignArtifactKind.MARKET_WINDOW,
                    campaign=campaign,
                    key=key,
                    generated_at=completed_at,
                    payload=payload,
                )
                self._append_activity(
                    campaign=campaign,
                    job=job,
                    status=CampaignActivityStatus.COMPLETED,
                    occurred_at=completed_at,
                    attempt=attempt,
                    reason_code=None,
                    payload={"artifact_key": key},
                )
                executed.append(f"MARKET_WINDOW:{key}")
                if activation is None and job.scheduled_at == first_window.scheduled_at:
                    activation = self._artifacts.save(
                        kind=CampaignArtifactKind.ACTIVATION,
                        campaign=campaign,
                        key=DAY1_ACTIVATION_KEY,
                        generated_at=completed_at,
                        payload={
                            "activated_at": completed_at.isoformat(),
                            "first_window_scheduled_at": job.scheduled_at.isoformat(),
                            "market_window_key": key,
                            "market_event_time": observation.event.event_time.isoformat(),
                            "market_available_at": observation.event.available_at.isoformat(),
                            "market_ingested_at": observation.ingested_at.isoformat(),
                            "broker_order_submission": "DISABLED",
                            "live_execution_used": False,
                        },
                    )
                    executed.append("ACTIVATION:day1")
                continue

            if job.kind is CampaignJobKind.DAILY_SCORECARD:
                key = job.trading_day.isoformat()
                if (
                    self._artifacts.load(
                        campaign_id=campaign_id,
                        kind=CampaignArtifactKind.DAILY_SCORECARD,
                        key=key,
                    )
                    is None
                ):
                    self._service.persist_daily_scorecard(
                        campaign_id=campaign_id,
                        trading_day=job.trading_day,
                        generated_at=now,
                    )
                    executed.append(f"DAILY_SCORECARD:{key}")
                continue

            if job.kind is CampaignJobKind.WEEKLY_REPORT:
                week_start = job.trading_day - timedelta(days=job.trading_day.weekday())
                key = week_start.isoformat()
                if (
                    self._artifacts.load(
                        campaign_id=campaign_id,
                        kind=CampaignArtifactKind.WEEKLY_REPORT,
                        key=key,
                    )
                    is None
                ):
                    self._service.persist_weekly_report(
                        campaign_id=campaign_id,
                        week_start=week_start,
                        generated_at=now,
                    )
                    executed.append(f"WEEKLY_REPORT:{key}")
                continue

            assert job.kind is CampaignJobKind.READINESS
            key = job.trading_day.isoformat()
            if (
                self._artifacts.load(
                    campaign_id=campaign_id,
                    kind=CampaignArtifactKind.READINESS,
                    key=key,
                )
                is None
            ):
                self._service.persist_readiness(
                    campaign_id=campaign_id,
                    evaluated_at=now,
                )
                executed.append(f"READINESS:{key}")

        activated = activation is not None
        disposition = (
            CampaignTickDisposition.PASS if executed else CampaignTickDisposition.NOOP
        )
        return self._result(
            campaign_id=campaign_id,
            now=now,
            activated=activated,
            disposition=disposition,
            executed_jobs=tuple(executed),
            reason=None if executed else "NO_DUE_UNEXECUTED_JOBS",
        )

    def _seed_and_reconcile_activity(
        self,
        *,
        campaign,
        jobs: tuple[CampaignScheduledJob, ...],
        now: datetime,
    ) -> None:
        if self._session_factory is None:
            return
        for job in jobs:
            latest = self._latest_activity(campaign.campaign_id, job)
            if latest is None:
                latest = self._append_activity(
                    campaign=campaign,
                    job=job,
                    status=CampaignActivityStatus.SCHEDULED,
                    occurred_at=now,
                    attempt=0,
                    reason_code=None,
                    payload={"scheduled_at": job.scheduled_at.isoformat()},
                )
            if latest.status.terminal:
                continue

            artifact_kind = self._artifact_kind(job.kind)
            artifact_key = self._artifact_key(job)
            artifact = (
                self._artifacts.load(
                    campaign_id=campaign.campaign_id,
                    kind=artifact_kind,
                    key=artifact_key,
                )
                if artifact_kind is not None
                else None
            )
            if artifact is not None:
                self._append_activity(
                    campaign=campaign,
                    job=job,
                    status=CampaignActivityStatus.COMPLETED,
                    occurred_at=max(now, artifact.generated_at),
                    attempt=max(latest.attempt, 1),
                    reason_code="RECONCILED_EXISTING_ARTIFACT",
                    payload={"artifact_key": artifact_key},
                )
                continue

            if (
                job.kind is CampaignJobKind.MARKET_WINDOW
                and now > job.scheduled_at + MARKET_WINDOW_GRACE
            ):
                self._append_activity(
                    campaign=campaign,
                    job=job,
                    status=CampaignActivityStatus.MISSED,
                    occurred_at=now,
                    attempt=latest.attempt,
                    reason_code="MARKET_WINDOW_GRACE_EXPIRED",
                    payload={"artifact_key": artifact_key},
                )

    def _latest_activity(self, campaign_id: UUID, job: CampaignScheduledJob):
        if self._session_factory is None:
            return None
        with self._session_factory() as session:
            return CampaignActivityRepository(session).latest_for_job(
                campaign_id=campaign_id,
                job_id=CampaignActivityRepository.job_id(campaign_id, job),
            )

    def _append_activity(
        self,
        *,
        campaign,
        job: CampaignScheduledJob,
        status: CampaignActivityStatus,
        occurred_at: datetime,
        attempt: int,
        reason_code: str | None,
        payload: dict[str, object],
    ):
        if self._session_factory is None:
            return None
        with self._session_factory.begin() as session:
            event = CampaignActivityRepository(session).append(
                campaign=campaign,
                job=job,
                status=status,
                occurred_at=occurred_at,
                attempt=attempt,
                reason_code=reason_code,
                payload=payload,
            )
        self._notifier.notify(notification_from_event(event))
        return event

    @staticmethod
    def _artifact_kind(kind: CampaignJobKind):
        return {
            CampaignJobKind.MARKET_WINDOW: CampaignArtifactKind.MARKET_WINDOW,
            CampaignJobKind.DAILY_SCORECARD: CampaignArtifactKind.DAILY_SCORECARD,
            CampaignJobKind.WEEKLY_REPORT: CampaignArtifactKind.WEEKLY_REPORT,
            CampaignJobKind.READINESS: CampaignArtifactKind.READINESS,
        }.get(kind)

    @classmethod
    def _artifact_key(cls, job: CampaignScheduledJob) -> str:
        if job.kind is CampaignJobKind.MARKET_WINDOW:
            return cls._job_key(job)
        if job.kind is CampaignJobKind.WEEKLY_REPORT:
            week_start = job.trading_day - timedelta(days=job.trading_day.weekday())
            return week_start.isoformat()
        return job.trading_day.isoformat()

    @staticmethod
    def _job_key(job: CampaignScheduledJob) -> str:
        return job.scheduled_at.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")

    @staticmethod
    def _utc(value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("tick clock must be timezone-aware")
        return value.astimezone(UTC)

    @staticmethod
    def _result(
        *,
        campaign_id: UUID,
        now: datetime,
        activated: bool,
        disposition: CampaignTickDisposition,
        executed_jobs: tuple[str, ...] = (),
        reason: str | None,
    ) -> CampaignTickResult:
        return CampaignTickResult(
            disposition=disposition,
            campaign_id=campaign_id,
            evaluated_at=now,
            activated=activated,
            executed_jobs=executed_jobs,
            reason=reason,
        )


def build_owner_machine_runner(
    *,
    database_url: str,
    artifact_dir: Path,
) -> CampaignTickRunner:
    engine = create_database_engine(database_url)
    factory = sessionmaker(
        bind=engine,
        autoflush=False,
        expire_on_commit=False,
        class_=Session,
    )
    artifacts = CampaignArtifactRepository(artifact_dir)
    service = CampaignOperationsService(
        session_factory=factory,
        artifacts=artifacts,
    )
    market_runtime = DurableWebullPaperMarketRuntime(
        adapter=build_keyring_sandbox_adapter(),
        session_factory=factory,
    )
    notification_path = artifact_dir / "notifications" / "activity.jsonl"
    notifiers: list[CampaignNotifier] = [JsonLineCampaignNotifier(notification_path)]
    webhook_url = os.getenv("ATS_NOTIFICATION_WEBHOOK_URL")
    if webhook_url:
        notifiers.append(WebhookCampaignNotifier(webhook_url))
    return CampaignTickRunner(
        service=service,
        artifacts=artifacts,
        scheduler=CampaignScheduler(),
        market_collector=market_runtime,
        session_factory=factory,
        notifier=NotificationFanout(*notifiers),
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="ATS-036D PAPER campaign scheduler tick"
    )
    parser.add_argument(
        "--campaign-id",
        type=UUID,
        default=DEFAULT_CAMPAIGN_ID,
    )
    parser.add_argument(
        "--database-url",
        default=os.getenv("ATS_DATABASE_URL") or os.getenv("DATABASE_URL"),
    )
    parser.add_argument(
        "--artifact-dir",
        type=Path,
        default=Path(os.getenv("ATS_CAMPAIGN_ARTIFACT_DIR", "artifacts/forward-test")),
    )
    parser.add_argument("--symbol", default=DEFAULT_SYMBOL)
    parser.add_argument("--category", default=DEFAULT_CATEGORY)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.database_url:
        parser.error("database URL required via ATS_DATABASE_URL or --database-url")
    try:
        result = build_owner_machine_runner(
            database_url=args.database_url,
            artifact_dir=args.artifact_dir,
        ).tick(
            campaign_id=args.campaign_id,
            symbol=args.symbol,
            category=args.category,
        )
    except Exception as error:  # noqa: BLE001 - never emit provider/credential text.
        print(
            json.dumps(
                {
                    "disposition": "HOLD",
                    "campaign_id": str(args.campaign_id),
                    "error_type": type(error).__name__,
                    "broker_order_submission": "DISABLED",
                    "live_execution_used": False,
                }
            )
        )
        return 2

    print(result.model_dump_json(indent=2))
    return 2 if result.disposition is CampaignTickDisposition.HOLD else 0


if __name__ == "__main__":
    raise SystemExit(main())
