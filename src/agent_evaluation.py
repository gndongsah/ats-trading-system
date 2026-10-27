"""ATS-021 research-agent evaluation metrics."""

from datetime import datetime
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator

from src.domain.models import _require_utc
from src.jev_experiment import (
    CalibrationSummary,
    ExperimentArm,
    TriageOutcome,
    TriagePrediction,
    calibration_points,
    summarize_calibration,
)


class ClassificationMetrics(BaseModel):
    model_config = ConfigDict(frozen=True)

    true_positive: int = Field(ge=0)
    false_positive: int = Field(ge=0)
    false_negative: int = Field(ge=0)
    true_negative: int = Field(ge=0)
    precision: float = Field(ge=0.0, le=1.0)
    recall: float = Field(ge=0.0, le=1.0)
    f1: float = Field(ge=0.0, le=1.0)
    accuracy: float = Field(ge=0.0, le=1.0)


class AgentEvaluationSummary(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: UUID = Field(default_factory=uuid4)
    experiment_id: UUID
    arm: ExperimentArm
    positive_label: str = Field(min_length=1, max_length=128)
    evaluated_at: datetime
    as_of: datetime
    observations: int = Field(ge=0)
    classification: ClassificationMetrics
    calibration: CalibrationSummary

    @field_validator("positive_label")
    @classmethod
    def trim_positive_label(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("positive_label must not be blank")
        return value

    @field_validator("evaluated_at", "as_of")
    @classmethod
    def times_are_utc(cls, value: datetime) -> datetime:
        return _require_utc(value)


class AgentArmComparison(BaseModel):
    model_config = ConfigDict(frozen=True)

    experiment_id: UUID
    positive_label: str
    as_of: datetime
    llm_only: AgentEvaluationSummary
    llm_jev: AgentEvaluationSummary

    @field_validator("as_of")
    @classmethod
    def as_of_is_utc(cls, value: datetime) -> datetime:
        return _require_utc(value)


def _safe_ratio(numerator: int, denominator: int) -> float:
    if denominator == 0:
        return 0.0
    return numerator / denominator


def _classification_metrics(
    *,
    predictions: tuple[TriagePrediction, ...],
    outcomes: dict[str, TriageOutcome],
    positive_label: str,
    as_of: datetime,
) -> tuple[ClassificationMetrics, int]:
    normalized_positive = positive_label.casefold()
    tp = fp = fn = tn = 0
    observations = 0

    for prediction in predictions:
        if prediction.observed_at > as_of:
            continue
        outcome = outcomes.get(prediction.case_id)
        if outcome is None or outcome.resolved_at > as_of:
            continue
        if outcome.resolved_at < prediction.observed_at:
            raise ValueError("outcome cannot resolve before prediction is observed")

        predicted_positive = (
            prediction.predicted_label.casefold() == normalized_positive
        )
        actual_positive = outcome.actual_label.casefold() == normalized_positive
        observations += 1
        if predicted_positive and actual_positive:
            tp += 1
        elif predicted_positive:
            fp += 1
        elif actual_positive:
            fn += 1
        else:
            tn += 1

    precision = _safe_ratio(tp, tp + fp)
    recall = _safe_ratio(tp, tp + fn)
    f1 = _safe_ratio(2 * precision * recall, precision + recall)
    accuracy = _safe_ratio(tp + tn, observations)

    return (
        ClassificationMetrics(
            true_positive=tp,
            false_positive=fp,
            false_negative=fn,
            true_negative=tn,
            precision=precision,
            recall=recall,
            f1=f1,
            accuracy=accuracy,
        ),
        observations,
    )


def evaluate_agent_predictions(
    *,
    experiment_id: UUID,
    arm: ExperimentArm,
    predictions: tuple[TriagePrediction, ...],
    outcomes: dict[str, TriageOutcome],
    positive_label: str,
    as_of: datetime,
    evaluated_at: datetime,
) -> AgentEvaluationSummary:
    as_of = _require_utc(as_of)
    evaluated_at = _require_utc(evaluated_at)
    if evaluated_at < as_of:
        raise ValueError("evaluated_at cannot precede as_of")

    selected_predictions = tuple(
        prediction
        for prediction in predictions
        if prediction.experiment_id == experiment_id
        and prediction.arm is arm
        and prediction.observed_at <= as_of
    )
    classification, observations = _classification_metrics(
        predictions=selected_predictions,
        outcomes=outcomes,
        positive_label=positive_label,
        as_of=as_of,
    )

    eligible_outcomes = {
        case_id: outcome
        for case_id, outcome in outcomes.items()
        if outcome.resolved_at <= as_of
    }
    points = calibration_points(
        predictions=selected_predictions,
        outcomes=eligible_outcomes,
    )
    calibration = summarize_calibration(arm=arm, points=points)

    if calibration.observations != observations:
        raise ValueError("classification and calibration observation counts diverged")

    return AgentEvaluationSummary(
        experiment_id=experiment_id,
        arm=arm,
        positive_label=positive_label,
        evaluated_at=evaluated_at,
        as_of=as_of,
        observations=observations,
        classification=classification,
        calibration=calibration,
    )


def compare_experiment_arms(
    *,
    experiment_id: UUID,
    predictions: tuple[TriagePrediction, ...],
    outcomes: dict[str, TriageOutcome],
    positive_label: str,
    as_of: datetime,
    evaluated_at: datetime,
) -> AgentArmComparison:
    llm_only = evaluate_agent_predictions(
        experiment_id=experiment_id,
        arm=ExperimentArm.LLM_ONLY,
        predictions=predictions,
        outcomes=outcomes,
        positive_label=positive_label,
        as_of=as_of,
        evaluated_at=evaluated_at,
    )
    llm_jev = evaluate_agent_predictions(
        experiment_id=experiment_id,
        arm=ExperimentArm.LLM_JEV,
        predictions=predictions,
        outcomes=outcomes,
        positive_label=positive_label,
        as_of=as_of,
        evaluated_at=evaluated_at,
    )
    return AgentArmComparison(
        experiment_id=experiment_id,
        positive_label=positive_label,
        as_of=as_of,
        llm_only=llm_only,
        llm_jev=llm_jev,
    )
