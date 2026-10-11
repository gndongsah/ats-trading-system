from fastapi.testclient import TestClient

from src.api import app
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
