"""ATS-020 Jev triage A/B experiment contracts and calibration metrics."""

from datetime import datetime
from enum import Enum
from math import sqrt
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from src.domain.models import _require_utc


class ExperimentArm(str, Enum):
    LLM_ONLY = "llm_only"
    LLM_JEV = "llm_jev"


class TriagePrediction(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: UUID = Field(default_factory=uuid4)
    experiment_id: UUID
    case_id: str = Field(min_length=1, max_length=255)
    subject: str = Field(min_length=1, max_length=255)
    arm: ExperimentArm
    predicted_label: str = Field(min_length=1, max_length=128)
    probability: float = Field(ge=0.0, le=1.0)
    model_name: str = Field(min_length=1, max_length=128)
    model_version: str | None = Field(default=None, max_length=128)
    observed_at: datetime
    latency_ms: int | None = Field(default=None, ge=0)
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    metadata: dict[str, str] = Field(default_factory=dict)

    @field_validator(
        "case_id",
        "subject",
        "predicted_label",
        "model_name",
        "model_version",
    )
    @classmethod
    def trim_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            raise ValueError("text fields must not be blank")
        return value

    @field_validator("observed_at")
    @classmethod
    def observed_at_is_utc(cls, value: datetime) -> datetime:
        return _require_utc(value)


class TriageOutcome(BaseModel):
    model_config = ConfigDict(frozen=True)

    case_id: str = Field(min_length=1, max_length=255)
    actual_label: str = Field(min_length=1, max_length=128)
    resolved_at: datetime

    @field_validator("case_id", "actual_label")
    @classmethod
    def trim_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("text fields must not be blank")
        return value

    @field_validator("resolved_at")
    @classmethod
    def resolved_at_is_utc(cls, value: datetime) -> datetime:
        return _require_utc(value)


class CalibrationPoint(BaseModel):
    model_config = ConfigDict(frozen=True)

    arm: ExperimentArm
    case_id: str
    predicted_label: str
    probability: float = Field(ge=0.0, le=1.0)
    correct: bool


class CalibrationSummary(BaseModel):
    model_config = ConfigDict(frozen=True)

    arm: ExperimentArm
    observations: int = Field(ge=0)
    accuracy: float = Field(ge=0.0, le=1.0)
    mean_confidence: float = Field(ge=0.0, le=1.0)
    brier_score: float = Field(ge=0.0, le=1.0)
    calibration_gap: float = Field(ge=0.0, le=1.0)
    rmse: float = Field(ge=0.0, le=1.0)


class PairedExperimentResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    case_id: str
    llm_only: TriagePrediction
    llm_jev: TriagePrediction

    @model_validator(mode="after")
    def validate_pair(self) -> "PairedExperimentResult":
        if self.llm_only.case_id != self.case_id or self.llm_jev.case_id != self.case_id:
            raise ValueError("paired predictions must share the same case_id")
        if self.llm_only.experiment_id != self.llm_jev.experiment_id:
            raise ValueError("paired predictions must share the same experiment_id")
        if self.llm_only.arm is not ExperimentArm.LLM_ONLY:
            raise ValueError("llm_only prediction has the wrong experiment arm")
        if self.llm_jev.arm is not ExperimentArm.LLM_JEV:
            raise ValueError("llm_jev prediction has the wrong experiment arm")
        return self


def calibration_points(
    *,
    predictions: tuple[TriagePrediction, ...],
    outcomes: dict[str, TriageOutcome],
) -> tuple[CalibrationPoint, ...]:
    points: list[CalibrationPoint] = []
    for prediction in predictions:
        outcome = outcomes.get(prediction.case_id)
        if outcome is None:
            continue
        if outcome.resolved_at < prediction.observed_at:
            raise ValueError("outcome cannot resolve before prediction is observed")
        points.append(
            CalibrationPoint(
                arm=prediction.arm,
                case_id=prediction.case_id,
                predicted_label=prediction.predicted_label,
                probability=prediction.probability,
                correct=prediction.predicted_label.casefold()
                == outcome.actual_label.casefold(),
            )
        )
    return tuple(points)


def summarize_calibration(
    *,
    arm: ExperimentArm,
    points: tuple[CalibrationPoint, ...],
) -> CalibrationSummary:
    selected = tuple(point for point in points if point.arm is arm)
    if not selected:
        return CalibrationSummary(
            arm=arm,
            observations=0,
            accuracy=0.0,
            mean_confidence=0.0,
            brier_score=0.0,
            calibration_gap=0.0,
            rmse=0.0,
        )

    correctness = [1.0 if point.correct else 0.0 for point in selected]
    probabilities = [point.probability for point in selected]
    n = len(selected)
    accuracy = sum(correctness) / n
    mean_confidence = sum(probabilities) / n
    squared_errors = [
        (probability - actual) ** 2
        for probability, actual in zip(probabilities, correctness, strict=True)
    ]
    brier = sum(squared_errors) / n
    return CalibrationSummary(
        arm=arm,
        observations=n,
        accuracy=accuracy,
        mean_confidence=mean_confidence,
        brier_score=brier,
        calibration_gap=abs(mean_confidence - accuracy),
        rmse=sqrt(brier),
    )
