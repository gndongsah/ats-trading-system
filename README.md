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
- Layer 0 returns deterministic `ACTIVE / WATCHLIST / BACKGROUND`.
- Layer 1 returns deterministic `TIER_A / TIER_B / WATCHLIST / REJECT`.
- No scoring weights or thresholds are duplicated in Python; they are loaded
  from the YAML source of truth.
- Secrets are not committed to Git; local `.env` files are ignored.
- The application can be started with one documented command:
  `python -m src`.