# ATS-036D owner-machine pre-activation proof

**PRE-ACTIVATION PASS on 2026-11-11 Central Time.** The owner checkout
was synchronized to `7ca657b017ec78386a53aa8fb09dc37f8a6bcece`. The
fixed wrapper successfully parsed Docker inspection JSON under Windows
PowerShell and reached the Python tick.

## PostgreSQL and campaign identity

The active `ats036d-postgres` container is healthy with restart policy
`unless-stopped`. Its `/var/lib/postgresql/data` mount uses the named
volume `ats036d-postgres-data`. The cluster was copied to that volume
while stopped during the prior setup, and the original container was
retained stopped as a rollback copy. A restart on the named volume was
verified during that setup. This retry renamed the running named-volume
container to the wrapper's expected name.

A fresh PostgreSQL connection using the scheduled user's persisted
`ATS_DATABASE_URL` verified Alembic head `20261028_13` and one unchanged
PAPER campaign.

| Field | Verified value |
| --- | --- |
| Campaign ID | `e00289e2-4e14-4819-a493-de93263c79ad` |
| Manifest digest | `e8de06486023eb6771d42bcee4ec3f7a592b6be46d3e97767ebde88cf964d356` |
| Start | 2026-11-12T14:45:00Z |
| End | 2027-02-04T14:45:00Z |
| Playbook | 3.0 |
| Operational policy | `ops-v1` |
| Setup scope | breakout, pullback |

The Windows keyring check passed for the current interactive user with
environment fallback disabled. Campaign runs, trade plans, order intents,
and fills were all zero.

## Wrapper and Windows task

The pre-Day-1 `scripts/run_ats036d_tick.ps1` run evaluated at
`2026-11-12T02:17:52.814017Z` and returned:

```text
disposition=NOOP
reason=CAMPAIGN_NOT_STARTED
activated=false
executed_jobs=[]
broker_order_submission=DISABLED
live_execution_used=false
proposal_creation_used=false
```

No campaign artifact directory or activation artifact was created.

The installer registered `ATS-036D-PAPER-Campaign`. Windows reported
state `Ready`, first trigger and next run at 8:45 AM Central on November
12, 2026 (14:45 UTC), and five-minute repetition. The action is
`powershell.exe` running `scripts/run_ats036d_tick.ps1` from the owner
checkout. The principal matches the current interactive Windows user,
with interactive logon and limited run level. No prior task run was
recorded. The installer used the existing user-scoped database setting.

Day 1 was not manually executed. LIVE remained disabled and Webull order
submission remained disabled. PR #87 was not merged.
