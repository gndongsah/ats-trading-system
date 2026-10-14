# ATS Trading System

The ATS foundation loads its scoring weights and decision thresholds from
[`playbook/playbook_v3.yaml`](playbook/playbook_v3.yaml). The YAML file is the
single source of truth for both Layer 0 Market Opportunity and Layer 1
Discovery.

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

With Docker and the Compose plugin installed, build the image and start the API
with one command:

```powershell
docker compose up --build
```

The API is available at `http://127.0.0.1:8000`; Compose binds it to localhost
only and starts PostgreSQL with a persistent named volume. For local
configuration, copy `.env.example` to `.env` and replace the development
password. `.env` is ignored by Git. Keep the `DATABASE_URL` credentials in sync
with the PostgreSQL variables; URL-encode reserved characters in the password.
The example/default credentials are for local development only. Stop the stack
with `Ctrl+C`, or run `docker compose down` in another terminal.

## Database foundation

The application has a SQLAlchemy engine, declarative base, and request-scoped
session dependency in `src/database.py`. Native runs default to a local SQLite
URL; Docker Compose configures PostgreSQL using `DATABASE_URL`. The foundation
does not yet define application tables or run migrations.

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

These models establish domain-level contracts only; persistent append-only
records, Alembic migrations, evidence verification policy, and portfolio-level
risk limits remain future implementation steps.

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
  shape and returns its weighted score and `TIER_A`, `TIER_B`, `WATCHLIST`, or
  `REJECT` decision.

For example, submit a JSON body of the form
`{"factor_scores":{"factor_name":100}}`, replacing `factor_name` with all
factor names required by that layer. Missing, unexpected, and out-of-range
factor scores return a clear HTTP 422 validation response.

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