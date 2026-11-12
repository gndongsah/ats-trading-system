"""ATS-036C durable campaign control and reporting artifacts.

This module deliberately does not grant LIVE authority. It binds operational
artifacts to one immutable PAPER campaign digest and stores them append-only so
restart/replay cannot silently change campaign evidence.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from enum import StrEnum
from hashlib import sha256
from pathlib import Path
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session, sessionmaker

from src.forward_test_campaign import (
    ForwardTestCampaignManifest,
    build_forward_test_campaign,
    campaign_manifest_digest,
)
from src.forward_test_readiness import (
    ForwardTestReadinessEvaluation,
    evaluate_forward_test_readiness,
)
from src.forward_test_report import (
    ForwardTestEvidenceReport,
    build_campaign_forward_test_report,
    build_weekly_forward_test_report,
)
from src.forward_test_scorecard import (
    ForwardTestDailyScorecard,
    build_forward_test_daily_scorecard,
)
from src.persistence.forward_test_repository import (
    ForwardTestCampaignRepository,
    ForwardTestRunRepository,
)
from src.persistence.repository import JournalRepository, PersistenceConflict

CAMPAIGN_DURATION = timedelta(weeks=12)
FROZEN_SETUP_SCOPE = ("breakout", "pullback")


class CampaignArtifactKind(StrEnum):
    DAILY_SCORECARD = "daily-scorecard"
    WEEKLY_REPORT = "weekly-report"
    CAMPAIGN_REPORT = "campaign-report"
    READINESS = "readiness"
    MARKET_WINDOW = "market-window"
    ACTIVATION = "activation"


class CampaignArtifactEnvelope(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: CampaignArtifactKind
    campaign_id: UUID
    campaign_digest: str = Field(min_length=64, max_length=64)
    key: str = Field(min_length=1, max_length=128)
    generated_at: datetime
    payload_digest: str = Field(min_length=64, max_length=64)
    payload: dict[str, object]


def _canonical_digest(payload: dict[str, object]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return sha256(encoded.encode("utf-8")).hexdigest()


class CampaignArtifactRepository:
    """Append-only JSON artifact store bound to campaign identity."""

    def __init__(self, root: Path | str) -> None:
        self._root = Path(root)

    def save(
        self,
        *,
        kind: CampaignArtifactKind,
        campaign: ForwardTestCampaignManifest,
        key: str,
        generated_at: datetime,
        payload: dict[str, object],
    ) -> CampaignArtifactEnvelope:
        generated_at = self._utc(generated_at)
        envelope = CampaignArtifactEnvelope(
            kind=kind,
            campaign_id=campaign.campaign_id,
            campaign_digest=campaign_manifest_digest(campaign),
            key=key,
            generated_at=generated_at,
            payload_digest=_canonical_digest(payload),
            payload=payload,
        )
        path = self._path(envelope.campaign_id, kind, key)
        serialized = envelope.model_dump_json(indent=2)
        if path.exists():
            existing = self._read(path)
            if existing != envelope:
                raise PersistenceConflict(
                    f"campaign artifact {kind.value}/{key} already exists"
                )
            return existing
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(serialized, encoding="utf-8")
        temporary.replace(path)
        return envelope

    def load(
        self,
        *,
        campaign_id: UUID,
        kind: CampaignArtifactKind,
        key: str,
    ) -> CampaignArtifactEnvelope | None:
        path = self._path(campaign_id, kind, key)
        if not path.exists():
            return None
        return self._read(path)

    def list_kind(
        self,
        *,
        campaign_id: UUID,
        kind: CampaignArtifactKind,
    ) -> tuple[CampaignArtifactEnvelope, ...]:
        directory = self._root / str(campaign_id) / kind.value
        if not directory.exists():
            return ()
        return tuple(self._read(path) for path in sorted(directory.glob("*.json")))

    @staticmethod
    def _read(path: Path) -> CampaignArtifactEnvelope:
        envelope = CampaignArtifactEnvelope.model_validate_json(path.read_text())
        if _canonical_digest(envelope.payload) != envelope.payload_digest:
            raise PersistenceConflict("campaign artifact payload digest mismatch")
        return envelope

    def _path(
        self,
        campaign_id: UUID,
        kind: CampaignArtifactKind,
        key: str,
    ) -> Path:
        if "/" in key or "\\" in key or key in {".", ".."}:
            raise ValueError("campaign artifact key must be path-safe")
        return self._root / str(campaign_id) / kind.value / f"{key}.json"

    @staticmethod
    def _utc(value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("artifact timestamp must be timezone-aware")
        return value.astimezone(UTC)


class CampaignOperationsService:
    """Own one immutable PAPER campaign and its durable derived evidence."""

    def __init__(
        self,
        *,
        session_factory: sessionmaker[Session],
        artifacts: CampaignArtifactRepository,
    ) -> None:
        self._session_factory = session_factory
        self._artifacts = artifacts

    def initialize_campaign(
        self,
        *,
        start_at: datetime,
        operational_policy_version: str,
        created_at: datetime | None = None,
    ) -> ForwardTestCampaignManifest:
        start_at = self._utc(start_at)
        manifest = build_forward_test_campaign(
            created_at=self._utc(created_at) if created_at is not None else None,
            start_at=start_at,
            end_at=start_at + CAMPAIGN_DURATION,
            setup_scope=FROZEN_SETUP_SCOPE,
            operational_policy_version=operational_policy_version,
        )
        with self._session_factory.begin() as session:
            repository = ForwardTestCampaignRepository(session)
            for existing in repository.list_all():
                overlaps = (
                    manifest.start_at < existing.end_at
                    and existing.start_at < manifest.end_at
                )
                if overlaps and existing.campaign_id != manifest.campaign_id:
                    raise PersistenceConflict(
                        "another forward-test campaign overlaps requested window"
                    )
            repository.save(manifest)
        return self.load_campaign(manifest.campaign_id)

    def load_campaign(self, campaign_id: UUID) -> ForwardTestCampaignManifest:
        with self._session_factory() as session:
            manifest = ForwardTestCampaignRepository(session).get(campaign_id)
        if manifest is None:
            raise LookupError(f"campaign {campaign_id} does not exist")
        return manifest

    def persist_daily_scorecard(
        self,
        *,
        campaign_id: UUID,
        trading_day: date,
        generated_at: datetime,
    ) -> ForwardTestDailyScorecard:
        campaign, runs, journals = self._campaign_evidence(campaign_id)
        scorecard = build_forward_test_daily_scorecard(
            trading_day=trading_day,
            run_evidence=runs,
            journals=journals,
        )
        self._artifacts.save(
            kind=CampaignArtifactKind.DAILY_SCORECARD,
            campaign=campaign,
            key=trading_day.isoformat(),
            generated_at=generated_at,
            payload=scorecard.model_dump(mode="json"),
        )
        return scorecard

    def persist_weekly_report(
        self,
        *,
        campaign_id: UUID,
        week_start: date,
        generated_at: datetime,
    ) -> ForwardTestEvidenceReport:
        campaign, runs, journals = self._campaign_evidence(campaign_id)
        scorecards = self._scorecards(campaign)
        report = build_weekly_forward_test_report(
            campaign=campaign,
            week_start=week_start,
            as_of=self._utc(generated_at),
            scorecards=scorecards,
            run_evidence=runs,
            journals=journals,
        )
        self._artifacts.save(
            kind=CampaignArtifactKind.WEEKLY_REPORT,
            campaign=campaign,
            key=week_start.isoformat(),
            generated_at=generated_at,
            payload=report.model_dump(mode="json"),
        )
        return report

    def persist_readiness(
        self,
        *,
        campaign_id: UUID,
        evaluated_at: datetime,
    ) -> ForwardTestReadinessEvaluation:
        campaign, runs, journals = self._campaign_evidence(campaign_id)
        scorecards = self._scorecards(campaign)
        evaluated_at = self._utc(evaluated_at)
        report = build_campaign_forward_test_report(
            campaign=campaign,
            as_of=evaluated_at,
            scorecards=scorecards,
            run_evidence=runs,
            journals=journals,
        )
        self._artifacts.save(
            kind=CampaignArtifactKind.CAMPAIGN_REPORT,
            campaign=campaign,
            key=evaluated_at.date().isoformat(),
            generated_at=evaluated_at,
            payload=report.model_dump(mode="json"),
        )
        evaluation = evaluate_forward_test_readiness(
            campaign=campaign,
            report=report,
            evaluated_at=evaluated_at,
        )
        self._artifacts.save(
            kind=CampaignArtifactKind.READINESS,
            campaign=campaign,
            key=evaluated_at.date().isoformat(),
            generated_at=evaluated_at,
            payload=evaluation.model_dump(mode="json"),
        )
        return evaluation

    def _campaign_evidence(
        self,
        campaign_id: UUID,
    ) -> tuple[
        ForwardTestCampaignManifest,
        tuple,
        tuple,
    ]:
        with self._session_factory() as session:
            campaign_repository = ForwardTestCampaignRepository(session)
            campaign = campaign_repository.get(campaign_id)
            if campaign is None:
                raise LookupError(f"campaign {campaign_id} does not exist")
            runs = ForwardTestRunRepository(session).list_by_campaign(campaign_id)
            journal_repository = JournalRepository(session)
            linked_ids = {run.journal_id for run in runs if run.journal_id is not None}
            journals = tuple(
                entry
                for journal_id in sorted(linked_ids, key=str)
                if (entry := journal_repository.get(journal_id)) is not None
            )
        return campaign, runs, journals

    def _scorecards(
        self,
        campaign: ForwardTestCampaignManifest,
    ) -> tuple[ForwardTestDailyScorecard, ...]:
        artifacts = self._artifacts.list_kind(
            campaign_id=campaign.campaign_id,
            kind=CampaignArtifactKind.DAILY_SCORECARD,
        )
        expected_digest = campaign_manifest_digest(campaign)
        scorecards = []
        for artifact in artifacts:
            if artifact.campaign_digest != expected_digest:
                raise PersistenceConflict("scorecard campaign digest mismatch")
            scorecards.append(
                ForwardTestDailyScorecard.model_validate(artifact.payload)
            )
        return tuple(sorted(scorecards, key=lambda item: item.trading_day))

    @staticmethod
    def _utc(value: datetime | None) -> datetime:
        if value is None:
            return datetime.now(UTC)
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("campaign timestamp must be timezone-aware")
        return value.astimezone(UTC)
