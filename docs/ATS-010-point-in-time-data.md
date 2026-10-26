# ATS-010 — Point-in-time data and replay

First implementation for Sprint 4, based on Sprint 3 commit
`699bd7586e8d27638d37b4efa73a3dbeddfd689e`. The target branch is
`feature/sprint-3-risk-engine`; `main` does not yet contain that integration.

## Data contract

`JsonMarketDataAdapter` v1 accepts a normalized JSON array of completed,
unadjusted bars (`1m` or `1d`) and USD equity reference records. The synthetic
fixture is `tests/fixtures/market_data_v1.json`. This is an offline file adapter,
not a live vendor/API integration. Provider-specific credentials, entitlements,
rate limiting, corporate actions, exchange gap calendars and backfill are later
work. Never use today's adjusted prices as yesterday's unadjusted observations.

Each immutable revision has three timezone-aware UTC timestamps:

| Field | Meaning |
| --- | --- |
| `event_time` | Completed bar end; effective time for reference data |
| `available_at` | When the provider published/made this revision available |
| `ingested_at` | When this collector first ingested this revision |

The collector assigns ingestion time; the JSON adapter rejects ingestion-time
fields. If publication time is unknown, upstream normalization must use the
collection time conservatively. Do not backdate ingestion to manufacture
historical coverage. Injected collector clocks are solely for synthetic tests.

Identity is `(source, symbol, stream, event_time, revision)`. Providers must map
their revisions to increasing integers per logical event. Corrections insert a
new revision; older data is never updated. Exact duplicates retain the original
ingestion time. Different content for the same identity raises `RevisionConflict`.
Concurrent duplicate delivery uses database conflict handling, not read-then-write.
Transactions belong to the caller; roll back a failed batch.

The PostgreSQL migration adds append-only UPDATE, DELETE and TRUNCATE triggers
and grants the runtime role SELECT/INSERT only. SQLite is a development replay
test backend; production persistence and simulation risk require PostgreSQL.

## Replay semantics

Provide an explicit source, symbol, stream and maximum event age for each input.
All three clocks must be `<= as_of` before selection. Replay then selects the
latest event and greatest eligible revision for each required stream. Out-of-order
delivery of an older revision cannot replace a newer revision. The cutoff is
inclusive; maximum age is inclusive. Missing or stale inputs raise
`ReplayUnavailable`; no forward filling from the future or silent source fallback.
Corrections cannot refresh old event ages. This first slice returns the latest
required snapshot, not a full bar-window or exchange-calendar gap audit.

The replay snapshot contains each original event, availability and ingestion
timestamp and revision, plus a deterministic digest. Replaying yesterday after
today's correction must return the exact same snapshot and digest. Previously
published records ingested today are also excluded from yesterday's view.

```python
from datetime import UTC, datetime, timedelta
from pathlib import Path
from src.market_data import JsonMarketDataAdapter, MarketDataStore, ReplayRequirement

events = JsonMarketDataAdapter().parse(
    Path("tests/fixtures/market_data_v1.json").read_text()
)
# `factory` is your configured SQLAlchemy sessionmaker; run Alembic to head first.
with factory.begin() as session:
    collector = MarketDataStore(session)
    for event in events:
        collector.ingest(event)  # stamps actual ingestion time, never historical time
with factory() as session:
    snapshot = MarketDataStore(session).replay(
        as_of=datetime.now(UTC),
        requirements=(
            ReplayRequirement(
                source="synthetic-json-v1",
                symbol="ABC",
                stream="bar:1m",
                max_age=timedelta(minutes=1),
            ),
        ),
    )  # old synthetic fixtures correctly deny as stale
```

## Simulation risk integration

Construct `ReplaySimulationRiskService` with a store-produced `market_snapshot`,
the same replay requirements, and the existing simulation service parameters.
Its harness clock and portfolio snapshot must equal the replay instant. The
service re-reads the store on every authorization, compares the bound snapshot,
requires exactly one bar and active reference record per candidate/held/pending
symbol, and requires the candidate entry to equal the replayed close. This first
slice models entry at that close; alternative fill models need separate policy.
Future-dated plan/score timestamps are denied. Missing, stale, replaced or
mismatched inputs produce a zero-quantity denial before reservation, including
idempotent requests. Risk reservation events include the full replay basis and
digest atomically with the existing simulation and loss-state evidence.

NLV, buying power, stops, taxonomy and strategy scores remain harness-owned
simulation inputs. This slice does not reconstruct them from market history or
establish that their source evidence is point-in-time. It is not a complete
look-ahead-free strategy backtest. Existing ATS-009 loss locks and execution
guards still apply; broker/live execution remains disabled.

## Validation

- `python -m pytest tests/test_market_data.py -q` covers yesterday/today invariance,
  all three future clocks, duplicates/conflicts, missing reference data, stale
  denial, out-of-order revisions, source isolation and persisted reopen.
- `tests/test_market_data_postgres.py` exercises the actual migration, concurrent
  duplicates, historical invariance and append-only/runtime privilege guards.
- `tests/test_replay_risk.py` feeds replay into the existing receipted simulation
  risk pipeline, verifies provenance, denial without reservation and rollback.
- Run the PostgreSQL tests and full suite with the existing dedicated
  `ATS_TEST_DATABASE_URL` and `--fail-on-skip`, as CI already does.

Local workspace: Ruff lint/format and replay unit tests pass. PostgreSQL-backed
tests require CI because this host cannot create/switch to a PostgreSQL service
user. ATS-009 broker evidence remains blocked independently of this slice.
