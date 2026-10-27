"""Persistence for ATS-021 agent evaluation snapshots."""

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.agent_evaluation import AgentEvaluationSummary
from src.jev_experiment import ExperimentArm
from src.persistence.models import AgentEvaluationRecord
from src.persistence.repository import PersistenceConflict


def _as_utc_from_storage(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


class AgentEvaluationRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def save(self, summary: AgentEvaluationSummary) -> None:
        existing = self._session.get(AgentEvaluationRecord, summary.id)
        values = summary.model_dump(mode="json")
        if existing is not None:
            if existing.summary_data != values:
                raise PersistenceConflict("agent evaluation id already exists")
            return

        self._session.add(
            AgentEvaluationRecord(
                id=summary.id,
                experiment_id=summary.experiment_id,
                arm=summary.arm.value,
                positive_label=summary.positive_label,
                evaluated_at=summary.evaluated_at,
                as_of=summary.as_of,
                observations=summary.observations,
                summary_data=values,
            )
        )
        self._session.flush()

    def list_for_experiment(
        self,
        *,
        experiment_id: UUID,
        arm: ExperimentArm | None = None,
    ) -> tuple[AgentEvaluationSummary, ...]:
        statement = select(AgentEvaluationRecord).where(
            AgentEvaluationRecord.experiment_id == experiment_id
        )
        if arm is not None:
            statement = statement.where(AgentEvaluationRecord.arm == arm.value)
        rows = self._session.scalars(
            statement.order_by(
                AgentEvaluationRecord.as_of,
                AgentEvaluationRecord.arm,
                AgentEvaluationRecord.positive_label,
            )
        ).all()
        summaries: list[AgentEvaluationSummary] = []
        for row in rows:
            data = dict(row.summary_data)
            data["evaluated_at"] = _as_utc_from_storage(row.evaluated_at)
            data["as_of"] = _as_utc_from_storage(row.as_of)
            summaries.append(AgentEvaluationSummary.model_validate(data))
        return tuple(summaries)
