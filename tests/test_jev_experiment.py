import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from src.database import Base
from src.jev_experiment import (
    ExperimentArm,
    PairedExperimentResult,
    TriageOutcome,
    TriagePrediction,
    calibration_points,
    summarize_calibration,
)
from src.persistence.triage_experiment_repository import TriageExperimentRepository
from src.typesafe_jev import TypeSafeJevClient, parse_boolean_response

NOW = datetime(2026, 10, 27, 16, 0, tzinfo=UTC)


def prediction(
    *,
    experiment_id,
    case_id="case-1",
    arm=ExperimentArm.LLM_ONLY,
    label="review",
    probability=0.8,
    model_name="baseline-llm",
):
    return TriagePrediction(
        experiment_id=experiment_id,
        case_id=case_id,
        subject="ABC",
        arm=arm,
        predicted_label=label,
        probability=probability,
        model_name=model_name,
        observed_at=NOW,
    )


def test_paired_experiment_requires_same_case_and_distinct_arms():
    experiment_id = uuid4()
    baseline = prediction(experiment_id=experiment_id)
    jev = prediction(
        experiment_id=experiment_id,
        arm=ExperimentArm.LLM_JEV,
        model_name="jev-latest",
    )

    pair = PairedExperimentResult(case_id="case-1", llm_only=baseline, llm_jev=jev)

    assert pair.llm_only.arm is ExperimentArm.LLM_ONLY
    assert pair.llm_jev.arm is ExperimentArm.LLM_JEV


def test_calibration_summary_compares_confidence_to_correctness():
    experiment_id = uuid4()
    predictions = (
        prediction(
            experiment_id=experiment_id,
            case_id="a",
            probability=0.9,
            label="review",
        ),
        prediction(
            experiment_id=experiment_id,
            case_id="b",
            probability=0.8,
            label="pass",
        ),
    )
    outcomes = {
        "a": TriageOutcome(
            case_id="a",
            actual_label="review",
            resolved_at=NOW + timedelta(hours=1),
        ),
        "b": TriageOutcome(
            case_id="b",
            actual_label="review",
            resolved_at=NOW + timedelta(hours=1),
        ),
    }

    points = calibration_points(predictions=predictions, outcomes=outcomes)
    summary = summarize_calibration(arm=ExperimentArm.LLM_ONLY, points=points)

    assert summary.observations == 2
    assert summary.accuracy == pytest.approx(0.5)
    assert summary.mean_confidence == pytest.approx(0.85)
    assert summary.brier_score == pytest.approx(0.325)
    assert summary.calibration_gap == pytest.approx(0.35)


def test_typesafe_parser_preserves_returned_model_probability_and_usage():
    raw = json.dumps(
        {
            "model": "jev-1.14.0",
            "answers": {"needs_review": {"noul": 0.93, "type": "noul"}},
            "usage": {"input_tokens": 120, "output_tokens": 12},
        }
    ).encode()

    answer = parse_boolean_response(raw, question_name="needs_review")

    assert answer.model == "jev-1.14.0"
    assert answer.probability_true == pytest.approx(0.93)
    assert answer.input_tokens == 120
    assert answer.output_tokens == 12


def test_typesafe_client_requires_environment_api_key(monkeypatch):
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)

    with pytest.raises(RuntimeError, match="TYPESAFE_API_KEY"):
        TypeSafeJevClient.from_environment()


def test_typesafe_client_builds_systemone_request_without_exposing_key():
    captured = {}

    def transport(request, timeout):
        captured["authorization"] = request.headers["Authorization"]
        captured["payload"] = json.loads(request.data.decode())
        captured["timeout"] = timeout
        return json.dumps(
            {
                "model": "jev-1.14.0",
                "answers": {"needs_review": {"noul": 0.7, "type": "noul"}},
                "usage": {"input_tokens": 10, "output_tokens": 1},
            }
        ).encode()

    client = TypeSafeJevClient(api_key="secret-test-key", model="jev-latest")
    answer = client.ask_boolean(
        state={"subject": "ABC", "claim": "Demand accelerated."},
        question_name="needs_review",
        instructions="Does this research claim require second-pass review?",
        transport=transport,
    )

    assert captured["authorization"] == "Bearer secret-test-key"
    assert captured["payload"]["model"] == "jev-latest"
    assert captured["payload"]["questions"]["needs_review"]["type"] == "noul"
    assert answer.probability_true == pytest.approx(0.7)


def test_repository_persists_both_arms_and_outcome_for_calibration():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    experiment_id = uuid4()
    baseline = prediction(experiment_id=experiment_id)
    jev = prediction(
        experiment_id=experiment_id,
        arm=ExperimentArm.LLM_JEV,
        probability=0.91,
        model_name="jev-latest",
    )

    with Session(engine) as session:
        repo = TriageExperimentRepository(session)
        repo.save_prediction(baseline)
        repo.save_prediction(jev)
        count = repo.attach_outcome(
            experiment_id=experiment_id,
            outcome=TriageOutcome(
                case_id="case-1",
                actual_label="review",
                resolved_at=NOW + timedelta(hours=1),
            ),
        )
        session.commit()

        predictions = repo.list_predictions(experiment_id=experiment_id)
        outcomes = repo.list_outcomes(experiment_id=experiment_id)

    assert count == 2
    assert {item.arm for item in predictions} == {
        ExperimentArm.LLM_ONLY,
        ExperimentArm.LLM_JEV,
    }
    assert outcomes["case-1"].actual_label == "review"


def test_outcome_before_prediction_is_rejected():
    experiment_id = uuid4()
    pred = prediction(experiment_id=experiment_id)
    outcome = TriageOutcome(
        case_id="case-1",
        actual_label="review",
        resolved_at=NOW - timedelta(seconds=1),
    )

    with pytest.raises(ValueError, match="cannot resolve before"):
        calibration_points(predictions=(pred,), outcomes={"case-1": outcome})
