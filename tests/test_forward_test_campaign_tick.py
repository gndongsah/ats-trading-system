from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.database import Base
from src.forward_test_campaign_ops import (
    CampaignArtifactKind,
    CampaignArtifactRepository,
    CampaignOperationsService,
)
from src.forward_test_campaign_tick import (
    DAY1_ACTIVATION_KEY,
    CampaignTickDisposition,
    CampaignTickRunner,
)
from src.forward_test_scheduler import CampaignScheduler
from src.market_data import Bar, MarketEvent, Observation

START = datetime(2026, 11, 12, 14, 45, tzinfo=UTC)


class FakeCollector:
    def __init__(self, observed_at: datetime) -> None:
        self.observed_at = observed_at
        self.calls = 0

    def collect(self, *, symbol: str, category: str, as_of=None) -> Observation:
        self.calls += 1
        available_at = self.observed_at - timedelta(seconds=2)
        return Observation(
            event=MarketEvent(
                source="webull-test",
                symbol=symbol,
                event_time=self.observed_at - timedelta(minutes=1),
                available_at=available_at,
                revision=1,
                payload=Bar(
                    interval="1m",
                    open=Decimal("100"),
                    high=Decimal("101"),
                    low=Decimal("99"),
                    close=Decimal("100.5"),
                    volume=1000,
                ),
            ),
            ingested_at=self.observed_at - timedelta(seconds=1),
        )


@pytest.fixture
def environment(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'campaign.db'}")
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
        yield campaign, factory, artifacts, service
    finally:
        engine.dispose()


def test_tick_before_start_is_noop(environment):
    campaign, _, artifacts, service = environment
    collector = FakeCollector(START)
    result = CampaignTickRunner(
        service=service,
        artifacts=artifacts,
        scheduler=CampaignScheduler(),
        market_collector=collector,
        clock=lambda: START - timedelta(minutes=1),
    ).tick(campaign_id=campaign.campaign_id)

    assert result.disposition is CampaignTickDisposition.NOOP
    assert result.activated is False
    assert result.reason == "CAMPAIGN_NOT_STARTED"
    assert collector.calls == 0


def test_first_market_window_activates_exact_campaign_identity(environment):
    campaign, _, artifacts, service = environment
    now = START + timedelta(minutes=1)
    collector = FakeCollector(now)
    runner = CampaignTickRunner(
        service=service,
        artifacts=artifacts,
        scheduler=CampaignScheduler(),
        market_collector=collector,
        clock=lambda: now,
    )

    result = runner.tick(campaign_id=campaign.campaign_id)

    assert result.disposition is CampaignTickDisposition.PASS
    assert result.activated is True
    assert result.broker_order_submission == "DISABLED"
    assert result.live_execution_used is False
    assert result.proposal_creation_used is False
    assert collector.calls == 1

    activation = artifacts.load(
        campaign_id=campaign.campaign_id,
        kind=CampaignArtifactKind.ACTIVATION,
        key=DAY1_ACTIVATION_KEY,
    )
    assert activation is not None
    assert activation.campaign_id == campaign.campaign_id
    assert activation.payload["first_window_scheduled_at"] == START.isoformat()


def test_repeated_tick_is_idempotent_after_activation(environment):
    campaign, _, artifacts, service = environment
    now = START + timedelta(minutes=1)
    collector = FakeCollector(now)
    runner = CampaignTickRunner(
        service=service,
        artifacts=artifacts,
        scheduler=CampaignScheduler(),
        market_collector=collector,
        clock=lambda: now,
    )
    runner.tick(campaign_id=campaign.campaign_id)

    replay = runner.tick(campaign_id=campaign.campaign_id)

    assert replay.disposition is CampaignTickDisposition.NOOP
    assert replay.activated is True
    assert replay.executed_jobs == ()
    assert collector.calls == 1


def test_missing_first_window_holds_without_shifting_campaign(environment):
    campaign, _, artifacts, service = environment
    now = START + timedelta(minutes=6)
    result = CampaignTickRunner(
        service=service,
        artifacts=artifacts,
        scheduler=CampaignScheduler(),
        market_collector=FakeCollector(now),
        clock=lambda: now,
    ).tick(campaign_id=campaign.campaign_id)

    assert result.disposition is CampaignTickDisposition.HOLD
    assert result.activated is False
    assert result.reason == "DAY1_FIRST_MARKET_WINDOW_MISSED"
    assert service.load_campaign(campaign.campaign_id) == campaign


def test_restart_reuses_activation_and_persists_daily_scorecard(environment):
    campaign, factory, artifacts, _ = environment
    first_now = START + timedelta(minutes=1)
    first_service = CampaignOperationsService(
        session_factory=factory,
        artifacts=artifacts,
    )
    CampaignTickRunner(
        service=first_service,
        artifacts=artifacts,
        scheduler=CampaignScheduler(),
        market_collector=FakeCollector(first_now),
        clock=lambda: first_now,
    ).tick(campaign_id=campaign.campaign_id)

    after_close = datetime(2026, 11, 12, 21, 16, tzinfo=UTC)
    restarted_service = CampaignOperationsService(
        session_factory=factory,
        artifacts=artifacts,
    )
    result = CampaignTickRunner(
        service=restarted_service,
        artifacts=artifacts,
        scheduler=CampaignScheduler(),
        market_collector=FakeCollector(after_close),
        clock=lambda: after_close,
    ).tick(campaign_id=campaign.campaign_id)

    assert result.disposition is CampaignTickDisposition.PASS
    assert result.activated is True
    assert "DAILY_SCORECARD:2026-11-12" in result.executed_jobs
    scorecard = artifacts.load(
        campaign_id=campaign.campaign_id,
        kind=CampaignArtifactKind.DAILY_SCORECARD,
        key="2026-11-12",
    )
    assert scorecard is not None
    assert scorecard.campaign_id == campaign.campaign_id
