# ATS-036B owner-machine proof

**PASS on 2026-11-11.** The owner checkout started from remote commit
`6ca53ad` on `feature/ats-036-paper-campaign-runtime`. The proof used its
existing Windows virtual environment and keyring-backed Webull market-data
client.

The adapter requests two RTH minute bars and selects the latest bar whose
full minute has elapsed. Regression tests cover both response orderings when
Webull includes the open minute.

| Proof field | Verified result |
| --- | --- |
| Market data | SPY, RTH, 1 minute, revision 1 |
| Provider event time | 2026-11-11T16:20:00+00:00 |
| Available at | 2026-11-11T16:21:12.730280+00:00 |
| Ingested at | 2026-11-11T16:21:12.733515+00:00 |
| Freshness | Default runtime gate accepted the event. Age at ingestion was 72.733515 seconds. |
| Database | Dedicated `ats036b-postgres` container, healthy, PostgreSQL 16.15, `ats_test` database, loopback port 55436, Alembic head `20261028_13`. |
| Durability | One committed proof row. A fresh engine and connection restored the exact observation using the restricted test runtime role. |
| Point-in-time replay | The observation was absent one microsecond before availability, at availability, and one microsecond before ingestion. The exact observation was visible at ingestion. |
| Observation SHA-256 | `8f7b39ce22df2128b501d3b20f23cd074b800c0859ba9a4fb01af004d0cd3b06` over the restored observation's model JSON. |
| Forward test | `ForwardTestOrchestrator` consumed the restored observation and recorded one simulated `FILLED` receipt through `PaperBrokerAdapter`, with run type `PAPER` and `live_execution_used=false`. The one-shot proof used test proposal, approval, and campaign fixtures with in-memory run and journal repositories. |
| Webull order submission | Disabled. The proof instantiated only the Webull market-data client, and the execution capability guard passed. |

The database URL was scoped to the test commands. The combined focused run
passed all 117 tests with zero skips, including 54 PostgreSQL tests, 40
runtime, orchestrator, and market-data tests, and 23 execution capability
guard tests. Repository-wide Ruff lint passed and all 209 files passed
Ruff format checking.
