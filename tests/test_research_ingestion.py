import json
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.orm import Session

from src.database import Base, create_database_engine
from src.domain.evidence import EvidenceItem, SourceDocument
from src.domain.models import EvidenceStatus
from src.evidence_verification import EligibilityReason, evaluate_scoring_eligibility
from src.persistence.evidence_repository import EvidenceRepository
from src.persistence.repository import PersistenceConflict
from src.research_ingestion import JsonResearchDocumentAdapter

T0 = datetime(2026, 10, 26, 14, 0, tzinfo=UTC)


def payload(**overrides):
    values = {
        "source_id": "sec:abc:10-q:2026-q3",
        "version": 1,
        "source_type": "regulatory_filing",
        "source_url": "https://example.test/sec/abc-q3",
        "publisher": "SEC",
        "subject": "ABC",
        "published_at": (T0 - timedelta(minutes=30)).isoformat(),
        "available_at": (T0 - timedelta(minutes=25)).isoformat(),
        "content_hash": "a" * 64,
        "version_metadata": {"form": "10-Q", "accession": "0000123"},
    }
    values.update(overrides)
    return values


def parse_one(**overrides) -> SourceDocument:
    document = json.dumps([payload(**overrides)])
    return JsonResearchDocumentAdapter().parse(document)[0]


def test_adapter_normalizes_filing_and_news_with_deterministic_version_ids():
    adapter = JsonResearchDocumentAdapter()
    filing = adapter.parse(json.dumps([payload()]))[0]
    news = adapter.parse(
        json.dumps(
            [
                payload(
                    source_id="news:wire:abc:1",
                    source_type="news",
                    publisher="Wire Service",
                    version_metadata={"story_id": "wire-1"},
                )
            ]
        )
    )[0]
    repeat = adapter.parse(json.dumps([payload()]))[0]

    assert filing.id == repeat.id
    assert filing.source_type == "regulatory_filing"
    assert filing.available_at == T0 - timedelta(minutes=25)
    assert filing.observed_at == filing.available_at
    assert filing.ingested_at is None
    assert news.source_type == "news"


def test_ingestion_owns_clock_and_retry_preserves_first_ingestion_time():
    engine = create_database_engine("sqlite://")
    Base.metadata.create_all(engine)
    source = parse_one()
    first_ingestion = T0 - timedelta(minutes=20)
    try:
        with Session(engine) as session, session.begin():
            repository = EvidenceRepository(session)
            first = repository.ingest_source(source, ingested_at=first_ingestion)
        with Session(engine) as session, session.begin():
            repository = EvidenceRepository(session)
            retry = repository.ingest_source(
                source, ingested_at=first_ingestion + timedelta(hours=2)
            )
        assert first == retry
        assert first.ingested_at == first_ingestion
    finally:
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_same_source_version_with_different_content_fails_closed():
    engine = create_database_engine("sqlite://")
    Base.metadata.create_all(engine)
    source = parse_one()
    conflict = parse_one(publisher="Different Publisher")
    try:
        with Session(engine) as session, session.begin():
            EvidenceRepository(session).ingest_source(
                source, ingested_at=T0 - timedelta(minutes=20)
            )
        with (
            pytest.raises(PersistenceConflict, match="already exists"),
            Session(engine) as session,
            session.begin(),
        ):
            EvidenceRepository(session).ingest_source(
                conflict, ingested_at=T0 - timedelta(minutes=19)
            )
    finally:
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_as_of_read_preserves_old_filing_until_correction_is_ingested():
    engine = create_database_engine("sqlite://")
    Base.metadata.create_all(engine)
    v1 = parse_one()
    v2 = parse_one(
        version=2,
        correction_of_version=1,
        available_at=(T0 + timedelta(minutes=10)).isoformat(),
        content_hash="b" * 64,
        version_metadata={"form": "10-Q/A", "accession": "0000123-A"},
    )
    try:
        with Session(engine) as session, session.begin():
            repository = EvidenceRepository(session)
            repository.ingest_source(v1, ingested_at=T0 - timedelta(minutes=20))
            repository.ingest_source(v2, ingested_at=T0 + timedelta(minutes=20))

        with Session(engine) as session:
            repository = EvidenceRepository(session)
            before_correction = repository.list_sources_as_of(
                as_of=T0 + timedelta(minutes=15),
                subject="ABC",
                source_types=("regulatory_filing",),
            )
            after_correction = repository.list_sources_as_of(
                as_of=T0 + timedelta(minutes=20),
                subject="ABC",
                source_types=("regulatory_filing",),
            )

        assert before_correction == (
            v1.model_copy(update={"ingested_at": T0 - timedelta(minutes=20)}),
        )
        assert after_correction[0].version == 2
        assert after_correction[0].correction_of_version == 1
        assert after_correction[0].content_hash == "b" * 64
    finally:
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_delayed_news_is_invisible_until_both_available_and_ingested():
    engine = create_database_engine("sqlite://")
    Base.metadata.create_all(engine)
    news = parse_one(
        source_id="news:abc:capacity:1",
        source_type="news",
        publisher="Wire Service",
        published_at=(T0 - timedelta(hours=1)).isoformat(),
        available_at=(T0 + timedelta(minutes=5)).isoformat(),
        version_metadata={"story_id": "capacity-1"},
    )
    try:
        with Session(engine) as session, session.begin():
            EvidenceRepository(session).ingest_source(
                news, ingested_at=T0 + timedelta(minutes=12)
            )
        with Session(engine) as session:
            repository = EvidenceRepository(session)
            assert repository.list_sources_as_of(as_of=T0) == ()
            assert repository.list_sources_as_of(
                as_of=T0 + timedelta(minutes=8)
            ) == ()
            visible = repository.list_sources_as_of(
                as_of=T0 + timedelta(minutes=12)
            )
        assert len(visible) == 1
        assert visible[0].source_id == news.source_id
    finally:
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_scoring_rejects_source_ingested_after_decision():
    source = parse_one(
        published_at=(T0 - timedelta(hours=1)).isoformat(),
        available_at=(T0 - timedelta(minutes=10)).isoformat(),
    ).model_copy(update={"ingested_at": T0 + timedelta(seconds=1)})
    evidence = EvidenceItem(
        source_document_version_id=source.id,
        subject="ABC",
        factor="demand_acceleration",
        inflection_stage=5,
        excerpt="Demand increased.",
        confidence="0.9",
        verification_status=EvidenceStatus.VERIFIED,
        verifier_identity="reviewer:test",
        verified_at=T0 - timedelta(seconds=1),
        verification_method="source_reconciliation",
        verification_policy_version="ats-evidence-1",
        observed_at=T0 - timedelta(minutes=5),
        recorded_at=T0 - timedelta(minutes=4),
    )
    result = evaluate_scoring_eligibility(
        subject="ABC",
        factor="demand_acceleration",
        requested_contribution="10",
        decision_at=T0,
        evidence_items=(evidence,),
        sources={source.id: source},
        inflection_stage=5,
    )
    assert not result.eligible
    assert EligibilityReason.INGESTION_AFTER_DECISION in result.reason_codes
