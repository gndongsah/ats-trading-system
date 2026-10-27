# ATS-022 — Broker abstraction and deterministic paper adapter

ATS-022 introduces the first broker-neutral execution boundary for Sprint 9.

## Broker-neutral contract

Core application code depends on the `BrokerGateway` Protocol and the `ExecutionService`. The contract accepts a typed `BrokerSubmissionRequest` and returns a typed `BrokerSubmissionReceipt`.

The request contains only ATS-owned fields:

- idempotency key;
- explicit run type;
- symbol;
- existing `OrderIntent`;
- reference price used by the paper simulator;
- UTC submission time.

No Webull, Alpaca, IBKR, or other provider payload is exposed to core modules.

## Paper adapter

`PaperBrokerAdapter` is deterministic and in-memory. It has:

- no network client;
- no credentials;
- no broker SDK;
- no live endpoint;
- idempotent same-key replay;
- fail-closed key collision handling;
- deterministic PAPER receipts;
- simple MARKET, LIMIT, STOP, and STOP_LIMIT fill rules.

The paper adapter is a simulation boundary, not broker evidence. It does not prove provider lifecycle semantics, execution quality, or real account connectivity.

## Safety boundary

ATS-022 accepts only `RunType.PAPER`. LIVE requests fail validation before reaching the adapter.

This story does not alter Layer 3's existing `execution_enabled=False` invariant and does not create a live execution path. ATS-023 remains responsible for the proposal and human-approval boundary before paper execution is integrated end to end.
