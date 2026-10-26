# ATS-009F3 — Webull cash-activity evidence parser

## Scope and authority

The cash-activities adapter remains read-only and evidence-only. It validates
records against Webull's published OpenAPI definition for
[List Cash Activities](https://developer.webull.com/apis/docs/reference/trade-cash-activity-by-type).
The definition documents `GET /trading/activities/cash-activities/list`, a
`data` array and optional `pagination_key`, and says an absent pagination key
marks the last page. The endpoint defaults to the last seven days when dates
are omitted.

The parser accepts only documented record fields. Required fields are `id`,
`account_id`, `activity_type`, `activity_sub_type`, `currency`, `trade_date`,
`net_amount`, and `biz_time`. Optional fields are `market` and `symbol`.
`account_id` must exactly match the account selected for the request. Event
identifiers and account IDs are not exposed in the evidence object's repr or
error messages.

`activity_type` and `activity_sub_type` are validated as an official pair.
Documented activity types are `TRADE`, `DEPOSIT`, `WITHDRAW`, `FEES`,
`TRANSFER`, `DIVIDENDS`, `TAX`, `INTERESTS`, `CORPORATE_ACTION`, `OPTION_EA`,
`JOURNAL`, `EC_SETTLEMENT`, and `OTHER`. Each type is limited to the matching
subtypes listed in Webull's OpenAPI definition. Currency is constrained to its
documented `USD` enum; optional market is constrained to `US`.

`net_amount` must be a finite decimal string and is retained as `Decimal`.
Webull documents positive values as credits and negative values as debits.
The adapter does not turn that sign into an external-flow classification or
apply it to ATS-009 risk calculations.

`trade_date` is validated as `yyyy-MM-dd`. Query windows are either both
omitted or both supplied using UTC `yyyy-MM-ddTHH:mm:ss.SSSZ` timestamps. A
bounded window must be ordered and remain within one calendar year, as Webull
does not support cross-year queries. Requiring both bounds together is a
stricter local validation rule to avoid an ambiguous partially bounded query.

## Broker business time and reversals

The official definition describes `biz_time` as the business event time and
shows an ISO-looking example, but does not establish a parsing contract or
timezone guarantee. The adapter therefore requires only a nonempty string and
preserves it verbatim in `biz_time_raw`; it does not parse, normalize, or use
it for ordering. Local `observed_at` remains separate.

The documented taxonomy includes reversal-like subtypes, including
`REVERSAL`, `ACH_REVERSE`, `REVERSAL_FEE`, and cancelled trade subtypes. The
schema does not provide a documented link to the original event or establish
whether a record reverses a prior cash flow. Such records are flagged
`reversal_ambiguous` with `REVERSAL_LINKAGE_UNVERIFIED`; no pairing, correction,
classification, or sign inference is performed.

## Pagination and trust limits

The adapter follows opaque `pagination_key` values until the key is absent,
rejects malformed/repeated cursors, rejects failed later pages, and returns no
partial history on pagination failure. Identical repeated IDs are
deduplicated with both page provenances; a conflicting same-ID record is
rejected rather than guessed to be a correction.

An account ID match verifies only that the returned record names the selected
API account. It does not prove desktop/API binding or that the account is the
intended risk account. The official endpoint's last-seven-day default and a
terminal page do not establish full historical coverage. Empty results do not
prove no cash flows.

Activity categories and credit/debit amount signs are retained as raw
documented evidence; external-flow eligibility, reversal linkage, cash-flow
reconciliation, and ATS-009 enforcement remain unverified. `biz_time` format,
timezone, freshness, and ordering guarantees also remain unverified.
`enforcement_eligible=False`, Layer 3 authorization remains disabled, and all
paper/live order submission remains disabled.

## Synthetic validation

`tests/test_webull_sandbox_cash_activities.py` covers documented field parsing,
selected-account matching, strict decimal and category validation, same-year
query constraints, raw `biz_time` retention, reversal ambiguity, pagination,
duplicate/correction handling, and malformed records without network or
credential access.
