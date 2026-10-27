# ATS-021 — Agent evaluation metrics

ATS-021 turns persisted research-agent predictions and outcomes into reproducible evaluation snapshots.

## Metrics

For a configured positive research label such as `review`, each arm stores:

- true positives, false positives, false negatives, true negatives;
- precision;
- recall;
- F1 score;
- accuracy;
- calibration observations;
- mean confidence;
- Brier score;
- calibration gap;
- RMSE.

## Point-in-time rule

Every evaluation has an `as_of` timestamp. Predictions observed after that timestamp and outcomes resolved after that timestamp are excluded. This preserves the ATS replay rule: an evaluation of yesterday cannot learn an outcome resolved today.

## A/B comparison

The same experiment can be evaluated for both:

- `llm_only`;
- `llm_jev`.

Both arms use the same positive label and cutoff so precision, recall and calibration can be compared without changing the evaluation window.

## Persistence

Evaluation summaries are append-only snapshots in `ats_agent_evaluations`. Raw predictions and outcomes remain in `ats_triage_experiments`; ATS-021 does not overwrite them.

## Safety boundary

These metrics evaluate research judgments only. They do not create TradePlans, Layer 3 authorization, OrderIntents, broker requests, or live trading authority.
