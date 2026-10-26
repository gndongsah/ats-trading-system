# ATS-028 — LIVE / PAPER / REPLAY Result Isolation

Status: implementation slice for Architecture Baseline v1.1 / ADR-011.

## Invariant

Outcome evidence domains are separate by default:

- LIVE outcomes may contribute to live validation evidence.
- PAPER outcomes do not contribute to live validation evidence.
- REPLAY outcomes do not contribute to live validation evidence.
- Shared analytics may combine domains only when the caller explicitly selects them.

## Shared run identity

`src/run_type.py` defines the canonical `RunType` enum: `LIVE`, `PAPER`, and `REPLAY`.

`PortfolioRunType` remains a compatibility alias to the same enum so ATS-027 callers do not fork the type system.

## Trade outcomes

`TradeResult.run_type` is persisted with every new trade result. The compatibility default is `PAPER`, which is deliberately non-live: an older caller that has not yet been upgraded cannot silently manufacture live validation evidence.

Journal schema v5 migrates pre-v5 journal documents that contain a result but no run type to explicit `PAPER` at read time. The stored append-only historical document is not rewritten.

## Validation boundary

`eligible_for_live_validation` and `live_validation_results` accept only `LIVE` outcomes. They are the current evidence-selection boundary; ATS-028 does not implement the future validation-stage promotion engine itself.

## Analytics boundary

`summarize_outcomes` defaults to a LIVE-only scope. A caller must construct an explicit `OutcomeScope` to include PAPER or REPLAY data.

This prevents a generic analytics call from silently changing live expectancy or sample counts when simulation data is added.

## Safety

ATS-028 does not enable broker execution, change Layer 3 authorization, or grant autonomy. It only labels and isolates outcome evidence.

## Acceptance evidence

Tests cover:

- legacy/default outcomes are PAPER, not LIVE;
- LIVE-only validation filtering;
- default analytics exclude PAPER and REPLAY;
- mixed-mode analytics require explicit scope;
- persisted TradeResult JSON contains run type;
- journal schema v4 data migrates missing run type to PAPER without rewriting history.
