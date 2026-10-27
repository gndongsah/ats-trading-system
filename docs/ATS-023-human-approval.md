# ATS-023 — Proposal and human approval workflow

ATS-023 adds the human gate between an evaluated trade proposal and the PAPER-only broker abstraction introduced by ATS-022.

## Proposal contents

A `TradeProposal` binds one immutable `TradePlan`, one `OrderIntent`, and one risk snapshot. Its display summary exposes the acceptance-criteria fields:

- market-opportunity, discovery, setup and regime scores;
- entry, stop and target levels;
- requested size;
- stop-risk budget;
- portfolio heat in R;
- current risk decision.

## Human approval

`HumanApproval` records:

- proposal ID;
- a SHA-256 fingerprint of the exact immutable proposal;
- APPROVE or REJECT;
- approver identity;
- UTC decision time.

Approval of one proposal cannot authorize a changed proposal.

## Fresh-risk gate

`ProposalExecutionGate` checks approval before invoking the configured risk checker. Immediately before PAPER execution it requires:

1. approval belongs to the exact proposal;
2. decision is APPROVE;
3. risk was rechecked after human approval;
4. the fresh risk result belongs to the same plan;
5. the fresh decision is not DENY;
6. the fresh admissible quantity is at least the human-approved quantity;
7. current PAPER execution uses whole shares.

A RESIZE below the approved size fails closed rather than silently changing a human-approved proposal. A new proposal/approval is required for a smaller size.

## Execution boundary

Only after those gates pass does ATS-023 construct a `BrokerSubmissionRequest` with `RunType.PAPER` and call the broker-neutral `ExecutionService`.

This story does not enable LIVE execution, add broker credentials, weaken Layer 3's execution-disabled invariant, or permit AI/autonomous approval.
