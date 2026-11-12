from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.database import Base
from src.forward_test_activity import (
    CampaignActivityRepository,
    CampaignActivityStatus,
    CampaignNotification,
    CampaignNotifier,
    JsonLineCampaignNotifier,
    NotificationFanout,
    notification_from_event,
)
from src.forward_test_campaign_ops import (
    CampaignArtifactKind,
    CampaignArtifactRepository,
    CampaignOperationsService,
)
from src.forward_test_campaign_tick import CampaignTickRunner
from src.forward_test_scheduler import CampaignScheduler

START = datetime(2026, 11, 12, 14, 45, tzinfo=UTC)


class NeverCollector:
    def collect(self, *, symbol: str, category: str, as_of=None):
        raise AssertionError("overdue windows must not fetch live market data")


class BrokenNotifier(CampaignNotifier):
    def notify(self, notification: CampaignNotification) -> None:
        raise OSError("synthetic notification failure")


@pytest.fixture
def environment(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'activity.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    artifacts = CampaignArtifactRepository(tmp_path / "artifacts")
    service = CampaignOperationsService(
        session_factory=factory,
        artifacts=artifacts,
    )
    campaign = service.initialize_campaign(
        start_at=START,
        created_at=START - timedelta(days=1),
        operational_policy_version="ops-v1",
    )
    try:
        yield campaign, factory, artifacts, service, tmp_path
    finally:
        engine.dispose()


def test_activity_history_is_append_only_and_terminal(environment):
    campaign, factory, _, _, _ = environment
    job = CampaignScheduler().jobs_for_day(
        campaign=campaign,
        trading_day=START.date(),
    )[0]

    with factory.begin() as session:
        repository = CampaignActivityRepository(session)
        scheduled = repository.append(
            campaign=campaign,
            job=job,
            status=CampaignActivityStatus.SCHEDULED,
            occurred_at=START - timedelta(minutes=1),
            attempt=0,
        )
        running = repository.append(
            campaign=campaign,
            job=job,
            status=CampaignActivityStatus.RUNNING,
            occurred_at=START,
            attempt=1,
        )
        completed = repository.append(
            campaign=campaign,
            job=job,
            status=CampaignActivityStatus.COMPLETED,
            occurred_at=START + timedelta(seconds=10),
            attempt=1,
        )

    assert scheduled.sequence == 1
    assert running.sequence == 2
    assert completed.sequence == 3
    assert completed.status.terminal

    with factory.begin() as session:
        repository = CampaignActivityRepository(session)
        with pytest.raises(Exception, match="terminal status"):
            repository.append(
                campaign=campaign,
                job=job,
                status=CampaignActivityStatus.RUNNING,
                occurred_at=START + timedelta(minutes=1),
                attempt=2,
            )


def test_notification_failure_does_not_change_durable_event(environment):
    campaign, factory, _, _, tmp_path = environment
    job = CampaignScheduler().jobs_for_day(
        campaign=campaign,
        trading_day=START.date(),
    )[0]
    feed = JsonLineCampaignNotifier(tmp_path / "notifications.jsonl")
    fanout = NotificationFanout(BrokenNotifier(), feed)

    with factory.begin() as session:
        event = CampaignActivityRepository(session).append(
            campaign=campaign,
            job=job,
            status=CampaignActivityStatus.SCHEDULED,
            occurred_at=START - timedelta(minutes=1),
            attempt=0,
        )

    fanout.notify(notification_from_event(event))

    with factory() as session:
        restored = CampaignActivityRepository(session).latest_for_job(
            campaign_id=campaign.campaign_id,
            job_id=event.job_id,
        )
    assert restored == event
    assert (tmp_path / "notifications.jsonl").read_text().strip()


def test_overdue_missing_windows_are_reconciled_as_missed(environment):
    campaign, factory, artifacts, service, tmp_path = environment
    artifacts.save(
        kind=CampaignArtifactKind.ACTIVATION,
        campaign=campaign,
        key="day1",
        generated_at=START + timedelta(seconds=20),
        payload={
            "activated_at": (START + timedelta(seconds=20)).isoformat(),
            "first_window_scheduled_at": START.isoformat(),
            "broker_order_submission": "DISABLED",
            "live_execution_used": False,
        },
    )
    after_close = datetime(2026, 11, 12, 21, 20, tzinfo=UTC)
    runner = CampaignTickRunner(
        service=service,
        artifacts=artifacts,
        scheduler=CampaignScheduler(),
        market_collector=NeverCollector(),
        session_factory=factory,
        notifier=JsonLineCampaignNotifier(tmp_path / "activity.jsonl"),
        clock=lambda: after_close,
    )

    runner.tick(campaign_id=campaign.campaign_id)

    with factory() as session:
        events = CampaignActivityRepository(session).list_for_campaign(
            campaign.campaign_id
        )
    latest = {}
    for event in events:
        latest[event.job_id] = event
    market_latest = [
        event for event in latest.values() if event.job_type == "MARKET_WINDOW"
    ]

    assert len(market_latest) == 25
    assert all(event.status is CampaignActivityStatus.MISSED for event in market_latest)
    assert all(
        event.reason_code == "MARKET_WINDOW_GRACE_EXPIRED"
        for event in market_latest
    )


def test_existing_market_artifact_reconciles_as_completed(environment):
    campaign, factory, artifacts, service, tmp_path = environment
    scheduler = CampaignScheduler()
    first = scheduler.jobs_for_day(
        campaign=campaign,
        trading_day=START.date(),
    )[0]
    key = first.scheduled_at.strftime("%Y%m%dT%H%M%SZ")
    artifacts.save(
        kind=CampaignArtifactKind.ACTIVATION,
        campaign=campaign,
        key="day1",
        generated_at=START + timedelta(seconds=20),
        payload={
            "activated_at": (START + timedelta(seconds=20)).isoformat(),
            "first_window_scheduled_at": START.isoformat(),
            "broker_order_submission": "DISABLED",
            "live_execution_used": False,
        },
    )
    artifacts.save(
        kind=CampaignArtifactKind.MARKET_WINDOW,
        campaign=campaign,
        key=key,
        generated_at=START + timedelta(seconds=20),
        payload={"scheduled_at": START.isoformat()},
    )
    after_close = datetime(2026, 11, 12, 21, 20, tzinfo=UTC)

    CampaignTickRunner(
        service=service,
        artifacts=artifacts,
        scheduler=scheduler,
        market_collector=NeverCollector(),
        session_factory=factory,
        notifier=JsonLineCampaignNotifier(tmp_path / "activity.jsonl"),
        clock=lambda: after_close,
    ).tick(campaign_id=campaign.campaign_id)

    with factory() as session:
        latest = CampaignActivityRepository(session).latest_for_job(
            campaign_id=campaign.campaign_id,
            job_id=CampaignActivityRepository.job_id(campaign.campaign_id, first),
        )
    assert latest is not None
    assert latest.status is CampaignActivityStatus.COMPLETED
    assert latest.reason_code == "RECONCILED_EXISTING_ARTIFACT"
