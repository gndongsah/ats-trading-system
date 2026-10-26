# ATS-012 — Unified Historical Replay & Stale-Data Proposal Gate

Status: final Sprint 4 implementation slice.

## Goal

One historical decision timestamp must resolve all required market and research inputs from persisted point-in-time data before proposal creation can proceed.

## Unified replay

`HistoricalReplayService` composes:

- ATS-010 `MarketDataStore.replay` for market streams;
- ATS-011 `EvidenceRepository.list_sources_as_of` for filings/news.

The caller supplies an explicit UTC `as_of` plus explicit market and research requirements. Empty requirement sets are rejected.

Market replay keeps ATS-010 semantics: event time, availability time and ingestion time are bounded before revision selection; missing or stale streams fail closed.

Research replay keeps ATS-011 semantics: publication, availability and ingestion must all be no later than `as_of` before a source version is eligible. The latest then-known revision of each logical source is selected before ATS-012 applies research freshness.

## Research freshness

`ResearchReplayRequirement.max_publication_age` defines how old the published research may be at the decision timestamp. This is intentionally separate from point-in-time knowledge:

- publication age answers whether the content is stale for the decision;
- availability/ingestion clocks answer whether ATS could actually have known it.

`min_sources` is an acceptance threshold. Once the threshold passes, the replay context retains all fresh then-known source documents in that required domain.

## Historical context

`HistoricalReplayContext` is immutable and contains:

- the exact `as_of` timestamp;
- the ATS-010 `ReplaySnapshot`;
- deterministic research selections.

Its SHA-256 digest is derived from the complete normalized context. The same persisted data and same replay requirements produce the same context/digest.

## Proposal gate

`HistoricalProposalGate.create_proposal` calls the proposal factory only after unified replay succeeds. If any required market or research input is missing or stale, `HistoricalReplayUnavailable` is raised and the factory is never invoked.

This is the Sprint 4 proposal-creation safety boundary. The repository does not yet have a production trade-proposal service, so ATS-012 does not invent a second planning model. Future proposal construction should be placed behind this gate.

## Correction behavior

Corrections remain append-only in their source systems. Replaying a timestamp before a correction's availability/ingestion continues to resolve the earlier version. Replaying after the correction becomes known may resolve the newer version.

## Acceptance invariant

> Replaying yesterday must not reveal information learned today.

Tests prove the invariant simultaneously across market-data revisions and filing corrections, plus missing/stale denial and proposal-factory non-invocation.

## Safety

ATS-012:

- performs no live external fetches;
- changes no Layer 3 risk arithmetic;
- enables no broker submission;
- grants no autonomy;
- uses only persisted point-in-time inputs.
