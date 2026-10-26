# ATS-027 — Portfolio State & Reconciliation Contract

Status: implementation slice for Architecture Baseline v1.1.

## Decision

The existing Sprint 3 `PortfolioSnapshot` model already carries the normalized
positions/orders/equity inputs used by deterministic portfolio-risk calculations.
ATS-027 evolves that contract into `PortfolioState` rather than introducing a
second competing account-state model.

`PortfolioSnapshot` remains a compatibility alias during migration.

## Portfolio State

The contract carries:

- account identity and monotonic state version;
- policy and taxonomy versions;
- account equity;
- optional cash and buying power;
- normalized positions and open orders;
- explicit run type: LIVE, PAPER, or REPLAY;
- source/source-version plus observed and ingestion timestamps;
- value-level provenance for equity, cash and buying power;
- optional reported open-R, theme and factor exposure summaries;
- daily and weekly realized-R context;
- validation stage;
- completeness and ATS-managed-exposure exclusion flags.

The supplemental aggregate values are context, not an authority over the risk
engine. Layer 3 still recomputes heat and concentration deterministically from
positions, pending orders, active reservations, stops and the configured
taxonomy.

## Freshness

When `observed_at` is available, freshness is measured from that source
observation time. Legacy records fall back to `as_of`. The configured
`PortfolioRiskPolicy.snapshot_max_age` remains the deterministic maximum age.

## Layer 3 boundary

`Layer3AuthorizationService.publish_state` is the canonical persistence
boundary. It accepts only the normalized Portfolio State after the configured
verifier validates the authenticated account and state.

The existing `publish_snapshot` method remains as a compatibility wrapper.
The PostgreSQL table remains `ats_portfolio_snapshots` for migration stability;
its JSON document now supports the richer Portfolio State fields.

## Broker independence

Broker, paper and replay adapters are responsible for constructing and verifying
Portfolio State. Core Layer 3 code does not read broker-specific payloads or
schemas.

ATS-027 does not enable broker execution. The existing unconditional execution
guard remains in force.

## Relationship to ATS-028

ATS-027 defines the run-type field and carries it durably. ATS-028 is responsible
for enforcing LIVE / PAPER / REPLAY isolation across results, validation
promotion and analytics.

## Acceptance tests

Focused tests cover:

- legacy `PortfolioSnapshot` import compatibility;
- typed LIVE/PAPER/REPLAY run mode;
- equity/cash/buying-power provenance;
- UTC and ingestion chronology validation;
- source-observation freshness;
- deterministic risk denial for stale Portfolio State;
- Layer 3 persistence and round-trip of the richer state contract.
