# ATS-009F11 — simulation-only opening capture

Base `4ac9ea449446ba3ea3c17ae6e4530c3b234f6acb`. Approved policy: [F10 PR #13](https://github.com/gndongsah/ats-trading-system/pull/13),
approval record `cbd448ad04b30f3b32624803c0a2cd50fe309db0` (owner A/A/A).
F9 source reconciliation: [PR #12](https://github.com/gndongsah/ats-trading-system/pull/12)
at `3eb2707`. Neither documentation merge status is treated as policy approval;
the explicit owner statement is the approval source.

**SIMULATION-ONLY.** No broker call, credentials, API route, portfolio-sizing
policy change, execution enablement or order submission. No migrations.
Author-executed implementation evidence; independent final-head review pending.

## Persistence design checkpoint — no migration needed

Existing `ats_loss_period_openings` holds the immutable financial facts. Existing
append-only `ats_source_documents` holds validated `SourceDocument` envelopes
with JSON payloads serialized inside `version_metadata`. Separate source types
and deterministic identities are used for:

- `SIM_OPENING_CANDIDATE`: typed `CaptureFacts` with opening, exact cutoff,
  observation time, sealed-prefix digest/watermark, policy and authority revision.
- `SIM_OPENING_RECEIPT`: typed `CompletionReceipt` linking candidate ID/digest
  to post-commit completion observation and monotonic elapsed duration.
- `SIM_OPENING_CORRECTION`: original capture ID/digest, account/period/kind,
  proposed NLV, reason, discovered time, policy, SIMULATION-ONLY source and
  PENDING review status. No activation/replacement API is implemented.

Reuse `EvidenceRepository.save_source` for append-only identity/version checking
and existing runtime SELECT/INSERT grants. `LossStateRepository` account locks
serialize candidate, receipt and correction operations. No changes to table
constraints, existing generic provider behavior or historical evidence schemas.
The original complete sealed ledger is owned by the harness; its digest and
watermark are persisted here, not a new durable fill/ledger journal.

## Capture and completion semantics

`SimulationOpeningCapture.capture` requires an injected `ModelClock` and an
XNYS calendar with an explicitly SIMULATION-ONLY authority revision. The source
is a revalidated `SealedLedger`, not a broker valuation. `seal` is the harness's
explicit completeness assertion; it cannot prove upstream ingestion.

1. Resolve daily opening or first-session-of-week opening via pinned XNYS
   schedule. Holidays/DST use the calendar, not fixed UTC hours. Require source
   cutoff exactly at the opening; reject missing/unverified schedule authority.
2. Seal includes every known ledger entry through the cutoff, including all
   same-time entries in ledger order. Future entries are excluded. Ordered
   prefix, watermark and mode are hashed canonically; reordered same-time
   events conflict even if their NLV sums match.
3. Observe source at model time `[T,T+10s]` inclusive. Separate daily/weekly
   records use the same cutoff NLV for opening equity and opening NLV, with
   explicit USD/SIMULATION-ONLY provenance.
4. Transaction 1 atomically writes opening plus candidate facts under the
   account lock. Metadata failure rolls back both. After actual commit returns,
   read model wall and monotonic clocks. Require completed time ≤T+60s,
   completed≥observed, elapsed in [0,60], and wall elapsed≥monotonic elapsed.
   Clock rollback cannot enlarge the window. Monotonic elapsed is persisted as
   exact decimal text; it is not used for money arithmetic.
5. Transaction 2 appends a linked receipt, with either accepted or rejected
   completion. It attests the **opening/candidate transaction's durable
   completion**, not the receipt transaction's commit time. The provider waits
   for the durable receipt before using the opening. Receipt writing can finish
   later; it does not change the recorded source or completion instant.
6. If late, receipt write fails or the process stops between phases, the
   opening/candidate stays quarantined. It is not accepted by the F11 provider.
   A candidate lacking a receipt can be completed by a matching retry only while
   the completion checks still pass. A rejected receipt never becomes accepted.
   Already accepted identical replay retrieves the original facts even later;
   this does not manufacture a missed opening or alter original timing.

This split avoids claiming a pre-commit timestamp proves durable completion.
The two transactions as a whole are intentionally not one atomic operation:
quarantined evidence is an allowed durable failure outcome. They never create a
usable half-capture. Denial is through absence of an accepted capture, not
rewriting or deleting audit evidence. Callers supply a SQLAlchemy sessionmaker
and must not wrap this transaction-owning operation in another transaction.
Database retry policy is not expanded; caller retries retain sealed identity.

`HISTORICAL_REPLAY` seals cannot pass the capture API. Historical ledger
valuation remains available separately; it cannot create a capture receipt.

## Loss-provider connection and trust limits

`SimulationCaptureLossProvider` is a separate explicit simulation adapter. It
requires a transaction/account lock, both current-period captures and valid
linked receipts, matching opening-row digests/identities/calendar, and existing
loss-state events referencing those exact opening rows. The current loss
valuation must have SIMULATION-ONLY provenance and age `[0,30s]` inclusive;
future/stale observations return no state. Historical openings themselves do
not expire after 30 seconds. It delegates threshold/sticky projection to the
existing repository provider, without another policy calculation.

Correction discovery appends evidence only and returns no usable simulation
state for the affected period. It never modifies a denominator or lock event.
A daily-only correction ceases to affect a different daily period; a weekly
correction continues to deny throughout its week. New periods require their
own valid captures and observations. No reviewer approval or same-period
activation machinery is invented for v0.1.

There is no runtime/API wiring or broker adapter. Existing generic loss-state
infrastructure remains structurally typed and is not a broker authenticator;
it must not be substituted for the receipt-aware simulation provider in F11
scenarios. The isolated adapter and SIMULATION-ONLY provenance prevent accidental
use through this component's supported interface, not malicious in-process
forgery or arbitrary database writes. A future broker consumer needs its own
authenticated-source boundary and must reject simulation provenance explicitly.
The model clock and completeness/authority assertions are trusted harness
inputs; real clock synchronization and emergency authority are unimplemented.
No broker freshness, exact-boundary API availability or history completeness is
established by these tests. Timing policy remains blocked for broker capture
until source semantics are verified.

## Executed evidence

Python 3.12; PostgreSQL 16.15, fresh disposable cluster/database with restricted
runtime roles and Alembic upgrade/check through head `20261020_06`.
The Work namespace-only scratch runtime procedure is recorded in F1 evidence;
no shim or PostgreSQL setup is repository code.

- Full suite with `--fail-on-skip`: **736 passed, zero skipped**, five dependency
  deprecation warnings. All database-gated cases executed.
- **32 new F11 cases** in `tests/test_simulation_opening.py`.
- Repository-wide Ruff lint/format and `git diff --check` passed.
- An earlier focused run exposed a receipt-hashing issue (float elapsed time
  was incompatible with canonical evidence hashing); elapsed evidence now uses
  finite decimal serialization. The final full run includes the fix.

| Requirement | Named tests (prefix `test_` omitted) |
|---|---|
| Exact source cutoff, historical replay, completeness | `historical_replay_incomplete_and_wrong_cutoff_never_capture` |
| Same-time ordering/sealed conflict | `seal_prefix_includes_all_same_cutoff_entries_and_excludes_later_events`, `changed_same_time_order_is_a_seal_conflict_even_if_nlv_is_equal` |
| Observation 0/10 inclusive and just outside | `observation_boundary_inclusive`, `observation_outside_window_no_writes` |
| Completion 60 inclusive, lateness and rollback | `completion_sixty_seconds_inclusive`, `late_or_clock_rollback_quarantines_durable_opening` |
| Holidays, DST, explicit simulation authority | `calendar_dst_holiday_week_ids`, `missing_schedule_authority_denied`, `explicit_simulation_authority_required` |
| Replay and concurrency | `replay_conflict_and_separate_daily_weekly_identity`, `concurrent_capture_serializes_and_deduplicates` |
| Atomic rollback/quarantine/recovery | `metadata_failure_rolls_back_opening`, `receipt_failure_leaves_candidate_quarantined`, `missing_receipt_denies_even_with_openings_and_current_loss_state` |
| Current valuation 0/30 inclusive and just outside | `current_valuation_age_and_missing_receipt_gate` |
| Corrections preserve locks/denominators, scope | `correction_preserves_denominators_sticky_locks_and_denies_new_risk`, `correction_denial_scope_at_daily_rollover` |
| Daily/weekly rollover and new frozen NLV | `daily_rollover_preserves_weekly_capture_and_new_week_requires_new_facts`, `new_week_separate_captures_follow_the_new_frozen_nlv` |

## Reproduction

On a normal disposable PostgreSQL host, create a dedicated `ats_test` database
and set `ATS_TEST_DATABASE_URL` to its SQLAlchemy psycopg URL. The test admin
needs schema/role creation privileges; never use a real account database.

```text
python -m pip install -e ".[dev,webull]"
python -m pytest -q -rs --fail-on-skip
python -m ruff check .
python -m ruff format --check .
git diff --check
```

Without a test database, ordinary local runs can explicitly skip database tests;
CI's fail-on-skip mode must reject that outcome. No Webull/keyring request is
needed for F11. Independent final-head review and CI remain pending. Sprint 3
and broker-backed acceptance are not declared; all order paths remain disabled.
