# ATS Trading System

The ATS foundation loads its scoring weights and decision thresholds from
[`playbook/playbook_v3.yaml`](playbook/playbook_v3.yaml). The YAML file is the
single source of truth for both Layer 0 Market Opportunity and Layer 1
Discovery.

## Architecture and delivery workflow

- [Architecture baseline v1.1](docs/architecture/baseline-v1.1.md)
- [Architecture documentation index](docs/architecture/README.md)
- [ChatGPT + Notion + GitHub + VS Code workflow](docs/WORKFLOW.md)

The implementation workflow treats Notion as the product/backlog source of truth,
GitHub `main` as the accepted engineering record, VS Code as the local implementation
cockpit, and this ChatGPT project as the planning/review/orchestration layer. Material
architecture changes must update the repository architecture docs, Notion ADR/backlog,
and relevant tests before the change is considered complete.

## Requirements

- Python 3.12 or newer

## Install and run

From the repository root, install the project and development dependencies:

```powershell
python -m pip install -e ".[dev]"
```

Start the API with the single documented command:

```powershell
python -m src
```

The API listens on `http://127.0.0.1:8000`. Interactive API documentation is
available at `http://127.0.0.1:8000/docs`.

## Run with Docker

With Docker and the Compose plugin installed, create a local `.env` from
`.env.example`, replace both password placeholders, and start the stack:

```powershell
docker compose up --build
```

Compose binds the API and PostgreSQL ports to localhost and keeps PostgreSQL
data in the existing `postgres_data` named volume. `.env` is ignored by Git;
never commit it. Choose URL-safe passwords for the two roles (or percent-encode
reserved characters in the migration-owner password used in its URL).
For an already initialized database volume, keep `POSTGRES_USER` and
`POSTGRES_PASSWORD` matched to the existing migration-owner role; changing
these container initialization variables does not rotate credentials stored
in PostgreSQL. Set a new, private `ATS_APP_PASSWORD` for the runtime role.
`provision-runtime-role` idempotently provisions the non-superuser `ats_app`
role in the existing database without initializing or replacing the data
volume. The one-shot `migrate` service applies Alembic using
`ATS_MIGRATION_DATABASE_URL` and sets `ATS_RUNTIME_ROLE=ats_app` before
migrations run. The API starts only after migrations complete and connects
using `ats_app`, never the schema-owner credentials. To apply later migrations,
run `docker compose run --rm migrate`; do not remove the `postgres_data`
volume. Stop the stack with `Ctrl+C`, or run `docker compose stop`.

## Database foundation

The application has a SQLAlchemy engine, declarative base, and request-scoped
session dependency in `src/database.py`. Native runs default to a local SQLite
URL; Docker Compose configures PostgreSQL with the restricted runtime
`DATABASE_URL`. Migrations use the separate schema-owner URL, not the API
connection.

For local Python runs that need PostgreSQL, set `DATABASE_URL` in the process
environment. Do not commit `.env` files or production credentials; use your
deployment platform's secret store outside local development.

## ATS-006 domain contracts

Immutable Pydantic domain models live in [`src/domain/models.py`](src/domain/models.py).
They cover securities, exposures, evidence-backed score snapshots, versioned
trade plans, risk intents, orders, fills, position snapshots, trade results,
and journal entries. Decision, evidence, and lifecycle timestamps must be
timezone-aware UTC values. Plan prices must match the long/short stop and target
ordering, and planned stop-loss risk must fit the declared risk budget. A
`trade_id` identifies one trade across immutable plan versions; journal entries
can retain prior plans and validate their trade identity, version sequence, and
supersession links. Scores, decision mood, risk intent, and each plan version
remain unchanged when post-trade feedback is recorded.

Market regimes use the five Playbook classifications. Regimes 1-3 have fixed
risk multipliers of 1.00, 0.75, and 0.50. Regime 4 requires an explicit
long-side multiplier from 0 to 0.25, while regime 5 requires an authorized
special setup and otherwise produces no-trade behavior. Trade results can
record nonnegative MAE/MFE R magnitudes with their observation source when
available; post-trade adherence, mood, thesis, and lesson are optional journal
feedback.

Run their focused tests with:

```powershell
python -m pytest tests/test_domain_models.py -v
```

These models establish immutable domain-level contracts. Append-only journal
records and their PostgreSQL schema are implemented in ATS-004. ATS-005
provides the append-only source/evidence store and deterministic scoring
eligibility service described below. Portfolio-level risk limits remain future
work.

## ATS-004 PostgreSQL journal persistence

SQLAlchemy 2.x mappings live in `src/persistence/models.py`; the
`JournalRepository` stores immutable trade plans and score snapshots alongside
orders, fills, position snapshots, results, and versioned journal feedback.
JSONB payloads preserve the complete Pydantic contracts (including evidence,
decimal values, risk intent, and plan history), while relational keys and
constraints provide stable trade/plan identities and lifecycle references.
Journal and score-snapshot JSONB documents carry explicit schema versions;
read-time migrations support historical unversioned and version-1 through
version-3 snapshots without rewriting their decision-time records. PostgreSQL
triggers reject updates and deletes from every journal table. Journal edits are stored as new
revisions; PostgreSQL serializes writes per trade with a transaction-scoped
advisory lock, so the runtime role needs no row-update privilege. Callers own
the transaction and should write with
`with session.begin():`. Pass `expected_revision` to `save` when a stale
concurrent append must be rejected rather than automatically assigned the next
revision.

Compose provisions `ats_app` and applies pending schema revisions as the
owner before starting the API. For native migrations, provision the runtime
role first and set both `ATS_RUNTIME_ROLE=ats_app` and
`ATS_MIGRATION_DATABASE_URL` to the schema-owner connection before running.
Alembic intentionally fails when this explicit migration URL is missing;
neither `DATABASE_URL` nor the `alembic.ini` default is a migration fallback:

```powershell
python -m alembic upgrade head
```

The ATS-004 privilege migration requires `ATS_RUNTIME_ROLE` to name a
pre-created, non-superuser role distinct from the migration/schema owner. The
Compose `provision-runtime-role` step creates `ats_app` with login but no
superuser, database/role creation, inheritance, or RLS-bypass privileges, and
revokes membership in `POSTGRES_USER`. The migration grants the role schema
`USAGE` and table `SELECT`/`INSERT` only; the API uses the role, while
`ATS_MIGRATION_DATABASE_URL` is used only by Alembic. The runtime role must not
own protected tables or have membership in their owner roles. Do not grant it
`UPDATE`, `DELETE`, `TRUNCATE`, trigger administration, or schema DDL
privileges.

SQLite remains available for fast repository unit tests, but does not install
the PostgreSQL append-only triggers. Run PostgreSQL migration and integration
tests against the dedicated `ats_test` database by setting
`ATS_TEST_DATABASE_URL`; the test suite refuses any other database name,
creates and drops an isolated schema there, and uses a temporary non-owner
runtime role:

```powershell
$env:ATS_TEST_DATABASE_URL = "postgresql+psycopg://user:password@localhost:5432/ats_test"
python -m pytest tests/test_postgres_persistence.py -v
```

## ATS-005 source evidence and scoring eligibility

`SourceDocument` and `EvidenceItem` in `src/domain/evidence.py` are persisted as
immutable, versioned records by `EvidenceRepository`. A source version retains
its source identifier, publisher, subject, publication and observation times,
original-content reference or SHA-256 hash, and version metadata. Evidence
versions retain their exact source-document version, subject, Playbook factor,
optional inflection stage, excerpt or structured metric, confidence, status,
notes, and timestamps. Verified records also carry verifier identity, UTC
verification time, method, policy version, and notes; these values are fixed
within an evidence version, and a later review is recorded as a new version.
PostgreSQL migration `20261014_03` adds append-only triggers and grants the
configured runtime role `SELECT` and `INSERT` only.

Use `evaluate_scoring_eligibility` or `score_discovery_with_evidence` from
`src/evidence_verification.py` for qualitative Discovery factors. Eligibility
requires a resolvable, traceable source; publication and observation no later
than the score decision; matching subject and factor; sufficient excerpt or
structured metric; `VERIFIED` status; complete verification provenance; and a
verification timestamp no later than the score decision. A URL by itself is
not traceable.
Unverified or semantically conflicting evidence remains pending; confidence
does not override failed checks. Ineligible positive factor scores receive
zero allowed points, and the weighted score still uses the existing Playbook
weights and limits. The lower-level `score_discovery` function remains a
numeric weighting primitive; callers scoring qualitative factors must apply
the evidence-aware gate first.

Evidence references embedded in score snapshots retain both evidence ID and
version plus the source-document version ID and verification provenance.
Snapshot JSONB is schema-versioned (current version 4);
rescoring creates a new score snapshot, leaving decision-time snapshots and
source/evidence versions unchanged. Journal entries continue to persist the
score snapshot reference through ATS-004.

## Continuous integration

GitHub Actions runs Ruff and the complete pytest suite on every push and pull
request.

## API

- `GET /health` returns `{"status":"healthy"}`.
- `GET /playbook/version` returns the version declared by the YAML playbook.
- `POST /layer0/market-opportunity` accepts every market opportunity factor
  score (each from zero to the configured maximum) in a `factor_scores` object
  and returns its weighted score and `ACTIVE`, `WATCHLIST`, or `BACKGROUND`
  decision.
- `POST /layer1/discovery` accepts every discovery factor score in the same
  shape, plus optional `subject`, `decision_at`, and `evidence_by_factor`
  entries containing persisted evidence IDs and versions. The endpoint loads
  evidence from the store and awards positive factor points only when the
  deterministic eligibility checks pass. It returns the weighted score,
  eligibility reasons and exact evidence versions, and a `TIER_A`, `TIER_B`,
  `WATCHLIST`, or `REJECT` decision. Without evidence, positive factor scores
  contribute zero.

For example, supply `factor_scores` with every configured factor, a `subject`,
an ISO-8601 UTC `decision_at`, and `evidence_by_factor` like
`{"demand_acceleration":[{"evidence_id":"...","version":1}]}` for persisted
evidence. Market Opportunity continues to accept numeric factor scores
directly. Missing, unexpected, and out-of-range factor scores return a clear
HTTP 422 validation response.

The playbook is loaded and validated when the application starts. Invalid or
unreadable configuration prevents startup with a `PlaybookValidationError`
that identifies the file and validation failure.

## Tests

Run the full automated test suite from the repository root:

```powershell
python -m pytest -v
```

## Sprint 1 exit criterion

A clean checkout can install all dependencies, load and validate
`playbook_v3.yaml`, start the API, run the full automated test suite, and
return deterministic Layer 0 Market Opportunity and Layer 1 Discovery
decisions without manual configuration.

Acceptance checks:

- `python -m pytest -v` passes from a clean environment.
- Invalid playbook configuration fails fast with a clear validation error.
- `GET /health` returns healthy status.
- `GET /playbook/version` returns version `3.0`.
- `docker compose up --build` starts the API in a fresh container.
- GitHub Actions runs Ruff and pytest on pushes and pull requests.
- Layer 0 returns deterministic `ACTIVE / WATCHLIST / BACKGROUND`.
- Layer 1 returns deterministic `TIER_A / TIER_B / WATCHLIST / REJECT`.
- No scoring weights or thresholds are duplicated in Python; they are loaded
  from the YAML source of truth.
- Secrets are not committed to Git; local `.env` files are ignored.
- The application can be started with one documented command:
  `python -m src`.