from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.domain.models import _require_utc
from src.jev_experiment import ExperimentArm, TriageOutcome, TriagePrediction
from src.persistence.models import TriageExperimentRecord
from src.persistence.repository import PersistenceConflict


class TriageExperimentRepository:
    """Persist paired ATS-020 predictions and later calibration outcomes."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def save_prediction(self, prediction: TriagePrediction) -> None:
        existing = self._session.scalar(
            select(TriageExperimentRecord).where(
                TriageExperimentRecord.experiment_id == prediction.experiment_id,
                TriageExperimentRecord.case_id == prediction.case_id,
                TriageExperimentRecord.arm == prediction.arm.value,
            )
        )
        values = prediction.model_dump(mode="json")
        if existing is not None:
            if existing.id != prediction.id or existing.prediction_data != values:
                raise PersistenceConflict(
                    "triage prediction already exists for experiment/case/arm"
                )
            return

        self._session.add(
            TriageExperimentRecord(
                id=prediction.id,
                experiment_id=prediction.experiment_id,
                case_id=prediction.case_id,
                subject=prediction.subject,
                arm=prediction.arm.value,
                predicted_label=prediction.predicted_label,
                probability=prediction.probability,
                model_name=prediction.model_name,
                model_version=prediction.model_version,
                observed_at=prediction.observed_at,
                outcome_label=None,
                outcome_resolved_at=None,
                prediction_data=values,
            )
        )
        self._session.flush()

    def attach_outcome(self, *, experiment_id: UUID, outcome: TriageOutcome) -> int:
        outcome_resolved_at = _require_utc(outcome.resolved_at)
        rows = self._session.scalars(
            select(TriageExperimentRecord).where(
                TriageExperimentRecord.experiment_id == experiment_id,
                TriageExperimentRecord.case_id == outcome.case_id,
            )
        ).all()
        if not rows:
            raise LookupError("no triage predictions found for experiment/case")
        for row in rows:
            if outcome_resolved_at < row.observed_at:
                raise ValueError("outcome cannot resolve before prediction is observed")
            if row.outcome_label is not None:
                if (
                    row.outcome_label != outcome.actual_label
                    or row.outcome_resolved_at != outcome_resolved_at
                ):
                    raise PersistenceConflict("triage outcome is already resolved")
                continue
            row.outcome_label = outcome.actual_label
            row.outcome_resolved_at = outcome_resolved_at
        self._session.flush()
        return len(rows)

    def list_predictions(
        self,
        *,
        experiment_id: UUID,
        arm: ExperimentArm | None = None,
    ) -> tuple[TriagePrediction, ...]:
        statement = select(TriageExperimentRecord).where(
            TriageExperimentRecord.experiment_id == experiment_id
        )
        if arm is not None:
            statement = statement.where(TriageExperimentRecord.arm == arm.value)
        rows = self._session.scalars(
            statement.order_by(
                TriageExperimentRecord.case_id,
                TriageExperimentRecord.arm,
            )
        ).all()
        return tuple(
            TriagePrediction.model_validate(row.prediction_data) for row in rows
        )

    def list_outcomes(self, *, experiment_id: UUID) -> dict[str, TriageOutcome]:
        rows = self._session.scalars(
            select(TriageExperimentRecord).where(
                TriageExperimentRecord.experiment_id == experiment_id,
                TriageExperimentRecord.outcome_label.is_not(None),
            )
        ).all()
        outcomes: dict[str, TriageOutcome] = {}
        for row in rows:
            if row.case_id in outcomes:
                continue
            outcomes[row.case_id] = TriageOutcome(
                case_id=row.case_id,
                actual_label=row.outcome_label,
                resolved_at=row.outcome_resolved_at,
            )
        return outcomes
