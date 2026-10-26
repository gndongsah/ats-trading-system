# ATS-009F17 — private cross-run event comparison

Base: merged F16 `b58f8cad424b53bad40da06ec7ee632b515d90b8`.

The owner’s four F16 queries returned aggregate counts 1, 1, 0, 1. Those logs cannot establish event identity or payload equality. This change adds an opt-in four-query comparison to the existing sandbox evidence runner. It fetches A, repeated A, overlapping B and enclosing C for the **same selected API account** in one invocation. Parsed histories exist only in process memory; no export, persistent hashes or comparison state is written.

## Local execution

From a checkout of this branch, using the existing environment with Webull/keyring dependencies:

```powershell
..\ats-f4-check\.venv\Scripts\python.exe -m src.webull_sandbox_evidence --map-paper-account --start-time 2026-10-07T00:00:00.000Z --end-time 2026-10-10T00:00:00.000Z --overlap-start-time 2026-10-09T00:00:00.000Z --enclosing-end-time 2026-10-11T00:00:00.000Z
```

Choose the intended masked account and privately enter the desktop identifier. Comparison requires all four bounds in strictly increasing order within one calendar year, validated before credentials or network access. Ordinary runner behavior is retained when both extra bounds are omitted.

## Interpretation

`cash_history_comparison` reports counts only:

- `repeat`: left-only, right-only, shared IDs, changed and unchanged parsed payloads; `equal_parsed_event_sets` describes this repeat pair only.
- `base_to_overlap`: differences are expected for different query windows; no date filtering is inferred locally.
- `base_to_enclosing` and `overlap_to_enclosing`: left-only IDs or changed shared payloads identify observations needing investigation. Extra enclosing IDs may be normal.

Parsed payload comparison reuses the strict adapter's duplicate comparison: category/subcategory, exact Decimal amount, currency, trade date, raw biz_time, optional market and symbol. Decimal representations such as 1.0 and 1.00 compare equal. Observation times, pagination provenance and return order are ignored. This is **parsed-value equality**, not byte-for-byte source payload equality; missing versus null unsupported fields are handled by the existing strict schema.

Each query follows its own cursor from the beginning with the existing 100-page cap. Identical duplicates collapse; conflicting duplicate IDs, foreign accounts, malformed records, repeated cursors, page caps and request errors fail the protocol. A failure in any later query reports NOT_COMPLETED, returns nonzero and clears the terminal-success flag. Completed comparisons return zero even if differences are observed: completion does not mean reconciliation passed. The existing page-shape list aggregates pages across all four queries; the per-pair report provides the comparison context.

No IDs, amounts, symbols, raw event times, cursors or fingerprints are emitted by the comparator. Existing masked account selection and explicit query bounds remain visible. Histories are retained in memory until the invocation ends; no claim of secure memory erasure or protection from host diagnostics is made.

## Validation and limits

Twenty committed F17 cases cover equal-count/different-ID detection, six changed-field cases, order/provenance and numeric-representation tolerance, incomplete/duplicate input rejection, same-account four-window routing, cursor reset/page deduplication, later-query failures, input rejection before credentials, enclosing discrepancies and output redaction. Existing parser/runner regressions also execute.

Author verification is recorded in the PR. No authenticated Webull/keyring calls were made by Work. Separate review and CI remain pending. The comparator cannot establish source-time authority, inclusive/exclusive boundary rules, immutable history, publication lag, complete history, reversal linkage, frozen opening equity or account-record reconciliation. Empty and equal sets are observations only. F2 and Sprint 3 acceptance remain incomplete; enforcement and all paper/live order submission remain disabled.
