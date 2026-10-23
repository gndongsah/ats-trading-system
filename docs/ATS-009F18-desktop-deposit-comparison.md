# ATS-009F18 — private desktop deposit comparison

Base: merged F17 `c1e2c35e5c7d8030d671cd6a35105741e503c06a`.

F17 owner execution observed one unchanged parsed event across repeated and enclosing queries. This is repeatability evidence from one API, not independent accounting reconciliation. F18 adds an opt-in operator-observed desktop record comparison. No broker calls or desktop inspection were performed by Work.

## Owner procedure

Open transaction history for the **same desktop paper account** used for the earlier exact identifier comparison. Locate the deposit in the October 7–10 window. Read the deposit amount, displayed transaction date, currency and deposit method. Do not substitute a live-account statement, infer a method from its amount, or change the date to force a match. If the desktop does not expose these fields clearly, leave all four inputs blank and report the evidence gap. A desktop screen can share the API's underlying data; it is a separate observed surface, not guaranteed independent source provenance.

From this branch's worktree with the existing environment:

```powershell
..\ats-f4-check\.venv\Scripts\python.exe -m src.webull_sandbox_evidence --map-paper-account --compare-desktop-deposit --start-time 2026-10-07T00:00:00.000Z --end-time 2026-10-10T00:00:00.000Z
```

The four prompts are hidden: positive plain decimal amount, displayed date as yyyy-MM-dd, USD, and the documented DEPOSIT subtype (for example WIRE or ACH). Enter a subtype only when the desktop label supports that exact meaning; no fuzzy label mapping is implemented. All four blank skips; partial or invalid input blocks before credentials/network access. Explicit query bounds and paper-identifier mapping are required. The account chooser and exact unique identifier comparison must succeed before balance/history requests proceed.

Expected data stays in memory and is not echoed or exported. Existing explicit query bounds, masked account selection and documented API category counts remain visible. Share sanitized output only. No secure-erasure guarantee is made for process memory or host diagnostics.

## Results

- UNIQUE_RECORD_MATCH_OBSERVED: exactly one returned DEPOSIT record matches amount, displayed date versus API trade_date, currency and subtype jointly.
- AMBIGUOUS_MULTIPLE_MATCHES: multiple distinct IDs have the same entered fields. Do not treat this as unique reconciliation.
- NO_EXACT_RECORD_MATCH: no single returned record matches every entered field. Investigate account/window/display conventions; do not adjust evidence to force equality.
- NOT_REQUESTED: all inputs blank.
- NOT_COMPLETED: account mapping, fetch or any requested later comparison query failed.

Matching returns the same completion exit status as other evidence observations; a zero exit code does not mean accounting approval. Exact Decimal numeric equality is used. Raw biz_time is not parsed or compared to the displayed date. Reversal-like subtype matches retain the ambiguity count and cannot establish reversal linkage. This is a point comparison of an operator-entered deposit, not period cash reconciliation or event provenance attestation. Optional F17 extra bounds can be combined; a later protocol failure suppresses the desktop result.

## Verification and remaining gates

Committed tests cover unique and ambiguous matches, separate partial matches, changed date/type/subtype/amount, opaque business time, invalid/partial inputs, blank skip, missing context, incomplete history, exact-account mismatch, sanitized output and later-protocol failure. Author results are recorded in the PR. No authenticated keyring/broker request, desktop observation, new opening capture or accounting authority is claimed by Work. CI, separate review and owner execution remain pending.

F2 and Sprint 3 acceptance remain incomplete. Full history coverage, broker valuation/event time, source provenance, reversal linkage, separately frozen broker openings and full accounting reconciliation remain unverified. Enforcement, Layer 3 trading authorization and all paper/live order submission remain disabled.
