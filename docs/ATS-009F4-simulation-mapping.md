# ATS-009F4 — paper identifier comparison and offline simulation ledger

Base: Sprint 3 merge `5126a0972330d050bb5e1d06070e9d6ef756cb15`.

## Local paper-account comparison

The existing runner and SDK builder already use `api.sandbox.webull.com`.
No endpoint migration was required. This Work workspace has no usable sandbox
keyring credentials; no authenticated broker request or actual account mapping
was performed here.

On the owner's machine, with sandbox credentials already in the existing
`ATS-Webull-Sandbox` keyring, run from this branch:

```powershell
python -m src.webull_sandbox_evidence --map-paper-account
```

Choose the intended masked CASH account. Enter the full account identifier
shown by the desktop paper interface at the hidden prompt. Do not put keys or
account identifiers in command arguments, commits or shared output.

The comparison checks exact `account_id` or `account_number` equality against
one unique account-list row and requires that row to be the selected account.
A suffix match is insufficient. A mismatch, duplicate match or invalid input
stops before balance, positions or cash requests. No match does not prove that
paper API access is unsupported: the UI may use another namespace, or the
account/application may be different. Do not fall back to a live account.

`EXACT_IDENTIFIER_MATCH_OBSERVED` is an observation, not proof of desktop/API
synchronization, ownership, complete history or accounting authority. The
existing binding and enforcement trust flags remain unverified/false. Keep the
comparison output for review; do not share full identifiers. Matching balances,
positions and existing orders across the two interfaces is a separate next
observation; this change does not add order-detail reads or submit test orders.

## Simulation ledger

`src/simulation_ledger.py` implements an immutable, USD-only synthetic NLV-delta
ledger with a positive frozen starting NLV. Explicit signed PNL entries include
fees and marked/realized changes; explicit external-flow entries represent
controlled contributions and withdrawals. These are scenario inputs, not a
fill simulator, market-data valuation, broker classification or reversal inference.
All contracts carry source/provenance `SIMULATION-ONLY`. Complete/reconciled
means complete within the synthetic scenario only. They must never be supplied
to a broker-backed authorization service. No runtime adapter or API route is
wired to the ledger.

Replay uses event identities: identical duplicates are no-ops and conflicting
identities fail. Construction and appends require ordered UTC entries after
opening. Valuations support historical cutoffs and exact arithmetic independent
of the ambient Decimal precision. Period-specific external flows project into
the existing accounting contracts; the existing loss engine computes thresholds.
No parallel risk-policy implementation was added. Ledger recovery alone does
not model sticky lock persistence; existing persistence tests retain that scope.

Integration scenarios cover the daily -3R and weekly -6R boundaries with flows
excluded from trading PNL, including just above the daily boundary. Other tests
cover fees/recovery, idempotency/conflicts, ordering and malformed amounts.

## Validation and remaining evidence

Full suite: **598 passed, zero skipped**, five dependency deprecation warnings;
all 46 PostgreSQL persistence cases executed on fresh disposable PostgreSQL
16.15. Repository-wide Ruff lint/format and git diff checks passed. The scratch
runtime setup follows the namespace-only procedure documented in F1 evidence;
no database setup or preload shim is repository code. This is author-executed
validation, not independent review approval. Actual paper-account mapping
remains pending the owner's local run. No broker/keyring request is fabricated
as successful evidence. A support ticket is not needed to execute this bounded
comparison or develop against synthetic scenarios. Broker-backed F2 acceptance,
opening-equity authority, history completeness and reconciliation remain open.
All enforcement and paper/live order submission remain disabled.
