# ATS-009F2 — Webull sandbox evidence and trust gaps

**Scope:** schema discovery and read-only evidence collection only. The sandbox
account is an independent API account. API-to-desktop binding, ATS-009
enforcement, Layer 3 authorization and all paper/live order submission remain
disabled.

## Reproducible local runner

The runner is in `src/webull_sandbox_evidence.py`; it contains no credentials or
machine-specific paths. It loads the existing `ATS-Webull-Sandbox` keyring
provider first, supports only the provider's configured complete-pair
environment fallback, and prints the credential source label only. It requires
an interactive numbered CASH-account selection, displays masked suffixes, and
passes the selected full API ID only in memory.

From the repository root, with the project environment and Webull optional
dependencies installed:

```powershell
.\.venv\Scripts\python.exe -m src.webull_sandbox_evidence
```

The runner sends only account-list, balance, positions and cash-activities
read-only requests to `api.sandbox.webull.com`. Cash activities use the
account-only request with optional dates omitted (the documented broker default
is the last seven days) and follow opaque pagination cursors in memory, bounded
to 100 pages. It reports sanitized field/type
shapes, page counts and cursor presence/type; it never prints values, full
account IDs, raw activities, cursors, credentials or SDK exception text.

Offline synthetic coverage is in
`tests/test_webull_sandbox_evidence_runner.py`. Those tests inject synthetic
credentials and responses; they do not access keyring or the network.

## Test-count reconciliation against F1

Using the same pytest 9.1.1 collection command at F1 base
`03374b43563b9d98a714d46adf485c53adb4d2d8` collected **563 cases**. The F2
worktree before the additional selection/debug/error regressions collected
**543 cases** (the previously reported 496 passed + 47 skipped), a net
reduction of 20. The node-ID comparison found 10 new cases and 30 removed
cases; there were no exact-name one-for-one renames:

- 10 cases were added for the in-repository runner and shape collection.
- 25 node IDs were removed from `test_webull_cash_runner.py`; those tests
  imported an external `Downloads` script and were skipped when that file was
  absent. They covered external runner selection, window, credentials,
  failures and source scanning.
- 5 node IDs were removed from `test_webull_sandbox_diagnostics.py`; these
  also imported an external `Downloads` script and covered its standalone
  auth diagnostic.
- Thus `563 + 10 - 25 - 5 = 543`; the difference is removed environment-bound
  tests, not evidence that 20 F1 tests were silently deleted. F2 replaces the
  external-runner dependency with in-repository runner tests, and adds direct
  noninteractive/no-account/debug/SDK-error regressions.

The 543 total consisted of **496 passed + 47 skipped**. The 47 skips were
PostgreSQL-gated (46 persistence cases plus 1 Sprint 2 acceptance case) because
`ATS_TEST_DATABASE_URL` was unset; this was the offline run and does not count
as PostgreSQL validation. Four additional portable regression cases were then
added for noninteractive selection, no CASH accounts, SDK debug blocking, and
sanitized SDK errors. The final tree therefore collects **547 cases**. Its full
suite against disposable PostgreSQL 16 completed with **547 passed, 0 skipped,
0 failed**. Ruff check and format check, `compileall`, and `git diff --check`
also passed. This final run had no PostgreSQL skip because the dedicated test
database was provisioned.

## Sanitized sandbox observation

An authenticated read-only probe used keyring credentials and the user's
explicit selection of CASH account `****0AA8`. The API account list, balance,
positions and cash-activities calls returned HTTP 200. Only field names and
types were retained:

- Account list: array of 5 accounts; one example account object exposed string
  fields `account_number`, `account_type`, `account_label`, `account_class` and
  `user_id`; the `account_id` value and field name were redacted.
- Balance: object fields `total_asset_currency`,
  `total_net_liquidation_value`, `total_market_value`, `total_cash_balance`,
  `total_unrealized_profit_loss`, `total_day_profit_loss` and
  `account_currency_assets` (array of one object). Nested asset fields were
  string-typed: `currency`, `net_liquidation_value`, `market_value`,
  `cash_balance`, `settled_cash`, `unsettled_cash`, `buying_power`,
  `option_buying_power`, `night_trading_buying_power`,
  `unrealized_profit_loss` and `day_profit_loss`.
- Positions: empty array.
- Cash activities: object with `data` as an empty array; `pagination_key` was
  absent. One page was terminal for that request sequence.

No amounts, position symbols, event identifiers, cursor values, credentials or
full account identifiers were recorded. The empty positions and activity arrays
do not establish completeness. In particular, the terminal activity page is
only for the account-only request's documented last-seven-day default; it does
not establish coverage of a daily/weekly accounting period or prove there were
no cash flows.

## Evidence interpretation

| Requirement | Evidence needed to establish it | Current status |
|---|---|---|
| API account identity | Stable broker account identity evidence tied to the intended account, not just a selected API ID or masked suffix | **UNVERIFIED**; desktop/API binding is not established |
| Broker event/valuation time | Documented broker-owned timestamp field, timezone, semantics, precision, and freshness/order guarantees | **UNVERIFIED**; local `observed_at` is distinct and is not broker time |
| Cash-activity history completeness | Documented query window/defaults, earliest available history, terminal-page semantics, and coverage across the required period | **UNVERIFIED**; terminal pagination only describes the returned account-only request sequence. Empty results do not prove no flows or complete history |
| Cash-flow categories and signs | Official definitions and verified examples/corrections establishing which records are external flows and their NLV sign convention | **UNVERIFIED**; raw category and amount must not be interpreted |
| Opening equity | Authenticated, complete, reconciled broker NLV frozen at each daily and weekly period boundary, with correction/restatement rules | **UNVERIFIED**; a balance response or current NLV does not establish a frozen opening |
| Positions completeness | Broker contract and reconciliation evidence proving the positions response includes all relevant holdings and exposure | **UNVERIFIED**; item count/shape is not completeness evidence |
| Reconciliation | Reproducible equation matching opening NLV, current NLV, signed external flows, corporate actions/fees, and corrections at aligned timestamps | **NOT PERFORMED** |
| Enforcement authority | All identity, timestamp, completeness, sign, opening, reconciliation, calendar and ordering gates independently satisfied | **NO-GO**; `enforcement_eligible=False` |

The runner's shape output is descriptive evidence only. Field names that resemble
timestamps or amounts do not prove their semantics. No category is inferred,
no sign is applied, and no empty activity response is treated as a complete
history. Cash activity records remain provisional and are not parsed into
enforcement inputs.

## Next acceptance gates

1. Obtain authoritative Webull documentation or support confirmation for
   account binding, timestamp semantics, cash-activity history windows,
   pagination, category definitions, signed-flow semantics and corrections.
2. Validate each contract with sanitized sandbox observations, including
   non-empty records and multi-page cases, without exposing transaction values.
3. Establish separately authenticated, frozen daily/weekly opening equity and
   required historical flow coverage.
4. Demonstrate positions and NLV completeness, ordering, freshness and
   reconciliation under corrections/restatements.
5. Keep ATS-009 enforcement, Layer 3 authorization and broker order submission
   disabled until all acceptance gates are independently reviewed and approved.
