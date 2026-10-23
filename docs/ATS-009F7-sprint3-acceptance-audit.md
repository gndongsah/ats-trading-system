# ATS-009F7 — Sprint 3 acceptance audit (non-enforcing)

**Status: AUDIT ONLY. Sprint 3 is NOT accepted. Trading, ATS-009 enforcement,
Layer 3 trading authorization and all paper/live order submission remain
disabled.** No features were added; this change adds only this document.

Audited commit: `d18070a` (`origin/feature/sprint-3-risk-engine`, includes F6
merged as PR #9). Audit branch: `feature/ats-009f7-sprint3-acceptance-audit` in a separate worktree; the
owner's local worktree and stashes were not touched.

## 1. Source of requirements — and a blocker

The repository contains **no ATS-008 specification, no ATS-009 policy
document and no Sprint 3 exit criteria**. The only exit criterion in the repo is
Sprint 1 (README). `git log` (13 commits) contains no ATS-008 reference. This
audit therefore derives requirements from (a) constants enforced in code, (b)
the F1–F6 evidence documents and (c) requirements stated by the owner in task
prompts. Rows sourced from (c) are labelled *owner-stated*. **Repository-only ATS-008 traceability is BLOCKED pending retrieval of the
authoritative requirements.** The existing Notion backlog and attached playbook
are additional sources to inspect before asking the owner to supply anything.
Their absence from this checkout does not establish that requirements do not exist.
Sprint 3 acceptance must be mapped to the authoritative criteria before declaration.

## 2. Evidence classes used in this document

| Class | Meaning |
|---|---|
| **SIM** | Synthetic/offline proof: deterministic tests, synthetic ledger, disposable PostgreSQL. Says nothing about the broker. |
| **OWNER** | Owner-observed Webull sandbox/desktop facts, reported in chat or sanitized into F2 docs. Not reproducible by this repo's CI; not independently reproduced here. |
| **UNVERIFIED** | Broker guarantee that no document, test or observation establishes. |
| **CODER / REPRODUCED** | CODER = result reported by the authoring agent. REPRODUCED = re-executed in this audit (below) or by a reviewer with a recorded transcript. |

## 3. Executed evidence (reproduced in this audit)

Environment: Python 3.12.10 (project `.venv`), PostgreSQL 16 (`postgres:16-alpine`,
disposable container, database `ats_test`, random password, loopback, stopped
afterwards). Run from the audit worktree at `d18070a`.

| Check | Result |
|---|---|
| Full suite with `ATS_TEST_DATABASE_URL` | **648 passed, 0 skipped, 0 failed**, 1 warning |
| Full suite without PostgreSQL | **576 passed, 72 skipped, 0 failed** (all 72 skips are PostgreSQL-gated; this is *not* PostgreSQL validation) |
| `pytest --collect-only` | 648 collected (matches F6's reported 648) |
| `ruff check .` / `ruff format --check .` / `git diff --check` | all clean (78 files formatted) |
| GitHub CI, head `53d2c39` of PR #9 | two `tests` jobs, both `success` (read via GitHub API; logs not inspected) |

Targeted claim checks (executed ad hoc, not added to the suite):

- `LossLockState` with weekly limit `-8R` is rejected
  (`ATS-009 weekly loss lock must be configured at -6R`); `-6R` accepted.
- **`Layer3Decision(..., execution_enabled=True)` constructs successfully.**
  The flag defaults to `False` and every code path sets it `False`, but the type
  does not forbid `True` (finding F7-1).
- Static scan: `src/api.py` exposes only `/health`, `/playbook/version`,
  `/layer0/market-opportunity`, `/layer1/discovery`. No module outside
  `webull_sandbox_*` and `simulation_ledger` imports Webull or the simulation
  ledger; `Layer3AuthorizationService` is instantiated only in tests;
  `BROKER_EXECUTION_ENABLED` is a constant `False` that no code reads;
  `submit_order_to_broker` always raises `ExecutionDisabledError`; no `src/`
  module contains an order-placement call.

The prior-evidence documents (F1: 526/33 then 530/33 skips; F2: 547/0; F6:
648/0) are consistent with the counts above; their agent-run numbers are CODER
results except where a reviewer transcript is recorded (F1 owner-supplied
review, F6 "Work review" in PR #9). Neither is an independent approval of
Sprint 3.

## 4. Acceptance matrix

Legend: **PASS** = implemented, tested and executed for the stated scope;
**PARTIAL** = implemented/proved only for synthetic scope or only some
sub-requirements; **BLOCKED** = cannot be evaluated or cannot pass without
external input. "Scope" is always SIM unless stated.

### 4.1 Portfolio heat, concentration, sizing (ATS-007 / ATS-010 as implemented)

| Requirement | Implementation | Named tests | Executed evidence | Remaining gap | Status |
|---|---|---|---|---|---|
| Total heat cap 4R, correlated factor cap 2R, theme 15% of equity, 1R = 0.5% (fixed v0.1 policy; alternate values rejected) | `portfolio.py` `PortfolioRiskPolicy`, `assess_portfolio_risk` (exact `Fraction` math) | `test_total_heat_resizes_to_the_exact_largest_whole_share_quantity`, `test_correlated_heat_is_counted_across_different_named_themes`, `test_theme_concentration_limits_gross_notional_separately_from_heat`, `test_multiple_correlated_factors_each_apply_their_own_heat_limit`, `test_candidate_reaching_theme_cap_exactly_is_allowed` | 14 `test_portfolio.py` cases pass (SIM) | Policy values are code constants with no source document in repo (§1). Heat/limit basis is `snapshot.equity` (current), **not** the frozen opening used by loss locks; whether that is intended is undefined (F7-3). | PARTIAL |
| Fail closed on stale, incomplete, unclassified, unconfirmed-stop, taxonomy/policy mismatch | `assess_portfolio_risk` early denials | `test_stale_incomplete_and_unclassified_snapshots_are_denied`, `test_unconfirmed_and_missing_stops_fail_closed`, `test_missing_freshness_policy_and_taxonomy_mismatch_fail_closed` | pass (SIM) | `positions_complete`, `orders_complete`, `stop_confirmed`, `ats_managed_exposure_excluded` are caller-asserted booleans; nothing proves a broker snapshot is complete (F7-4). | PARTIAL |
| Partial fills/pending orders/reservations conserve exact risk | `ReservationExposure`, `FilledExposure` | `test_partial_fill_and_remaining_order_conserve_the_same_exact_risk`, `test_fill_at_higher_price_uses_actual_risk_and_unconfirmed_stop_blocks`, `test_external_pending_orders_consume_risk_and_theme_capacity` | pass (SIM) | No real broker order/fill stream is consumed; broker event verification is a Protocol with no production implementation. | PARTIAL |
| Position sizing (ATS-007) | `risk.py` | 19 `test_risk.py` cases (e.g. `test_whole_share_rounding_boundaries_and_small_risk_budget`, `test_regime_five_always_denies_without_executable_quantity`) | pass (SIM) | none identified within synthetic scope | PASS (SIM) |

### 4.2 ATS-009 loss locks and accounting

| Requirement | Implementation | Named tests | Executed evidence | Remaining gap | Status |
|---|---|---|---|---|---|
| Daily −3R and weekly −6R, inclusive; other values rejected | `ats009_accounting.py` `DAILY_LOSS_LIMIT_R`, `WEEKLY_LOSS_LIMIT_R`; `layer3.LossLockState.blocks_risk_increase` | `test_inclusive_boundaries`, `test_weekly_limit_other_than_minus_six_is_rejected`, `test_daily_limit_other_than_minus_three_is_rejected`, `test_weekly_loss_just_inside_inclusive_boundary_does_not_cross`, F6 `test_f6_daily_minus_3r_inclusive_boundary_locks_daily_only`, `test_f6_weekly_minus_6r_inclusive_boundary_locks_weekly_only` | pass incl. PostgreSQL (SIM); −8R rejection re-checked here | none for synthetic scope | PASS (SIM) |
| 1R = 0.5% of **separately frozen** daily/weekly opening equity | `PeriodOpeningEquity`, `evaluate_loss_accounting` | `test_daily_and_weekly_use_independent_frozen_equity_denominators`, `test_f6_new_week_uses_separate_frozen_openings_and_clears_prior_locks` | pass (SIM) | **No component captures or freezes an opening snapshot at a period boundary.** F6 derives openings from the synthetic ledger's history; `source_id`/`reconciled` are caller assertions ("provenance is not authentication": `test_contracts_are_immutable_and_provenance_is_not_authentication`). | PARTIAL |
| Flow-adjusted NLV; deposits/withdrawals not P&L; fees are | `evaluate_loss_accounting` (`valuation − opening − Σflows`) | `test_deposit_and_withdrawal_are_excluded_from_net_pnl`, `test_cash_flows_are_signed_and_period_scoped`, `test_fee_already_reflected_in_nlv_is_counted_once`, F6 `test_f6_flows_excluded_from_pnl_and_fees_included` (6 cases) | pass (SIM) | Flow kind/sign are synthetic. Broker cash-flow categories, signs and reversal linkage are UNVERIFIED (see §5). | PARTIAL |
| Sticky until valid period reset; NLV recovery does not unlock; daily resets next session, weekly persists | `LossStateRepository.append_evaluation` (`locked = crossed or prior locked for same period`) | `test_sticky_flag_blocks_after_nlv_recovery`, F6 `test_f6_lock_survives_nlv_recovery_and_service_recreation`, `test_f6_next_session_clears_daily_lock_but_weekly_lock_persists` | pass incl. PostgreSQL (SIM) | Reset validity depends on the calendar attestation, which is a test wrapper (next row). | PARTIAL |
| Session calendar and emergency-schedule authority | `ats009_calendar.py` (`exchange-calendars==4.13.1`, XNYS); package schedule alone never marks risk eligible | 13 `test_ats009_calendar.py` cases (e.g. `test_no_production_attestation_never_marks_risk_eligible`, `test_invalid_or_closed_attestation_never_allows_new_risk`) | pass (SIM) | **No production emergency-closure/authority adapter exists**; only a Protocol. Tests use `SIMULATION-ONLY-CALENDAR-ATTESTATION`. | BLOCKED for production, PASS (SIM) for fail-closed behavior |
| Missing/stale opening, valuation, flow, reconciliation evidence denies new risk | `evaluate_loss_accounting` reasons; provider returns `None`; Layer 3 denies | `test_stale_or_future_valuation_is_unavailable`, `test_incomplete_or_unreconciled_valuation_is_unavailable`, F6 `test_f6_stale_valuation_is_unavailable_and_creates_no_state`, `test_f6_layer3_denies_when_no_ledger_loss_state_exists`, `test_f6_missing_daily_opening_evidence_yields_no_lock_state` | pass (SIM) | none for synthetic scope | PASS (SIM) |
| Broker timestamps, ordering, freshness | `biz_time` kept raw; `BROKER_ORDERING_UNVERIFIED` flag | `test_webull_sandbox_cash_activities.py` (24), F5/F3 runners | pass (SIM) | Format/timezone/ordering of `biz_time` UNVERIFIED; local `observed_at` ≠ broker time. | BLOCKED (broker) |
| Restatement/corrections | duplicate/conflict rejection; reversal marked ambiguous | `test_duplicate_external_flow_identity_is_rejected`, `test_conflicting_duplicate_flow_is_unavailable_even_if_model_bypassed`, F3 reversal-ambiguity tests | pass (SIM) | No restatement or correction policy beyond fail-closed conflict. | PARTIAL |

### 4.3 Reservations, replay, version binding, atomicity, retries (Layer 3)

| Requirement | Implementation | Named tests | Executed evidence | Remaining gap | Status |
|---|---|---|---|---|---|
| Same-PostgreSQL account lock; concurrent same-account requests serialize | `Layer3AuthorizationService._lock_account` (advisory lock), requires READ COMMITTED | `test_f1_concurrent_boundary_replays_share_one_durable_reservation`, F1 account-capacity serialization cases in `test_postgres_persistence.py` | pass (SIM, real PostgreSQL) | Single-database scope only (as designed). | PASS (SIM) |
| Loss-state version/policy bound to reservation; stale version denied; legacy unbound replay cannot bypass | `_loss_binding_is_current` | `test_reservation_binds_loss_state_and_stale_replay_is_denied`, `test_unbound_legacy_reservation_cannot_replay_with_unverifiable_loss_state`, `test_transactional_provider_without_version_or_sticky_flags_is_denied`, F1 missing-binding PostgreSQL regressions | pass (SIM) | Independent reviewer confirmation of revised F1 head is recorded only as reviewer-reported. | PASS (SIM) |
| Non-dispatching replay; original key preserved | `authorize` replay path sets `replayed=True, execution_enabled=False` | `test_f1_boundary_retries_postgres_failure_after_reservation_atomically`, `test_f1_boundary_real_lock_timeout_exhausts_without_writes`, F6 `test_f6_duplicate_replay_creates_no_duplicate_state_or_evidence` | pass (SIM) | See F7-1 for the type-level hole. | PARTIAL |
| Bounded whole-transaction retries on approved SQLSTATEs only; exhaustion fails closed, no partial rows | `transaction_retry.run_in_transaction` (`40001`, `40P01`, `55P03`) | `test_transient_error_is_retried_with_same_key_and_no_partial_rows`, `test_exhaustion_fails_closed_and_rolls_back_every_attempt`, `test_non_transient_error_is_not_retried`, `test_unknown_sqlstate_is_not_retried`, `test_attempt_bound_is_enforced`, F1 `test_f1_retry_helper_handles_real_serialization_conflict`, `test_f1_retry_helper_handles_real_deadlock`, F6 retry cases | pass; real `55P03`, `40001`, `40P01` conflicts produced on PostgreSQL (SIM). Server-raised injection is distinguished from genuine conflicts in F1 doc. | Layer 3 itself runs READ COMMITTED; genuine `40001` is helper-level only. Retries never wrap broker calls — true because no broker call exists in the path. | PASS (SIM) |
| Append-only evidence; migration chain/head | migrations `20261014_01` … `20261020_06`; triggers; runtime role is SELECT/INSERT only | `test_postgres_persistence.py` (46 cases incl. append-only/grants, Alembic upgrade/check) | pass (SIM) | none | PASS (SIM) |
| Enforcement and order paths disabled | `BROKER_EXECUTION_ENABLED=False`, `submit_order_to_broker` raises, no route, `enforcement_eligible=False` on Webull evidence objects | `test_layer3.py` (execution-disabled), `test_f6_module_has_no_broker_credential_or_order_dependencies`, `test_webull_sandbox_evidence_runner.py` source-token scan, `test_webull_sandbox_readonly.py::test_module_has_no_order_capability_or_credential_output` | Static scan and tests above (REPRODUCED) | **Disabled by convention, not by type or by an invariant test over `src/`** (F7-1). `_build_sdk_trade_client` instantiates the full SDK `TradeClient`; both account and activity APIs are used through read-only wrappers (safe today, brittle). | PARTIAL |

### 4.4 Webull sandbox (read-only) evidence

| Requirement | Implementation | Named tests | Executed evidence | Remaining gap | Status |
|---|---|---|---|---|---|
| Keyring-only credential handling, hostname verification, SDK log suppression, sanitized errors | `webull_sandbox_credentials.py`, `webull_sandbox_readonly.py`, `webull_sandbox_evidence.py` | 16 credentials, 29 readonly, 13 runner, 12 diagnostics tests | pass (SIM) | Real-credential behavior is OWNER-observed only. | PASS (SIM) / OWNER for live run |
| Documented cash-activity parser (account match, decimals, categories, pagination, same-year windows, raw `biz_time`, reversal ambiguity) | `webull_sandbox_cash_activities.py` | 24 + 9 + 4 cases | pass (SIM) | Parser proven only on synthetic records built from documentation; the owner observed one strictly parsed `DEPOSIT/WIRE` record in the explicit October 7–10 UTC window, with the expected deposit amount matching. Multi-page behavior remains synthetic-only. | PARTIAL |
| Balance/deposit comparison (hidden prompts, MATCH/MISMATCH only) | F5 runner | 16 comparison cases | pass (SIM) | A match is an observation, not reconciliation. | PARTIAL |
| Account identity (API↔desktop) | F4 `EXACT_IDENTIFIER_MATCH_OBSERVED` | F4 tests | OWNER-observed exact identifier match for `****0AA8` | Not binding proof; binding flag stays UNVERIFIED. | PARTIAL |
| History completeness, flow signs, opening equity, positions completeness, reconciliation | — | — | OWNER: empty positions/default-window activities; explicit October 7–10 query returned one DEPOSIT/WIRE record matching the expected amount; NLV and cash comparisons MATCH | Nothing establishes any of these (see §5). | BLOCKED |

## 5. What is proven, observed and unverified

| Topic | SIM proof | OWNER observation | Broker guarantee |
|---|---|---|---|
| Account identity | selection/masking/matching logic | exact paper identifier matched API CASH `****0AA8`; list/balance/positions/activities HTTP 200 | **UNVERIFIED** (binding, ownership, persistence of mapping) |
| Balance/NLV | decimal compare logic | F5 hidden expected NLV and cash comparisons both MATCH; desktop showed $1,000,000 NLV/cash | **UNVERIFIED** as authoritative frozen opening; freshness unknown |
| Positions | shape and risk usage | empty array | **UNVERIFIED** completeness |
| Cash activities | documented-schema parser, pagination, repeated-cursor and bound checks | default window empty; explicit October 7–10 UTC query returned one strictly parsed DEPOSIT/WIRE record matching the privately entered expected amount; desktop showed the known October 7 deposit | **UNVERIFIED** history completeness, window semantics, categories/signs on real data, `biz_time` timezone, ordering, reversal linkage |
| Loss locks | full persistence/rollover/replay/retry on PostgreSQL | none | n/a (not broker-dependent, but inputs are) |
| Opening equity | frozen from synthetic ledger | none | **UNVERIFIED**; no snapshot service |

The owner observations in this table come from chat reports in earlier tasks;
the F5 owner results are recorded in the PR #8 description and Notion tracker; repository evidence documents do not yet contain the complete updated observation. They are not
independently reproduced in this audit (no real account access was made).

## 6. Findings (severity-ordered)

| ID | Severity | Finding |
|---|---|---|
| F7-1 | Medium | `Layer3Decision.execution_enabled` is a plain `bool`; `True` is constructible, and no test or static guard asserts that no `src/` path yields `True`. Disabled-ness rests on convention. |
| F7-2 | Medium | No ATS-008 text, ATS-009 policy document or Sprint 3 exit criteria in the repository. Requirement traceability is by inference. |
| F7-3 | Medium | Portfolio-heat 1R/caps use `snapshot.equity` (current) while loss locks use frozen opening equity. No document states which basis the heat policy requires; no test pins it. |
| F7-4 | Medium | Trust-bearing inputs (`positions_complete`, `orders_complete`, `reconciled`, `complete`, `schedule_verified`, `source_id`) are caller-asserted booleans/strings. Contracts state provenance is not authentication, but there is no production authenticator, so they are trustworthy only if a future adapter earns it. |
| F7-5 | Low | CI's final full-suite step does not fail on skips; only the `test_postgres_persistence.py` step does. F6's 21 PostgreSQL test functions (26 cases) are not covered by the zero-skip guard if `ATS_TEST_DATABASE_URL` is ever lost. |
| F7-6 | Low | `_build_sdk_trade_client` builds the full SDK trade client (order-capable object) while `.account_v2` and `.activity` are used. Narrowing must preserve both read-only capabilities. |
| F7-7 | Info | Python 3.12.10 locally vs 3.12 in CI; PostgreSQL 16 in both. |

No defect was found in the tested loss-lock, reservation, replay, retry or
rollback behavior within synthetic scope.

## 7. Minimum remaining work

### A. Offline simulation acceptance
1. Retrieve ATS-008, ATS-009 policy and Sprint 3 exit criteria from the existing Notion backlog/playbook, then commit a traceable requirements snapshot;
   rebuild this matrix against them (**blocker**, F7-2).
2. Make execution-disabled a type-level and test-level invariant (F7-1, F7-6).
3. Decide and pin the portfolio-heat equity basis (F7-3).
4. CI zero-skip guard for the full suite (F7-5).
5. A simulated opening-snapshot freezer (period-boundary capture, append-only,
   restatement rules) so frozen openings are produced by a component rather than
   test helpers.
6. Independent review of the acceptance matrix and authoritative criteria. Work already reproduced the unchanged F6 source head `53d2c39` with 648 passed/zero skipped; do not require a redundant run solely for a docs-only audit.

### B. Broker-backed read-only accounting acceptance
1. Webull documentation or support confirmation: account binding, `biz_time`
   timezone/format, history window and earliest availability, pagination
   terminal semantics, category/sign/reversal semantics, restatement.
2. Nonempty single-page cash-activity observation is complete for the known deposit; multi-page observation remains pending in
   an explicit window covering the owner's known deposit (F5 runner), including
   a data/identity-minimized comparison with the desktop.
3. A broker-sourced opening-equity capture at a real period boundary, and a
   reconciliation (`opening + flows + P&L = current NLV`) at aligned timestamps.
4. Positions/orders completeness evidence.
5. A production calendar-authority/emergency-closure adapter.
6. Authentication of evidence provenance (F7-4) so asserted flags become
   verified facts.

### C. Future paper-order enablement — NOT AUTHORIZED here
Requires A and B complete and independently reviewed, plus: broker order/fill
event verification and stop confirmation adapters, an explicit enablement
mechanism with its own review, kill switch, paper-only account binding proof,
and a reviewed runbook. Nothing in this document authorizes any of it.

## 8. Reproduction

```powershell
git fetch origin
git worktree add ..\ats-audit origin/feature/sprint-3-risk-engine
# disposable database named ats_test only; never a real database
docker run -d --rm --name ats-audit-pg -e POSTGRES_PASSWORD=<random> -e POSTGRES_DB=ats_test -p 127.0.0.1:55432:5432 postgres:16-alpine
$env:ATS_TEST_DATABASE_URL = "postgresql://postgres:<random>@127.0.0.1:55432/ats_test"
.\.venv\Scripts\python.exe -m pytest -q -rs        # expect 648 passed, 0 skipped
Remove-Item Env:ATS_TEST_DATABASE_URL
.\.venv\Scripts\python.exe -m pytest -q -rs        # expect 576 passed, 72 skipped
.\.venv\Scripts\python.exe -m ruff check . ; .\.venv\Scripts\python.exe -m ruff format --check . ; git diff --check
docker stop ats-audit-pg
```

## 9. Recommendation

Next bounded task, **ATS-009F8 — execution-disabled invariants and CI zero-skip
guard** (offline, no broker access): constrain `execution_enabled` so `True` is
unrepresentable, add a static test over `src/` rejecting order-capable SDK calls,
narrow exposed SDK capabilities while preserving read-only account and activity APIs, and fail CI on any skip
in the full suite. Pair it with retrieval of ATS-008/ATS-009/Sprint 3 requirements from the existing
Notion backlog and playbook; ask the owner only for gaps those sources cannot resolve. The following task should be the simulated opening-snapshot
freezer (A.5). Broker-backed acceptance (B) waits on the Webull confirmations.

## 10. Coding evidence vs independent review

- Coding/audit evidence: §3 (re-executed here by the same agent family that
  produced earlier work; **not independent**).
- Independent review of this audit: **not yet performed.**
- Sprint 3 acceptance: **not declared.** GO for further read-only/offline work;
  NO-GO for enforcement or any order enablement.

## 11. Work review corrections

Work checked this audit against the owner-observed F5 results and corrected stale
nonempty-record/balance statements, account-plus-activity capability requirements,
and the repository-only scope of the missing-requirements finding. The heat basis
is a policy question, not an established defect. Type constraints alone do not
prove execution-disabled invariants across unchecked Pydantic construction/copy
paths; F8 should examine those paths explicitly and avoid claiming universal
unrepresentability from a Literal annotation alone. Static scans are regression
guards, not a security sandbox. Independent policy acceptance remains pending.

The audit author's 648/0 and 576/72 runs above remain author-executed evidence.
Work did not rerun tests for this docs-only revision; its prior exact-head F6
648/0 reproduction remains separately recorded in PR #9 and the tracker.
