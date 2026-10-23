# ATS-009F10 — equity and opening policy proposal

**APPROVED POLICY FOR FUTURE IMPLEMENTATION — NOT TRADING ENABLEMENT.**
Documentation only; no code, configuration, migration, enforcement flag or order
path changed. Approval does not establish broker-backed readiness.

## Owner approval record — 2026-10-22 America/Chicago

The owner explicitly replied “I approve” after the F10 summary linked PR #13
at `a6cfa0360aa1e5bed3f6101b66ea93d0d9fc034d` and listed the three recommended
choices. This approves **option A for each of Decisions 1, 2 and 3**, as specified
in that revision. Source: owner statement in the project conversation; no
independent reviewer approval or broker attestation is implied.

| Decision | Selected policy |
|---|---|
| 1 — equity | One validated current USD NLV snapshot for sizing, total/correlated heat and theme concentration; final reconciled flow treatment; separately frozen daily/weekly loss denominators. |
| 2 — timing | Exact resolved XNYS opening source cutoff; observation lag ≤10s, completed capture ≤60s, current-source age ≤30s; reject future timestamps and missing/late evidence. Distinct daily/weekly facts and calendar authority requirements remain. |
| 3 — corrections | Append correction evidence; preserve active opening denominators and existing sticky locks; deny affected-period new risk; no in-period denominator replacement in v0.1. |

Alternative B/C options below were **not selected**. Proposal/recommendation
wording in the design comparison is retained to explain the alternatives;
this approval record determines the selected status. Timing values are owner
policy choices, not Webull guarantees. The remaining work is implementation
and independent verification under these constraints, not a repeat approval
of the same choices. Schema/migration changes require separate design review.
All broker trading, enforcement and paper/live submission remain disabled.

Prepared 2026-10-22 America/Chicago. Base: `4ac9ea449446ba3ea3c17ae6e4530c3b234f6acb`.
F9 dependency: [draft PR #12](https://github.com/gndongsah/ats-trading-system/pull/12),
head `3eb27077eeac30070c2eaaf75480bc302b629eaf`; read from its branch. F10 is based
on Sprint 3 directly and does not duplicate the still-open F9 document.

## Established requirements and decision authority

| Source | Established requirement | Source/date limits |
|---|---|---|
| [Sprint 3](https://app.notion.com/p/3f08afe6392181f78830e2540634e2d3), “Portfolio risk policy approved” | Reference R = 0.5% validated account equity; total heat ≤4R; correlated-theme/factor heat ≤2R; gross theme concentration ≤15%; tighter stop counts only after confirmation. | Section dated 2026-10-16; fetched as of 2026-10-22T15:39:12.254Z. Notion immutable revision ID unavailable. |
| [ATS-009](https://app.notion.com/p/3f08afe6392181228e02ecd6539bff6f), Acceptance Criteria | −3R daily/−6R weekly inclusive sticky locks, separately frozen authenticated daily/weekly opening equity, trusted flow-adjusted NLV, authoritative events, current version binding, PostgreSQL atomicity/retry; no enforcement before evidence/reconciliation and independent review. | F9 retrieval as of 2026-10-23T04:00:00.705Z (Oct 22 Chicago); original approval date not exposed. |
| [ATS-008](https://app.notion.com/p/3f08afe639218117a7d7f4ecd53f5612) | Risk breaches resized/denied; broader heat ranges refined by the explicit Sprint 3 configuration. | Fetch as of 2026-10-05T23:29:31.624Z; immutable revision ID unavailable. |
| Playbook v3, October 2026, pages 3–4/19 | Risk hierarchy, sizing from stop distance, original heat/concentration/loss ranges. | SHA-256 `f9f632fc73b6eaa24f2304caa03c0fe7d29869e17c058da465f3fb8ee714cf26`, verified in F9. No precise equity timestamp or capture tolerance specified. |
| Repository `src/ats009_calendar.py` / loss accounting | Existing XNYS calendar and separate opening facts inform compatibility. | Implementation evidence, not approval of the following choices. |

The three decisions were approved as option A in the record above. Existing
risk limits do not require reapproval. Timing numbers below are selected owner
policy, not a Webull service guarantee. No universal precedence
rule is inferred from edit dates.

## Decision 1 proposal — one current NLV snapshot for new-risk capacity

Recommend **A: validated current USD NLV** for sizing, total/correlated heat,
and gross theme concentration at each new-risk evaluation. Cash/buying power
are separate affordability constraints; neither substitutes for equity. NLV
includes cash and marked positions, with fees already reflected once.

Use one immutable snapshot identity/version, account/currency, positions,
pending orders, existing reservations and confirmed stops. Every input must
be aligned to one documented valuation cutoff; concurrent reads alone do not
prove an atomic snapshot. If a broker cannot supply this, its integration stays
ineligible. A timestamp-less balance or operator-entered match is insufficient.

- Proposed current-source age at evaluation: **0–30 seconds inclusive**.
- Proposed receipt lag: **0–10 seconds inclusive** from source as-of to local
  observation. Reject future source times; no silent clock-skew allowance.
- The same frozen snapshot is used through sizing and capacity assessment.
  Under the database account lock, stale snapshot/state versions require a
  fresh evaluation, not mixing an old sizing basis with a new heat basis.
- USD only initially. Nonpositive/nonfinite NLV, missing freshness policy,
  identity mismatch or incomplete/unreconciled exposures deny new risk.
- Incoming funds may enlarge capacity only after source-defined settled/final
  NLV recognition and complete reconciled flow evidence. Provisional or
  ambiguous credits do not earn capacity; until recognition is verified, deny
  new risk rather than inventing a discounted equity number. Outgoing funds
  must reduce measured capacity when reflected by authoritative source rules;
  unknown pending-withdrawal recognition also denies new risk.
- Deposits/withdrawals never change the already frozen daily/weekly loss R
  denominators. Fee/market P&L affects current NLV and adjusted P&L exactly once.
- Reference R remains 0.5% of the chosen equity. A setup's permitted risk
  fraction/regime multiplier can be smaller: a 0.25% trade is 0.5 reference R,
  not a redefinition of the portfolio reference unit.

### Alternatives and numerical comparison

B: use `min(current NLV, frozen daily opening equity)` for sizing/heat/theme.
This prevents same-day gains/deposits from enlarging capacity but adds a new
cross-purpose coupling and depends on a valid daily opening. It is a proposed
policy change, not implied by the approved word “validated”.

C: use frozen daily opening equity for sizing/heat/theme. Stable limits, but
losses/withdrawals do not reduce those dollar limits intraday; a separate
current-NLV solvency/capacity check would still be needed.

Examples assume USD 100,000 daily AND weekly openings, no earlier weekly P&L,
and an independently validated/reconciled current observation. These are
capacity limits before existing exposures, buying-power limits or regime
multipliers. Contributions/withdrawals are explicit external flows.

| Scenario | Current NLV | Adjusted period P&L | A: R / 4R total / 2R correlated / 15% theme | B: same limits | C: same limits |
|---|---:|---:|---|---|---|
| Unchanged | 100,000 | 0 | 500 / 2,000 / 1,000 / 15,000 | same | same |
| Market gain +2,000 | 102,000 | +2,000 | 510 / 2,040 / 1,020 / 15,300 | 500 / 2,000 / 1,000 / 15,000 | same as B |
| Market loss −2,000 | 98,000 | −2,000 | 490 / 1,960 / 980 / 14,700 | same as A | 500 / 2,000 / 1,000 / 15,000 |
| Final reconciled deposit +20,000 | 120,000 | 0 | 600 / 2,400 / 1,200 / 18,000 | 500 / 2,000 / 1,000 / 15,000 | same as B |
| Final reconciled withdrawal −20,000 | 80,000 | 0 | 400 / 1,600 / 800 / 12,000 | same as A | 500 / 2,000 / 1,000 / 15,000 |

A USD 2 stop with reference risk and multiplier 1 yields 250 shares initially,
255 after the gain, 245 after the loss, 300 after the final deposit, and 200
after the withdrawal under A, before all other gates. For loss locks, both
opening R units stay USD 500 in these examples: −2,000 P&L is −4 daily R and
−4 weekly R, so the daily lock denies new risk regardless of available heat.
No alternative may bypass a sticky lock or treat a deposit as P&L.

Why recommend A: it makes one validated current observation govern current
capacity, matches existing snapshot-based heat behavior and avoids silently
introducing an opening-equity cap. This is an engineering consistency choice,
not a claim that it improves returns or is uniquely safest.

## Decision 2 proposal — exact opening cutoff, bounded acquisition

Recommend **A: exact source boundary, 60-second capture deadline**, with the
30-second current-valuation age and 10-second receipt-lag limits above. These
numbers form a proposed v0.1 policy bundle and must be explicit/versioned,
not optional defaults whose absence allows risk.

| Fact | Proposed definition |
|---|---|
| Daily boundary T | Regular-session opening from pinned XNYS schedule for the resolved local session date; never midnight UTC or local computer midnight. |
| Weekly boundary T | Opening of the first scheduled XNYS session in the local Monday–Sunday week, using the existing first-session-based week_id. Monday holiday moves it to the first actual session; no session means no opening. |
| Timezones/DST | Resolve America/New_York through calendar/timezone data then serialize UTC-aware instants. Do not hardcode one UTC hour. Early closes change session close, not the definition of its opening. |
| Authority | Verify emergency closure/delay/revision separately. A pinned package alone is not emergency authority. Uncertain schedule => unavailable. If authoritative timing contradicts resolved timing, deny and revise the calendar contract rather than inventing T. |
| Source cutoff | Source valuation as-of must equal T exactly, in the source's documented timestamp precision and ordering model. Source must define which same-T flows/events are included. Coarse/ambiguous timestamps without ordering evidence are ineligible. |
| Acquisition/receipt | Record source_as_of=T and capture_observed_at separately. Observation must be in [T,T+10s]; completed capture/persistence deadline is [T,T+60s], inclusive. Monotonic deadline handling must prevent clock rollback from enlarging the window. |
| Capture vs freshness | The 60-second window is the completion deadline; the 10-second lag limits observation of the source fact. Neither permits a later balance to masquerade as T. The current valuation age ≤30s applies at risk evaluation, not to the historical opening, which remains usable throughout its valid period. |
| Separate records | Daily and weekly facts have distinct identities even when both open at the same instant/value. Daily rollover never overwrites the weekly fact. |
| Values | For this USD NLV policy, opening_equity=opening_nlv from the same source cutoff, account and snapshot. Separate model fields remain explicit; other semantics require a future amendment. |
| Event cutoff | Opening includes all ordered facts through its source cutoff; adjusted P&L uses only flows strictly after that cutoff through evaluation. An as-of timestamp alone cannot resolve same-time event ordering; reject ambiguity. |
| Replay | Same account/period/policy/source identity and canonical facts => same digest/existing record; conflict => unavailable plus conflict evidence. Database writes/capture metadata must be atomic. |
| Missed/late opening | No accepted opening => no new risk for that period; no fallback to first later observation, current balance, manual number, or previous period. Existing protective actions are separate future scope. |

**Alternatives:** B, exact boundary but 5-second observation/15-second completion
and 5-second current-age limits: less tolerance, more unavailable sessions.
C, documented approximate cutoff within 60 seconds after opening: more likely
to work with polling, but changes the P&L baseline and needs explicit handling
of intervening P&L/flows; not recommended as an implicit fallback.

Calendar examples checked against installed `exchange-calendars==4.13.1` using
the repository resolver: 2026-10-30 opens at 13:30 UTC; 2026-11-02 at 14:30 UTC
across the DST change. 2026-09-07 resolves to no session, and the following
2026-09-08 session has week_id `XNYS:week:2026-09-08` and opens at 13:30 UTC.
These are package schedule checks, not emergency-closure authority; all resolved
examples remained schedule_verified=False without an authority adapter.

### Simulation versus broker capture

Simulation has model time, a sealed ledger watermark and explicit event order.
A deterministic boundary-capture function can evaluate exact T, but an automated
run that misses its configured deadline must deny new simulated risk. Historical
replay/backtest may reconstruct T only in an explicitly labeled replay mode;
it cannot produce a valid live-style capture or conceal a missed deadline.
All evidence is SIMULATION-ONLY, never broker-authenticated. Include policy,
calendar revision, watermark and event-order rules in the canonical digest.

Broker capture additionally needs authenticated account binding, documented
balance as-of/precision/order, coherent complete exposures, flow recognition
and aligned reconciliation. Existing Webull observations do not establish
those guarantees. Exact-boundary capture may be unsupported; that means this
policy remains blocked for Webull, not that the timestamp may be guessed.
No broker implementation or support ticket is authorized by this proposal.

## Decision 3 proposal — corrections restrict immediately, relaxation deferred

Recommend **A: append-only restatement evidence; no denominator replacement
within an already active period in the first implementation**.

1. A correction records original identity/digest, candidate facts, source
   revision, reason, discovered_at, effective cutoff, policy revision and
   review status. Never mutate/delete the original or reuse its identity with
   changed content. Duplicate identical correction evidence is idempotent.
2. A newly discovered discrepancy immediately marks the affected scope
   unavailable for new risk pending review; an already-set lock stays set.
   A correction's lower loss number cannot reset daily/weekly locks.
3. Review must establish source authority, account/currency, exact original
   cutoff, complete impacted flow/valuation history, before/after P&L and
   denominator effects, affected reservations/replays and any new breaches.
   Owner approval plus independent technical evidence review is proposed for
   activation. Exact reviewer identity/audit evidence must be retained.
4. In v0.1, an approved restatement is attached evidence; retain affected-period
   denial through its end instead of swapping the active denominator. A new
   period still requires its own valid opening and cannot inherit unavailable
   facts just because its predecessor ended. Weekly denial/lock persists through
   its week even if a new daily period starts.
5. Preserve reservation history; new evidence invalidates stale version-bound
   replay as required. Never rewrite past decisions, dispatch a replay, or
   reverse prior trades as a correction side effect.

B: allow independently reviewed new opening versions within a period, with
full recomputation and monotonic locks. More available but requires an explicit
activation/version-selection protocol, source retention, concurrency tests and
possibly schema changes. Not recommended for the initial capture task.

C: keep only the original opening and reject correction records. Simpler but
loses important audit evidence; not recommended.

The current unique-opening constraint rejects changed replay; it does not
implement correction records, review activation or period-denial evidence.
Those are proposed requirements for later design. Approving A does not itself
approve a migration. New correction evidence must not accidentally make the
provider use an unreviewed “latest” record.

## Review and subsequent implementation plan

Documentation checks: exact example arithmetic and calendar examples checked
locally; no production tests rerun for this docs-only proposal. Existing tests
are evidence of implemented behavior, not approval of this proposal.

After explicit decisions: version the selected policy, design a simulation-only
capture service with caller-supplied/model clock and sealed cutoff, then add
PostgreSQL replay/conflict/deadline/rollover/denial tests. Resolve any schema
changes separately. Keep broker capture blocked until its evidence contracts
are met. Final policy text and implementation need independent review.

## Three owner decisions — option A approved in each case

1. **Equity basis:** recommend **A**, one validated current USD NLV snapshot for
   sizing/heat/theme, final reconciled flow treatment, separate frozen lock
   bases. Alternative **B** caps at min(current,daily opening); **C** freezes
   all capacity at daily opening. Tradeoff: A follows current capacity, B caps
   upside capacity, C is stable but needs an additional current-capacity check.
2. **Opening timing/freshness:** recommend **A**, exact XNYS opening cutoff,
   ≤10s observation lag, ≤60s completion, ≤30s current-source age, missed capture
   denies risk. **B** tighter 5s/15s/5s limits; **C** approximate post-open cutoff
   with a separately defined baseline. Tradeoff: stricter availability versus
   baseline fidelity; none makes undocumented Webull timestamps trustworthy.
3. **Corrections:** recommend **A**, append correction evidence, preserve the
   original denominator, deny affected-period new risk and preserve sticky
   locks. **B** reviewed in-period version activation/recomputation; **C** no
   correction evidence. Tradeoff: A is restrictive and simpler to audit; B needs
   a more complex reviewed state/version protocol; C loses audit context.

No approval is inferred from opening or merging this documentation PR. Policy
approval comes from the explicit owner response recorded above.
All broker trading, enforcement and paper/live order submission stay disabled.
