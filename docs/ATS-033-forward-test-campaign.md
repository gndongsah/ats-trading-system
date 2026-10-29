# ATS-033 Forward-Test Campaign Boundary

ATS-033 moves the accepted ATS-031/032 PAPER forward-test infrastructure into a controlled, durable campaign identity.

## Campaign contract

`ForwardTestCampaignManifest` freezes:

- PAPER run type;
- campaign ID;
- creation, start and end timestamps;
- an 8–12 week evidence window;
- Playbook version and canonical SHA-256 digest;
- operational-policy version;
- normalized setup scope.

`campaign_manifest_digest()` produces a deterministic digest used by both campaign persistence and every newly orchestrated forward-test run.

## Fail-closed preflight

Before risk or broker submission, ATS-031 now validates the active campaign against:

- PAPER-only mode;
- current Playbook version and digest;
- current operational-policy version;
- campaign start/end window;
- approved setup scope.

A failed preflight produces a BLOCKED run with campaign identity attached and does not call the risk checker or broker.

## Persistence

Alembic revision `20261028_13` adds `ats_forward_test_campaigns` and campaign identity columns to `ats_forward_test_runs`.

The campaign repository is idempotent and immutable by campaign ID. The run repository rejects new evidence unless:

1. `campaign_id` and `campaign_digest` are present;
2. the referenced campaign exists;
3. the persisted campaign digest matches the run evidence.

Read-back also verifies scalar campaign columns and run campaign bindings against the typed JSON payload.

Existing pre-ATS-033 forward-test rows remain readable because the new run columns are nullable for migration compatibility. New repository writes require campaign binding.

## Evidence queries

`ForwardTestRunRepository.list_by_campaign()` provides the campaign-scoped evidence boundary needed by ATS-034 weekly/campaign reporting.

## Safety boundary

This story does not enable LIVE execution, does not change Layer 3 authorization rules, and does not treat ATS-009 broker-backed loss-lock evidence as satisfied. PAPER forward testing remains isolated from LIVE approval.
