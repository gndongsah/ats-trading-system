# ATS-009F13 — Sprint 3 acceptance reconciliation

Date: 2026-10-23 (America/Chicago). Documentation-only assessment of merged
Sprint 3 `801812a1b4dcd7cff3f242ae6b911b8c08d0c2f9` (tree
`f371283a2f983322645cf5cb9806050c0c31aaab`). **Sprint acceptance: NOT READY.**
No policy, code, tests, status, schedule or execution flag changes are authorized
by this report. F2 and Sprint 3 acceptance remain unchecked; orders stay disabled.

## Sources and evidence boundaries

| Source | Retrieved requirement / evidence | Identity |
|---|---|---|
| [ATS-007](https://app.notion.com/p/3f08afe63921814887dcd6d3013c7b89) | Worked risk-dollar, stop-distance, regime and share-count examples; Done for standalone sizing only | Fetch as-of 2026-10-16T21:22:33.688Z |
| [ATS-008](https://app.notion.com/p/3f08afe639218117a7d7f4ecd53f5612) | Resize/deny beyond total 4–5R and correlated 2–2.5R; status Backlog, body blank | Fetch as-of 2026-10-05T23:29:31.624Z |
| [ATS-009](https://app.notion.com/p/3f08afe6392181228e02ecd6539bff6f) | Inclusive sticky -3R/-6R, separate authenticated frozen openings, trusted flow-adjusted NLV, authoritative events, version binding and PostgreSQL atomicity/retry; independent end-to-end review before enforcement | Fetch as-of 2026-10-23T15:09:31.162Z; In Progress |
| [Sprint 3](https://app.notion.com/p/3f08afe6392181f78830e2540634e2d3) | Non-bypassable deterministic gate; sizing, multiplier, heat, loss locks, allow/resize/deny and tests. Oct 16 approval: 0.5% reference R, 4R total, 2R correlated, 15% theme; confirmed stops only | Fetch as-of 2026-10-22T15:39:12.254Z; Planned Nov 2–15 |
| F10 owner approval | Current validated USD NLV shared by sizing/heat/theme; separate frozen loss bases; exact XNYS opening, 10s observation/60s completion/30s current age; append corrections and deny affected period without denominator replacement/unlocking | `docs/ATS-009F10-equity-opening-policy-proposal.md`, approval commit `cbd448ad04b30f3b32624803c0a2cd50fe309db0`; explicit owner approval Oct 22 |
| Attached Playbook v3 | Broad original strategy ranges; detailed extraction retained in F9 | 19 pages; SHA-256 `f9f632fc73b6eaa24f2304caa03c0fe7d29869e17c058da465f3fb8ee714cf26`, rechecked against attached file |
| Merged implementation | Source and named tests inspected at the assessment commit | Files below; test existence alone is not acceptance |

Notion fetch timestamps are exposed snapshots, not immutable revision IDs or
historical approval dates. Explicit approved configuration selects endpoints
within broader strategy ranges; no global newer-source precedence is inferred.
F9/F10 are now both merged: PR #12 `dc77cb5a450deed230f13add9183f0ff4609cd39`,
PR #13 `7f45ddc15949263959557e5e382db8905f6c9720`. Historical draft/dependency
wording in those documents is superseded by these verified merge facts.

## Validation and review register

- Prior F12 Work author-executed run: **752 passed, zero skipped** on disposable
  PostgreSQL 16.15, including 16 F12 integration cases; five dependency warnings.
  That is prior execution evidence, not a new F13 rerun. Its transient local
  JUnit artifact is unavailable in this session.
- Independently retrieved GitHub [CI run 39967104613](https://github.com/gndongsah/ats-trading-system/actions/runs/39967104613)
  on exact F12 head `f68d11278088903b512b4761104a06dfeae40129`:
  job `120459716262` successful; logs show **46 passed** in the dedicated
  PostgreSQL module and **752 passed** in the full suite. The 46 are a subset,
  not all database cases; the full command includes `--fail-on-skip`.
  Ruff lint/format steps succeeded. The merged tree matches that tested head.
- PR #14 merged F11 as `50a3405`; PR #15 merged F12 as `801812a`.
  GitHub reviews API returned empty review lists for PRs #11, #14 and #15 during
  F13. This does not prove no review happened outside GitHub. Owner reported
  F11 reviewed/merged; that report does not identify an independently reproduced
  final-head review transcript. F12 merge/CI report did not supply one either.
- Earlier reviewer-reported F1 results and Work independent F2/F3/F6 executions
  remain checkpoint-specific evidence in ATS-009; they are not approval of later
  F8/F11/F12 changes. Work authored fixes/implementation in those later heads.
- F13 is a source/evidence reconciliation, not an independent security/code audit,
  a new execution run or a broker validation. No broker/keyring calls made.

## Requirement-to-evidence matrix

**SIM VERIFIED** means named behavior exercised in the passing synthetic suite,
not broker authority or independent acceptance. **PARTIAL** means implemented
contracts with unresolved integration/review scope. **BLOCKED** needs external
source evidence. No percentage-complete score is inferred from test counts.

| ID / source | Requirement | Implementation and named evidence | Finding / smallest remaining gate |
|---|---|---|---|
| R1 / ATS-007 | Exact risk dollars / stop distance / shares | `src/risk.py`; `tests/test_risk.py::test_position_sizing_playbook_example_and_regime_scaling`, high-precision/context cases | SIM VERIFIED; standalone ATS-007 acceptance preserved; integrated review still required |
| R2 / Sprint | Regime multiplier and no-trade rules | `resolve_regime_risk_multiplier`; sizing regime 4 explicit multiplier and regime 5 denial tests; F12 `test_same_current_nlv_drives_sizing_and_theme` covers regimes 1 and 3 | PARTIAL integration coverage: add final-adapter regime 2/4/5/missing-regime cases if reviewer requires; distinguish unsupported-policy denial from accepted explicit regime-4 flow |
| R3 / F10 | Same current NLV for sizing, heat and theme | `SimulationRiskService.authorize`, `_sized_quantity`; F12 same-NLV gain/regime and mismatch tests | SIM VERIFIED within explicit adapter; generic Layer3 retains externally sized plans. A future enabled entry point must mandate the approved sizing/basis path |
| R4 / ATS-008 + Oct 16 policy | 4R total / 2R correlated / 15% gross theme | `src/portfolio.py`; exact boundary/factor/theme tests; F12 `test_total_and_correlated_capacity_use_current_nlv` | SIM VERIFIED; ATS-008 remains Backlog in source. Capture independent integrated verdict before changing status |
| R5 / Sprint policy | Confirmed tighter stops and full exposure lifecycle | Layer3 verified-event protocols; partial-fill/final-cancel/stop tests in `test_layer3.py` and PostgreSQL fill/cancel race | PARTIAL: synthetic verifier only; validate actual broker identity, sequence, fills and stop confirmation before claiming trusted lifecycle |
| R6 / ATS-009 | -3R/-6R inclusive sticky losses | Accounting + loss repository/provider; F6 daily/weekly exact boundary, recovery/recreation and rollover tests | SIM VERIFIED; accepted broker facts and final-head review still absent |
| R7 / ATS-009 + F10 | Separate frozen daily/weekly opening bases | F11 capture, distinct IDs/receipts; `test_replay_conflict_and_separate_daily_weekly_identity`, new-week capture tests | SIM VERIFIED only; authenticated broker opening service/authority remains BLOCKED |
| R8 / F10 | Exact cutoff, 10s observation, 60s completion | F11 boundary/deadline/rollback/quarantined-receipt tests | SIM VERIFIED under model clock and harness seals; obtain authoritative source cutoff and time semantics before broker adaptation |
| R9 / F10 | Current age <=30s; future/stale/missing deny | F11 inclusive age tests; F12 unavailable-evidence cases and stale replay denial | SIM VERIFIED; broker timestamp source and arrival/observation semantics BLOCKED |
| R10 / ATS-009 | External flows excluded, fees included | Exact accounting tests for signed flows, fees once, separate periods; F6 flow integration | SIM VERIFIED arithmetic; DEPOSIT/WIRE owner observation does not validate all flow classes, signs, settlements or corrections |
| R11 / F10 | Append corrections, retain denominator/locks, deny period | F11 correction and daily/weekly scope tests; F12 correction denial | SIM VERIFIED; no broker reversal-linkage contract, correction activation or in-period replacement claimed |
| R12 / ATS-009 | Version-bound reservation/replay, rollback/retry | Layer3 account lock, final loss version check, `authorize_with_retry`; F1 server-raised SQLSTATE recovery/exhaustion, real locks/helper deadlock/serialization; F12 concurrent replay/post-write rollback | SIM VERIFIED listed cases. F12-specific transient recovery/exhaustion and correction-after-reservation replay are not new F12 cases; prioritize them in independent review scope rather than claiming full cross-product coverage |
| R13 / Sprint | Exact Decimal-safe domain validation | Domain `Fraction` comparisons; `test_trade_plan_stop_risk_is_exact_under_decimal_context`, journal aggregation context tests | SIM VERIFIED; old Oct 16 context-sensitivity concern has implemented regression coverage, not still a proven open defect |
| R14 / ATS-009 | Authenticated identity, valuation and complete holdings | Principal/snapshot/event verifier interfaces, read-only sandbox discovery; owner exact identifier and NLV/cash MATCH | BLOCKED for authority/completeness; observations consistent but no authenticated complete accounting snapshot contract |
| R15 / ATS-009 | Authoritative regular/emergency session evidence | `XNYSCalendar` with attestation contract, calendar tests; F11 SIMULATION-ONLY authority required | PARTIAL: pinned schedule and synthetic attestation are not operational emergency-status authority |
| R16 / Sprint + safety | Non-bypassable gate, no dispatch until approved | Literal false decision guards, disabled submit function, capability/skip guards and CI; F12 decisions remain disabled | Safety regression coverage passes; static scans are not a sandbox. No enabled execution route exists to accept as a production non-bypassable end-to-end path |
| R17 / acceptance | Independent final integrated review and traceable evidence | Prior execution records + CI, empty formal review lists for sampled latest implementation PRs | OPEN: obtain named reviewer, exact head/tree, findings/resolution and independently executed targeted/full results; merging alone cannot close |

## Remaining F2 broker evidence: minimum next deliverables

| Gap | Evidence already available | Smallest verifiable next step / dependency |
|---|---|---|
| Account binding / synchronization | Owner exact desktop/API identifier match and NLV/cash MATCH on masked CASH account ending 0AA8 | Retain sanitized authenticated account-to-source mapping and simultaneous comparison protocol; establish whether both surfaces share the same ledger, not just matching labels/balances |
| Broker timestamps / ordering | `biz_time` retained raw; no verified broker as-of | Obtain endpoint-specific format/timezone/as-of/sequence guarantees and sanitized nonempty sample; only then define adapter parsing and freshness validation. Earlier cross-API assertions are not accepted guarantees |
| History / flows / reversals | One strict DEPOSIT/WIRE amount MATCH in explicit Oct 7–10 UTC window, terminal cursor | Reconcile an explicit bounded period against independent account records including deposits/withdrawals/fees/corrections; record documented query/default windows, completeness and reversal linkage. Empty/default/terminal pages alone do not prove coverage |
| Frozen opening authority | F11 synthetic receipts only | After source-time and account authority exist, design separate broker daily/weekly opening capture under F10. Never promote a simulation receipt or current balance into a historical opening |
| Positions / NLV reconciliation | Empty positions observed, matching cash/NLV | Establish complete position/order scope and valuation convention; reconcile NLV, cash, positions and flows for the same as-of with fail-closed discrepancy handling |
| Emergency calendar authority | Pinned XNYS schedule, synthetic attestations | Identify and validate operational market-status authority, freshness and closure/correction behavior; no authority means no new risk |

These are evidence contracts, not instructions to place transactions to manufacture
samples. No support ticket is required by this report. Use official endpoint
materials and existing read-only account records first; unresolved guarantees stay
blocked rather than guessed. No full account identifiers, credentials or financial
values need to enter repository evidence.

## Recommended next bounded assignment

**ATS-009F14 — independent final-tree risk integration review and targeted failure
verification**, before further feature expansion. Review the F11/F12 source at
`801812a` against R1–R17; independently execute existing full suite with disposable
PostgreSQL and fail-on-skip, and probe F12-specific retry/exhaustion,
correction-after-reservation replay, differing timestamps with equal NLV, regime
2/4/5/missing-policy behavior, snapshot replacement and reservation basis metadata.
A probe is evidence only for the case executed; commit regression tests for actual
findings. Do not assume all probes are defects or that this author can supply an
independent verdict on its own code. Record reviewer identity, exact tree, findings,
resolved head and remaining limits under existing ATS-009; no duplicate story needed.

In parallel, collect the bounded broker source-time/history contracts above using
read-only sources. Both tracks are prerequisites to broker acceptance. Review can
close simulation review gaps; it cannot close broker authority by itself.

## Status reconciliation and checks

- ATS-007 Done remains standalone; ATS-008 Backlog and ATS-009 In Progress remain
  unchanged. Sprint 3 stays Planned, Nov 2–15; no scheduling/acceptance change.
- F9/F10 merged and F10 policy approved; do not ask the owner to reapprove the same
  three choices. F11/F12 merged and tested; neither closes F2 automatically.
- Local F13 validation: inspected requirements, implementation/test mappings,
  exact head/tree identity, GitHub merge/review/CI records and Playbook hash;
  `git diff --check` passed. No application tests rerun for this docs-only change.
- No paper/live submission, enforcement or Layer 3 trading approval.
