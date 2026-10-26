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

Source snapshots were retrieved by Work on 2026-10-22 America/Chicago.
Notion fetch exposes an as-of timestamp, not an immutable revision ID; no
revision identifier or historical approval authorship is inferred. The dated
approval section is distinguished from the page's last-change timestamp.

| ID | Source | Version / date | Access / role |
|---|---|---|---|
| S1 | Trading Strategy Playbook.pdf, 19 pages | Version 3, October 2026; SHA-256 `f9f632fc73b6eaa24f2304caa03c0fe7d29869e17c058da465f3fb8ee714cf26` | Read by author and Work; original strategy ranges. |
| S2 | `playbook/playbook_v3.yaml` | v3.0, commit `06e7fc4`, 2026-10-06 | Read; Layer 0/1 scoring only, no risk-basis parameters. |
| S3a | [ATS-007](https://app.notion.com/p/3f08afe63921814887dcd6d3013c7b89) | Fetch as of 2026-10-16T21:22:33.688Z; immutable revision ID unavailable | Retrieved; standalone sizing worked-example criteria. |
| S3b | [ATS-008](https://app.notion.com/p/3f08afe639218117a7d7f4ecd53f5612) | Fetch as of 2026-10-05T23:29:31.624Z; immutable revision ID unavailable | Retrieved; broad 4–5R / 2–2.5R heat ranges, resize/deny. |
| S3c | [ATS-009](https://app.notion.com/p/3f08afe6392181228e02ecd6539bff6f) | Fetch as of 2026-10-23T04:00:00.705Z (Oct 22 Chicago); immutable revision ID and historical approval date unavailable | Retrieved current Acceptance Criteria: −3R/−6R inclusive sticky locks, separate frozen authenticated openings, adjusted NLV, authoritative events and atomicity/retry. |
| S3d | [Sprint 3](https://app.notion.com/p/3f08afe6392181f78830e2540634e2d3) | Fetch as of 2026-10-22T15:39:12.254Z; section “Portfolio risk policy approved” dated **2026-10-16**; immutable revision ID unavailable | Retrieved approved specification: 0.5% reference R, 4R total, 2R correlated, 15% gross theme concentration; also sprint exit criteria. |
| S4 | Owner task statements, 2026-10-22 | Conversation evidence, not a versioned requirements document | Retained where more detailed than retrieved sources; not a substitute for source approval. |
| S5 | Repository policy/configuration | No standalone ATS-008/009/Sprint 3 requirements file before this report | Source register now supplies external traceability. Implementation constants are not requirements. |

S3a–S3d are collectively S3 below. Access and endpoint-selection blockers are
resolved. No universal approval-precedence rule was retrieved: this report uses
explicit recorded configuration within the broader strategy ranges, not a rule
that newer pages automatically override older ones. Source retrieval and these
edits were performed by Work; the coding agent did not independently read Notion.

## 2. Requirements snapshot

Legend — *Origin*: **ORIG** = S1 strategy text; **OWNER** = task detail retained where not explicitly covered by S3; **APPROVED CONFIGURATION** = dated S3d record; **RETRIEVED** = current Notion requirement. No original authorship is inferred from a current fetch.

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
| O-6 | Reference 1R = 0.5% of validated account equity; total heat ≤4R; correlated-theme/factor heat ≤2R; gross theme concentration ≤15% of account equity. | S3d approved specification, 2026-10-16 | APPROVED CONFIGURATION |
| O-7 | Enforcement, Layer 3 authorization and all paper/live order submission stay disabled. | S4 (every task) | OWNER |
| A-8 | Heat breaches are resized or denied; broad 4–5R / 2–2.5R ranges. | S3b; endpoints selected by S3d | RETRIEVED |
| A-9 | −3R/−6R inclusive sticky locks, authenticated separate frozen openings, trusted adjusted NLV/events, version binding, PostgreSQL atomicity/retry, fail-closed Layer 3. | S3c current Acceptance Criteria | RETRIEVED |
| A-S3 | Sizing, regime multiplier, portfolio/theme heat, loss locks, allow/resize/deny and risk tests pass. | S3d Exit Criteria | RETRIEVED; acceptance not declared |

**What S1 does not say** (verified by reading all 19 pages): which equity
(opening, current, NLV, cash) defines R or "account"; at what instant it is
measured; what "day"/"week" boundary applies; how deposits/withdrawals/fees affect
R; what "return fresh" resets (period vs manual review); any data-freshness
requirement; any broker/account binding requirement.

## 3. Implementation parameters vs. sources

| Parameter | S1 range | Code value | Relationship | Selecting authority |
|---|---|---|---|---|
| Daily loss limit | −3R | −3R | equal | S1 |
| Weekly loss limit | −6R to −8R | −6R (`-8R` and every other value rejected) | **inside the range at the strict end** | S3c current accepted requirement (approval chronology not exposed) |
| Total heat | 4–5R | 4R | strict end | S3d approval dated 2026-10-16 |
| Correlated heat | 2–2.5R | 2R | strict end | S3d approval dated 2026-10-16 |
| Theme concentration | ~15–20% | 15% | strict end | S3d approval dated 2026-10-16 |
| Sizing risk fraction | 0.25–0.5% (unproven stage) | accepts 0.25–0.5% only | equal to the stage-1 band | S1 P-1 |
| R unit | per-trade risk (varies by stage) | fixed 0.5% of equity | see C-5 | OWNER (O-1) |

## 4. Conflicts and discrepancies

No global approval-precedence rule is documented in the retrieved snapshots.
Explicit approved configuration selects endpoints within the original ranges;
no value is chosen from implementation alone.

| # | Finding | Status |
|---|---|---|
| C-1 | Weekly −6R is within S1’s −6R…−8R range and is explicitly selected in [ATS-009 Acceptance Criteria](https://app.notion.com/p/3f08afe6392181228e02ecd6539bff6f). Snapshot as of 2026-10-23T04:00:00.705Z; historical approval date unavailable. −8R is not a current ATS-009 alternative. | **APPROVED / RECORDED CURRENT REQUIREMENT; D-4 CLOSED.** |
| C-2 | Heat/theme endpoints 4R, 2R and 15%, with reference R=0.5% validated equity, are explicitly approved in [Sprint 3](https://app.notion.com/p/3f08afe6392181f78830e2540634e2d3), “Portfolio risk policy approved”, **2026-10-16**. They lie within S1’s ranges. | **APPROVED; D-4 CLOSED.** |
| C-3 | "Daily −3R" is identical in S1, S4 and code. | No conflict. |
| C-4 | S1 defines R as the risk taken on a trade, which changes with evidence stage (P-1); the owner policy fixes 1R at 0.5% of opening equity (O-1). Today sizing is capped at 0.5% so the fixed unit is the conservative upper bound (a 0.25% trade is 0.5R of heat). If a future stage allows 0.5–1% risk per trade, a single trade could exceed 1R under the fixed unit. | **FLAG for when risk-per-trade exceeds 0.5%**; not reachable in current code. |
| C-5 | Equity basis is unspecified in S1 and **differs by purpose in code** (§5). | **OPEN — narrowed decisions D-1…D-3; D-4 closed.** |
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

**Resolution status: partly resolved by S3/S4; precise metric and timestamps remain open.**
- Locks: frozen separate daily/weekly opening equity — **specified (O-1)**; keep.
- Sizing, heat, theme: S3d specifies **validated account equity**, reference 0.5% R and account-equity concentration. Exact metric/as-of and snapshot coupling remain undefined. Historical options for review
  (none applied here):
  - **A** — current authenticated NLV from one snapshot (one `snapshot_id`) for sizing, heat and theme (matches S1's "account" example and current code for heat).
  - **B** — conservative: `min(current NLV, daily opening equity)` for heat/theme limits, so gains and deposits cannot loosen caps intraday.
  - **C** — frozen daily opening equity for all risk purposes.
  Recommended for review: **A for sizing/theme, B for heat**, because heat is
  the control that must not loosen mid-session; this is a recommendation, not a
  decision.

**Decision status after Notion retrieval:**
- **D-1 — PARTLY RESOLVED:** reference R is 0.5% of validated account equity. Specify which metric (e.g. NLV) and as-of timestamp feeds sizing; no A/B/C choice approved here.
- **D-2 — PARTLY RESOLVED:** total/correlated caps are approved at 4R/2R against validated account equity. Specify metric, snapshot coupling, and intraday flow treatment. Current versus frozen/min basis is still undecided.
- **D-3 — PARTLY RESOLVED:** gross theme concentration is approved at 15% of account equity. Specify the metric and timestamp; no formula change approved here.
- **D-4 — CLOSED:** selected 0.5%/4R/2R/15% configuration recorded in S3d on 2026-10-16; −3R/−6R inclusive sticky requirements recorded in current S3c. Historical approval date for S3c is not exposed. No repeated endpoint approval requested.
- **D-5 — ACCESS RESOLVED:** Notion sources retrieved by Work; no owner export needed. A universal approval-precedence rule remains undocumented and is not invented.
- **D-6 — OPEN:** opening boundary/timing tolerance, capture-window duration and maximum valuation age require explicit policy values.
- **Opening corrections — OPEN:** define restatement approval/versioning and lock preservation before implementing opening corrections. Section 7’s design is proposed only.

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
| P-3 weekly −6R…−8R | S1 p.3, O-2 | −6R only | `test_weekly_limit_other_than_minus_six_is_rejected` (−5/−7/−8/−6.0001/−5.9999), `test_f6_weekly_minus_6r_inclusive_boundary_locks_weekly_only`, `test_f6_weekly_just_inside_limit_does_not_lock` (PG) | C-1 resolved by S3c; broker opening/capture still missing | PARTIAL |
| P-3 "return fresh" / O-3 sticky | S1, O-3 | sticky projection; reset only by valid period | `test_f6_lock_survives_nlv_recovery_and_service_recreation`, `test_f6_next_session_clears_daily_lock_but_weekly_lock_persists`, `test_f6_new_week_uses_separate_frozen_openings_and_clears_prior_locks`, `test_postgres_loss_state_threshold_and_sticky_lock_survive_recovery`, `test_postgres_daily_rollover_preserves_weekly_lock_and_week_requires_opening` | S1 does not define "fresh" (period vs review); reset semantics are owner-stated only | PARTIAL |
| O-1 frozen R unit | S4 | `PeriodOpeningEquity`, `evaluate_loss_accounting` | `test_daily_and_weekly_use_independent_frozen_equity_denominators`, `test_invalid_opening_equity_rejected` | **No component captures/freezes an opening at a boundary** (§7) | PARTIAL |
| O-4 flows | S4 | `ReconciledExternalFlow`, flow-adjusted P&L | `test_cash_flows_are_signed_and_period_scoped`, `test_f6_flows_excluded_from_pnl_and_fees_included` (PG) | Flow signs/categories on real broker data UNVERIFIED | PARTIAL |
| O-5 missing/stale evidence | S4 | `LossAccountingEvaluation` unavailable reasons; Layer 3 denies | `test_f6_stale_valuation_is_unavailable_and_creates_no_state`, `test_f6_stale_opening_period_is_unavailable`, `test_f6_missing_daily_opening_evidence_yields_no_lock_state`, `test_f6_layer3_denies_when_no_ledger_loss_state_exists`, `test_f6_incomplete_flow_reconciliation_is_unavailable` | Freshness limit is a caller parameter; no policy value | PARTIAL |
| P-4 total heat | S1 p.3, O-6 | `portfolio.assess_portfolio_risk` (`Fraction` math, 4R) | `test_total_heat_resizes_to_the_exact_largest_whole_share_quantity`, `test_external_pending_orders_consume_risk_and_theme_capacity` | Basis D-2; endpoint approval retrieved (C-2); snapshot flags caller-asserted | PARTIAL |
| P-4 correlated heat | S1 p.3/4 | factor-keyed heat, 2R | `test_correlated_heat_is_counted_across_different_named_themes`, `test_multiple_correlated_factors_each_apply_their_own_heat_limit` | Endpoint 2R approved by S3d; factor taxonomy is test data | PARTIAL |
| P-5 theme concentration | S1 p.4, O-6 | gross notional ≤ 15% × equity | `test_theme_concentration_limits_gross_notional_separately_from_heat`, `test_candidate_reaching_theme_cap_exactly_is_allowed`, `test_candidate_above_theme_cap_is_resized_to_boundary`, `test_over_cap_theme_denies_candidate_in_a_different_theme` | Basis D-3; 15% endpoint approved by S3d | PARTIAL |
| Stale/incomplete portfolio | O-5 | `PORTFOLIO_STATE_STALE/INCOMPLETE`, policy-not-configured denies | `test_stale_incomplete_and_unclassified_snapshots_are_denied` | `snapshot_max_age` has no default value | PARTIAL |
| Replay/version binding | O-3 | `_loss_binding_is_current` | `test_reservation_binds_loss_state_and_stale_replay_is_denied`, `test_postgres_layer3_binds_loss_state_version_and_denies_stale_replay` | — | PASS (SIM/PG) |
| Replay/conflict/rollback/retry | O-5 | `LossStateRepository`, bounded retries | `test_f6_duplicate_replay_creates_no_duplicate_state_or_evidence`, `test_f6_conflicting_identities_fail_closed_and_roll_back`, `test_f6_transient_failure_after_writes_retries_with_same_request_identity`, `test_f6_retry_exhaustion_fails_closed_without_partial_records` | — | PASS (SIM/PG) |
| O-7 disabled | S4 | `Literal[False]`, static scans, CI `--fail-on-skip` | `tests/test_layer3_execution_invariant.py`, `tests/test_execution_capability_guard.py`, `tests/test_ci_workflow_guards.py` | Python-level guard, not a security boundary (F8) | PASS (guard); not a trading approval |
| A-8 / A-9 / A-S3 | S3b/S3c/S3d | Existing components above | Named tests above; source mappings §10.2 | Source retrieval complete; metric/timing, broker authority and full acceptance still open | PARTIAL |

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
- Owner-stated rows are now reconciled to S3 where supported; detail beyond the retrieved sources remains explicitly owner-stated/proposed.
- All test evidence is SIM/PG from F7 (648 passed, 0 skipped on disposable PostgreSQL at `d18070a`) and F8 (702 passed, 0 skipped with database; 630 passed / 72 skipped without). This document re-ran no tests; it changes no code. Coding-agent results are not independent review.
- Broker guarantees remain UNVERIFIED: account binding, timestamps/timezone, ordering, history completeness, flow signs/categories, reversal linkage, opening equity, reconciliation.

## 9. Recommended next steps

1. Source retrieval and D-4 endpoint selection are complete. Resolve only precise equity metric/as-of/coupling, opening timing/freshness (D-6), and correction rules; source-linked options may be drafted without changing policy.
2. Then implement §7.4 (simulation opening-capture) as the next bounded task.
3. Only after D-1…D-3 are decided: add a single-snapshot equity-basis contract and tests; no thresholds change before approval.
4. Broker-backed capture remains blocked on the §7.3 items.

Sprint 3 acceptance is not declared and is not claimed by this document.


## 10. Work reconciliation against retrieved Notion sources

Retrieved 2026-10-22 America/Chicago. These are source snapshots, not changes to
Notion requirements or new owner approvals. Work has read the attached 19-page
PDF; SHA-256 matches S1 exactly (case-insensitive). PDF pages 3, 4 and 19 confirm
the broader loss/heat/concentration ranges. No policy code changed.

### 10.1 Retrieved source register

| Source | Link / retrieved source timestamp | Requirement record |
|---|---|---|
| N7 ATS-007 | [Sizing story](https://app.notion.com/p/3f08afe63921814887dcd6d3013c7b89), fetched source as of 2026-10-16T21:22:33.688Z | Risk dollars, stop distance, regime multiplier and shares reproduce known worked examples. Standalone sizing accepted; not an execution gate. |
| N8 ATS-008 | [Heat story](https://app.notion.com/p/3f08afe639218117a7d7f4ecd53f5612), source as of 2026-10-05T23:29:31.624Z | Breaches of 4–5R total / 2–2.5R correlated-theme risk are resized or denied. Story remains Backlog in retrieved properties; implementation evidence alone does not update acceptance. |
| N9 ATS-009 | [Loss-lock story](https://app.notion.com/p/3f08afe6392181228e02ecd6539bff6f), current fetch 2026-10-23 UTC | Current Acceptance Criteria explicitly require daily −3R / weekly −6R inclusive sticky locks, frozen authenticated separate openings, trusted flow-adjusted NLV, authoritative session/broker events, current version binding, PostgreSQL atomicity/retry and fail-closed Layer 3 tests. No enforcement before broker reconciliation and independent end-to-end review. Status In Progress. |
| NS3 Sprint 3 | [Risk Engine sprint](https://app.notion.com/p/3f08afe6392181f78830e2540634e2d3), source as of 2026-10-22T15:39:12.254Z | Exit Criteria: sizing, regime multiplier, portfolio/theme heat, loss locks, allow/resize/deny and risk tests pass. Goal: non-bypassable deterministic Layer 3 gate. Scheduled Nov 2–15, 2026; status Planned. |
| NS3 policy record | Same Sprint 3 page, section dated 2026-10-16, “Portfolio risk policy approved” | Approved configuration for specification: reference 1R = 0.5% of validated account equity; account heat ≤4R; correlated-theme/factor heat ≤2R; gross single-theme concentration ≤15% of account equity. Tighter stops reduce measured planned risk only after confirmation. Enforcement NO-GO pending authoritative evidence and atomic/lifecycle requirements. |

### 10.2 Reconciled requirements and traceability

| Requirement | Sources and relationship | Implementation/test pointers | Current gap |
|---|---|---|---|
| Reference R / heat / concentration endpoints | NS3 explicit approved configuration selects 0.5%, 4R, 2R and 15% within S1/N8 ranges. This resolves historical O-6/C-2/D-4 endpoint-approval uncertainty. | `PortfolioRiskPolicy`, `assess_portfolio_risk`; total/correlated heat and theme-boundary cases in §6 | Exact meaning/time of validated account equity remains unspecified. Do not switch current snapshot basis to min/opening equity on recommendation alone. |
| Daily/weekly locks | N9 explicit current criteria select −3R/−6R and stickiness within the PDF's weekly range. −8R is not an alternative supported by current ATS-009 criteria. | Existing limit-validation, inclusive boundary and F6 persistence/rollover cases in §6 | Historical authorship/approval chronology of N9 cannot be inferred from a current page fetch; no global precedence policy retrieved. No need to ask owner to resupply the current −6R criterion. |
| Separate frozen authenticated openings and adjusted NLV | N9 current acceptance criterion, not merely unretrieved chat | `PeriodOpeningEquity`, loss-accounting denominator/flow cases and F6 tests | Capture service/authentication/correction policy absent; broker-backed evidence remains incomplete. |
| ATS-007 standalone sizing | N7 worked-example acceptance, S1 risk hierarchy | `risk.py`, sizing/regime tests cited §6 | Source and timestamp/convention for sizing equity at integrated gate still need a precise requirement. Standalone arithmetic acceptance is not source authentication. |
| Sprint 3 exit | NS3 current properties, safety conditions in NS3/N9 | Existing suite covers synthetic sizing/multiplier/heat/locks/allow-resize-deny; F8 guards | Traceability is now available, but source-authority and integrated gate acceptance still incomplete. Do not mark Sprint 3 Complete. |

### 10.3 Remaining decisions only (consistent with the revised main tables)

- D-1/D-2/D-3 narrowed: NS3 already specifies validated account equity and
  account-equity concentration. The exact metric (NLV versus other equity),
  as-of convention, one-snapshot coupling for sizing/heat/theme, and any
  intraday deposit treatment still need explicit source text. Retain existing
  behavior while resolving these; A/B/C recommendations above are not approvals.
- D-4 endpoint approvals are retrieved as above; no repeated approval requested.
- D-5 access blocker is resolved in Work. No export is needed from the owner.
  A universal change-approval precedence rule has not been retrieved; do not
  invent one or infer it merely from page modification dates.
- D-6 remains open: capture-window duration and valuation-age policies, plus
  whether boundary as-of must be exact or tolerate a documented offset.
- Opening restatements/correction references in §7 are **proposals**, not newly
  approved policy. Existing unique opening identity rejects changed replay;
  versioned opening replacement needs a separately reviewed contract/migration.
- `SIMULATION-ONLY` is the existing provenance label. Simulation chronology is
  deterministic model time, not a broker's observation time; broker capture
  requires independently established timestamp semantics. Do not transplant
  synthetic backfill assumptions into broker-backed capture.
- Existing model provenance/boolean checks alone do not prove synthetic facts
  are rejected by every future broker consumer. Require an explicit trust-boundary
  test when such a consumer is implemented.

### 10.4 Revised next task and evidence limits

First record a precise, source-linked equity metric/as-of and opening timing
policy, using the retrieved sources and requesting only unresolved choices.
Then implement simulation-only opening capture under that policy. No code or
migration is authorized by this document, and no source ambiguity is resolved by
choosing the more conservative-looking formula without approval.

Work review at this revision is documentation/source reconciliation, not
independent approval of its newly authored text. No tests were rerun for this
docs-only change. Prior final F8 evidence is **704 passed/zero skipped** at
`5e4dc87`, including Work's deprecated-copy fix; the historical 702-case author
run in §8 describes the earlier F8 head. All safety/acceptance gates remain.
