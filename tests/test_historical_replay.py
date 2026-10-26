import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from src.database import Base
from src.domain.evidence import SourceDocument
from src.historical_replay import (
    HistoricalProposalGate,
    HistoricalReplayService,
    HistoricalReplayUnavailable,
    ResearchReplayRequirement,
)
from src.market_data import (
    Bar,
    MarketDataStore,
    MarketEvent,
    ReplayRequirement,
)
from src.persistence.evidence_repository import EvidenceRepository

T = datetime(2026, 10, 26, 14, 0, tzinfo=UTC)


@pytest.fixture
def session():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session, session.begin():
        yield session
    engine.dispose()


def market_event(*, revision=1, available_at=None, close="100") -> MarketEvent:
    available_at = available_at or (T - timedelta(seconds=20))
    return MarketEvent(
        source="synthetic-json-v1",
        symbol="ABC",
        event_time=T - timedelta(seconds=30),
        available_at=available_at,
        revision=revision,
        payload=Bar(
            interval="1m",
            open=Decimal("99"),
            high=Decimal("101"),
            low=Decimal("98"),
            close=Decimal(close),
            volume=1000,
        ),
    )


def source_document(
    *,
    version=1,
    published_at=None,
    available_at=None,
    correction_of_version=None,
    content_hash=None,
) -> SourceDocument:
    published_at = published_at or (T - timedelta(hours=2))
    available_at = available_at or (T - timedelta(hours=1))
    return SourceDocument(
        source_id="sec:abc:10-q:2026-q3",
        version=version,
        source_type="regulatory_filing",
        source_url="https://example.test/sec/abc-q3",
        publisher="SEC",
        subject="ABC",
        published_at=published_at,
        observed_at=available_at,
        available_at=available_at,
        correction_of_version=correction_of_version,
        content_hash=content_hash or ("a" * 64),
        version_metadata={"form": "10-Q" if version == 1 else "10-Q/A"},
    )


def market_requirements(max_age=timedelta(minutes=1)):
    return (
        ReplayRequirement(
            source="synthetic-json-v1",
            symbol="ABC",
            stream="bar:1m",
            max_age=max_age,
        ),
    )


def research_requirements(max_age=timedelta(hours=4)):
    return (
        ResearchReplayRequirement(
            subject="ABC",
            source_type="regulatory_filing",
            max_publication_age=max_age,
        ),
    )


def seed(session):
    MarketDataStore(session, clock=lambda: T - timedelta(seconds=10)).ingest(
        market_event()
    )
    filing = EvidenceRepository(session).ingest_source(
        source_document(),
        ingested_at=T - timedelta(minutes=50),
    )
    return filing


def replay(session, *, as_of=T, market_age=None, research_age=None):
    return HistoricalReplayService(session).replay(
        as_of=as_of,
        market_requirements=market_requirements(
            market_age if market_age is not None else timedelta(minutes=1)
        ),
        research_requirements=research_requirements(
            research_age if research_age is not None else timedelta(hours=4)
        ),
    )


def test_unified_replay_is_deterministic_and_contains_then_known_inputs(session):
    filing = seed(session)

    first = replay(session)
    second = replay(session)

    assert first == second
    assert first.digest == second.digest
    assert first.market.observations[0].event.revision == 1
    assert first.research[0].sources == (filing,)
    assert first.research[0].sources[0].effective_ingested_at <= T


def test_yesterday_context_never_reveals_market_or_filing_corrections_learned_today(
    session,
):
    seed(session)
    yesterday = replay(session)

    tomorrow = T + timedelta(days=1)
    MarketDataStore(session, clock=lambda: tomorrow).ingest(
        market_event(
            revision=2,
            available_at=tomorrow,
            close="100.5",
        )
    )
    EvidenceRepository(session).ingest_source(
        source_document(
            version=2,
            available_at=tomorrow,
            correction_of_version=1,
            content_hash="b" * 64,
        ),
        ingested_at=tomorrow,
    )

    replayed_yesterday = replay(session)
    assert replayed_yesterday.digest == yesterday.digest
    assert replayed_yesterday.market.observations[0].event.revision == 1
    assert replayed_yesterday.research[0].sources[0].version == 1

    current = replay(
        session,
        as_of=tomorrow,
        market_age=timedelta(days=2),
        research_age=timedelta(days=2),
    )
    assert current.market.observations[0].event.revision == 2
    assert current.research[0].sources[0].version == 2


def test_missing_research_blocks_proposal_factory(session):
    MarketDataStore(session, clock=lambda: T - timedelta(seconds=10)).ingest(
        market_event()
    )
    called = False

    def factory(context):
        nonlocal called
        called = True
        return context.digest

    gate = HistoricalProposalGate(HistoricalReplayService(session))
    with pytest.raises(HistoricalReplayUnavailable, match="MISSING_RESEARCH"):
        gate.create_proposal(
            as_of=T,
            market_requirements=market_requirements(),
            research_requirements=research_requirements(),
            proposal_factory=factory,
        )
    assert not called


def test_stale_market_blocks_proposal_factory(session):
    seed(session)
    called = False

    def factory(context):
        nonlocal called
        called = True
        return context

    gate = HistoricalProposalGate(HistoricalReplayService(session))
    with pytest.raises(HistoricalReplayUnavailable, match="MARKET:STALE"):
        gate.create_proposal(
            as_of=T + timedelta(minutes=2),
            market_requirements=market_requirements(),
            research_requirements=research_requirements(),
            proposal_factory=factory,
        )
    assert not called


def test_stale_research_blocks_proposal_factory(session):
    MarketDataStore(session, clock=lambda: T - timedelta(seconds=10)).ingest(
        market_event()
    )
    EvidenceRepository(session).ingest_source(
        source_document(published_at=T - timedelta(days=2)),
        ingested_at=T - timedelta(minutes=50),
    )
    called = False

    def factory(context):
        nonlocal called
        called = True
        return context

    gate = HistoricalProposalGate(HistoricalReplayService(session))
    with pytest.raises(HistoricalReplayUnavailable, match="STALE_RESEARCH"):
        gate.create_proposal(
            as_of=T,
            market_requirements=market_requirements(),
            research_requirements=research_requirements(timedelta(hours=12)),
            proposal_factory=factory,
        )
    assert not called


def test_successful_replay_is_required_before_proposal_factory_is_called(session):
    seed(session)
    calls = []

    def factory(context):
        calls.append(context.digest)
        return {"context_digest": context.digest, "as_of": context.as_of}

    proposal = HistoricalProposalGate(HistoricalReplayService(session)).create_proposal(
        as_of=T,
        market_requirements=market_requirements(),
        research_requirements=research_requirements(),
        proposal_factory=factory,
    )

    assert calls == [proposal["context_digest"]]
    assert proposal["as_of"] == T


def test_explicit_requirements_and_utc_timestamp_are_mandatory(session):
    seed(session)
    service = HistoricalReplayService(session)

    with pytest.raises(ValueError, match="market replay requirements"):
        service.replay(
            as_of=T,
            market_requirements=(),
            research_requirements=research_requirements(),
        )
    with pytest.raises(ValueError, match="research replay requirements"):
        service.replay(
            as_of=T,
            market_requirements=market_requirements(),
            research_requirements=(),
        )
    with pytest.raises(ValueError, match="UTC"):
        service.replay(
            as_of=T.replace(tzinfo=None),
            market_requirements=market_requirements(),
            research_requirements=research_requirements(),
        )
