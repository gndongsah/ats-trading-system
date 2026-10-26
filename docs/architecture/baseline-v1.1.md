# ATS Architecture Baseline v1.1

Status: **Implementation baseline**

This version refines v1 by adding an explicit human-approval boundary, point-in-time research provenance, Portfolio State/Reconciliation, run-type separation, a clearer data model, runtime sequence, and event lineage.

## Architectural invariants

1. `playbook_v3.yaml` is the source of truth for deterministic strategy rules.
2. AI produces evidence and qualitative judgments; it does not control capital.
3. The Layer 3 Risk Engine is non-bypassable.
4. Risk authorization and human trade approval are separate decisions.
5. Research decisions consume persisted point-in-time evidence, not ephemeral web state.
6. LIVE, PAPER, and REPLAY outcomes are distinguishable and cannot silently contaminate one another.
7. Portfolio/account state enters the Risk Engine through a defined Portfolio State contract.
8. Broker-specific code remains behind an adapter.
9. Every production decision must be reproducible from stored inputs, config version, code version, and timestamps.
10. Historical replay must never reveal information unavailable at the requested as-of timestamp.

## 1. System architecture

```mermaid
flowchart LR
    USER["Trader / Operator"]

    subgraph EXT["External Systems"]
        MKT["Market & Fundamental Data"]
        FILINGS["SEC / News / Transcripts"]
        AI["LLM Provider<br/>Optional Jev"]
        BROKER["Paper / Webull / Alpaca / IBKR"]
        ALERTS["Telegram<br/>OpenClaw later"]
    end

    subgraph ATS["Agentic Trading System"]
        API["FastAPI / Trading API"]
        CONFIG["Playbook Configuration<br/>playbook_v3.yaml"]
        INGEST["Point-in-Time<br/>Data Ingestion"]
        RESEARCH["Layer 0–1<br/>Research & Evidence"]
        SETUP["Layer 2<br/>Regime & Setup Engine"]
        PROPOSAL["Trade Proposal"]
        PORTFOLIO["Portfolio State<br/>& Reconciliation"]
        RISK["Layer 3<br/>RISK ENGINE<br/>NON-BYPASSABLE"]
        APPROVAL["Human Approval /<br/>Autonomy Policy"]
        EXEC["Execution Service<br/>Broker Adapter"]
        LIVEJ["Live / Paper Journal"]
        SIM["Replay / Backtest<br/>Simulation Results"]
        ANALYTICS["Layer 4 Analytics<br/>R, MAE/MFE, Expectancy"]
    end

    subgraph DATA["Persistence"]
        PG["PostgreSQL"]
        TS["TimescaleDB"]
        EVIDENCE["Point-in-Time<br/>Evidence Store"]
        SNAPSHOTS["Immutable Score<br/>& Plan Snapshots"]
    end

    USER --> API
    USER <--> ALERTS
    ALERTS <--> API

    MKT --> INGEST
    FILINGS --> INGEST
    INGEST --> TS
    INGEST --> PG
    INGEST --> EVIDENCE

    EVIDENCE --> RESEARCH
    AI <--> RESEARCH
    CONFIG --> RESEARCH
    CONFIG --> SETUP
    CONFIG --> RISK

    TS --> SETUP
    PG --> SETUP
    RESEARCH --> SNAPSHOTS
    SETUP --> SNAPSHOTS

    BROKER --> PORTFOLIO
    PG --> PORTFOLIO
    SNAPSHOTS --> PROPOSAL
    EVIDENCE --> PROPOSAL
    PROPOSAL --> RISK
    PORTFOLIO --> RISK

    RISK -->|"DENY"| LIVEJ
    RISK -->|"ALLOW / RESIZE"| APPROVAL
    APPROVAL -->|"REJECT"| LIVEJ
    APPROVAL -->|"APPROVE / POLICY-AUTHORIZED"| EXEC

    EXEC <--> BROKER
    BROKER --> LIVEJ
    LIVEJ --> PG

    TS --> SIM
    PG --> SIM
    SNAPSHOTS --> SIM
    EVIDENCE --> SIM

    LIVEJ --> ANALYTICS
    SIM --> ANALYTICS
```

## 2. Trading decision flow

```mermaid
flowchart TD
    A["Layer 0<br/>Market Opportunity"] --> B["Layer 1<br/>Company Discovery"]
    B --> C["Layer 2<br/>Regime + Setup"]
    C --> D["Trade Proposal"]
    D --> E["Layer 3<br/>Risk Gate"]

    E -->|"DENY"| X["No Trade<br/>reason journaled"]
    E -->|"ALLOW / RESIZE"| F["Human Approval /<br/>Autonomy Policy"]
    F -->|"REJECT"| X
    F -->|"APPROVE"| G["Execution Service"]
    G --> H["Broker Adapter"]
    H --> I["Order + Protective Exit"]
    I --> J["Fills / Position State"]
    J --> K["LIVE/PAPER Journal"]

    K --> L["R Result"]
    K --> M["MAE / MFE"]
    K --> N["Plan Adherence"]
    L --> O["Expectancy"]
    M --> O
    N --> O
    O --> P["Validation Stage<br/>Experimental → Mature"]
    P --> Q["Allowed Risk / Autonomy"]
    Q -. "future policy" .-> E
    Q -. "future policy" .-> F
```

## 3. Core data architecture

`INSTRUMENT` replaces the ambiguous entity name `SECURITY`.

A trade can reference multiple decision-time score snapshots: Market Opportunity, Discovery, Setup, and Regime.

```mermaid
erDiagram
    INSTRUMENT ||--o{ TRADE_PLAN : has
    TRADE_PLAN ||--o{ SCORE_SNAPSHOT : captures
    SCORE_SNAPSHOT ||--o{ SCORE_FACTOR : contains
    SCORE_FACTOR }o--o{ EVIDENCE_ITEM : supported_by
    TRADE_PLAN ||--o{ PLAN_VERSION : versions
    TRADE_PLAN ||--o{ ORDER_INTENT : proposes
    ORDER_INTENT ||--o{ BROKER_ORDER : creates
    BROKER_ORDER ||--o{ FILL : receives
    TRADE_PLAN ||--o| TRADE_RESULT : closes_as
    TRADE_RESULT ||--|| JOURNAL_ENTRY : records
    TRADE_PLAN }o--o{ THEME_EXPOSURE : adds
    TRADE_PLAN }o--o{ FACTOR_EXPOSURE : adds
    PORTFOLIO_SNAPSHOT ||--o{ POSITION_SNAPSHOT : contains
    PORTFOLIO_SNAPSHOT ||--o{ OPEN_ORDER_SNAPSHOT : contains
```

Recommended `SCORE_SNAPSHOT.score_type` values:

- `MARKET_OPPORTUNITY`
- `DISCOVERY`
- `SETUP`
- `REGIME`

Decision-time plans and score snapshots are immutable. Re-planning or re-scoring creates another version/snapshot.

## 4. Portfolio State contract

The Risk Engine must receive a defined state object rather than query arbitrary broker/database fields.

Minimum inputs:

- account equity / NLV
- available cash or buying power, with provenance
- current positions
- open orders
- current total open R
- theme and factor heat
- daily realized R
- weekly realized R
- strategy validation stage
- state timestamp and freshness
- source/provenance
- run type: LIVE, PAPER, or REPLAY

The Portfolio State/Reconciliation component owns broker/account normalization. The Risk Engine owns risk decisions.

## 5. Trust boundaries

```mermaid
flowchart LR
    INTERNET["Untrusted Internet<br/>news / filings / external text"]
    INGEST["Point-in-Time Ingestion"]
    STORE["Persisted Evidence Store"]
    AGENTS["AI Research Sandbox"]
    CORE["Trusted ATS Core<br/>deterministic engines"]
    SECRETS["Secrets Manager"]
    EXEC["Execution Service"]
    BROKER["Broker API"]

    INTERNET -->|"untrusted data"| INGEST
    INGEST --> STORE
    STORE -->|"typed persisted evidence"| AGENTS
    AGENTS -->|"typed judgments + evidence IDs"| CORE
    CORE -->|"risk-approved + authorization-approved intent"| EXEC
    SECRETS -->|"broker credentials"| EXEC
    EXEC --> BROKER

    AGENTS -. "NO ACCESS" .-> SECRETS
    AGENTS -. "NO DIRECT ORDERS" .-> BROKER
```

## 6. Runtime trade sequence

```mermaid
sequenceDiagram
    participant Scheduler
    participant Data as Data Service
    participant Research as Research Agent
    participant Setup as Setup Engine
    participant Proposal as Proposal Service
    participant Portfolio as Portfolio State
    participant Risk as Risk Engine
    participant User
    participant Exec as Execution Service
    participant Broker
    participant Journal

    Scheduler->>Data: Refresh point-in-time data
    Data->>Research: Persisted evidence snapshot
    Research->>Research: Evaluate opportunity/discovery
    Research->>Setup: Candidate + evidence IDs
    Setup->>Data: Request market snapshot
    Data-->>Setup: OHLCV/regime inputs
    Setup->>Setup: Calculate Setup Score
    Setup->>Proposal: Actionable candidate
    Proposal->>Portfolio: Request current state
    Portfolio-->>Proposal: Portfolio snapshot
    Proposal->>Risk: Proposal + portfolio state
    Risk->>Risk: Sizing + heat + locks + freshness

    alt Risk Denied
        Risk->>Journal: Record denial
    else Risk Allowed
        Risk->>User: Trade proposal
        alt User Rejects
            User->>Journal: Record rejection
        else User Approves
            User->>Exec: Approve
            Exec->>Broker: Submit order
            Broker-->>Exec: Order status/fill
            Exec->>Journal: Persist execution
        end
    end
```

## 7. Point-in-time event lineage

```mermaid
flowchart LR
    SOURCE["External Source"] --> EVENT["Market / Research Event"]
    EVENT --> ET["event_time"]
    EVENT --> AT["available_at"]
    EVENT --> IT["ingested_at"]

    ET --> RAW["Immutable Raw Record"]
    AT --> RAW
    IT --> RAW

    RAW --> NORMALIZED["Normalized Observation"]
    NORMALIZED --> SNAPSHOT["As-Of Snapshot"]

    SNAPSHOT --> RESEARCH["Research Decision"]
    SNAPSHOT --> SETUP["Setup Decision"]
    RESEARCH --> DECISION["Decision Record"]
    SETUP --> DECISION

    DECISION --> REPLAY["Historical Replay"]
    REPLAY --> CHECK{"available_at <= replay timestamp?"}
    CHECK -->|"Yes"| INCLUDE["Include"]
    CHECK -->|"No"| EXCLUDE["Exclude"]
```

Acceptance invariant: **replaying yesterday must not reveal information learned today.**

## 8. LIVE / PAPER / REPLAY separation

Every outcome-bearing record must carry an explicit run type.

- `LIVE`: real broker execution.
- `PAPER`: broker sandbox or controlled simulated execution using current data.
- `REPLAY`: historical as-of simulation.

Shared analytics may aggregate them only when the query explicitly requests that aggregation. Live validation-stage promotion must never silently count replay observations.

## 9. Deployment view

```mermaid
flowchart TB
    subgraph AWS["AWS"]
        API["Dockerized ATS API"]
        WORKER["Scheduler / Worker"]
        EXEC["Isolated Execution Service"]
        DB["RDS PostgreSQL<br/>+ time-series capability"]
        SM["Secrets Manager"]
        LOG["CloudWatch Logs / Metrics"]
    end

    PROVIDERS["Market / SEC / News / AI APIs"] --> API
    WORKER --> API
    API <--> DB
    API --> LOG
    EXEC --> LOG
    SM --> EXEC
    API -->|"typed approved order intent"| EXEC
    EXEC --> BROKER["Broker APIs"]
    OP["Operator"] --> UI["Dashboard / Telegram"]
    UI --> API
```

## 10. Architecture-to-sprint impact

- Sprint 2: immutable evidence, plans, and score snapshots remain foundational.
- Sprint 3: Risk Engine remains authoritative; Portfolio State becomes its explicit input contract.
- Sprint 4: implement point-in-time lineage and run-type-safe replay.
- Sprint 5: regime/setup outputs create typed score snapshots.
- Sprint 6: backtests write REPLAY results only.
- Sprint 7–8: AI consumes persisted evidence and returns typed judgments/evidence references.
- Sprint 9: implement Human Approval/Autonomy Policy and broker adapter path.
- Sprint 10: harden execution isolation, observability, journal analytics, and promotion rules.
