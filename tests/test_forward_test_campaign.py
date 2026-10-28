from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from src.forward_test_campaign import (
    ForwardTestCampaignManifest,
    build_forward_test_campaign,
    campaign_manifest_digest,
    validate_campaign_preflight,
)
from src.playbook import PLAYBOOK
from src.run_type import RunType

START = datetime(2026, 11, 2, 14, 30, tzinfo=UTC)
CREATED = START - timedelta(minutes=5)


def campaign(*, weeks=8, setups=("BREAKOUT", "PULLBACK")):
    return build_forward_test_campaign(
        created_at=CREATED,
        start_at=START,
        end_at=START + timedelta(weeks=weeks),
        setup_scope=setups,
        operational_policy_version="ops-v1",
    )


def test_build_campaign_freezes_paper_playbook_identity():
    manifest = campaign()

    assert manifest.run_type is RunType.PAPER
    assert manifest.playbook_version == PLAYBOOK.version
    assert len(manifest.playbook_digest) == 64
    assert manifest.setup_scope == ("BREAKOUT", "PULLBACK")
    assert validate_campaign_preflight(manifest) == campaign_manifest_digest(manifest)


@pytest.mark.parametrize("weeks", [7, 13])
def test_campaign_rejects_duration_outside_eight_to_twelve_weeks(weeks):
    with pytest.raises(ValidationError, match="between 8 and 12 weeks"):
        campaign(weeks=weeks)


@pytest.mark.parametrize(
    "setups, message",
    [
        ((), "at least one setup"),
        (("BREAKOUT", ""), "must not be blank"),
        (("BREAKOUT", "BREAKOUT"), "cannot contain duplicates"),
    ],
)
def test_campaign_rejects_invalid_setup_scope(setups, message):
    with pytest.raises(ValidationError, match=message):
        campaign(setups=setups)


def test_campaign_rejects_non_paper_mode_even_if_model_is_reconstructed():
    base = campaign().model_dump(mode="python")
    base["run_type"] = RunType.LIVE

    with pytest.raises(ValidationError, match="PAPER-only"):
        ForwardTestCampaignManifest.model_validate(base)


def test_preflight_rejects_playbook_identity_drift():
    manifest = campaign()
    drifted = PLAYBOOK.model_copy(deep=True)
    drifted.version = "3.0-drifted"

    with pytest.raises(PermissionError, match="version drift"):
        validate_campaign_preflight(manifest, playbook=drifted)


def test_manifest_digest_is_stable_and_sensitive_to_scope():
    first = campaign()
    same = ForwardTestCampaignManifest.model_validate(first.model_dump(mode="python"))
    changed = first.model_copy(update={"setup_scope": ("BREAKOUT",)})

    assert campaign_manifest_digest(first) == campaign_manifest_digest(same)
    assert campaign_manifest_digest(first) != campaign_manifest_digest(changed)


def test_campaign_requires_utc_timestamps():
    naive_start = START.replace(tzinfo=None)

    with pytest.raises(ValidationError, match="timezone-aware"):
        build_forward_test_campaign(
            created_at=CREATED,
            start_at=naive_start,
            end_at=START + timedelta(weeks=8),
            setup_scope=("BREAKOUT",),
            operational_policy_version="ops-v1",
        )
