"""ATS-033 immutable PAPER forward-test campaign identity and preflight gate."""

import json
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from src.domain.models import _require_utc
from src.forward_test import playbook_digest
from src.playbook import PLAYBOOK, PlaybookConfig
from src.run_type import RunType

MIN_CAMPAIGN_DURATION = timedelta(weeks=8)
MAX_CAMPAIGN_DURATION = timedelta(weeks=12)


class ForwardTestCampaignManifest(BaseModel):
    """Immutable identity for one controlled PAPER forward-test campaign."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    campaign_id: UUID = Field(default_factory=uuid4)
    run_type: RunType = RunType.PAPER
    created_at: datetime
    start_at: datetime
    end_at: datetime
    playbook_version: str = Field(min_length=1)
    playbook_digest: str = Field(min_length=64, max_length=64)
    operational_policy_version: str = Field(min_length=1)
    setup_scope: tuple[str, ...]

    @field_validator("created_at", "start_at", "end_at")
    @classmethod
    def timestamps_are_utc(cls, value: datetime) -> datetime:
        return _require_utc(value)

    @field_validator("setup_scope")
    @classmethod
    def setup_scope_is_nonempty_unique(
        cls, value: tuple[str, ...]
    ) -> tuple[str, ...]:
        normalized = tuple(item.strip() for item in value)
        if not normalized:
            raise ValueError("forward-test campaign requires at least one setup")
        if any(not item for item in normalized):
            raise ValueError("setup scope entries must not be blank")
        if len(set(normalized)) != len(normalized):
            raise ValueError("setup scope cannot contain duplicates")
        return normalized

    @model_validator(mode="after")
    def campaign_window_is_valid(self) -> "ForwardTestCampaignManifest":
        if self.run_type is not RunType.PAPER:
            raise ValueError("forward-test campaign must be PAPER-only")
        if self.end_at <= self.start_at:
            raise ValueError("campaign end must be after campaign start")
        duration = self.end_at - self.start_at
        if duration < MIN_CAMPAIGN_DURATION or duration > MAX_CAMPAIGN_DURATION:
            raise ValueError("campaign duration must be between 8 and 12 weeks")
        if self.created_at > self.start_at:
            raise ValueError("campaign manifest must be created no later than campaign start")
        return self


def build_forward_test_campaign(
    *,
    start_at: datetime,
    end_at: datetime,
    setup_scope: tuple[str, ...],
    operational_policy_version: str,
    created_at: datetime | None = None,
    playbook: PlaybookConfig = PLAYBOOK,
) -> ForwardTestCampaignManifest:
    """Build a frozen PAPER campaign manifest from the current accepted policy."""

    created = created_at or datetime.now(UTC)
    return ForwardTestCampaignManifest(
        created_at=created,
        start_at=start_at,
        end_at=end_at,
        playbook_version=playbook.version,
        playbook_digest=playbook_digest(playbook),
        operational_policy_version=operational_policy_version,
        setup_scope=setup_scope,
    )


def campaign_manifest_digest(manifest: ForwardTestCampaignManifest) -> str:
    """Return a deterministic digest for persistence and evidence linking."""

    payload = json.dumps(
        manifest.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
    )
    return sha256(payload.encode("utf-8")).hexdigest()


def validate_campaign_preflight(
    manifest: ForwardTestCampaignManifest,
    *,
    playbook: PlaybookConfig = PLAYBOOK,
) -> str:
    """Fail closed if a stored campaign identity no longer matches policy."""

    if manifest.run_type is not RunType.PAPER:
        raise PermissionError("forward-test campaign must remain PAPER-only")
    if manifest.playbook_version != playbook.version:
        raise PermissionError("forward-test campaign playbook version drift")
    if manifest.playbook_digest != playbook_digest(playbook):
        raise PermissionError("forward-test campaign playbook digest drift")
    return campaign_manifest_digest(manifest)
