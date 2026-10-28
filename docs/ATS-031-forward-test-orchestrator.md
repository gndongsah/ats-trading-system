# ATS-031 — Real-time forward-test orchestrator

ATS-031 starts the Forward Test release without expanding execution authority.

## Run contract

`ForwardTestRunInput` binds one immutable proposal and human approval to one point-in-time ATS-010 market observation. The market observation must match the proposal symbol and must have event, availability and ingestion timestamps no later than the run `as_of` timestamp.

The reference price comes from the completed bar carried by that observation; the orchestrator does not fetch an untraceable current price.

## Accepted pipeline

Each run follows the existing MVP path:

1. validate point-in-time market input;
2. verify the Playbook has not changed from the orchestrator's frozen version and SHA-256 digest;
3. convert explicit human approval to ATS-029 execution authorization;
4. execute ATS-023 fresh risk validation;
5. execute ATS-025 operational guards;
6. submit through ATS-022 broker-neutral PAPER execution;
7. when filled, invoke ATS-024 automatic journal capture;
8. persist a `ForwardTestRunEvidence` record.

Rejected approval, risk/operational denial, stale market data and Playbook drift are recorded as `BLOCKED` forward-test runs rather than disappearing from the dataset.

## Policy freeze

The orchestrator freezes both Playbook version and a canonical SHA-256 digest when the forward-test service starts. Any in-process parameter drift blocks the run before risk or broker submission. Strategy changes therefore require a new forward-test window/service instance and create a clean evidence boundary.

## Durable evidence

`ats_forward_test_runs` records the run ID, proposal ID, PAPER run type, status, as-of time, Playbook version/digest and the complete typed evidence payload. Evidence includes market source/revision/timestamps, approval identity, broker adapter/submission identity, journal revision and block reason when applicable.

## Safety boundary

ATS-031 is PAPER-only. It uses human approval as the only manual execution step, does not enable ATS-029 policy autonomy, and does not add a LIVE broker path.
