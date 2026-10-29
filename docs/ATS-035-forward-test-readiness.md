# ATS-035 — Forward-Test Readiness Gate

## Goal

Evaluate the accepted ATS-034 campaign report against the Sprint 12 evidence gate without changing strategy parameters or enabling LIVE execution.

## Gate

A campaign can return **PASS** only when all of the following are true:

- at least 8 weeks of the campaign have elapsed;
- every setup in the frozen campaign scope has at least 20 closed PAPER trades;
- every setup has positive expectancy after captured execution costs;
- every setup has plan adherence strictly greater than 80%;
- zero explicit risk-gate breach events are present;
- zero unresolved evidence-integrity exceptions are present.

## Decisions

- **PASS** — the forward-test evidence gate is satisfied.
- **HOLD** — criteria are not yet satisfied but the active campaign can still accumulate evidence.
- **FAIL** — a hard safety/integrity breach exists, or the campaign ends without satisfying the gate.

A normal risk denial is not a risk-gate breach. A denial demonstrates that the gate blocked the proposal as designed. ATS-035 therefore uses the explicit `risk_gate_breach` evidence flag added to forward-test run evidence rather than inferring a breach from blocked risk reasons.

## Evidence

The result includes deterministic references to the campaign identity, campaign report digest and setup-level report anchors.

## Safety boundary

PASS does not grant LIVE authority, change sizing, waive human approval, or override ATS-009 broker-backed acceptance. The evaluation always emits `live_approval_granted=false` and `ats009_override=false`.

## Real campaign status

Implementation acceptance is separate from campaign evidence acceptance. Synthetic tests prove gate behavior; they do not prove that an 8–12 week real PAPER campaign has completed.
