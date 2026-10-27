"""ATS-029 execution authorization boundary downstream of risk."""

from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from src.domain.models import _require_utc
from src.run_type import RunType


class AuthorizationSource(StrEnum):
    HUMAN = "HUMAN"
    POLICY = "POLICY"


class AutonomyLevel(StrEnum):
    HUMAN_APPROVAL_REQUIRED = "HUMAN_APPROVAL_REQUIRED"
    PAPER_POLICY = "PAPER_POLICY"


class AutonomyPolicy(BaseModel):
    """Future autonomy policy. Disabled by default for the MVP."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    policy_id: str = Field(min_length=1, max_length=128)
    version: str = Field(min_length=1, max_length=128)
    enabled: bool = False
    permitted_level: AutonomyLevel = AutonomyLevel.HUMAN_APPROVAL_REQUIRED
    permitted_run_type: RunType = RunType.PAPER

    @model_validator(mode="after")
    def policy_is_paper_only(self) -> "AutonomyPolicy":
        if self.permitted_run_type is not RunType.PAPER:
            raise ValueError("ATS-029 autonomy policy is PAPER-only")
        return self


class ExecutionAuthorization(BaseModel):
    """Independent execution authorization bound to one immutable proposal."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    proposal_id: UUID
    proposal_fingerprint: str = Field(min_length=64, max_length=64)
    source: AuthorizationSource
    autonomy_level: AutonomyLevel
    authorized_at: datetime
    actor_identity: str = Field(min_length=1, max_length=255)
    policy_id: str | None = Field(default=None, max_length=128)
    policy_version: str | None = Field(default=None, max_length=128)

    @field_validator("authorized_at")
    @classmethod
    def authorized_at_is_utc(cls, value: datetime) -> datetime:
        return _require_utc(value)

    @field_validator("actor_identity")
    @classmethod
    def actor_is_not_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("actor identity must not be blank")
        return value

    @model_validator(mode="after")
    def source_matches_provenance(self) -> "ExecutionAuthorization":
        if self.source is AuthorizationSource.HUMAN:
            if self.autonomy_level is not AutonomyLevel.HUMAN_APPROVAL_REQUIRED:
                raise ValueError("human authorization must use human-approval level")
            if self.policy_id is not None or self.policy_version is not None:
                raise ValueError("human authorization cannot carry policy provenance")
        else:
            if self.autonomy_level is not AutonomyLevel.PAPER_POLICY:
                raise ValueError("policy authorization must use PAPER_POLICY level")
            if not self.policy_id or not self.policy_version:
                raise ValueError("policy authorization requires policy provenance")
        return self


def authorize_from_human_approval(
    *,
    proposal_id: UUID,
    proposal_fingerprint: str,
    approver_identity: str,
    approved_at: datetime,
) -> ExecutionAuthorization:
    return ExecutionAuthorization(
        proposal_id=proposal_id,
        proposal_fingerprint=proposal_fingerprint,
        source=AuthorizationSource.HUMAN,
        autonomy_level=AutonomyLevel.HUMAN_APPROVAL_REQUIRED,
        authorized_at=approved_at,
        actor_identity=approver_identity,
    )


def authorize_from_policy(
    *,
    proposal_id: UUID,
    proposal_fingerprint: str,
    policy: AutonomyPolicy,
    authorized_at: datetime,
) -> ExecutionAuthorization:
    if not policy.enabled:
        raise PermissionError("autonomy policy is disabled")
    if policy.permitted_level is not AutonomyLevel.PAPER_POLICY:
        raise PermissionError("autonomy policy does not permit policy execution")
    return ExecutionAuthorization(
        proposal_id=proposal_id,
        proposal_fingerprint=proposal_fingerprint,
        source=AuthorizationSource.POLICY,
        autonomy_level=AutonomyLevel.PAPER_POLICY,
        authorized_at=authorized_at,
        actor_identity=f"policy:{policy.policy_id}",
        policy_id=policy.policy_id,
        policy_version=policy.version,
    )


def validate_execution_authorization(
    *,
    authorization: ExecutionAuthorization,
    proposal_id: UUID,
    proposal_fingerprint: str,
    at: datetime,
    autonomy_policy: AutonomyPolicy | None,
) -> None:
    at = _require_utc(at)
    if authorization.proposal_id != proposal_id:
        raise PermissionError("execution authorization belongs to another proposal")
    if authorization.proposal_fingerprint != proposal_fingerprint:
        raise PermissionError("execution authorization does not match proposal")
    if authorization.authorized_at > at:
        raise ValueError("execution cannot predate execution authorization")

    if authorization.source is AuthorizationSource.HUMAN:
        return

    if autonomy_policy is None:
        raise PermissionError("policy authorization has no active autonomy policy")
    if not autonomy_policy.enabled:
        raise PermissionError("autonomy policy is disabled")
    if autonomy_policy.permitted_run_type is not RunType.PAPER:
        raise PermissionError("autonomy policy is not PAPER-scoped")
    if autonomy_policy.permitted_level is not authorization.autonomy_level:
        raise PermissionError("autonomy level is not permitted by current policy")
    if (
        authorization.policy_id != autonomy_policy.policy_id
        or authorization.policy_version != autonomy_policy.version
    ):
        raise PermissionError("execution authorization policy version is stale")
