# ATS-025 — Operational execution guards

ATS-025 adds fail-closed operational controls immediately before the PAPER broker boundary.

## Controls

The `OperationalGuard` enforces:

- **kill switch** — when engaged, all new PAPER submissions are denied;
- **maximum order quantity** — requested quantity may not exceed the configured cap;
- **maximum order notional** — quantity × reference price may not exceed the configured cap;
- **daily order-count cap** — new submissions stop when the configured daily count is reached;
- **market-hours guard** — new submissions require an open XNYS session;
- **stale-data guard** — the reference market-data observation must not be in the future and may not exceed the configured maximum age.

The guard evaluation timestamp must equal the broker submission timestamp so controls cannot be evaluated earlier and reused later.

## Integration

`ProposalExecutionGate` now requires an `OperationalGuard`. The order of checks is:

1. exact human approval;
2. fresh risk recheck;
3. quantity/risk compatibility;
4. operational guards;
5. broker-neutral PAPER submission.

This preserves the ATS-023 approval boundary and ensures operational controls cannot be bypassed by an otherwise valid proposal.

## Failure behavior

Every failed control raises `OperationalGuardViolation` before the `ExecutionService` is called. Tests cover kill-switch activation, quantity/notional caps, daily order limits, closed markets, future timestamps, stale data, and the exact freshness boundary.

## Safety boundary

ATS-025 does not add a LIVE broker adapter, credentials, network order endpoint, or autonomous approval. `BrokerSubmissionRequest` remains PAPER-only.
