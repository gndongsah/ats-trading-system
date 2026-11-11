# ATS-036C owner-machine acceptance proof

**PASS on 2026-11-11.** Verified on
`feature/ats-036c-paper-campaign-scheduler` at
`ba3254f660150cfdcb3952ba040996564ed1f3f7` using the owner checkout's
existing virtual environment and the dedicated `ats_test` PostgreSQL 16.15
database in the healthy `ats036b-postgres` container. Alembic was at head
`20261028_13`. The database URL was scoped to the proof processes.

## Campaign manifest

| Field | Verified value |
| --- | --- |
| Campaign ID | `e00289e2-4e14-4819-a493-de93263c79ad` |
| Manifest digest | `e8de06486023eb6771d42bcee4ec3f7a592b6be46d3e97767ebde88cf964d356` |
| Run type | PAPER |
| Created at | 2026-11-11T17:34:02.546527Z |
| Start at | 2026-11-12T14:45:00Z |
| End at | 2027-02-04T14:45:00Z, exactly 12 weeks after start |
| Playbook version | `3.0` |
| Playbook digest | `fb093f514d015693f79a4b06d5284c59e2a5e3d1157fb2c50ff8683cc49afd23` |
| Operational policy version | `ops-v1` |
| Setup scope | breakout, pullback |

The CLI calculated the next future XNYS start and committed one manifest
with `init`. After that process exited, `status` reloaded the identical
manifest from a fresh PostgreSQL session. The recomputed digest matched
the stored digest.

## Schedule and artifacts

- Separate `plan-day` invocations returned identical first-day job lists
  under `campaign-schedule-v1`. The 25 market windows ran at 15-minute
  intervals from the campaign start at 14:45 UTC through 20:45 UTC.
  XNYS closed at 21:00 UTC. The daily scorecard was scheduled for
  21:15 UTC. The plan contained only market windows and the scorecard.
  Planning produced no execution.
- The `scorecard` command wrote one empty ATS-032 scorecard for the
  pre-Day-1 date 2026-11-10 to an isolated filesystem artifact directory.
  Its envelope bound the campaign ID and manifest digest. The payload
  digest was
  `1e9b8bfbf14516de1d3e4e0d1ea814564930c93e0f08d5318893188c5393a7b3`.
  A fresh CLI process and PostgreSQL session replayed the same output
  and preserved the original file bytes. A separate process loaded and
  validated the same envelope. A changed payload at the same key was
  rejected, and a tampered copy failed payload digest validation.
- Fresh CLI invocations of `weekly-report` and `readiness` succeeded
  against the same persisted identity. These report-only checks used
  synthetic as-of timestamps of 2026-11-18T21:30:00Z and
  2026-11-12T21:30:00Z respectively. Readiness returned `HOLD` and
  `live_approval_granted=false`. The artifacts stayed in the isolated
  proof directory.

The wall clock remained before campaign start. PostgreSQL had zero campaign
runs, trade plans, order intents, and fills. The scorecard had zero proposals
and approvals. Webull order submission and LIVE execution remained disabled.

The focused campaign, scheduler, runtime, scorecard, reporting, readiness,
PostgreSQL, and execution capability tests passed with **136 passed and zero
skips**. Repository-wide Ruff lint passed, and all 214 files passed Ruff
format checking.
