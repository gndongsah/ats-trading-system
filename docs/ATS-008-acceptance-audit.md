# ATS-008 acceptance audit and Sprint 3 reconciliation

Date: 2026-10-23 (America/Chicago). Assessed merged base
`ca680d76e83e7fbe3b5bd5a60363a0d1602cf666`. Author audit/remediation;
**not independent acceptance or trading approval**.

## Governing requirements

[Sprint 3](https://app.notion.com/p/3f08afe6392181f78830e2540634e2d3)
explicitly approves reference R = 0.5% of validated account equity, total heat
<=4R, correlated-theme/factor heat <=2R, gross single-theme concentration <=15%,
and tighter stops reducing risk only after confirmation (October 16 policy).
F10 owner approval chooses one current validated USD NLV basis for sizing/heat/theme,
with separately frozen daily/weekly loss denominators. No policy reapproval needed.

The [ATS-008 card](https://app.notion.com/p/3f08afe639218117a7d7f4ecd53f5612)
still had broad 4–5R/2–2.5R ranges and Backlog status at audit start. These are
being reconciled to the existing approved policy, not used to change runtime limits.

## Reproduced finding and remediation

**Same theme with disjoint factors bypassed its 2R heat budget.** At equity 100,000,
1R=500. Existing exposure: 450 shares at entry10/stop8, risk900. A candidate in
that same theme but a different factor requested100 shares at entry100/stop98,
risk200. Total1100 is within4R and gross notional14500 within15%, but theme risk
would reach2.2R. Base allowed100; the approved theme cap permits only50.

Three pre-fix regressions failed for existing positions, external pending orders
and remaining ATS reservations. A fourth committed path covers confirmed fills.
The fix accumulates exact theme risk across the same three exposure sources and
checks the candidate's theme headroom alongside each candidate factor's headroom.
Shared theme and shared factor are independent constraints, not summed twice into
account heat. Existing reason CORRELATED_HEAT_LIMIT is retained. No migration,
new configuration, broker request or execution route is added.

The policy also already denies new risk when any theme is over15% gross. This
change does not introduce portfolio liquidation, shorts/options support or a new
rule for unrelated pre-existing factor breaches. Quantities remain whole-share
long-equity planned-risk calculations using entry/actual-fill prices and confirmed
stops, not a claim about broker mark-to-market margin or affordability.

## Requirement-to-evidence audit

| Requirement | Code / executed or existing named coverage | Acceptance disposition |
|---|---|---|
| Fixed 0.5% R / 4R total / 2R theme and factor /15% theme | PortfolioRiskPolicy; new rejection of unapproved ranges; exact boundary cases | Remediated theme gap; review pending |
| Largest admissible integer; ALLOW/RESIZE/DENY | assess_portfolio_risk; test_exact_approved_boundaries and existing exhausted-cap tests | Synthetic behavior covered |
| Same theme, disjoint factors | test_same_theme_disjoint_factors_share_two_r_budget (position/pending/reservation/fill) | Base failed three cases; revised paths pass |
| Shared factor across different themes | test_factor_budget_applies_across_different_themes | Synthetic behavior covered |
| 15% gross distinct from stop risk | test_theme_concentration_limits_gross_notional_separately_from_heat; above-entry stop and unrelated over-cap theme tests | Synthetic entry-notional convention; broker convention open |
| Position/pending/reservation and actual-price fills | test_external_pending_orders_consume_risk_and_theme_capacity; test_fill_at_higher_price_uses_actual_risk_and_unconfirmed_stop_blocks; Layer3 fill/cancel tests | Broker lifecycle verification remains external |
| Missing/stale/incomplete/taxonomy/stop evidence denies | Existing portfolio tests plus Layer3 trusted-provider tests | Operational source authority remains open |
| Decimal-context independence | Fraction arithmetic; test_small_decimal_context_does_not_change_budget_result | Synthetic arithmetic covered |
| Atomic capacity and replay | PostgreSQL account-serialized reservation, same-key, fill/cancel race, rollback and retry cases | Full-suite evidence recorded separately |
| Same NLV sizing/heat/theme, final freshness and buying power | F12 integration; F15 remediation and separate-agent report | Bounded simulation scope only |

## Executed validation

- Pre-fix finding probes: three failures (position, pending, remaining reservation).
- Revised focused portfolio/audit/Layer3: **46 passed**.
- Full suite: **838 passed, zero skipped**, five dependency warnings, fresh
  disposable PostgreSQL 16.15, Python 3.12.14, SQLAlchemy 2.1.6, psycopg 3.3.6.
  Command: `python -m pytest -x -q -rs --fail-on-skip`; all database-gated cases ran.
- Repository-wide `python -m ruff check .`, `python -m ruff format --check .`
  and `git diff --check` passed.
- Disposable cluster used UTF8 in /tmp and was stopped/removed after the run.
  Runtime-only namespace UID shim from prior Work verification was needed to
  start PostgreSQL; it is not application code. An initial SQL_ASCII cluster
  produced a driver/SQLAlchemy version-decoding setup error; that attempted run
  was discarded and not counted as passing evidence.

Sixteen new committed audit cases cover the finding and policy boundaries.
F15 separate-agent review remains valid for its exact reviewed tree; it did not
review this new change. The author cannot supply independent approval of this fix.
CI and separate final-head review are required before acceptance.

## Broker evidence disposition

No further comparison tool is proposed as a substitute for missing broker contracts.
The F16 official source register already identifies what public definitions establish.

| Gate | Evidence now available | What remains / next evidence owner |
|---|---|---|
| Account identity | Owner exact identifier equality and earlier expected NLV/cash matches | Broker/owner evidence of same ledger provenance, synchronization and complete account scope |
| Valuation/event time | Cash UTC query bounds; raw biz_time retained; balance/position schemas lack authoritative as-of | Broker source definition/attestation of valuation time, freshness, ordering; local clock is insufficient |
| Cash history/flow semantics | F17 same ID and unchanged parsed fields on repeat/enclosing; one DEPOSIT/WIRE | Owner bounded account history/statement reconciliation; broker completeness, signs, settlement/correction/reversal contracts |
| Desktop point comparison | F18 implementation merged; owner's run returned NOT_REQUESTED | Owner enters clearly observed fields, or records unavailable UI evidence. This does not close period reconciliation |
| Daily/weekly opening authority | Simulation F11 capture and F10 policy | Trusted broker source/time needed before separate real opening capture; never promote current NLV to historical opening |
| Positions/orders/NLV | HTTP200, zero positions returned, earlier balance matches | Broker scope/valuation convention and same-as-of full reconciliation; empty array not completeness |
| Calendar authority | Pinned XNYS schedule and simulation attestation | Operational emergency/session source, freshness and correction handling |

Work made no authenticated Webull/keyring calls or desktop observations in this audit.
These external gates remain unresolved rather than filled with guessed semantics.
A broker-supplied authoritative artifact may be required if public docs/account
records do not expose a guarantee; this report does not require a support ticket.

## Sprint disposition

ATS-007 standalone acceptance retained. ATS-008 moves from Backlog to In Progress
because audit found and remediated a runtime gap; not Done. ATS-009 remains
In Progress. Sprint 3 stays Planned for November 2–15 with acceptance incomplete.
Historical F13 notes that simulation review was wholly absent are superseded by
F15's scoped review; the new theme fix requires its own review. F1 transaction
integration and F11/F12 simulation work are implemented, not current missing-code
items. F18 owner execution occurred but skipped the actual desktop comparison.

Next acceptance work: review this fix and its exact head, verify green full CI,
then close the source/time/opening/reconciliation gates with actual evidence.
No order submission, enforcement or Layer3 trading authorization is enabled.
