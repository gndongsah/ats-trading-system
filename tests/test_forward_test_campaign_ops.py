from datetime import UTC, date, datetime, timedelta
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.database import Base
from src.forward_test_campaign import campaign_manifest_digest
from src.forward_test_campaign_ops import (
    CAMPAIGN_DURATION,
    FROZEN_SETUP_SCOPE,
    CampaignArtifactKind,
    CampaignArtifactRepository,
    CampaignOperationsService,
)
from src.forward_test_scheduler import CampaignJobKind, CampaignScheduler
from src.persistence.repository import PersistenceConflict

AT = datetime(2026, 11, 11, 16, 30, tzinfo=UTC)


@pytest.fixture
def factory(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'campaign.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    try:
        yield factory
    finally:
        engine.dispose()


def test_campaign_initialization_is_12_week_paper_identity(factory, tmp_path):
    service = CampaignOperationsService(
        session_factory=factory,
        artifacts=CampaignArtifactRepository(tmp_path / "artifacts"),
    )
    start = datetime(2026, 11, 12, 14, 45, tzinfo=UTC)
    campaign = service.initialize_campaign(
        start_at=start,
        operational_policy_version="ops-v1",
        created_at=AT,
    )

    assert campaign.start_at == start
    assert campaign.end_at - campaign.start_at == CAMPAIGN_DURATION
    assert campaign.setup_scope == FROZEN_SETUP_SCOPE
    assert campaign.run_type.value == "PAPER"
    assert len(campaign_manifest_digest(campaign)) == 64
    assert service.load_campaign(campaign.campaign_id) == campaign


def test_overlapping_campaign_identity_is_rejected(factory, tmp_path):
    service = CampaignOperationsService(
        session_factory=factory,
        artifacts=CampaignArtifactRepository(tmp_path / "artifacts"),
    )
    start = datetime(2026, 11, 12, 14, 45, tzinfo=UTC)
    service.initialize_campaign(
        start_at=start,
        operational_policy_version="ops-v1",
        created_at=AT,
    )

    with pytest.raises(PersistenceConflict, match="overlaps"):
        service.initialize_campaign(
            start_at=start + timedelta(days=1),
            operational_policy_version="ops-v1",
            created_at=AT,
        )


def test_artifact_store_is_append_only_and_campaign_bound(factory, tmp_path):
    service = CampaignOperationsService(
        session_factory=factory,
        artifacts=CampaignArtifactRepository(tmp_path / "artifacts"),
    )
    campaign = service.initialize_campaign(
        start_at=datetime(2026, 11, 12, 14, 45, tzinfo=UTC),
        operational_policy_version="ops-v1",
        created_at=AT,
    )
    artifacts = CampaignArtifactRepository(tmp_path / "artifacts")
    payload = {"proposals": 0, "fills": 0}
    first = artifacts.save(
        kind=CampaignArtifactKind.DAILY_SCORECARD,
        campaign=campaign,
        key="2026-11-12",
        generated_at=AT,
        payload=payload,
    )
    replay = artifacts.save(
        kind=CampaignArtifactKind.DAILY_SCORECARD,
        campaign=campaign,
        key="2026-11-12",
        generated_at=AT,
        payload=payload,
    )

    assert replay == first
    assert artifacts.load(
        campaign_id=campaign.campaign_id,
        kind=CampaignArtifactKind.DAILY_SCORECARD,
        key="2026-11-12",
    ) == first

    with pytest.raises(PersistenceConflict, match="already exists"):
        artifacts.save(
            kind=CampaignArtifactKind.DAILY_SCORECARD,
            campaign=campaign,
            key="2026-11-12",
            generated_at=AT,
            payload={"proposals": 1, "fills": 0},
        )


def test_scheduler_uses_first_valid_xnys_window_and_daily_close_job(factory, tmp_path):
    service = CampaignOperationsService(
        session_factory=factory,
        artifacts=CampaignArtifactRepository(tmp_path / "artifacts"),
    )
    scheduler = CampaignScheduler()
    start = scheduler.next_start_at(
        datetime(2026, 11, 11, 22, 0, tzinfo=UTC)
    )
    campaign = service.initialize_campaign(
        start_at=start,
        operational_policy_version="ops-v1",
        created_at=AT,
    )

    assert start.date() == date(2026, 11, 12)
    jobs = scheduler.jobs_for_day(campaign=campaign, trading_day=start.date())
    assert jobs
    assert jobs[0].kind is CampaignJobKind.MARKET_WINDOW
    assert any(job.kind is CampaignJobKind.DAILY_SCORECARD for job in jobs)
    assert all(campaign.start_at <= job.scheduled_at <= campaign.end_at for job in jobs)


def test_scheduler_adds_weekly_report_but_not_readiness_before_week_8(factory, tmp_path):
    service = CampaignOperationsService(
        session_factory=factory,
        artifacts=CampaignArtifactRepository(tmp_path / "artifacts"),
    )
    scheduler = CampaignScheduler()
    start = datetime(2026, 11, 12, 14, 45, tzinfo=UTC)
    campaign = service.initialize_campaign(
        start_at=start,
        operational_policy_version="ops-v1",
        created_at=AT,
    )

    jobs = scheduler.jobs_for_day(
        campaign=campaign,
        trading_day=date(2026, 11, 13),
    )
    kinds = {job.kind for job in jobs}
    assert CampaignJobKind.WEEKLY_REPORT in kinds
    assert CampaignJobKind.READINESS not in kinds


def test_artifact_store_rejects_payload_tampering(factory, tmp_path):
    service = CampaignOperationsService(
        session_factory=factory,
        artifacts=CampaignArtifactRepository(tmp_path / "artifacts"),
    )
    campaign = service.initialize_campaign(
        start_at=datetime(2026, 11, 12, 14, 45, tzinfo=UTC),
        operational_policy_version="ops-v1",
        created_at=AT,
    )
    artifacts = CampaignArtifactRepository(tmp_path / "artifacts")
    artifacts.save(
        kind=CampaignArtifactKind.DAILY_SCORECARD,
        campaign=campaign,
        key="2026-11-12",
        generated_at=AT,
        payload={"proposals": 0, "fills": 0},
    )
    path = (
        tmp_path
        / "artifacts"
        / str(campaign.campaign_id)
        / CampaignArtifactKind.DAILY_SCORECARD.value
        / "2026-11-12.json"
    )
    raw = path.read_text()
    path.write_text(raw.replace('"proposals": 0', '"proposals": 9'))

    with pytest.raises(PersistenceConflict, match="payload digest mismatch"):
        artifacts.load(
            campaign_id=campaign.campaign_id,
            kind=CampaignArtifactKind.DAILY_SCORECARD,
            key="2026-11-12",
        )
