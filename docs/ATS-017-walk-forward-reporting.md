# ATS-017 — Walk-Forward Backtest Reporting

Status: final Sprint 6 deterministic reporting layer.

## Goal

ATS-017 summarizes accepted ATS-016 REPLAY outcomes without changing strategy logic, risk policy or execution behavior.

## Evidence-domain isolation

`src/outcome_analytics.py` remains LIVE-only by default under ATS-028.

ATS-017 does not weaken that contract. `src/backtest_reporting.py` is a separate reporting surface that accepts `BacktestTradeOutcome` records and requires every embedded `TradeResult.run_type` to be `REPLAY`.

The reporter also checks that the outcome's realized P&L, R multiple and MAE/MFE values still match the accepted ATS-016 result contract. Duplicate deterministic request identities are rejected.

## Deterministic ordering

All calculations order trades by:

1. `decision_at`;
2. deterministic `request_digest` as the tie-breaker.

This makes drawdown and output identity independent of caller input order.

## Performance metrics

Every report bucket includes:

- trade count;
- wins, losses and breakeven trades;
- win rate;
- cumulative R;
- expectancy / average R;
- maximum drawdown in R;
- profit factor when at least one losing R exists;
- MAE observation count, average MAE and median MAE;
- MFE observation count, average MFE and median MFE.

Missing MAE/MFE observations are excluded from the corresponding excursion statistic and are not silently converted to zero. Coverage counts make the denominator explicit.

Profit factor is undefined (`None`) when there is no losing R denominator.

## Maximum drawdown

Maximum drawdown is calculated from the cumulative R curve in deterministic decision order.

The curve starts at 0. At each trade:

- add the trade's accepted rendered R multiple;
- update the running peak;
- calculate `peak - cumulative R`;
- retain the largest drawdown.

Drawdown is therefore expressed in R, not dollars.

## Numeric convention

Cumulative R is an exact Decimal sum of the accepted ATS-016 `TradeResult.r_multiple` values.

Division-derived metrics such as expectancy, win rate, average excursions and profit factor use an explicit Decimal context and are rendered to 12 decimal places using `ROUND_HALF_EVEN`.

Median excursion values are deterministic; even-sized samples use the arithmetic mean of the two middle observations.

## Grouping

ATS-017 reports:

- overall;
- by ATS-015 trigger/setup type;
- by ATS-013 regime;
- by setup × regime.

ATS-016 already carries those dimensions in each outcome, so ATS-017 does not reconstruct historical inputs.

## Walk-forward windows

`WalkForwardPlan` requires one or more explicit UTC `[start, end)` windows.

Windows must:

- have unique labels;
- be ordered by start time;
- not overlap.

Trades are assigned by `decision_at`, not close time. The start boundary is inclusive and the end boundary is exclusive.

When a walk-forward plan is supplied, every reported outcome must belong to exactly one window. This prevents trades from disappearing silently through date gaps or boundary errors.

Empty windows are retained and reported with zero trade count.

ATS-017 intentionally does not invent monthly, quarterly or other default window lengths. It also does not perform parameter optimization or strategy tuning; those would require a separately specified methodology.

## Safety boundary

ATS-017 does not:

- call an LLM or research agent;
- fetch live/current data;
- alter Setup Score or regime classification;
- alter trigger rules;
- calculate new position sizes;
- authorize Layer 3;
- create orders;
- submit to a broker.

It is a deterministic measurement layer over already accepted REPLAY outcomes.
