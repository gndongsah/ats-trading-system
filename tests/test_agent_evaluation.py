from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from src.agent_evaluation import compare_experiment_arms, evaluate_agent_predictions
from src.database import Base
from src.jev_experiment import ExperimentArm, TriageOutcome, TriagePrediction
from src.persistence.agent_evaluation_repository import AgentEvaluationRepository

T0 = datetime(2026, 10, 27, 15, 0, tzinfo=UTC)
T1 = T0 + timedelta(hours=1)
T2 = T0 + timedelta(hours=2)


def pred(*, experiment_id, case_id, arm, label, probability):
    return TriagePrediction(
        experiment_id=experiment_id,
        case_id=case_id,
        subject="ABC",
        arm=arm,
        predicted_label=label,
        probability=probability,
        model_name="jev-latest" if arm is ExperimentArm.LLM_JEV else "baseline-llm",
        observed_at=T0,
    )


def outcome(case_id, label, resolved_at=T1):
    return TriageOutcome(
        case_id=case_id,
        actual_label=label,
        resolved_at=resolved_at,
    )


def test_agent_metrics_compute_precision_recall_f1_and_accuracy():
    experiment_id = uuid4()
    predictions = (
        pred(
            experiment_id=experiment_id,
            case_id="tp",
            arm=ExperimentArm.LLM_ONLY,
            label="review",
            probability=0.9,
        ),
        pred(
            experiment_id=experiment_id,
            case_id="fp",
            arm=ExperimentArm.LLM_ONLY,
            label="review",
            probability=0.8,
        ),
        pred(
            experiment_id=experiment_id,
            case_id="fn",
            arm=ExperimentArm.LLM_ONLY,
            label="pass",
            probability=0.7,
        ),
        pred(
            experiment_id=experiment_id,
            case_id="tn",
            arm=ExperimentArm.LLM_ONLY,
            label="pass",
            probability=0.6,
        ),
    )
    outcomes = {
        "tp": outcome("tp", "review"),
        "fp": outcome("fp", "pass"),
        "fn": outcome("fn", "review"),
        "tn": outcome("tn", "pass"),
    }

    summary = evaluate_agent_predictions(
        experiment_id=experiment_id,
        arm=ExperimentArm.LLM_ONLY,
        predictions=predictions,
        outcomes=outcomes,
        positive_label="review",
        as_of=T1,
        evaluated_at=T2,
    )

    assert summary.observations == 4
    assert summary.classification.true_positive == 1
    assert summary.classification.false_positive == 1
    assert summary.classification.false_negative == 1
    assert summary.classification.true_negative == 1
    assert summary.classification.precision == pytest.approx(0.5)
    assert summary.classification.recall == pytest.approx(0.5)
    assert summary.classification.f1 == pytest.approx(0.5)
    assert summary.classification.accuracy == pytest.approx(0.5)


def test_point_in_time_evaluation_excludes_future_outcomes():
    experiment_id = uuid4()
    predictions = (
        pred(
            experiment_id=experiment_id,
            case_id="known",
            arm=ExperimentArm.LLM_ONLY,
            label="review",
            probability=0.9,
        ),
        pred(
            experiment_id=experiment_id,
            case_id="future",
            arm=ExperimentArm.LLM_ONLY,
            label="review",
            probability=0.9,
        ),
    )
    outcomes = {
        "known": outcome("known", "review", T1),
        "future": outcome("future", "pass", T2),
    }

    summary = evaluate_agent_predictions(
        experiment_id=experiment_id,
        arm=ExperimentArm.LLM_ONLY,
        predictions=predictions,
        outcomes=outcomes,
        positive_label="review",
        as_of=T1,
        evaluated_at=T2,
    )

    assert summary.observations == 1
    assert summary.classification.true_positive == 1
    assert summary.classification.false_positive == 0
    assert summary.calibration.observations == 1


def test_zero_denominators_fail_closed_to_zero_metrics():
    experiment_id = uuid4()
    summary = evaluate_agent_predictions(
        experiment_id=experiment_id,
        arm=ExperimentArm.LLM_ONLY,
        predictions=(),
        outcomes={},
        positive_label="review",
        as_of=T1,
        evaluated_at=T2,
    )

    assert summary.observations == 0
    assert summary.classification.precision == 0.0
    assert summary.classification.recall == 0.0
    assert summary.classification.f1 == 0.0
    assert summary.classification.accuracy == 0.0


def test_compare_experiment_arms_produces_same_cutoff_for_both_arms():
    experiment_id = uuid4()
    predictions = (
        pred(
            experiment_id=experiment_id,
            case_id="case-1",
            arm=ExperimentArm.LLM_ONLY,
            label="pass",
            probability=0.7,
        ),
        pred(
            experiment_id=experiment_id,
            case_id="case-1",
            arm=ExperimentArm.LLM_JEV,
            label="review",
            probability=0.9,
        ),
    )
    outcomes = {"case-1": outcome("case-1", "review")}

    comparison = compare_experiment_arms(
        experiment_id=experiment_id,
        predictions=predictions,
        outcomes=outcomes,
        positive_label="review",
        as_of=T1,
        evaluated_at=T2,
    )

    assert comparison.llm_only.classification.recall == 0.0
    assert comparison.llm_jev.classification.recall == 1.0
    assert comparison.llm_only.as_of == comparison.llm_jev.as_of == T1


def test_evaluated_at_cannot_precede_point_in_time_cutoff():
    with pytest.raises(ValueError, match="cannot precede"):
        evaluate_agent_predictions(
            experiment_id=uuid4(),
            arm=ExperimentArm.LLM_ONLY,
            predictions=(),
            outcomes={},
            positive_label="review",
            as_of=T2,
            evaluated_at=T1,
        )


def test_repository_round_trips_evaluation_snapshot():
    experiment_id = uuid4()
    predictions = (
        pred(
            experiment_id=experiment_id,
            case_id="case-1",
            arm=ExperimentArm.LLM_JEV,
            label="review",
            probability=0.9,
        ),
    )
    outcomes = {"case-1": outcome("case-1", "review")}
    summary = evaluate_agent_predictions(
        experiment_id=experiment_id,
        arm=ExperimentArm.LLM_JEV,
        predictions=predictions,
        outcomes=outcomes,
        positive_label="review",
        as_of=T1,
        evaluated_at=T2,
    )

    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        repository = AgentEvaluationRepository(session)
        repository.save(summary)
        session.commit()
        loaded = repository.list_for_experiment(experiment_id=experiment_id)

    assert len(loaded) == 1
    assert loaded[0].id == summary.id
    assert loaded[0].classification.precision == pytest.approx(1.0)
    assert loaded[0].calibration.brier_score == pytest.approx(0.01)
