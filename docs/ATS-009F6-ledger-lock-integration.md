# ATS-009F6 — Simulation-ledger integration with persisted loss locks

**Provenance: SIMULATION-ONLY.** Every input comes from `SimulationLedger`
(`source_id = SIMULATION-ONLY`). No broker credentials, API routes, Layer 3
authorization, enforcement or order submission are touched. This does **not**
complete broker-backed F2 acceptance.

## Design

`tests/test_simulation_ledger_loss_locks_postgres.py` drives ledger scenarios
through the existing pipeline: `SimulationLedger` -> `evaluate_loss_accounting`
-> `LossStateRepository` (PostgreSQL) -> `RepositoryLossLockProvider` /
`Layer3AuthorizationService`. No threshold or P&L logic is reimplemented; the
tests only assert on the existing policy's output. No production code changed.

## Requirement -> named cases (all `test_f6_*`)

| # | Requirement | Cases |
|---|---|---|
| 1 | Daily -3R / weekly -6R inclusive | `daily_minus_3r_inclusive_boundary_locks_daily_only`, `weekly_minus_6r_inclusive_boundary_locks_weekly_only`, `weekly_just_inside_limit_does_not_lock` |
| 2 | Lock survives NLV recovery and recreation | `lock_survives_nlv_recovery_and_service_recreation`, `layer3_denies_ledger_locked_account_without_reservation_rows` |
| 3 | Next session clears daily only | `next_session_clears_daily_lock_but_weekly_lock_persists` |
| 4 | New week uses separate frozen openings | `new_week_uses_separate_frozen_openings_and_clears_prior_locks` |
| 5 | Flows are not P&L; fees are | `flows_excluded_from_pnl_and_fees_included` (6 parametrized cases) |
| 6 | Replay / conflicting identity | `duplicate_replay_creates_no_duplicate_state_or_evidence`, `conflicting_identities_fail_closed_and_roll_back` |
| 7 | Rollback / bounded retry | `non_retryable_failure_rolls_back_all_partial_writes`, `transient_failure_after_writes_retries_with_same_request_identity`, `retry_exhaustion_fails_closed_without_partial_records` |
| 8 | Missing/stale evidence denies risk | `stale_valuation_is_unavailable_and_creates_no_state`, `incomplete_flow_reconciliation_is_unavailable`, `stale_opening_period_is_unavailable`, `unreconciled_valuation_is_unavailable`, `missing_daily_opening_evidence_yields_no_lock_state`, `layer3_denies_when_no_ledger_loss_state_exists` |
| — | Provenance / scope | `all_persisted_evidence_is_simulation_only`, `module_has_no_broker_credential_or_order_dependencies` |

Notes:
- A stale opening period is rejected at `append_opening` by the XNYS calendar
  check (`opening period ID does not match the XNYS calendar`), before any
  evaluation is persisted.
- Transient failures are real PostgreSQL `55P03` lock timeouts raised after
  writes, using a second session holding an advisory lock; they are not
  injected exceptions. `22012` (division by zero) is the non-retryable case.

## Results (coding evidence)

| Run | Result |
|---|---|
| New module, disposable PostgreSQL 16 | 26 passed |
| Full suite, disposable PostgreSQL 16 | **648 passed, 0 skipped** |
| Full suite, no `ATS_TEST_DATABASE_URL` | 576 passed, 72 skipped (all DB-gated; 26 new + 46 existing) |
| `ruff check .` / `ruff format --check .` / `git diff --check` | clean |

## Reproduce

```powershell
docker run -d --rm --name ats-f6-pg -e POSTGRES_PASSWORD=<random> -e POSTGRES_DB=ats_test -p 127.0.0.1:55432:5432 postgres:16-alpine
$env:ATS_TEST_DATABASE_URL = "postgresql://postgres:<random>@127.0.0.1:55432/ats_test"
.\.venv\Scripts\python.exe -m pytest -q -rs
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m ruff format --check .
git diff --check
docker stop ats-f6-pg
```

## Limitations / unverified

- Synthetic data only; opening equity is not an authenticated broker fact.
- Flow kind (CONTRIBUTION/WITHDRAWAL) is derived from sign in the simulator
  and is not broker-verified.
- Calendar/schedule attestation is a test wrapper (`SIMULATION-ONLY-CALENDAR-ATTESTATION`).
- Provider returns no state (fail closed) when the new period has no evidence.
- No concurrent-writer stress beyond existing tests and the lock-timeout cases.
- Broker account binding, timestamps, history completeness and reconciliation
  remain UNVERIFIED (F2 incomplete).

## Independent review

Not yet performed.
