from datetime import UTC, datetime, timedelta
from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.api import app
from src.database import Base, get_db
from src.domain.evidence import EvidenceItem, SourceDocument
from src.domain.models import EvidenceStatus
from src.persistence.evidence_repository import EvidenceRepository
from src.playbook import PLAYBOOK

client = TestClient(app)


def test_health_endpoint_returns_healthy_status():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "healthy"}


def test_playbook_version_endpoint_returns_yaml_version():
    response = client.get("/playbook/version")
    assert response.status_code == 200
    assert response.json() == {"version": "3.0"}


def test_layer_endpoints_return_deterministic_decisions():
    market = client.post(
        "/layer0/market-opportunity",
        json={
            "factor_scores": {
                factor: PLAYBOOK.market_opportunity.max_score
                for factor in PLAYBOOK.market_opportunity.factors
            }
        },
    )
    discovery = client.post(
        "/layer1/discovery",
        json={"factor_scores": {factor: 0 for factor in PLAYBOOK.discovery.factors}},
    )

    assert market.status_code == 200
    assert market.json()["decision"] == "ACTIVE"
    assert market.json()["score"] == PLAYBOOK.market_opportunity.max_score
    assert discovery.status_code == 200
    assert discovery.json()["decision"] == "REJECT"
    assert discovery.json()["score"] == 0


def test_layer_endpoints_report_invalid_factor_input():
    response = client.post(
        "/layer0/market-opportunity",
        json={"factor_scores": {"unknown_factor": 10}},
    )
    assert response.status_code == 422
    assert "missing factors" in response.json()["detail"]


def test_discovery_api_denies_positive_points_without_evidence():
    factor_scores = {factor: 0 for factor in PLAYBOOK.discovery.factors}
    factor_scores["demand_acceleration"] = 80

    response = client.post(
        "/layer1/discovery",
        json={"factor_scores": factor_scores},
    )

    assert response.status_code == 200
    assert response.json()["score"] == 0
    factor_result = response.json()["eligibility"]["demand_acceleration"]
    assert factor_result["allowed_contribution"] == "0"
    assert factor_result["reason_codes"] == ["no_evidence"]


def test_discovery_api_scores_only_persisted_verified_evidence():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    decision_at = datetime(2026, 10, 14, 14, 0, tzinfo=UTC)
    source = SourceDocument(
        source_id="api-source-abc",
        version=1,
        source_type="filing",
        publisher="ABC Inc.",
        subject="ABC",
        published_at=decision_at - timedelta(hours=1),
        observed_at=decision_at - timedelta(minutes=30),
        content_hash="b" * 64,
    )
    evidence = EvidenceItem(
        source_document_version_id=source.id,
        subject="ABC",
        factor="demand_acceleration",
        excerpt="Demand grew during the quarter.",
        confidence=Decimal("0.91"),
        verification_status=EvidenceStatus.VERIFIED,
        verifier_identity="reviewer:api-test",
        verified_at=decision_at - timedelta(minutes=10),
        verification_method="source_reconciliation",
        verification_policy_version="ats-evidence-1",
        observed_at=decision_at - timedelta(minutes=20),
    )
    with factory.begin() as session:
        repository = EvidenceRepository(session)
        repository.save_source(source)
        repository.save_evidence(evidence)

    def override_get_db():
        with factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    factor_scores = {factor: 0 for factor in PLAYBOOK.discovery.factors}
    factor_scores["demand_acceleration"] = 100
    try:
        response = client.post(
            "/layer1/discovery",
            json={
                "factor_scores": factor_scores,
                "subject": "ABC",
                "decision_at": decision_at.isoformat(),
                "evidence_by_factor": {
                    "demand_acceleration": [
                        {"evidence_id": str(evidence.id), "version": 1}
                    ]
                },
            },
        )
    finally:
        app.dependency_overrides.clear()
        Base.metadata.drop_all(engine)
        engine.dispose()

    assert response.status_code == 200
    assert response.json()["score"] == 15
    factor_result = response.json()["eligibility"]["demand_acceleration"]
    assert factor_result["eligible"]
    assert Decimal(factor_result["allowed_contribution"]) == 100
    assert factor_result["supporting_evidence"][0]["evidence_id"] == str(evidence.id)
