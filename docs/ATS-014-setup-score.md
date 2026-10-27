# ATS-014 — Deterministic Setup Score

Status: Sprint 5 Layer 2 implementation.

## Playbook source

Trading Strategy Playbook v3 defines the Setup Score as 0–100 with five weighted components:

- trend alignment — 25
- relative strength vs SPY — 25
- structure quality / not extended — 25
- liquidity and volume confirmation — 15
- distance-to-earnings safety — 10

The score bands are:

- 70–100 — actionable at planned size;
- 50–69 — actionable only at unproven size;
- below 50 — no trade.

The playbook does not define a fractional sub-formula inside each weighted component. ATS-014 therefore accepts deterministic component point values bounded by their configured maxima and sums them exactly. It does not invent hidden scoring anchors.

## Configuration

`playbook/playbook_v3.yaml` now contains a versioned `setup_score` section.

Playbook validation requires:

- exactly the five Setup Score factors;
- exact weights 25 / 25 / 25 / 15 / 10;
- total max score exactly 100;
- exact actionability thresholds 70 and 50;
- the ordinary swing-long screen limits.

## Runtime contracts

`SetupScoreComponents` is immutable and contains the five exact point contributions.

`SetupScoreResult` records:

- exact score;
- score-only band;
- final actionability;
- component points;
- failed execution-screen gates;
- accepted ATS-013 regime classification;
- Setup Score rule version;
- playbook version.

The setup timestamp must exactly match the ATS-013 regime-classification timestamp.

## Ordinary swing-long execution screen

For swing-long candidates, ATS-014 also enforces the explicit Playbook execution screen:

- price above the 50DMA;
- 50DMA above 200DMA;
- outperforming SPY over 3 months;
- outperforming SPY over 6 months;
- average daily volume strictly above 500,000 shares;
- no more than 10% extended from the 50DMA;
- earnings more than 10 days away unless the trade is explicitly an earnings plan.

A screen failure forces final actionability to `NO_TRADE` even when the numeric Setup Score is 70+.

Exactly 500,000 shares fails the "above 500k" rule. Exactly 10% extension passes the "not more than 10%" rule. Exactly 10 days to earnings fails the ordinary swing rule; day 11 passes.

If the earnings date is unknown, the ordinary swing-long screen fails closed. An explicitly flagged earnings plan waives only the ordinary earnings-distance gate.

## Scope boundary

The Playbook text explicitly describes the above trend/RS screen for longs. ATS-014 therefore applies this hard screen only to ordinary swing-long candidates and does not manufacture a symmetric short-side rule.

ATS-015 is responsible for style-specific trigger, invalidation and actionability mechanics such as breakout retest, 21-EMA pullback and breakdown short.

ATS-014 does not:

- generate entry levels;
- generate stop/invalidation levels;
- implement trigger detection;
- fetch live market data;
- alter Layer 3 risk math;
- enable broker execution.
