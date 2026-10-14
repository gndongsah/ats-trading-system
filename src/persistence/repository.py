from collections.abc import Callable
from datetime import datetime
from typing import Any, Protocol, TypeVar
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from src.domain.models import JournalEntry, ScoreSnapshot, TradePlan
from src.persistence.models import (
    FillRecord,
    JournalEntryRecord,
    OrderIntentRecord,
    PositionSnapshotRecord,
    ScoreSnapshotRecord,
    TradePlanRecord,
    TradeRecord,
    TradeResultRecord,
)

Record = TypeVar("Record")
DocumentMigration = Callable[[dict[str, Any]], dict[str, Any]]


class PersistenceConflict(ValueError):
    """An append-only record ID was reused with different data."""


JOURNAL_SCHEMA_VERSION = 4
SCORE_SNAPSHOT_SCHEMA_VERSION = 4


def _migrate_score_snapshot_v1_to_v2(payload: dict[str, Any]) -> dict[str, Any]:
    migrated = dict(payload)
    contributions: list[dict[str, Any]] = []
    for contribution in payload.get("contributions", []):
        migrated_contribution = dict(contribution)
        migrated_contribution["evidence"] = [
            {
                **evidence,
                "verification_status": evidence.get(
                    "verification_status", "unverified"
                ),
            }
            for evidence in contribution.get("evidence", [])
        ]
        contributions.append(migrated_contribution)
    if "contributions" in payload:
        migrated["contributions"] = contributions
    return migrated


def _migrate_score_snapshot_v2_to_v3(payload: dict[str, Any]) -> dict[str, Any]:
    migrated = dict(payload)
    if "contributions" in payload:
        migrated["contributions"] = [
            {
                **contribution,
                "evidence": [
                    {
                        **evidence,
                        "version": evidence.get("version", 1),
                        "source_document_version_id": evidence.get(
                            "source_document_version_id"
                        ),
                    }
                    for evidence in contribution.get("evidence", [])
                ],
            }
            for contribution in payload["contributions"]
        ]
    return migrated


def _migrate_score_snapshot_v3_to_v4(payload: dict[str, Any]) -> dict[str, Any]:
    migrated = dict(payload)
    if "contributions" in payload:
        migrated["contributions"] = [
            {
                **contribution,
                "evidence": [
                    {
                        **evidence,
                        "verifier_identity": evidence.get("verifier_identity"),
                        "verified_at": evidence.get("verified_at"),
                        "verification_method": evidence.get("verification_method"),
                        "verification_policy_version": evidence.get(
                            "verification_policy_version"
                        ),
                        "verification_notes": evidence.get("verification_notes"),
                    }
                    for evidence in contribution.get("evidence", [])
                ],
            }
            for contribution in payload["contributions"]
        ]
    return migrated


def _migrate_journal_v1_to_v2(payload: dict[str, Any]) -> dict[str, Any]:
    migrated = dict(payload)
    plan = migrated["plan"]
    migrated["plan"] = {
        **plan,
        "score_snapshot": _migrate_score_snapshot_v1_to_v2(plan["score_snapshot"]),
    }
    migrated["prior_plans"] = [
        {
            **prior_plan,
            "score_snapshot": _migrate_score_snapshot_v1_to_v2(
                prior_plan["score_snapshot"]
            ),
        }
        for prior_plan in migrated.get("prior_plans", [])
    ]
    return migrated


def _migrate_journal_v2_to_v3(payload: dict[str, Any]) -> dict[str, Any]:
    migrated = dict(payload)
    migrated["prior_plans"] = [
        {
            **plan,
            "score_snapshot": _migrate_score_snapshot_v2_to_v3(plan["score_snapshot"]),
        }
        for plan in migrated.get("prior_plans", [])
    ]
    migrated["plan"] = {
        **migrated["plan"],
        "score_snapshot": _migrate_score_snapshot_v2_to_v3(
            migrated["plan"]["score_snapshot"]
        ),
    }
    return migrated


def _migrate_journal_v3_to_v4(payload: dict[str, Any]) -> dict[str, Any]:
    migrated = dict(payload)
    migrated["prior_plans"] = [
        {
            **plan,
            "score_snapshot": _migrate_score_snapshot_v3_to_v4(plan["score_snapshot"]),
        }
        for plan in migrated.get("prior_plans", [])
    ]
    migrated["plan"] = {
        **migrated["plan"],
        "score_snapshot": _migrate_score_snapshot_v3_to_v4(
            migrated["plan"]["score_snapshot"]
        ),
    }
    return migrated


def _encode_document(payload: dict[str, Any], schema_version: int) -> dict[str, Any]:
    return {"schema_version": schema_version, "data": payload}


def decode_score_snapshot(document: dict[str, Any]) -> ScoreSnapshot:
    payload = _decode_document(
        document,
        document_name="score snapshot",
        migrations={
            1: _migrate_score_snapshot_v1_to_v2,
            2: _migrate_score_snapshot_v2_to_v3,
            3: _migrate_score_snapshot_v3_to_v4,
        },
        current_version=SCORE_SNAPSHOT_SCHEMA_VERSION,
    )
    return ScoreSnapshot.model_validate(payload)


def _decode_document(
    document: dict[str, Any],
    *,
    document_name: str,
    migrations: dict[int, DocumentMigration],
    current_version: int,
) -> dict[str, Any]:
    if "schema_version" in document and "data" in document:
        version = document["schema_version"]
        payload = document["data"]
        if isinstance(version, bool) or not isinstance(version, int) or version < 1:
            raise ValueError(f"{document_name} has an invalid schema version")
        if not isinstance(payload, dict):
            raise ValueError(f"{document_name} payload must be an object")
    else:
        version = 1
        payload = document

    if version > current_version:
        raise ValueError(
            f"{document_name} schema version {version} is newer than supported "
            f"version {current_version}"
        )
    while version < current_version:
        migration = migrations.get(version)
        if migration is None:
            raise ValueError(
                f"no {document_name} migration is registered from schema version {version}"
            )
        payload = migration(payload)
        version += 1
    return payload


class JournalRepositoryProtocol(Protocol):
    def save(
        self, entry: JournalEntry, expected_revision: int | None = None
    ) -> int: ...

    def get(self, journal_id: UUID) -> JournalEntry | None: ...

    def get_revision(self, journal_id: UUID, revision: int) -> JournalEntry | None: ...

    def get_by_trade_id(self, trade_id: UUID) -> JournalEntry | None: ...


class JournalRepository:
    """Append-only persistence for complete ATS-006 journal aggregates.

    The caller owns transaction boundaries: use ``with session.begin()`` around
    writes so a failed aggregate write cannot leave a partial lifecycle record.
    """

    def __init__(self, session: Session) -> None:
        self._session = session

    def save(self, entry: JournalEntry, expected_revision: int | None = None) -> int:
        plans = sorted((*entry.prior_plans, entry.plan), key=lambda plan: plan.version)
        if self._session.get_bind().dialect.name == "postgresql":
            self._session.execute(
                select(
                    func.pg_advisory_xact_lock(
                        func.hashtextextended(str(entry.plan.trade_id), 0)
                    )
                )
            )
        trade = self._session.get(TradeRecord, entry.plan.trade_id)
        if trade is None:
            self._session.add(TradeRecord(trade_id=entry.plan.trade_id))
            self._session.flush()

        prior_entry_id = self._session.scalar(
            select(JournalEntryRecord.id)
            .where(JournalEntryRecord.trade_id == entry.plan.trade_id)
            .limit(1)
        )
        if prior_entry_id is not None and prior_entry_id != entry.id:
            raise PersistenceConflict(
                f"trade {entry.plan.trade_id} already has journal {prior_entry_id}"
            )
        current_revision = (
            self._session.scalar(
                select(func.max(JournalEntryRecord.revision)).where(
                    JournalEntryRecord.trade_id == entry.plan.trade_id
                )
            )
            or 0
        )
        if expected_revision is not None and expected_revision != current_revision:
            raise PersistenceConflict(
                f"stale journal revision for trade {entry.plan.trade_id}: "
                f"expected {expected_revision}, current revision is {current_revision}"
            )
        revision = current_revision + 1

        for plan in plans:
            self._save_plan(plan)
        self._session.flush()

        for order in entry.orders:
            self._save_record(
                OrderIntentRecord,
                order.id,
                {
                    "id": order.id,
                    "trade_id": entry.plan.trade_id,
                    "plan_id": order.plan_id,
                    "order_data": order.model_dump(mode="json"),
                },
            )
        self._session.flush()

        for fill in entry.fills:
            self._save_record(
                FillRecord,
                fill.id,
                {
                    "id": fill.id,
                    "trade_id": entry.plan.trade_id,
                    "order_intent_id": fill.order_intent_id,
                    "fill_data": fill.model_dump(mode="json"),
                },
            )
        for snapshot in entry.position_snapshots:
            self._save_record(
                PositionSnapshotRecord,
                snapshot.id,
                {
                    "id": snapshot.id,
                    "trade_id": entry.plan.trade_id,
                    "plan_id": snapshot.plan_id,
                    "snapshot_data": snapshot.model_dump(mode="json"),
                },
            )
        if entry.result is not None:
            self._save_record(
                TradeResultRecord,
                entry.result.id,
                {
                    "id": entry.result.id,
                    "trade_id": entry.plan.trade_id,
                    "plan_id": entry.result.plan_id,
                    "result_data": entry.result.model_dump(mode="json"),
                },
            )

        self._session.flush()
        payload = entry.model_dump(mode="json")
        self._session.add(
            JournalEntryRecord(
                id=entry.id,
                revision=revision,
                trade_id=entry.plan.trade_id,
                plan_id=entry.plan.id,
                created_at=entry.created_at,
                plan_followed=entry.plan_followed,
                mood=entry.mood.value if entry.mood is not None else None,
                lesson=entry.lesson,
                thesis=entry.thesis,
                entry_data=_encode_document(payload, JOURNAL_SCHEMA_VERSION),
            )
        )
        self._session.flush()
        return revision

    def get(self, journal_id: UUID) -> JournalEntry | None:
        record = self._session.scalar(
            select(JournalEntryRecord)
            .where(JournalEntryRecord.id == journal_id)
            .order_by(JournalEntryRecord.revision.desc())
            .limit(1)
        )
        return self._decode(record)

    def get_revision(self, journal_id: UUID, revision: int) -> JournalEntry | None:
        record = self._session.scalar(
            select(JournalEntryRecord).where(
                JournalEntryRecord.id == journal_id,
                JournalEntryRecord.revision == revision,
            )
        )
        return self._decode(record)

    def get_by_trade_id(self, trade_id: UUID) -> JournalEntry | None:
        record = self._session.scalar(
            select(JournalEntryRecord)
            .where(JournalEntryRecord.trade_id == trade_id)
            .order_by(JournalEntryRecord.revision.desc())
            .limit(1)
        )
        return self._decode(record)

    @staticmethod
    def _decode(record: JournalEntryRecord | None) -> JournalEntry | None:
        if record is None:
            return None
        payload = _decode_document(
            record.entry_data,
            document_name="journal entry",
            migrations={
                1: _migrate_journal_v1_to_v2,
                2: _migrate_journal_v2_to_v3,
                3: _migrate_journal_v3_to_v4,
            },
            current_version=JOURNAL_SCHEMA_VERSION,
        )
        return JournalEntry.model_validate(payload)

    def _save_plan(self, plan: TradePlan) -> None:
        self._save_record(
            ScoreSnapshotRecord,
            plan.score_snapshot.id,
            {
                "id": plan.score_snapshot.id,
                "decided_at": plan.score_snapshot.decided_at,
                "snapshot_data": _encode_document(
                    plan.score_snapshot.model_dump(mode="json"),
                    SCORE_SNAPSHOT_SCHEMA_VERSION,
                ),
            },
        )
        self._session.flush()
        self._save_record(
            TradePlanRecord,
            plan.id,
            {
                "id": plan.id,
                "trade_id": plan.trade_id,
                "version": plan.version,
                "supersedes_plan_id": plan.supersedes_plan_id,
                "score_snapshot_id": plan.score_snapshot.id,
                "planned_at": plan.planned_at,
                "plan_data": plan.model_dump(mode="json"),
            },
        )

    def _save_record(
        self,
        record_type: type[Record],
        identity: UUID,
        values: dict[str, Any],
    ) -> None:
        existing = self._session.get(record_type, identity)
        if existing is None:
            self._session.add(record_type(**values))
            return
        for name, expected in values.items():
            actual = getattr(existing, name)
            if record_type is ScoreSnapshotRecord and name == "snapshot_data":
                actual = decode_score_snapshot(actual)
                expected = decode_score_snapshot(expected)
            elif (
                isinstance(actual, datetime)
                and isinstance(expected, datetime)
                and self._session.bind is not None
                and self._session.bind.dialect.name == "sqlite"
                and actual.tzinfo is None
                and expected.tzinfo is not None
            ):
                actual = actual.replace(tzinfo=expected.tzinfo)
            if actual != expected:
                raise PersistenceConflict(
                    f"{record_type.__name__} {identity} already exists with different {name}"
                )
