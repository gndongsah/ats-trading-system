# ATS-009F9 — Requirements reconciliation (documentation only)

**Status: DOCUMENTATION ONLY. Sprint 3 is NOT accepted.** No risk-policy values,
code, tests, migrations or CI were changed. Trading, ATS-009 enforcement, Layer 3
trading authorization and all paper/live order submission remain disabled.

Base: `4ac9ea4` (`origin/feature/sprint-3-risk-engine`, includes F6 = PR #9,
the corrected F7 audit and F8 = merged guards). Worktree: separate; the owner's
main worktree and stashes were not touched.

Rule applied throughout: requirements come **only** from the sources in §1.
Implementation constants and tests are recorded as "what the code does", never as
evidence of what is required.

## 1. Source register and accessibility

| ID | Source | Version / date | Access | Role |
|---|---|---|---|---|
| S1 | *Trading Strategy Playbook.pdf* ("Version 3 … October 2026 — Revised playbook", 19 pp.) | PDF metadata created 2026-10-03; local file modified 2026-10-05; SHA-256 `F9F632FC…CF26` (full: `F9F632FC73B6EAA24F2304CAA03C0FE7D29869E17C058DA465F3FB8EE714CF26`) | **READ** (owner's local copy; text extracted with a temporary `pypdf`, nothing added to the repo) | Original strategy requirements |
| S2 | `playbook/playbook_v3.yaml` (`version: "3.0"`, commit `06e7fc4`, 2026-10-06) | versioned in repo | **READ** | Layer 0/1 scoring weights/thresholds only. **Contains no risk-limit, equity-basis or loss-lock parameter.** |
| S3 | Notion backlog: ATS-008, ATS-009, Sprint 3 exit criteria | unknown | **INACCESSIBLE** — this session has no Notion tool, and no Notion URL or export exists in the repo or git history | Would be the authority for ATS tickets, exit criteria and any approved amendments |
| S4 | Owner-stated policy in task prompts (ATS-009D/E/F6/F7/F8, 2026-10-22) | session text, not a retrievable document | READ (conversation only) | Treated as *owner-stated, unconfirmed against S3*. Not a versioned source; cannot establish precedence. |
| S5 | Repo policy/configuration documents | none exist | — | The repo has no ATS-008 spec, no ATS-009 policy, no Sprint 3 exit criteria, no equity-basis document (confirmed by `git grep`; consistent with F7 §1) |
| — | Repo `docs/ATS-009F1…F8` | 2026-10-22 | READ | Evidence/audit documents. **Not requirement sources.** |
| — | GitHub PRs / Notion comment history | — | PR #10 (F7) is merged; it is an audit, not a requirements source | — |

**Blockers from inaccessible sources:** S3 is the only place ATS-008/ATS-009
requirement text, sprint exit criteria, approval records and a documented
approval-precedence rule can live. Without it this document cannot (a) quote
ATS-008/009 acceptance criteria, (b) distinguish "approved amendment" from
"owner chat statement" with authority, or (c) apply a documented precedence
rule. Every affected row is marked **SOURCE-UNRETRIEVED**.

## 2. Requirements snapshot

Legend — *Origin*: **ORIG** = in S1 as written; **OWNER** = S4 only (possible later
amendment, approval record unretrieved); **NONE** = no accessible source.

| Req | Requirement (as stated in source) | Source | Origin |
|---|---|---|---|
| P-1 | Risk per trade by evidence stage: 0.25–0.5% (0–50 trades), 0.5–1% (50–100), approaches 1–2% only when mature; "When in doubt, you are in the first row." | S1 p.3, p.19 | ORIG |
| P-2 | Position size = risk dollars / distance to stop; "The stop sets the size." Example: $50,000 account, 0.5% ($250), $2 stop = 125 shares. | S1 p.3 | ORIG |
| P-3 | "Daily limit −3R; weekly limit −6R to −8R. Hit either and you stop, review, and return fresh." Day trading: stop at −3R; common-mistakes: "the −3R daily shutdown, no exceptions." | S1 p.3, p.10, p.18 | ORIG |
| P-4 | Max open portfolio risk 4–5R across all positions; max correlated-theme risk 2–2.5R per theme "regardless of how many tickers"; before each entry add the new trade's R to both totals; breach = no trade, or close an existing position first. | S1 p.3, p.19 | ORIG |
| P-5 | Keep any single theme "near ~15–20% of the account"; map positions to shared underlying factors; two themes sharing a factor are one exposure for heat. | S1 p.4 | ORIG |
| P-6 | Regime multiplier (1.0/0.75/0.5/0–0.25/cash) scales every position size. | S1 p.8, p.19 | ORIG |
| O-1 | 1R = 0.5% of **separately authenticated, frozen daily and weekly opening equity**. | S4 (ATS-009D/E/F6) | OWNER |
| O-2 | Daily −3R and weekly −6R are **inclusive**; weekly is **never −8R**. | S4 (ATS-009E) | OWNER |
| O-3 | Locks are sticky: remain after NLV recovery until a valid period reset; next-session rollover clears only daily; weekly persists to new week. | S4 (ATS-009E/F6) | OWNER |
| O-4 | P&L is flow-adjusted NLV: deposits/withdrawals are not trading P&L; fees are. | S4 (ATS-009D/F6) | OWNER |
| O-5 | Missing/stale opening, valuation or flow evidence denies new risk. | S4 (F6) | OWNER |
| O-6 | Heat caps are fixed "v0.1 policy": total 4R, correlated 2R, theme 15% of equity, 1R = 0.5%; alternate values rejected. | repo code comment "approved v0.1 policy" and F7 matrix; no retrievable approval | OWNER/NONE |
| O-7 | Enforcement, Layer 3 authorization and all paper/live order submission stay disabled. | S4 (every task) | OWNER |
| A-8 | ATS-008 requirements | — | **SOURCE-UNRETRIEVED (S3)** |
| A-9 | ATS-009 ticket acceptance criteria | — | **SOURCE-UNRETRIEVED (S3)** |
| A-S3 | Sprint 3 exit criteria (only Sprint 1 criterion exists, in README) | — | **SOURCE-UNRETRIEVED (S3)** |

**What S1 does not say** (verified by reading all 19 pages): which equity
(opening, current, NLV, cash) defines R or "account"; at what instant it is
measured; what "day"/"week" boundary applies; how deposits/withdrawals/fees affect
R; what "return fresh" resets (period vs manual review); any data-freshness
requirement; any broker/account binding requirement.

## 3. Implementation parameters vs. sources

| Parameter | S1 range | Code value | Relationship | Selecting authority |
|---|---|---|---|---|
| Daily loss limit | −3R | −3R | equal | S1 |
| Weekly loss limit | −6R to −8R | −6R (`-8R` and every other value rejected) | **inside the range at the strict end** | OWNER (O-2); approval record unretrieved |
| Total heat | 4–5R | 4R | strict end | OWNER/NONE (O-6) |
| Correlated heat | 2–2.5R | 2R | strict end | OWNER/NONE |
| Theme concentration | ~15–20% | 15% | strict end | OWNER/NONE |
| Sizing risk fraction | 0.25–0.5% (unproven stage) | accepts 0.25–0.5% only | equal to the stage-1 band | S1 P-1 |
| R unit | per-trade risk (varies by stage) | fixed 0.5% of equity | see C-5 | OWNER (O-1) |

## 4. Conflicts and discrepancies

No documented approval-precedence rule is retrievable (S3 inaccessible), so none
is applied and **no value is silently chosen**; each item states the finding and
what is needed.

| # | Finding | Status |
|---|---|---|
| C-1 | **Weekly limit**: S1 permits −6R…−8R; owner-stated and code = −6R. Per the owner's ATS-009E task text, Layer 3 previously carried −8R and was aligned to −6R. The −6R value is **not in conflict with S1** (it is an allowed endpoint); the conflict is only that S1 leaves the choice open and the approval that selects −6R is unretrieved. | **CONSISTENT, APPROVAL RECORD UNRETRIEVED.** Code remains −6R (conservative). Confirm against S3. |
| C-2 | Heat/theme caps (4R, 2R, 15%) are the strict ends of S1's ranges (4–5R, 2–2.5R, 15–20%). Not conflicts; endpoint selection has no accessible approval source. | **UNRETRIEVED APPROVAL**; code remains strict. |
| C-3 | "Daily −3R" is identical in S1, S4 and code. | No conflict. |
| C-4 | S1 defines R as the risk taken on a trade, which changes with evidence stage (P-1); the owner policy fixes 1R at 0.5% of opening equity (O-1). Today sizing is capped at 0.5% so the fixed unit is the conservative upper bound (a 0.25% trade is 0.5R of heat). If a future stage allows 0.5–1% risk per trade, a single trade could exceed 1R under the fixed unit. | **FLAG for when risk-per-trade exceeds 0.5%**; not reachable in current code. |
| C-5 | Equity basis is unspecified in S1 and **differs by purpose in code** (§5). | **OPEN — owner decisions D-1…D-4.** |
| C-6 | Heat check enforces "add the new trade's R to both totals" as dollar risk ÷ (current equity × 0.5%); S1 states it in R units of the trade. Equivalent only while sizing risk = 0.5%. | Informational. |

## 5. Equity-basis resolution

Different purposes may legitimately use different bases. Observed behavior
(source: code, shown to expose gaps, not as requirements):

| Purpose | S1 says | Code basis | Authenticated? | Freshness enforced? |
|---|---|---|---|---|
| Position-sizing 1R | "account", instant unspecified (P-2) | `size_long_equity_position(equity=…)` — **caller-supplied number** | **No** (any positive Decimal) | **No** (no timestamp parameter) |
| Total / correlated heat | "account" (P-4) | `PortfolioSnapshot.equity` × 0.5% × cap — current equity at `snapshot.as_of` | Caller-asserted flags | Yes if `snapshot_max_age` set; **policy is "not configured" (denies) until set** |
| Theme concentration | "~15–20% of the account" (P-5) | `PortfolioSnapshot.equity` × 15% vs gross notional | same as heat | same as heat |
| Daily/weekly loss locks | none; O-1 specifies frozen opening | R unit = `PeriodOpeningEquity.opening_equity` × 0.5% (separate daily and weekly records); P&L = valuation NLV − `opening_nlv` − signed flows | Provenance asserted by caller; no broker capture exists | Valuation age is a required parameter; opening must be calendar-valid |

Consequences of the divergence (arithmetic, not policy): intraday, after a loss the
current-equity basis shrinks the heat R-unit while the lock R-unit stays fixed;
after a deposit, heat and theme limits rise immediately while the lock basis does
not move until the next period.

**Resolution status: partly resolved by S4, otherwise FLAGGED.**
- Locks: frozen separate daily/weekly opening equity — **specified (O-1)**; keep.
- Sizing, heat, theme: **no accessible requirement.** Options for the owner
  (none applied here):
  - **A** — current authenticated NLV from one snapshot (one `snapshot_id`) for sizing, heat and theme (matches S1's "account" example and current code for heat).
  - **B** — conservative: `min(current NLV, daily opening equity)` for heat/theme limits, so gains and deposits cannot loosen caps intraday.
  - **C** — frozen daily opening equity for all risk purposes.
  Recommended for review: **A for sizing/theme, B for heat**, because heat is
  the control that must not loosen mid-session; this is a recommendation, not a
  decision.

**Owner decisions required (S3 could not be read):**
- **D-1** Which equity basis defines 1R for position sizing (A/B/C above)?
- **D-2** Basis for total/correlated heat caps?
- **D-3** Basis for theme concentration (the S1 text says "account")?
- **D-4** Confirm in the Notion backlog the selected endpoints −6R weekly, 4R, 2R and 15%, and whether weekly −8R is rejected by an approved amendment (O-2) or by owner preference.
- **D-5** Provide ATS-008/ATS-009/Sprint 3 text (Notion export or access) and the approval-precedence rule.
- **D-6** Approve the opening-capture window and maximum valuation age (§7.1); neither has a source value today.

## 6. Traceability matrix

Evidence class: **SIM** = synthetic/offline (and disposable PostgreSQL where marked
PG). No row below is broker-backed. "Tests" are existing named tests; none was
added here. Counts and runs are in F7/F8; this document ran nothing new (it is
documentation-only).

| Req | Source | Implementation | Named tests | Remaining gap | Status |
|---|---|---|---|---|---|
| P-2 sizing | S1 p.3 | `risk.py:size_long_equity_position` (whole shares, stop-derived) | `test_position_sizing_playbook_example_and_regime_scaling`, `test_planned_stop_risk_never_exceeds_budget`, `test_invalid_long_stop_direction_is_rejected` | Equity input unauthenticated/unfrozen; basis D-1 | PARTIAL |
| P-1 risk band | S1 p.3 | bounds 0.0025–0.005 in `size_long_equity_position` | `test_risk.py` sizing cases | Stage promotion (>0.5%) not implemented (by design); evidence-stage gating not traced | PARTIAL |
| P-6 regime multiplier | S1 p.8 | `risk.py` regime handling | `test_regime_four_explicit_multiplier`, `test_regime_four_requires_multiplier_and_rejects_invalid_multiplier`, `test_regime_five_always_denies_without_executable_quantity` | Regime classification source out of scope | PASS (SIM) |
| P-3 daily −3R | S1 p.3/10/18, O-2 | `ats009_accounting`, `layer3.LossLockState` (−3R enforced, others rejected) | `test_daily_limit_other_than_minus_three_is_rejected`, `test_f6_daily_minus_3r_inclusive_boundary_locks_daily_only` (PG) | Opening equity not broker-captured | PARTIAL |
| P-3 weekly −6R…−8R | S1 p.3, O-2 | −6R only | `test_weekly_limit_other_than_minus_six_is_rejected` (−5/−7/−8/−6.0001/−5.9999), `test_f6_weekly_minus_6r_inclusive_boundary_locks_weekly_only`, `test_f6_weekly_just_inside_limit_does_not_lock` (PG) | C-1: approval record unretrieved | PARTIAL |
| P-3 "return fresh" / O-3 sticky | S1, O-3 | sticky projection; reset only by valid period | `test_f6_lock_survives_nlv_recovery_and_service_recreation`, `test_f6_next_session_clears_daily_lock_but_weekly_lock_persists`, `test_f6_new_week_uses_separate_frozen_openings_and_clears_prior_locks`, `test_postgres_loss_state_threshold_and_sticky_lock_survive_recovery`, `test_postgres_daily_rollover_preserves_weekly_lock_and_week_requires_opening` | S1 does not define "fresh" (period vs review); reset semantics are owner-stated only | PARTIAL |
| O-1 frozen R unit | S4 | `PeriodOpeningEquity`, `evaluate_loss_accounting` | `test_daily_and_weekly_use_independent_frozen_equity_denominators`, `test_invalid_opening_equity_rejected` | **No component captures/freezes an opening at a boundary** (§7) | PARTIAL |
| O-4 flows | S4 | `ReconciledExternalFlow`, flow-adjusted P&L | `test_cash_flows_are_signed_and_period_scoped`, `test_f6_flows_excluded_from_pnl_and_fees_included` (PG) | Flow signs/categories on real broker data UNVERIFIED | PARTIAL |
| O-5 missing/stale evidence | S4 | `LossAccountingEvaluation` unavailable reasons; Layer 3 denies | `test_f6_stale_valuation_is_unavailable_and_creates_no_state`, `test_f6_stale_opening_period_is_unavailable`, `test_f6_missing_daily_opening_evidence_yields_no_lock_state`, `test_f6_layer3_denies_when_no_ledger_loss_state_exists`, `test_f6_incomplete_flow_reconciliation_is_unavailable` | Freshness limit is a caller parameter; no policy value | PARTIAL |
| P-4 total heat | S1 p.3, O-6 | `portfolio.assess_portfolio_risk` (`Fraction` math, 4R) | `test_total_heat_resizes_to_the_exact_largest_whole_share_quantity`, `test_external_pending_orders_consume_risk_and_theme_capacity` | Basis D-2; 4R vs 4–5R endpoint (C-2); snapshot flags caller-asserted | PARTIAL |
| P-4 correlated heat | S1 p.3/4 | factor-keyed heat, 2R | `test_correlated_heat_is_counted_across_different_named_themes`, `test_multiple_correlated_factors_each_apply_their_own_heat_limit` | Endpoint 2R vs 2–2.5R; factor taxonomy is test data | PARTIAL |
| P-5 theme concentration | S1 p.4, O-6 | gross notional ≤ 15% × equity | `test_theme_concentration_limits_gross_notional_separately_from_heat`, `test_candidate_reaching_theme_cap_exactly_is_allowed`, `test_candidate_above_theme_cap_is_resized_to_boundary`, `test_over_cap_theme_denies_candidate_in_a_different_theme` | Basis D-3; 15% vs 15–20% | PARTIAL |
| Stale/incomplete portfolio | O-5 | `PORTFOLIO_STATE_STALE/INCOMPLETE`, policy-not-configured denies | `test_stale_incomplete_and_unclassified_snapshots_are_denied` | `snapshot_max_age` has no default value | PARTIAL |
| Replay/version binding | O-3 | `_loss_binding_is_current` | `test_reservation_binds_loss_state_and_stale_replay_is_denied`, `test_postgres_layer3_binds_loss_state_version_and_denies_stale_replay` | — | PASS (SIM/PG) |
| Replay/conflict/rollback/retry | O-5 | `LossStateRepository`, bounded retries | `test_f6_duplicate_replay_creates_no_duplicate_state_or_evidence`, `test_f6_conflicting_identities_fail_closed_and_roll_back`, `test_f6_transient_failure_after_writes_retries_with_same_request_identity`, `test_f6_retry_exhaustion_fails_closed_without_partial_records` | — | PASS (SIM/PG) |
| O-7 disabled | S4 | `Literal[False]`, static scans, CI `--fail-on-skip` | `tests/test_layer3_execution_invariant.py`, `tests/test_execution_capability_guard.py`, `tests/test_ci_workflow_guards.py` | Python-level guard, not a security boundary (F8) | PASS (guard); not a trading approval |
| A-8 / A-9 / A-S3 | S3 | — | — | **SOURCE-UNRETRIEVED** | BLOCKED |

## 7. Minimum opening-equity capture contract

Status: **specification for a future task.** Today `append_opening` stores a
caller-built `PeriodOpeningEquity`; **nothing captures an opening at a boundary**
(F7-4). Fields below map to existing model fields where they exist. Items marked
*(exists)* are implemented in the repository; everything else is **proposed** and
unimplemented.

### 7.1 Common requirements (simulation and broker-backed)

| Aspect | Requirement |
|---|---|
| Period boundary | Daily facts are keyed by XNYS `session_id`, weekly by `week_id`, both resolved by `XNYSCalendar` with `calendar_version` (`period_id`, `calendar_version`) *(exists, validated on append)*. `opened_at` must fall in the period's resolved session. |
| Separate facts | One daily and one weekly record per account and period; never derived from each other at evaluation time. Weekly `opened_at` ≤ daily `opened_at` *(exists, `PERIOD_OPENING_ORDER_INVALID`)*. |
| Source timestamp | `opened_at` is the **source's** as-of time, UTC-aware. Local capture time is stored separately as `observed_at` and must never substitute for it *(observed_at: proposed)*. |
| Freshness | A capture is valid only if `capture_observed_at − opened_at ≤ capture_window` and the valuation used against it is within `max_valuation_age` *(valuation age exists, caller parameter)*. **The capture-window and max-age values are undefined; owner decision D-6.** Until set, evaluation is unavailable (deny). |
| Account / currency | Internal account UUID plus the broker binding evidence ref; 3-letter base currency; mismatch with valuation or flows → unavailable *(exists: `ACCOUNT_MISMATCH`, `CURRENCY_MISMATCH`)*. Multi-currency conversion is out of scope: reject. |
| Values | `opening_equity` (R basis; finite, > 0) and `opening_nlv` (P&L baseline; finite) as exact `Decimal`/`Fraction`; the model keeps them as two fields *(exists)*. Their relationship (equal vs. different) must be stated by the source; not assumed. |
| Completeness | `complete` and `reconciled` must both be true to store *(exists)*. |
| Immutability | Identity `(account, period_kind, period_id)` unique; `immutable_version ≥ 1`; canonical digest stored *(exists)*; append-only database triggers were reported for the loss-state tables in ATS-009E; this document did not re-inspect them *(reported, not re-verified here)*. |
| Replay | Same identity + same digest → returns the existing row, no new row; same identity + different digest → `LossStateConflict`, fail closed *(exists; `test_f6_conflicting_identities_fail_closed_and_roll_back`)*. |
| Corrections | An opening is **never overwritten**. A restated opening is a new record that references the original (`corrects_ref`, new `immutable_version`) with its own provenance; it can change the denominator of future evaluations only after approval, and **can never clear a lock already set** (corrections append evidence without unlocking *(exists for state events: `test_postgres_correction_appends_evidence_without_unlocking`; opening corrections: ABSENT)*. |
| Missed boundary | If no capture exists in the window, the period is **unavailable**: no retroactive inference from later balances. New risk is denied *(exists: `test_f6_missing_daily_opening_evidence_yields_no_lock_state`, `test_f6_layer3_denies_when_no_ledger_loss_state_exists`)*. |

### 7.2 Simulation-only requirements

- Openings derived from the synthetic ledger are labelled `SIMULATION` provenance and must never be accepted by a broker-backed path *(exists: `test_f6_all_persisted_evidence_is_simulation_only`)*.
- The capture routine is a pure function of ledger state at the calendar boundary and must be reproducible (same ledger → same digest).
- Source identity fields (`source_id`, `source_epoch`, `source_sequence`) are assertions of the harness, not authentication.

### 7.3 Broker-backed additional requirements (all currently UNVERIFIED)

| Requirement | Why it is not met today |
|---|---|
| Authenticated read-only source (keyring credentials, sandbox/paper host) | exists for sandbox reads (F2–F5); not tied to a capture |
| Verified API↔desktop account binding persisted with the capture | exact identifier match **observed by owner** (F4); binding flag remains UNVERIFIED |
| Broker as-of timestamp for the balance, with documented timezone/semantics | the balance response's timestamp semantics are undocumented/unverified; `biz_time` format and timezone unverified (F3) |
| Capture performed **at** the boundary | balance endpoint reports current values; historical NLV availability not demonstrated; therefore capture must be live inside the capture window |
| Reconciliation: opening NLV + signed flows + P&L = current NLV at aligned timestamps | NOT PERFORMED; F5 only compared owner-entered expected NLV/cash (MATCH, owner-observed) |
| Flow history complete from boundary to evaluation | empty or single-record results prove nothing about completeness (F2/F7); watermark/completeness semantics unverified |
| External-flow signs and categories from real records | one owner-observed DEPOSIT/WIRE record; no sign/category semantics verified; reversal linkage unverified |
| Restatement rule from broker | unknown |

### 7.4 Proposed acceptance criteria for the capture task (next bounded implementation)

1. Simulation: a boundary-capture function produces daily and weekly `PeriodOpeningEquity` with `SIMULATION` provenance, `observed_at` separate from `opened_at`, identical digests on replay, `LossStateConflict` on conflict.
2. Missing/late capture ⇒ unavailable ⇒ denial with no state rows (PG, zero skips).
3. Opening correction appends a referencing record, never mutates, never unlocks (PG).
4. Capture-window and max-age values are read from approved configuration (D-6), not defaulted in code.
5. No broker call, credential access, migration of existing tables beyond what the owner approves, or order path.

## 8. Evidence classes and limits of this document

- Playbook requirements are read from an extracted copy of the owner's PDF; the PDF is not committed. The SHA-256 above identifies the version read.
- Owner-stated rows (S4) are chat statements; they are not independently verifiable and have not been reconciled to Notion.
- All test evidence is SIM/PG from F7 (648 passed, 0 skipped on disposable PostgreSQL at `d18070a`) and F8 (702 passed, 0 skipped with database; 630 passed / 72 skipped without). This document re-ran no tests; it changes no code. Coding-agent results are not independent review.
- Broker guarantees remain UNVERIFIED: account binding, timestamps/timezone, ordering, history completeness, flow signs/categories, reversal linkage, opening equity, reconciliation.

## 9. Recommended next steps

1. Owner provides S3 access/export (D-5) and answers D-1…D-6; update this snapshot with source version/date and mark each amendment's approval.
2. Then implement §7.4 (simulation opening-capture) as the next bounded task.
3. Only after D-1…D-3 are decided: add a single-snapshot equity-basis contract and tests; no thresholds change before approval.
4. Broker-backed capture remains blocked on the §7.3 items.

Sprint 3 acceptance is not declared and is not claimed by this document.
