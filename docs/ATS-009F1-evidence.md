# ATS-009F1 — transaction integration evidence

Base: `7d4f9c3d4211cef790954df9e3fed070955ac00a` on `feature/sprint-3-risk-engine`.
Date: 2026-10-22. Status: implementation and local PostgreSQL verification complete; independent review pending. No sprint acceptance or enforcement approval.

## Changes

- `Layer3AuthorizationService.authorize_with_retry` owns the database-only transaction boundary and returns after commit; preserves original plan/key across bounded retries. No API route, broker request or execution enabled.
- Same-key replay explicitly returns `replayed=True`, retains the reservation identity and sets `execution_enabled=False`. New decisions remain execution-disabled.
- Normalize PostgreSQL TIMESTAMPTZ to UTC in the repository loss-state provider. A database session in America/Los_Angeles reproduced the prior validation failure; normalization fixes it without relaxing UTC validation.
- Declare the exact calendar dependency already enforced by source; add a Webull/keyring optional extra for existing SDK tests. Compare with the owner's unpushed pyproject.toml before merging.

## Executed evidence

Python 3.12.14; PostgreSQL 16.15; psycopg 3.3.6; SQLAlchemy 2.1.6; exchange-calendars 4.13.1; Webull SDK 3.0.4. Each PostgreSQL case used a unique schema and restricted runtime role with Alembic upgrade/check through head `20261020_06`.

Final full suite: **526 passed, 33 skipped**, five dependency deprecation warnings. All 44 PostgreSQL cases executed, including 14 new F1 cases; **zero PostgreSQL skips**. All 33 skips concern absent external Webull diagnostic/runner scripts; they are not accepted as validated. Ruff `check src tests` and `git diff --check` passed.

## Coverage

- `test_f1_boundary_retries_postgres_failure_after_reservation_atomically`: PostgreSQL-server-raised SQLSTATE 40001/40P01/55P03 after real reservation writes, both recovery and exhaustion. Verifies same original key, distinct transaction IDs, loss-state reads/version recheck/write in the same session/transaction, no externally visible uncommitted row, one commit on success, zero reservations on exhaustion, and non-dispatching replay. Fault injection is explicitly server-raised, not a genuine conflict.
- `test_f1_boundary_does_not_retry_application_or_integrity_failure`: application exception and SQLSTATE 23505 are single-attempt failures and roll back the reservation.
- `test_f1_boundary_rejects_state_change_before_reservation`: changed final version fails closed without a reservation (provider-injected version change).
- `test_f1_concurrent_boundary_replays_share_one_durable_reservation`: three simultaneous same-account requests produce one reservation, two marked replays and no execution-enabled result.
- `test_f1_boundary_real_lock_timeout_exhausts_without_writes`: genuine competing advisory lock yields 55P03, two fresh sessions exhaust with no reservation; original key works after release.
- `test_f1_retry_helper_handles_real_serialization_conflict`: actual SERIALIZABLE concurrent update triggers 40001, proves whole-transaction re-read, recovery and exhaustion. The Layer 3 account-lock service requires READ COMMITTED; this genuine SERIALIZABLE check is helper-level, not a claim that Layer 3 runs SERIALIZABLE.
- `test_f1_retry_helper_handles_real_deadlock`: actual opposing row locks trigger 40P01 and helper retries the entire transaction, preserving one request row and excluding rolled-back increments.
- Existing PostgreSQL coverage additionally checks account-capacity serialization, stale snapshots, sticky lock after recovery, version-bound stale replay, loss-state CAS concurrency, append-only/runtime grants and partial-write rollback.

## Reproduction on a normal disposable PostgreSQL host

Install `python -m pip install -e ".[dev,webull]"`, create a disposable database named `ats_test`, and set `ATS_TEST_DATABASE_URL` to its PostgreSQL SQLAlchemy/psycopg URL. The test administrator needs CREATE SCHEMA/CREATE ROLE; never point this suite at production. Run:

```text
python -m pytest -q -rs --junitxml=f1-results.xml
ruff check src tests
git diff --check
```

Work runtime note: only uid 0 is mapped and each command has isolated networking. PostgreSQL Ubuntu binaries were extracted into scratch; a scratch-only preload bypassed the initial root guard (then returned the actual namespace uid for ownership checks). The final disposable cluster lived in /tmp outside workspace synchronization, bound to loopback with Unix sockets disabled, and was stopped after the run. No such shim or database setup is part of repository code. An earlier workspace-backed cluster encountered storage/synchronization errors; its results were discarded and the final run used a fresh /tmp cluster.

## Review and remaining gates

The Work agent directly reran the supplied implementation and found the timezone defect; it also authored these F1 changes. This is independently executed evidence relative to the earlier coding-agent report, **not independent reviewer approval of the new diff**. A separate reviewer must inspect the final commit/diff and rerun these cases. In particular, review SQLSTATE policy, trusted local-only provider contracts, replay consumers and loss-state/version/account-lock coupling.

Authenticated broker/opening equity/flow/calendar/reconciliation evidence (F2), external diagnostic runners and Sprint 3 acceptance remain incomplete. No ATS-009 enforcement, Layer 3 trading authorization, paper orders or live orders are approved.
