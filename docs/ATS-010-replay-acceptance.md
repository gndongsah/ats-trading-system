# ATS-010 — Replay-driven simulation acceptance

Run from the repository checkout, with dev dependencies installed and
`ATS_TEST_DATABASE_URL` pointing to a dedicated PostgreSQL database named
`ats_test`:

```bash
python -m src.replay_acceptance --output replay-acceptance.json
```

The command prints a JSON report, optionally saves the same report atomically,
and exits **0 only when all four required scenarios pass**. Missing PostgreSQL,
skips, test failures, missing cases, duplicate cases, missing provenance evidence,
invalid XML or a timeout produce FAIL and a nonzero exit. The command runs only
the fixed acceptance test file; inherited pytest selection flags are cleared.
Database URLs and raw pytest failure diagnostics are excluded from the JSON.
The underlying PostgreSQL fixture creates, migrates and drops isolated schemas
and runtime roles in `ats_test`. It never modifies a broker or production account.

| Scenario | Acceptance evidence |
| --- | --- |
| Three simulated days | One account advances through daily openings, persisted loss accounting, versioned portfolio snapshots and actual risk reservations. Today's correction leaves yesterday's replay and digest unchanged; both revisions remain stored. |
| Delayed arrival | Missing bar denies without reservation. Later ingestion permits a subsequent tick, never the earlier tick. Duplicate delivery preserves original ingestion time and snapshot. |
| Stale/recovered ticks | A fresh tick reserves, a stale tick denies, and a fresh update recovers. A later correction to an old bar cannot refresh its age. |
| Missing next-day bars | Prior-day bars cannot satisfy the next day's freshness requirement. New bars permit recovery after daily opening rollover. |

Every successful reservation verifies the persisted full replay snapshot and
digest, retains the existing SIMULATION-ONLY basis, and exercises same-key
idempotency. Every denial verifies zero quantity and no added reservation. All
decisions verify `execution_enabled=False`. JSON case evidence includes each
tick's timestamp, expected outcome, decision, quantity, replay digest, reservation
count, denial cause and provenance check.

CI runs the same command after the full suite and uploads the report as
`replay-acceptance-report`. The four underlying acceptance tests also run in the
full suite with `--fail-on-skip`.

This is a chronological synthetic simulation, not a profit backtest. Bars and
reference data use the offline JSON adapter. Portfolio NLV, buying power, stops,
taxonomy and score inputs remain synthetic harness inputs. The small ledger P&L
increments establish fresh, distinct source sequences; they do not model market
returns. Historical market replay is checked independently after later arrivals;
the current risk ledger is not rewound to grant historical authorizations.
ATS-009 broker evidence remains separately blocked and execution stays disabled.

For detailed assertion diagnostics, run:

```bash
python -m pytest tests/test_replay_acceptance.py -q --fail-on-skip
```
