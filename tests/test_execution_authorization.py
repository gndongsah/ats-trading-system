from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from src.execution_authorization import (
    AuthorizationSource,
    AutonomyLevel,
    AutonomyPolicy,
    authorize_from_human_approval,
    authorize_from_policy,
    validate_execution_authorization,
)

NOW = datetime(2026, 10, 27, 22, 0, tzinfo=UTC)


def test_human_authorization_is_separate_from_risk_and_bound_to_proposal():
    proposal_id = uuid4()
    authorization = authorize_from_human_approval(
        proposal_id=proposal_id,
        proposal_fingerprint="a" * 64,
        approver_identity="human:owner",
        approved_at=NOW,
    )

    assert authorization.source is AuthorizationSource.HUMAN
    assert authorization.autonomy_level is AutonomyLevel.HUMAN_APPROVAL_REQUIRED
    assert authorization.proposal_id == proposal_id
    assert authorization.policy_id is None


def test_human_authorization_validates_without_autonomy_policy():
    authorization = authorize_from_human_approval(
        proposal_id=uuid4(),
        proposal_fingerprint="b" * 64,
        approver_identity="human:owner",
        approved_at=NOW,
    )

    validate_execution_authorization(
        authorization=authorization,
        proposal_id=authorization.proposal_id,
        proposal_fingerprint=authorization.proposal_fingerprint,
        at=NOW + timedelta(seconds=1),
        autonomy_policy=None,
    )


def test_authorization_for_another_proposal_fails_closed():
    authorization = authorize_from_human_approval(
        proposal_id=uuid4(),
        proposal_fingerprint="c" * 64,
        approver_identity="human:owner",
        approved_at=NOW,
    )

    with pytest.raises(PermissionError, match="another proposal"):
        validate_execution_authorization(
            authorization=authorization,
            proposal_id=uuid4(),
            proposal_fingerprint=authorization.proposal_fingerprint,
            at=NOW + timedelta(seconds=1),
            autonomy_policy=None,
        )


def test_mutated_proposal_fingerprint_fails_closed():
    authorization = authorize_from_human_approval(
        proposal_id=uuid4(),
        proposal_fingerprint="d" * 64,
        approver_identity="human:owner",
        approved_at=NOW,
    )

    with pytest.raises(PermissionError, match="does not match"):
        validate_execution_authorization(
            authorization=authorization,
            proposal_id=authorization.proposal_id,
            proposal_fingerprint="e" * 64,
            at=NOW + timedelta(seconds=1),
            autonomy_policy=None,
        )


def test_future_authorization_cannot_be_used_early():
    authorization = authorize_from_human_approval(
        proposal_id=uuid4(),
        proposal_fingerprint="f" * 64,
        approver_identity="human:owner",
        approved_at=NOW + timedelta(minutes=1),
    )

    with pytest.raises(ValueError, match="cannot predate"):
        validate_execution_authorization(
            authorization=authorization,
            proposal_id=authorization.proposal_id,
            proposal_fingerprint=authorization.proposal_fingerprint,
            at=NOW,
            autonomy_policy=None,
        )


def test_mvp_autonomy_policy_is_disabled_by_default():
    policy = AutonomyPolicy(
        policy_id="mvp",
        version="1",
    )

    assert policy.enabled is False
    assert policy.permitted_level is AutonomyLevel.HUMAN_APPROVAL_REQUIRED


def test_disabled_policy_cannot_mint_execution_authorization():
    policy = AutonomyPolicy(
        policy_id="paper-auto",
        version="1",
        enabled=False,
        permitted_level=AutonomyLevel.PAPER_POLICY,
    )

    with pytest.raises(PermissionError, match="disabled"):
        authorize_from_policy(
            proposal_id=uuid4(),
            proposal_fingerprint="1" * 64,
            policy=policy,
            authorized_at=NOW,
        )


def test_explicit_paper_policy_grant_is_version_bound():
    policy = AutonomyPolicy(
        policy_id="paper-auto",
        version="2",
        enabled=True,
        permitted_level=AutonomyLevel.PAPER_POLICY,
    )
    authorization = authorize_from_policy(
        proposal_id=uuid4(),
        proposal_fingerprint="2" * 64,
        policy=policy,
        authorized_at=NOW,
    )

    validate_execution_authorization(
        authorization=authorization,
        proposal_id=authorization.proposal_id,
        proposal_fingerprint=authorization.proposal_fingerprint,
        at=NOW + timedelta(seconds=1),
        autonomy_policy=policy,
    )

    stale_policy = policy.model_copy(update={"version": "3"})
    with pytest.raises(PermissionError, match="version is stale"):
        validate_execution_authorization(
            authorization=authorization,
            proposal_id=authorization.proposal_id,
            proposal_fingerprint=authorization.proposal_fingerprint,
            at=NOW + timedelta(seconds=1),
            autonomy_policy=stale_policy,
        )
