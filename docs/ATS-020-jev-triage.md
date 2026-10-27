# ATS-020 — Jev triage experiment

ATS-020 implements a bounded A/B evaluation of **LLM-only** versus **LLM + Jev** triage.

## Experiment contract

Each prediction stores:

- experiment and case identifiers;
- experiment arm;
- subject and predicted class;
- probability/confidence;
- model name and returned model version;
- observation time;
- optional latency and token usage;
- metadata needed for reproducibility.

Ground-truth outcomes are attached later and calibration is computed from the persisted probability and correctness outcome. The initial metrics are accuracy, mean confidence, Brier score, calibration gap and RMSE.

## TypeSafe/Jev adapter

The adapter targets TypeSafe System One and uses a probabilistic boolean (`noul`) question for the first triage experiment.

Runtime configuration:

- `TYPESAFE_API_KEY` — required only for live Jev calls.
- `TYPESAFE_JEV_MODEL` — optional; defaults to `jev-latest`.

The API key is never persisted in experiment records or source control. The response model name is recorded so model alias movement remains visible in calibration data.

## Safety boundary

ATS-020 is evaluation infrastructure only. Jev does not create TradePlans, OrderIntents, Layer 3 authorizations, or broker actions. A Jev probability may inform research triage, but deterministic ATS policy remains authoritative.

The experiment should graduate only after enough labeled cases exist to compare the two arms on accuracy, calibration, latency and cost.
