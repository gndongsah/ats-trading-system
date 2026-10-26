# ATS-009F12 — simulation end-to-end integration

Base: Sprint 3 `50a34050d02fc731e36b7314bf86ed3b53a62460` (merged F11).

## Scope

`SimulationRiskService` explicitly composes F11's receipt-qualified loss provider,
persisted ledger loss accounting, ATS-007 sizing and Layer 3 portfolio reservations.
It is a harness adapter, with no API route, broker client or runtime installation.
The F10 owner-approved policy supplies current USD NLV, 0.5% reference risk,
4R total / 2R correlated / 15% theme limits and a 30-second current-age limit.
Frozen daily/weekly loss denominators remain independent from current NLV.

A service instance binds one immutable portfolio snapshot and a separate finite,
nonnegative buying-power amount supplied by the harness. Under the account lock,
its preflight requires an accepted opening receipt for both periods and a current
loss event. The event's valuation NLV and timestamp must equal the snapshot exactly.
This check runs for new requests and replay. F11 additionally denies pending
corrections, missing receipts, stale/future valuations and invalid periods.
Layer 3 then reads the persisted snapshot, validates loss state, sizes from that
same snapshot equity, applies portfolio limits and writes the reservation and
simulation basis together. Final loss-version recheck and whole-transaction retry
remain inherited. The generic Layer 3 sizing hook preserves its previous requested
quantity; the explicit simulation subclass caps it using the existing sizing engine.
Requested plan quantity is always an upper bound. Existing plan stop-risk budget
remains a separate constraint validated by TradePlan. Regimes without an explicit
usable sizing policy deny through the existing sizing engine.

Reservation event metadata records simulation source, NLV, timestamp, snapshot
version, buying power and reference fraction. A sizing-only resize can retain the
portfolio assessor's WITHIN_LIMITS reason; that reason describes portfolio limits,
while the decision quantity and simulation basis record the sizing cap.

## Executed evidence

Focused integration: 16 cases passed against fresh disposable PostgreSQL 16.15.
Cases exercise real opening capture/receipts, durable accounting, publication and
Layer 3 authorization: ordinary reservation; buying-power resize/zero denial;
missing receipt, NLV mismatch, stale/future valuation, correction and inclusive
-3R loss denial; three concurrent same-key requests; stale replay denial;
application failure after write with rollback; current-NLV gain changing theme
capacity; regime sizing from the same NLV; total and correlated capacity limits.
All decisions remain execution-disabled; replay retains reservation identity.

Full-suite results are recorded in the PR and tracker after execution. Validation
is author-executed, not independent approval. Reproduce with disposable `ats_test`
and ATS_TEST_DATABASE_URL, then `python -m pytest -q --fail-on-skip`,
`ruff check src tests`, `ruff format --check src tests`, and `git diff --check`.
The scratch-only PostgreSQL launch procedure is the same isolated root-namespace
workaround documented in F1; no shim or database configuration enters repository code.

## Limits

Ledger completeness, positions, buying power, schedule and principal are explicit
harness assertions. This adapter does not authenticate broker facts, derive positions
from fills, ingest a ledger, capture openings automatically or establish broker
history/time/reconciliation authority. Callers persist accounting evidence and publish
the bound snapshot before authorization. Opening/accounting publication transactions
are separate from the atomic authorization/reservation transaction. Sizing checks
never increase the saved plan quantity. Replay does not dispatch or resize an existing
reservation. Changing buying power alone does not reauthorize an old reservation.

F2 and Sprint 3 acceptance remain incomplete. No enforcement or paper/live order
submission enabled. Independent final-head review and CI are required before merge.
