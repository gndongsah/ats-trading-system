# ATS-029 — Human approval / autonomy policy boundary

ATS-029 separates execution authorization from risk authorization.

## Core rule

A risk result of ALLOW or RESIZE is necessary but is never sufficient to submit an order. Before PAPER execution, the proposal must also carry an `ExecutionAuthorization` bound to the exact immutable proposal fingerprint.

## Human path

`HumanApproval` remains the user-facing approval record. An APPROVE decision can be converted into an `ExecutionAuthorization` with source `HUMAN`. A REJECT decision cannot mint execution authorization.

## Future autonomy path

`AutonomyPolicy` models a future policy-controlled PAPER autonomy capability. The MVP default is disabled and requires human approval. Policy authorization is valid only when all of the following are true:

- the policy is explicitly enabled;
- the permitted level is `PAPER_POLICY`;
- the run type remains PAPER;
- the authorization carries the exact current policy ID/version;
- the authorization is bound to the exact proposal fingerprint.

No policy path enables LIVE execution.

## Execution ordering

The proposal gate now requires:

1. execution authorization bound to the proposal;
2. fresh deterministic risk recheck after that authorization;
3. requested size within the fresh risk result;
4. ATS-025 operational guard checks;
5. broker-neutral PAPER submission.

This makes risk authorization and execution authorization independent gates.
