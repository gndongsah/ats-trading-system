# ATS-009F5 — read-only balance and deposit comparison

Extends `src/webull_sandbox_evidence.py` (F2 runner, F4 identifier mapping).
Read-only: account list, balance, positions and cash activities only. No order
path exists. Nothing here establishes trust; every result is an observation.

## Options

| Option | Behavior |
| --- | --- |
| `--map-paper-account` | F4 hidden exact-identifier comparison (unchanged). |
| `--start-time` / `--end-time` | UTC `yyyy-MM-ddTHH:mm:ss.SSSZ`, both or neither, same calendar year, start <= end (F3 validation). Invalid windows block before credentials or network use. |
| `--compare-balance` | Hidden prompts for expected desktop NLV and cash balance. Blank skips that item. |
| `--check-deposit` | Hidden prompt for an expected deposit amount (positive or zero). Blank skips matching but still uses the strict parser. |

With none of the new options the runner behaves as in F2/F4 (account-only,
default window, shape capture). With a window or `--check-deposit`, cash
activities go through the strict F3 parser: account match, documented
type/subtype pairs, decimal strings, repeated-cursor detection, a 100-page
bound, and `IncompleteHistory` when a later page fails.

## Output contract

- `balance_nlv_comparison` / `balance_cash_comparison`:
  `MATCH`, `MISMATCH`, `NOT_REQUESTED` or `ACTUAL_UNAVAILABLE`.
  Exact `Decimal` equality; expected input must be a plain decimal
  (no exponent, sign prefix `+`, separators, `NaN`, `Infinity`).
  Expected and actual values are never printed.
- `cash_activity_summary`: event count, documented `TYPE/SUBTYPE` counts,
  reversal-ambiguous count, `expected_deposit` (`MATCH`/`NO_MATCH`/`NOT_REQUESTED`)
  and matching record count. Only records with category `DEPOSIT` can match.
  Event IDs, amounts, `biz_time`, cursors and account IDs are not printed.
- Fixed trust lines remain: `biz_time: RAW_UNVERIFIED_FORMAT_AND_TIMEZONE`,
  `reversal_linkage: NOT_INFERRED`, `external_flows_verified: False`,
  `reconciliation: NOT_PERFORMED`, `enforcement_eligible: False`,
  `paper_live_order_submission: DISABLED`.

## What a match does not prove

A matching balance or deposit amount is one coincident observation. It does not
prove account binding (beyond the F4 identifier observation), history
completeness, deposit sign/classification semantics, `biz_time` timezone,
reversal linkage, authenticated frozen opening equity or reconciliation.
Empty activity pages are not proof of no flows.

## Reproduction (operator machine, keyring credentials, hidden prompts)

```powershell
python -m src.webull_sandbox_evidence --map-paper-account --compare-balance --check-deposit `
  --start-time 2026-10-01T00:00:00.000Z --end-time 2026-10-31T23:59:59.999Z
```

Offline tests (no network, no credentials):

```powershell
python -m pytest tests/test_webull_sandbox_evidence_comparison.py tests/test_webull_sandbox_evidence_runner.py -q
python -m ruff check . ; python -m ruff format --check . ; git diff --check
```

PostgreSQL-gated tests need `ATS_TEST_DATABASE_URL` pointing at a disposable
database; otherwise they are reported as skipped.
