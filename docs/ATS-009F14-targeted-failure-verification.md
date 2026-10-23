# ATS-009F14 — targeted risk integration failure verification

Date: 2026-10-23 America/Chicago. **Author-executed verification, not independent
review approval.** Work authored F11/F12 and therefore cannot supply its own
independent acceptance verdict. The independent-review gate remains open.

## Assessed source and change scope

Assessed merged Sprint 3 commit `72b52f2802b0326a86f4cedc60ccedc08565b810`,
tree `65868e1f94d6a3291f4d46625f8a1a0295bcd738` (merged F13). F11/F12 runtime
code is the same implementation assessed by F13 at `801812a`.

This change adds only `tests/test_simulation_risk_failure_verification.py` and
this report. No runtime source, migration, policy, execution flag, broker request
or API route changed. The new test module reuses F12's real PostgreSQL capture,
accounting and publication setup; it does not replace the loss provider with a
passing stub. Fixtures retain synthetic principals, portfolios and calendar
attestations. They are not authenticated broker facts.

Inspected paths: F11 candidate/opening commit and post-commit receipt eligibility;
receipt-qualified period/correction/current-state reads; F12 valuation/snapshot
NLV and timestamp equality; Layer 3 sizing hook, plan cap, portfolio assessment,
final version check and reservation metadata; whole-transaction retry helper.
No new runtime defect was demonstrated by this bounded inspection and the cases
below. This is not an exhaustive review or a claim that remaining risk is zero.

## Committed probe matrix

All names below are in `tests/test_simulation_risk_failure_verification.py`.

| Probe | Cases | Observed assertion |
|---|---:|---|
| `test_f14_retry_after_reservation_preserves_basis_and_atomicity` | 6 | PostgreSQL-server-raised SQLSTATE 40001/40P01/55P03 after actual reservation writes, recovery and exhaustion: two different transaction IDs, unchanged original key and exact basis metadata; observer sees no uncommitted row; recovery commits one four-share reservation and non-dispatching replay; exhaustion leaves zero reservations |
| `test_f14_correction_after_reservation_denies_same_key_replay` | 2 | Daily or weekly correction after successful reservation denies original-key replay, keeps one durable reservation and returns no execution-enabled result |
| `test_f14_final_adapter_regime_policy` | 4 | Regime 2 returns 149 shares under the theme limit; regime 4 without explicit multiplier, regime 5 without approved special setup, and missing regime deny with no reservation |
| `test_f14_equal_nlv_different_timestamp_denies` | 2 | Equal equity with portfolio timestamps one microsecond before/after loss valuation denies despite fresh evidence |
| `test_f14_snapshot_replacement_invalidates_old_adapter` | 2 | New snapshot version invalidates old adapter for a new request or original-key replay; correctly bound new adapter can reserve where no prior reservation exists |
| `test_f14_invalid_buying_power_rejected_before_database` | 3 | NaN, negative and float buying power rejected at construction; no reservation written (fixture setup itself uses the database) |
| **Total** | **19** | Bounded scenarios, not a coverage percentage |

Retry faults are explicit PostgreSQL RAISE exceptions, **not genuine conflicting
transactions**. Genuine lock timeout and helper serialization/deadlock scenarios
remain in F1's existing suite. F14 does not mislabel those helper-level conflicts
as end-to-end simulation conflicts. Regime 2's final quantity is theme-limited;
the isolated 0.75 multiplier arithmetic is covered by existing sizing tests,
not independently isolated by this particular integration case. Regime 4 denial
proves the current adapter's unsupported-policy behavior, not support for an
explicit multiplier path that the adapter does not expose.

The basis assertion checks the persisted fields exactly: SIMULATION-ONLY source,
snapshot version, as-of, NLV, separate buying power and reference risk fraction.
It does not assert that metadata itself authenticates any of those values.

## Execution evidence

Focused run: **19 passed, zero skipped**, four dependency warnings, fresh disposable
PostgreSQL 16.15, `--fail-on-skip`. Python 3.12.14, SQLAlchemy 2.1.6, psycopg 3.3.6,
Pydantic 2.14.1, pinned exchange-calendars 4.13.1. Each database case uses the
existing unique schema/restricted runtime-role fixture and Alembic upgrade/check.
The scratch-only cluster uses the F1 root-namespace workaround; no shim or database
configuration is introduced into the repository. The cluster is stopped after tests.

Full-suite run: **771 passed, zero skipped**, five dependency warnings, fresh
disposable PostgreSQL 16.15 with `--fail-on-skip`; all database-gated cases
executed. The 752 existing cases plus 19 F14 cases reconcile to 771.
Repository-wide Ruff lint/format and `git diff --check` passed. No broker/keyring
calls or order paths were used. No independent execution is inferred from CI.

Reproduce on a normal disposable PostgreSQL host with the documented `ats_test`
database and `ATS_TEST_DATABASE_URL`:

```text
python -m pip install -e ".[dev,webull]"
python -m pytest -q tests/test_simulation_risk_failure_verification.py --fail-on-skip
python -m pytest -q --fail-on-skip
ruff check src tests
ruff format --check src tests
git diff --check
```

## Independent review handoff — still required

A reviewer who did not author these changes should inspect the exact final
commit/tree linked in the PR, F11/F12 runtime code and F13 requirements matrix.
Record reviewer identity, inspected commit/tree, source requirements, findings and
severity, independent targeted/full test commands and results, environment, and
resolved revision if changes are needed. Include the receipt/correction/version
coupling, replay behavior and the gap between generic externally sized Layer 3
plans and the explicitly composed simulation adapter. Reuse these tests, but do
not treat author-designed test success as the review verdict.

The independent gate is not closed by an owner merge report, this author rerun,
green CI or this document. No further policy approval is requested here.
Broker-backed F2 acceptance and Sprint 3 acceptance remain incomplete. Current
simulation source completeness/positions/buying power/schedule/principal remain
harness assertions. All enforcement and paper/live order submission stay disabled.
