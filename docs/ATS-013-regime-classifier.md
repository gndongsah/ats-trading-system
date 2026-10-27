# ATS-013 — Market Regime Classifier

Status: Sprint 5 implementation for Layer 2.

## Source of truth

Trading Strategy Playbook v3 defines five market regimes:

1. Risk On — SPY above the 50DMA, the 50DMA above the 200DMA, both moving averages rising, healthy breadth, advancers leading and new highs expanding.
2. Risk On / Narrow — indexes trending up while breadth deteriorates and leadership is concentrated.
3. Neutral / Chop — indexes sideways around the 50DMA, breadth mixed and breakouts failing both ways.
4. Risk Off — SPY below the 50DMA, breadth deteriorating and new lows expanding.
5. Capitulation / Reversal — breadth washed out, volatility spiking and early reclaim attempts.

The playbook does not currently specify numerical breadth thresholds. ATS-013 therefore does not invent them.

## Configuration

`playbook/playbook_v3.yaml` now contains a `regime` section with:

- a version;
- explicit rule precedence;
- one rule for each regime 1–5;
- the normalized boolean signals required by each rule.

Playbook loading fails if:

- any regime 1–5 is missing or duplicated;
- precedence does not list every rule exactly once;
- a rule has no required signals;
- a rule references an unknown signal.

## Runtime contract

`RegimeInputs` is immutable and carries the normalized Layer 2 signals at one UTC timestamp.

It rejects contradictory normalized states such as:

- SPY simultaneously above and below the 50DMA;
- multiple breadth-state labels at once;
- an index simultaneously labelled trending-up and sideways-around-50DMA.

`classify_market_regime` evaluates rules in configured precedence order. The first complete rule match wins.

If no configured rule matches, the classifier raises `RegimeUnclassified`; it does not guess Neutral/Chop.

`RegimeClassification` records:

- as-of timestamp;
- regime;
- matched rule;
- regime-rule version;
- playbook version;
- required matched signals;
- all active signals;
- the fixed risk multiplier for Regimes 1–3.

Regimes 4 and 5 intentionally return no fixed multiplier because their sizing policy remains conditional in the existing domain model.

## Numeric feature boundary

ATS-013 starts at normalized market signals. The conversion of raw breadth series into labels such as `breadth_healthy`, `breadth_deteriorating`, or `breadth_washed_out` remains a separate feature-definition decision because Playbook v3 has not assigned numeric thresholds yet.

This preserves the playbook-as-code rule: code may enforce written strategy rules, but it must not silently manufacture strategy thresholds.

## Out of scope

ATS-013 does not implement:

- Setup Score (ATS-014);
- swing triggers (ATS-015);
- live market-data acquisition;
- broker execution.
