# ATS-032 — Forward-Test Daily Scorecard

Status: Sprint 11 implementation.

## Goal

Build a reproducible daily scorecard for the real-time forward-test window without changing strategy logic or enabling LIVE execution.

## Evidence boundary

`src/forward_test_scorecard.py` consumes already persisted ATS evidence:

- ATS-031 `ForwardTestRunEvidence` for proposals, approvals/rejections, denials and fills;
- PAPER journal entries for closed trades, R, MAE/MFE and plan adherence;
- ATS-021 agent-evaluation snapshots for calibration metrics.

The scorecard is strict about run-type isolation. Any non-PAPER forward-test run or trade outcome supplied to the builder fails closed with `ValueError`; LIVE and REPLAY evidence cannot silently contaminate the forward-test report.

## Metrics

For the requested trading day the report includes:

- proposals, approvals, human rejections and blocked runs;
- fresh-risk denials and operational-guard denials;
- PAPER fills and trades closed that day;
- cumulative PAPER R and expectancy through that date;
- maximum drawdown in R;
- average MAE and MFE;
- observed plan-adherence rate;
- performance slices by setup and regime;
- latest point-in-time agent calibration for each experiment arm.

All cumulative metrics are calculated only from journal outcomes known by the requested date. Future journal outcomes and future agent-evaluation snapshots are excluded.

## Non-goals

ATS-032 does not submit orders, alter Playbook thresholds, promote a strategy, or grant LIVE authority. It is an evidence/reporting layer only.

## Validation

Focused tests cover the end-to-end metric set, setup/regime slicing, run-type isolation, drawdown, and future-evidence exclusion.
