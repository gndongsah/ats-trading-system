# ATS-011 — Filings / News Point-in-Time Ingestion

Status: Sprint 4 implementation for Architecture Baseline v1.1.

## Decision

ATS-011 extends the existing ATS-005 `SourceDocument` and `EvidenceRepository` boundary. It does not create a second filings/news store.

## Time model

Every newly ingested filing/news source version preserves four relevant clocks:

- `published_at` — when the publisher says the document/event was published;
- `available_at` — first time the normalized source was externally available to the collector;
- `ingested_at` — collector-owned time when ATS durably stored the source version;
- `observed_at` — legacy ATS-005 compatibility clock; new adapter records it equal to first availability.

Legacy source documents that predate ATS-011 remain readable. For them, `observed_at` is the fallback availability/ingestion clock.

## Identity and corrections

`source_id` is the stable logical source identity and `version` is monotonic. Corrections/restatements append a new immutable version; `correction_of_version` may identify the prior version.

The JSON adapter derives deterministic source-version UUIDs from source type, source ID and version so retrying the same normalized provider record does not create a second identity.

Same source/version with identical content is idempotent. Same source/version with conflicting content fails closed with `PersistenceConflict`.

## Persistence

Alembic revision `20261026_09` projects source type, subject and the point-in-time clocks into queryable relational columns on `ats_source_documents`.

The migration backfills existing rows from their immutable JSON documents. Because ATS-005 already has an append-only UPDATE trigger, the migration owner disables only that trigger inside the migration transaction for the projection backfill and immediately re-enables it.

Database constraints enforce:

- `available_at >= published_at`;
- `ingested_at >= available_at`;
- existing source version monotonicity and append-only history.

`ix_ats_source_replay` supports source-type/subject/as-of filtering for ATS-012.

## As-of reads

`EvidenceRepository.list_sources_as_of` applies all three point-in-time cutoffs before choosing the latest source version. A correction that was available but not yet ingested at the requested timestamp is invisible; the prior version remains selected.

## Research safety

`evaluate_scoring_eligibility` rejects sources whose publication, availability, ingestion, observation or verification occurred after the decision timestamp.

This preserves the Architecture v1.1 rule that research decisions consume persisted point-in-time evidence rather than current web state.

## Adapter

`JsonResearchDocumentAdapter` is the first normalized adapter and supports synthetic/regression `regulatory_filing` and `news` documents. It never supplies ingestion time; the repository collector clock owns that timestamp.

ATS-011 does not connect to a live SEC/news provider. ATS-012 will consume these persisted records together with ATS-010 market data in the unified historical replay/stale-data gate.
