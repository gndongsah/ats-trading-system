# ATS-009F16 — broker timestamp and bounded-history evidence

Assessed 2026-10-23 America/Chicago, merged Sprint 3 `2b93c62b760c739a947a10707beea136edf4dfbf`.
**Documentation evidence collected; broker accounting acceptance remains blocked.**
No authenticated API/keyring request, account enumeration, raw account export,
order, policy change or runtime change was made. Public documentation retrieval
is separate from the owner's earlier sandbox observations.

## Source register and retrieval

Official endpoint pages were fetched directly as Markdown over HTTPS and their
first OpenAPI JSON fences parsed. The text-only search rendering omitted parts
of request/response schemas; it was not treated as a complete schema. The
published [machine-readable documentation guide](https://developer.webull.com/apis/docs/AI-friendly-Resources/llm/)
and [index](https://developer.webull.com/apis/llms.txt) explain Markdown access.
Machine-readable hashes, sizes, operation identities and schema selectors are in
[evidence/ATS-009F16-source-register.json](evidence/ATS-009F16-source-register.json).
Hashes identify bytes retrieved, not an immutable Webull release or a broker
signature. Full upstream schemas are not copied into the repository.

| Source | Official URL | Relevant scope |
|---|---|---|
| C | [Cash activities](https://developer.webull.com/apis/docs/reference/trade-cash-activity-by-type.md) | Trading API cash ledger query and response schema |
| B | [Account balance](https://developer.webull.com/apis/docs/reference/account-balance.md) | Trading API balance/NLV response |
| P | [Account positions](https://developer.webull.com/apis/docs/reference/account-position.md) | Trading API position response |
| T | [Transfer activities](https://developer.webull.com/apis/docs/reference/list-transfer-activities.md) | Separate ACATS/crypto transfer records |
| L | [Change log](https://developer.webull.com/apis/docs/changelog/) | Dated endpoint-specific changes, not universal guarantees |

## Endpoint-specific conclusions

| Question | Retrieved evidence | Disposition for ATS |
|---|---|---|
| Cash default/query bounds | C documents seven-day default and UTC millisecond query strings; start/end must share a calendar year | F3 same-year validation agrees. Requiring both bounds together remains stricter local policy |
| Cash business timestamp | C's `biz_time` is a string with a UTC-looking example, without a `format` declaration or explicit field timezone guarantee | Retain raw; example does not establish a parser, ordering or freshness contract |
| Accounting day | C labels `trade_date` as an accounting/trade date | A date alone cannot establish an event instant or opening cutoff |
| Cash pagination | C supplies opaque next-page keys; absent key marks final page | End of this request sequence, not a historical completeness watermark |
| Signed amount | C describes credits as positive and debits as negative | This does not classify deposits versus P&L, final settlement, or reversals |
| Cash coverage/order | C does not specify retention, inclusive/exclusive bound semantics, stable snapshot isolation, publication lag, or a cash-record sort/tie-break contract | Do not impose a guessed order or claim complete history |
| Balance valuation time | B lists NLV/cash and per-currency assets but no response as-of, sequence or valuation timestamp | Local observation time cannot satisfy authoritative source freshness |
| Position valuation time | P lists positions/legs but no response as-of or snapshot watermark | Concurrent balance/position calls are not a documented atomic accounting snapshot |
| Request timestamp | Endpoint schemas define `x-timestamp` among request headers | Authentication request time is not balance/event source time |
| Transfer timestamps/order | T explicitly specifies UTC creation/update times and descending creation-time order; ACATS grouping has its own identifier | Applies to transfer records only; not cash `biz_time` or a cash-reversal link |

Absence claims above are limited to the retrieved endpoint schemas/descriptions;
they are not assertions that Webull has no other documentation or support for
these facts. No latency, UTC or immutability property is inferred from the SDK
transport, JSON examples, HTTP response Date, local receipt time or market quotes.

## Corrections to earlier broad architecture assertions

These dispositions replace unsupported generalizations supplied earlier in the
project conversation; they do not change risk policy.

- A universal UTC `biz_time` contract and newest-first cash-ledger ordering are
  not established. T provides explicit transfer-specific guarantees; C does not.
- A universal two-year maximum is incorrect as a cross-endpoint rule. L's
  2026-07-25 entry says the order-history two-year limit was removed. C instead
  constrains a query to one calendar year; it does not specify total retention.
- L's 2026-09-05 entry confirms cash/order-list cursor-envelope migration.
  A pagination cursor does not expand endpoint retention or certify coverage.
- Immutable cash history, original-event reversal linkage, transfer recognition
  timing in NLV and midnight opening-equity snapshots remain unverified.
  An order status or ACATS grouping field does not establish those cash facts.
- Auction schedules and quote freshness do not authenticate account valuation
  time. Broker API event-contract/SOD material is not evidence that these
  individual-account Trading API endpoints provide frozen opening facts.

## Existing adapter comparison

`src/webull_sandbox_cash_activities.py` already preserves `biz_time_raw`, validates
same-year explicit bounds and documented activity pairs, deduplicates identical
IDs and rejects conflicting duplicates/repeated cursors/later-page failures.
Its `pagination_complete` field is scoped to a successful cursor traversal;
`history_window` is the supplied filter. Neither proves historical completeness.
The sandbox runner separately reports history/time/reconciliation unverified
and enforcement false. No source-backed runtime parser change is justified here.

Named synthetic contracts inspected: `test_cursor_pagination_parses_official_fields_and_forwards_same_year_window`,
`test_query_rejects_cross_year_invalid_or_reversed_windows`,
`test_identical_duplicate_is_deduplicated_with_both_page_provenances`,
`test_documented_record_fields_match_selected_account_and_keep_biz_time_raw`,
and `test_page_limit_prevents_unbounded_cursor_walk` in
`tests/test_webull_sandbox_cash_activities.py`. Their existence is not a broker run.

## Bounded owner-side read-only verification protocol

This is an observation protocol, **not a completeness proof**. Use existing
sandbox keyring credentials and privately select the already compared account;
never substitute live-account credentials or place transactions to manufacture
samples. The existing runner supports the following explicit historical window:

```text
python -m src.webull_sandbox_evidence --map-paper-account --check-deposit --start-time 2026-10-07T00:00:00.000Z --end-time 2026-10-10T00:00:00.000Z
```

1. Privately compare against the same desktop/account statement and known deposit.
   Preserve only the runner's sanitized comparison statuses, record/page counts,
   documented categories, terminal reason and selected account mask.
2. Repeat that unchanged window, then inspect an overlapping window (for example
   October 9–11 UTC) and the enclosing October 7–11 window using the same syntax.
   These are same-year requests; do not silently assume half-open boundaries.
3. A changed count/result is a discrepancy to investigate, not automatic proof
   of a correction or duplicate. The existing CLI exposes aggregate summaries,
   not cross-run event identity sets, so aggregate equality is insufficient.
4. To claim set-level coverage later, add a separately reviewed private comparison
   path for identical-account event IDs/payloads, page/query provenance, retained
   raw time and independent statement entries. Keep identifiers/values out of
   shared evidence. Do not invent equality from counts or amount matches.
5. Close boundary, delayed-publication, retention and reversal questions using
   endpoint-specific official guarantees plus independent account records.
   Repeated stable queries alone cannot certify settlement or exhaustiveness.

This task did **not** execute these authenticated observations. Prior owner F5
observation remains one DEPOSIT/WIRE amount MATCH in the October 7–10 window;
it is not new F16 execution or general history/flow authority.

## Gate result and next smallest work

Documentation resolves query format/default, scope of pagination, and the
separate transfer-time contract. It does not resolve cash source time, atomic
valuation identity, full history, settlement/reversal handling or authoritative
opening capture. F2 and Sprint 3 acceptance remain unchecked; all enforcement
and paper/live orders remain disabled.

Next bounded evidence: owner-side explicit-window repeat/overlap observations
above and an independent account-record comparison. For engineering, a reviewed
private cross-run event-set comparator can improve discrepancy evidence while
retaining unverified trust flags; it cannot create missing broker guarantees.
Do not build a broker opening service from timestamp-less balance reads.

Validation: source JSON parsing and registered byte hashes checked; repository
CI-equivalent lint/format and diff checks run for this docs-only change. No
application suite rerun or broker review approval is claimed. F15's 780-test
independent/CI results remain distinct historical evidence.
