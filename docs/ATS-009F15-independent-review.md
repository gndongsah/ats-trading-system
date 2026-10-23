# ATS-009F15 — independent final-tree simulation review

Review date: 2026-10-23 UTC. Reviewer: a separate Codex review agent, distinct
from the implementation agent; not a human reviewer or organizational approver.

## Leading disposition

**Revised simulation review: PASS within the documented scope**, on runtime/test
commit `8e553a3e95ef35a86219adbb921d5db1a7bbebed` (tree
`6a0a5e1101dfa4c994cf1050ebf598b4c9f5fbcb`). Both reproduced findings were
corrected and independently retested: 780 committed-suite tests passed without
skips, plus 8 separate reviewer probes. This is a separate Codex agent review,
not human or organizational approval, broker-backed F2 acceptance, Sprint 3
acceptance or permission to enable trading. The final report-only commit can be
added on top without changing the reviewed runtime/test tree.

**Original F14 tree: REQUEST CHANGES**, preserved below with its exact evidence.
Its baseline tests passed while the two additional probes demonstrated defects.
The revised assessment at the end records their resolution and remaining limits.

## Original assessed identity and verdict

Repository `gndongsah/ats-trading-system`, exact merged F14 commit
`dda680fc6e3f4ec8ef5051424bf8f8d5e5fa3012`, tree
`627ccf7782ad97010ee942f64af2029e6af0c656`.

**REQUEST CHANGES for simulation integrated acceptance.** Two independently
reproduced findings remain in that tree. Passing baseline tests do not resolve
them. No runtime source was edited during this review. Broker-backed F2 and
Sprint 3 acceptance remain open; trading/enforcement remain disabled.

## Requirements and inspection

Read F10's approved A/A/A policy and its explicit approval record, F11/F12 design,
F13 R1–R17 reconciliation and F14 handoff. Used these as requirements and scope,
not as independent proof of implementation correctness. No new Notion retrieval
or historical-source reauthentication is claimed. No AGENTS.md existed in the
checkout or its workspace ancestors.

Independently inspected `simulation_opening.py`, `simulation_risk.py`, Layer 3
reservation/replay/lifecycle paths, portfolio and standalone sizing, calendar
resolution, loss-state repository/provider, evidence append-only repository and
whole-transaction retry. Checked receipt timing, corrections and daily/weekly
scope, snapshot/NLV/time binding, generic versus simulation sizing, replay,
account-lock serialization and final basis checks. Reviewed the committed F14
probes before designing two additional probes outside the checkout.

## Findings

### F15-1 — P1: separate plans reuse the same buying-power allowance

Location in assessed tree: `src/simulation_risk.py:91–109`, especially line 100;
`src/layer3.py:582–616` gathers existing exposures for portfolio limits but does
not pass their affordability consumption into simulation sizing.

One service binds a snapshot plus buying power of USD 500. Two distinct valid
RISK_ON plans in the same account each request 50 shares, entry 100.5, stop 98.
Sequential authorizations using different keys both reserve four shares. The
second runs after the first commit under the same account-lock protocol. Durable
remaining entry notional is USD 804 against the unchanged USD 500 allowance.
Both decisions remain execution-disabled. No concurrency or broker calls are
needed to reproduce this.

F10 explicitly treats buying power as a separate affordability constraint and
requires existing reservations in capacity assessment. F12's single-order
buying-power cap does not enforce that constraint across supported successive
reservations. This is an F12 integration gap, rather than a defect in standalone
single-position division arithmetic. P1 describes required correction before
this simulation contract can be accepted, not current live order exposure.

Required resolution: reserve affordability atomically with other capacity, or
fail closed when a second reservation cannot establish remaining buying power.
Define whether harness buying power includes broker commitments and existing ATS
charges. Conservative charging of all outstanding ATS remaining plus filled gross
notional is acceptable for this isolated simulation if documented: a later
snapshot must not silently release the charge, and double-counting against a
broker-style net figure may understate capacity. Do not claim broker buying-power
reconciliation or reclaim filled capital without a supported lifecycle contract.

### F15-2 — P2: final authorization reuses an expired evaluation timestamp

Location: `src/layer3.py:474–476`, `511–517`, `650–653`, `699–703`;
`src/simulation_risk.py:65–84` has the same pre-lock sampling pattern.

The base service samples `now` before locking and reuses it during final loss
version validation. In the real simulation pipeline, the reviewer advances its
injected model clock by 31 seconds immediately before the final binding check,
then calls the unmodified check with its original `at`. Result: ALLOW, 50 shares,
one durable reservation while current model age is 31 seconds. An immediate
fresh replay denies. This is a deterministic latency/clock-boundary probe, not a
claim that 31 seconds of genuine database contention was induced.

A slow evaluation can therefore satisfy the 30-second current-source rule using
an old time sample at the final decision. The same cached-time structure affects
replay and potentially calendar boundaries; the executed probe establishes age
expiry only. Base timestamp sampling predates F12 (`git blame` identifies
`7d4f9c3d`); F12 inherits it and its preflight does not close the final-check gap.

Required resolution: obtain time after account-lock acquisition and refresh it
at final pre-write and replay checks, rejecting rollback, stale/future snapshot
and valuation evidence and expired simulation session eligibility. Explicitly
bound the promise to final evaluation, not database commit time. A genuinely
commit-time freshness guarantee would require a separate protocol and is not
established by this review.

## Independent execution evidence

Fresh isolated disposable PostgreSQL 16.15 clusters for focused baseline,
reviewer probes and full baseline; dedicated `ats_test`, per-case schema and
restricted role created by the existing fixture, Alembic upgrade/check through
`20261020_06`. Application tests run independently by this reviewer.

Environment: Python 3.12.14; SQLAlchemy 2.1.6; psycopg 3.3.6; Pydantic 2.14.1;
exchange-calendars 4.13.1; pytest 9.1.1; Ruff 0.17.2; Webull SDK 3.0.4 installed
for offline imports/mocks only. No credentials, keyring, broker requests or order
submission were used.

The host has only namespace UID/GID 0 mapped. Parent agent prepared scratch-only
PostgreSQL binaries and a preload that changes the initial root-guard effective
UID result, then returns actual UID 0. Reviewer inspected the runner; server,
client and tests run within one exec network namespace. The preload is unset
before Python tests. It is not repository code. This validates real PostgreSQL
semantics in a disposable namespace, not normal operating-system role isolation.
Clusters were stopped and removed by runner EXIT traps.

Commands on a normal disposable PostgreSQL host, with `ATS_TEST_DATABASE_URL`
pointing at dedicated `ats_test` (the namespace runner supplies that URL here):

```text
python -m pip install -e ".[dev,webull]"
python -m pytest -q tests/test_simulation_opening.py tests/test_simulation_risk.py tests/test_simulation_risk_failure_verification.py --fail-on-skip
python -m pytest -q --fail-on-skip
python -m ruff check src tests
python -m ruff format --check src tests
git diff --check
```

- Focused F11/F12/F14 baseline: **67 passed, zero skipped**, 4 pandas dependency
  warnings, 23.42 seconds.
- Reviewer probes: **2 passed, zero skipped**, 4 dependency warnings, 1.66 seconds.
  They assert the reproduced defective outcomes below; they are not passing
  regressions for desired behavior. An initial draft probe omitted the second
  plan's regime / fresh score identity; those fixture errors were corrected
  before the recorded reproduction.
- Full baseline: **771 passed, zero skipped**, 5 dependency warnings, 46.84
  seconds. This was a new independent execution, not inferred from author or CI.
- Ruff lint and formatting: passed, 73 files. `git diff --check`: passed.

Observed reviewer probe output:

```text
BP=500; quantities=4,4; total reserved notional=804.0; events=2
decision=ALLOW; quantity=50; actual model age=0:00:31; events=1
```

Local execution logs and JUnit reports were retained in scratch for this session;
they are supplementary transient evidence. This document and the probe source
below retain the substantive commands, assertions and results.

## Scope disposition

Receipt completion explicitly attests candidate/opening transaction commit,
not receipt commit; delayed receipt persistence is consistent with F11's stated
contract. Provider checks bind accepted receipt/candidate digest, exact opening
rows, correction quarantine, current period and loss event. Existing F11/F14
tests independently passed for those cases. No additional defect demonstrated
in those bounded paths; no exhaustive assurance is implied.

F14 retry tests use server-raised SQLSTATEs after real reservation writes, with
rollback/recovery/exhaustion; they are not genuine end-to-end conflicting
simulation transactions. Existing lock/deadlock/serialization helper cases are
part of the full baseline. Neither category proves all concurrency schedules.

Generic Layer3 intentionally preserves externally sized quantities and supports
nontransactional test providers; its existence is not evidence of integrated
regime sizing or authenticated production loss authority. Only explicitly
composed SimulationRiskService supplies F12 sizing and receipt-aware checks.
A future enabled entry point must mandate an approved authenticated path.

No broker identity, source completeness, complete positions, settled buying power,
source timestamps, reversals or emergency authority was verified. Synthetic
principals, seals, schedules and valuations remain harness assertions. R17 review
was executed on the assessed tree and found changes required; it is not closed
by green tests or by this report being saved. F2 and overall acceptance remain open.

## Reproduction source

Save the following outside the repository as `reviewer_probes.py`; run from the
assessed checkout with its dependencies and disposable database URL:

```text
python -m pytest -q -s -p tests.fail_on_skip /absolute/path/reviewer_probes.py --fail-on-skip
```

```python
from datetime import timedelta
from decimal import Decimal
from uuid import uuid4
from sqlalchemy import select
from src.domain.models import Regime, RiskIntent
from src.persistence.models import RiskReservationEventRecord
from src.portfolio import RiskDecision
from tests import test_postgres_persistence as pg
from tests import test_simulation_risk as f12

migrated_postgres_database = pg.migrated_postgres_database
migrated_postgres_engine = pg.migrated_postgres_engine


def test_reviewer_static_buying_power_reused(migrated_postgres_engine):
    ctx = f12.setup(migrated_postgres_engine, buying_power='500')
    factory, service, first_id, *_ = ctx
    entry = pg.make_complete_trade()
    plan = entry.plan.model_copy(update={
        'account_id': service.snapshot.account_id,
        'quantity': Decimal(50),
        'score_snapshot': entry.plan.score_snapshot.model_copy(update={'regime': Regime.RISK_ON, 'id': uuid4()}),
        'risk_intent': RiskIntent(stop_risk_budget=Decimal(125)),
    })
    with factory.begin() as session:
        pg.JournalRepository(session).save(entry.model_copy(update={'plan': plan}))
    first = f12.reserve(ctx, 'review-bp-1')
    second = service.authorize_with_retry(factory, plan_id=plan.id, idempotency_key='review-bp-2')
    with factory() as session:
        rows = session.scalars(select(RiskReservationEventRecord)).all()
        total = sum(Decimal(row.event_data['reservation']['entry']) * row.event_data['reservation']['remaining_quantity'] for row in rows)
    print(f'BP={service.buying_power}; quantities={first.quantity},{second.quantity}; total reserved notional={total}; events={len(rows)}')
    assert first.quantity == second.quantity == 4
    assert total == Decimal('804.0') > service.buying_power
    assert not first.execution_enabled and not second.execution_enabled


def test_reviewer_clock_advance_during_final_check(migrated_postgres_engine, monkeypatch):
    ctx = f12.setup(migrated_postgres_engine)
    factory, service, _, now, *_ = ctx
    original = service._loss_binding_is_current
    def delayed(session, account_id, at, event_data):
        now[0] += timedelta(seconds=31)
        return original(session, account_id, at, event_data)
    monkeypatch.setattr(service, '_loss_binding_is_current', delayed)
    result = f12.reserve(ctx, 'review-stale')
    print(f'decision={result.decision}; quantity={result.quantity}; actual model age={now[0] - service.snapshot.as_of}; events={f12.count(factory)}')
    assert result.decision is not RiskDecision.DENY
    assert f12.count(factory) == 1
    assert not result.execution_enabled
    # An immediate fresh evaluation observes the expiration and denies replay.
    monkeypatch.setattr(service, '_loss_binding_is_current', original)
    replay = f12.reserve(ctx, 'review-stale')
    assert replay.decision is RiskDecision.DENY
```

## Revised-tree independent assessment

The implementation agent supplied remediation commit
`8e553a3e95ef35a86219adbb921d5db1a7bbebed`, tree
`6a0a5e1101dfa4c994cf1050ebf598b4c9f5fbcb`. Reviewer fetched this commit and
created a separate detached checkout. The original assessed checkout and its
runtime source remained unchanged. Review of the exact revised diff found:

- Simulation sizing receives the account-locked active reservation exposures and
  uses exact Fraction arithmetic to deduct their remaining entry notional plus
  filled actual-price notional. A later snapshot version does not release a
  prior charge. The saved plan quantity and existing risk/portfolio caps remain.
- Initial time is sampled after locking. New authorizations and replay resample
  time after loss-provider work and check both snapshot and valuation ages against
  the configured maximum, future evidence and rollback relative to the base
  evaluation start. Simulation final evaluation also rechecks market eligibility.
  This is a final-evaluation check, not commit-time freshness or a global
  monotonic-clock attestation across independent calls/preflight stages.
- Nine committed regression cases cover sequential/concurrent affordability,
  exact partial-fill charging, inclusive 30 seconds, one microsecond beyond it,
  and clock rollback for new authorization and replay. These are bounded cases;
  the reviewer added independent probes described below.

### Conservative affordability contract and limits

For this simulation adapter, harness buying power is a ceiling **before active
ATS commitments**. Charge every active reservation's remaining quantity at entry
plus fills at actual price, regardless of whether that reservation predates the
current snapshot. Other preexisting broker/non-ATS commitments must already be
reflected in the harness ceiling. A new snapshot cannot itself prove prior ATS
charges were reconciled away. This prevents reuse of one allowance across plans
without claiming broker buying-power semantics.

Supplying a broker-style available figure that already deducts an ATS commitment
can cause double charging and underallocation. Filled capital remains charged
while the ATS filled exposure remains active; this code has no complete broker
position-close/capital-release reconciliation. A partial cancellation releases
only its unfilled charge. Conservative underallocation and missing filled-capital
release are explicit limits, not proof of full affordability lifecycle support.
No additional policy decision or real-account buying-power guarantee is inferred.

### Revised independent execution

Original reviewer probes were rerun with safe expected outcomes: first quantity
4, second 0, total remaining notional 402 <= 500; delayed final evaluation DENY,
quantity 0, zero new rows at age 31 seconds. **2 passed, zero skipped**, 4 dependency
warnings, 2.03 seconds, fresh disposable PostgreSQL 16.15. The original defective
assertions would fail against this tree; only the assertions were changed, not
the independent scenarios.

Six additional reviewer cases, another fresh PostgreSQL cluster: **6 passed,
zero skipped**, 4 warnings, 3.14 seconds:

| Independent probe | Observation |
|---|---|
| Fresh valuation at XNYS close minus one second, no delay, new/replay | ALLOW in both cases |
| Same evidence, advance final binding work by one second to exact close, new/replay | DENY in both cases while evidence age is only one second; no new reservation |
| Reserve four shares against 500, publish newer snapshot, attempt distinct plan | DENY; old affordability charge persists |
| Verified synthetic two-share fill at 101 and terminal cancellation of remaining two shares; confirm stop; authorize distinct plan | New quantity two; filled charge 202 plus new remaining 201 = 403 <= 500 |

The final-session probe uses an actual later ledger PNL entry of +1 and matching
100001 NLV/time, preserving independently captured opening denominators. Its first
construction attempt used a forbidden zero-value entry and was corrected before
the recorded run. The lifecycle probe uses the existing synthetic event verifier;
it makes no broker authentication claim.

Revised full committed suite: **780 passed, zero skipped**, 5 dependency warnings,
53.22 seconds. These 780 exclude the 8 separate reviewer probe cases; the
original 771 plus 9 committed remediation cases reconcile to 780. Executed
`python -m pytest -q --fail-on-skip` with a JUnit output path in another fresh
disposable PostgreSQL cluster. Revised Ruff lint/format passed (74 files),
and `git diff --check` passed. Same Python/dependency environment and fresh-cluster
runner described above. No CI or author execution is substituted for these runs.

Revised source review disposition: F15-1 and F15-2 are addressed for the documented
simulation contract by this exact runtime/test commit. **Revised verdict: PASS for the bounded
independent simulation review** after the full-suite completion. No unresolved
blocker was demonstrated within this review scope; this is not exhaustive
assurance or broker acceptance. The original F14 tree remains
changes-requested; no claim that it was retrospectively correct is made.

Reproduce revised independent scenarios using the original Appendix script,
changing only assertions: expect first/second quantities 4/0, total 402 <= 500,
final delayed decision DENY and zero reservation rows. The additional source
below is runnable with the same external-file pytest command and disposable URL.

```python
from datetime import timedelta
from decimal import Decimal
from uuid import uuid4
import pytest
from src.domain.models import Regime
from src.layer3 import BrokerOrderUpdate, BrokerOrderStatus, BrokerFillEvent, BrokerStopConfirmation
from src.portfolio import RiskDecision
from src.simulation_ledger import SimulationLedger, SimulationEntry, EntryKind
from tests import test_simulation_risk as f12
from tests import test_simulation_risk_failure_verification as f14

migrated_postgres_database=f12.migrated_postgres_database
migrated_postgres_engine=f12.migrated_postgres_engine

def second_plan(ctx):
    entry=f12.pg.make_complete_trade()
    plan=entry.plan.model_copy(update={'account_id':ctx[1].snapshot.account_id,'score_snapshot':entry.plan.score_snapshot.model_copy(update={'id':uuid4(),'regime':Regime.RISK_ON})})
    with ctx[0].begin() as session:
        f12.pg.JournalRepository(session).save(entry.model_copy(update={'plan':plan}))
    return plan

@pytest.mark.parametrize('replay',[False,True])
@pytest.mark.parametrize('delay,allowed',[(0,True),(1,False)])
def test_final_xnys_close(migrated_postgres_engine,monkeypatch,replay,delay,allowed):
    ctx=f12.setup(migrated_postgres_engine)
    factory,original,plan,now,*_=ctx
    calendar=original._loss_locks.calendar
    near=calendar.resolve(f12.f11.T).close_utc-timedelta(seconds=1)
    book=SimulationLedger(account_id=original.snapshot.account_id,opened_at=f12.f11.T,opening_nlv='100000').append(SimulationEntry(event_id='later-tick',occurred_at=near,kind=EntryKind.PNL,amount='1'))
    f12.f6.store(factory,book,near)
    service=f14.adapter(original,original.snapshot.model_copy(update={'version':3,'as_of':near,'equity':Decimal(100001)}))
    with factory.begin() as session: service.publish_snapshot(session,service.snapshot)
    now[0]=near
    if replay: assert service.authorize_with_retry(factory,plan_id=plan,idempotency_key='close').quantity>0
    check=service._loss_binding_is_current
    def delayed(*args,**kwargs):
        result=check(*args,**kwargs)
        now[0]+=timedelta(seconds=delay)
        return result
    monkeypatch.setattr(service,'_loss_binding_is_current',delayed)
    result=service.authorize_with_retry(factory,plan_id=plan,idempotency_key='close')
    print(f'close replay={replay} delay={delay} result={result.decision} age={(now[0]-near).total_seconds()}')
    assert (result.decision is not RiskDecision.DENY)==allowed
    assert f12.count(factory)==int(replay or allowed)
    assert not result.execution_enabled

def test_new_snapshot_keeps_active_affordability_charge(migrated_postgres_engine):
    ctx=f12.setup(migrated_postgres_engine,buying_power='500')
    assert f12.reserve(ctx).quantity==4
    plan=second_plan(ctx)
    service=f14.adapter(ctx[1],ctx[1].snapshot.model_copy(update={'version':3}))
    with ctx[0].begin() as session: service.publish_snapshot(session,service.snapshot)
    result=service.authorize_with_retry(ctx[0],plan_id=plan.id,idempotency_key='after-snapshot')
    assert result.decision is RiskDecision.DENY
    assert f12.count(ctx[0])==1

def test_cancel_releases_only_unfilled_charge(migrated_postgres_engine):
    ctx=f12.setup(migrated_postgres_engine,buying_power='500')
    first=f12.reserve(ctx)
    service=ctx[1]
    service._broker_event_verifier=f12.pg._PostgresBrokerEventVerifier()
    with ctx[0].begin() as session:
        state=service._reservation_state(session,service.snapshot.account_id,first.reservation_id)
        update=BrokerOrderUpdate(event_id='review-fillcancel',account_id=state.account_id,order_attempt_id=state.order_attempt_id,broker_order_id='review-order',sequence=1,status=BrokerOrderStatus.CANCELLED,authorized_quantity=4,cumulative_filled_quantity=2,fills=(BrokerFillEvent(fill_id='review-fill',quantity=2,price=Decimal(101)),))
        state=service.process_broker_order_update(session,reservation_id=first.reservation_id,event=update)
        stop=BrokerStopConfirmation(event_id='review-stop',account_id=state.account_id,order_attempt_id=state.order_attempt_id,broker_order_id='review-order',sequence=2,covered_fill_ids=('review-fill',),active_stop=Decimal(98))
        service.confirm_filled_stops(session,reservation_id=first.reservation_id,event=stop)
    plan=second_plan(ctx)
    result=service.authorize_with_retry(ctx[0],plan_id=plan.id,idempotency_key='after-fillcancel')
    print(f'after partial fill/cancel newquantity={result.quantity}; fillednotional=202; total=403')
    assert result.quantity==2
    assert f12.count(ctx[0])==4
    assert not result.execution_enabled

```
