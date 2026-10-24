# Sprint 3 — independent simulation acceptance review

Date: 2026-10-24 UTC. Reviewer: a separate Codex review agent, independently
inspecting and executing the implementation agent's final source. This is an
automated separate-agent review, not human or organizational approval.

## Disposition and exact source

**PASS for the explicitly bounded simulation-only Sprint 3 acceptance review.**
No unresolved defect was demonstrated within that scope. This verdict applies to
repository `gndongsah/ats-trading-system`, merged branch
`feature/sprint-3-risk-engine`, commit
`d80f471b6fa43d27ba504ac2f4ee8fe6c85d15b6`, tree
`73df2bea6b0888f6f7c5004c7e83489dcfebfa7c`.

The reviewer fetched the branch and checked out that exact commit in a separate
detached worktree. No application source, policy, migration or committed test was
edited. No AGENTS.md was found in the checkout/workspace. A later report-only
commit may preserve this assessment without changing the assessed runtime/test
content. A later runtime change requires its own review.

This verdict does not close broker-backed F2 acceptance or enable enforcement,
paper orders or live orders. It does not independently change project status.
The owner-approved separation of simulation acceptance from broker acceptance is
the review scope supplied to this reviewer; it is not evidence that synthetic
facts have become authenticated broker facts.

## Requirements and source inspection

Assessed the current source against the supplied simulation criteria and the
repository's approved F10 policy, F11/F12 contracts, F13 reconciliation, F14
handoff, F15 separate review/remediation and ATS-008 acceptance audit. Documents
were used as requirements and historical context, not as proof that code works.
No new Notion retrieval, human approval or independent reauthentication of prior
policy history is claimed.

Inspected `src/portfolio.py` in full, the new theme-risk diff, standalone sizing
and regime contracts, receipt-qualified opening capture/provider, accounting
and loss-state projection, simulation adapter and Layer3 authorization/replay,
final time/version checks and whole-transaction retry. F15-reviewed risk runtime
paths other than `portfolio.py` are unchanged from its reviewed revision
`8e553a3`; current execution was still performed rather than inferred from that
prior review. Later broker comparison helpers do not install an enabled risk or
order route.

The new `portfolio.py` code accumulates theme risk for positions (lines 337–339),
external pending orders (355–357), and ATS remaining plus actual-price confirmed
fills (385–387), then caps candidate theme headroom (417–428) independently of
candidate factor headroom (429–438). Total risk is added once per exposure, not
once per theme/factor membership. Gross theme notional remains a separate 15%
constraint. Fraction arithmetic and whole-share flooring are preserved.

## Requirement-to-evidence assessment

All results below refer to synthetic inputs. The committed cases were
independently run in the full suite; the additional reviewer cases were designed
and run separately outside the checkout.

| Criterion | Inspected behavior and execution evidence | Assessment |
|---|---|---|
| Approved sizing/regimes | Standalone `risk.py`; new reviewer final-adapter cases isolate regime arithmetic with entry 100.5 / stop 95.5, so the 15% theme cap does not mask it: regime 1/2/3 yield 100/75/50 shares on 100000 NLV; 110 after +10000 NLV in regime 1 | Pass for supported simulation policy. Unsupported explicit regime-4 path, regime 5 and missing regime deny |
| One current USD NLV basis | Simulation adapter requires exact loss valuation/snapshot NLV and timestamp equality, sizes and assesses capacity from that snapshot; committed gain, mismatch, timestamp and snapshot-version cases rerun | Pass; current NLV remains distinct from frozen daily/weekly loss bases |
| 4R total / 2R each candidate theme and factor / 15% gross theme | Full portfolio/audit boundaries; 24 reviewer cases cover same-theme/disjoint-factor and three overlapping factors across position/pending/remaining/fill paths at 997.5/1000/1002.5 risk; 2 mixed-source probes use actual fill prices | Pass; independent theme and factor constraints do not sum the same exposure into account heat repeatedly |
| Confirmed stops, actual fills and lifecycle | Mixed-source risk = position 200 + pending 300 + remaining 100 + actual fills 300 = 900; candidate quantity 40 at 2.5 stop distance; confirmed synthetic partial-fill/cancel retains filled charge | Pass within synthetic verified-event contracts; unconfirmed stops deny |
| Receipt-qualified separate openings | Committed F11 observation 0/10 inclusive and outside, exact opening cutoff, daily/weekly separate identity, 60-second completion, receipt quarantine/replay and calendar cases all execute | Pass for synthetic candidate/opening commit receipts; not broker source authority |
| Current evidence age and session boundary | Committed inclusive 30 seconds, future/stale/missing cases; prior reviewer latency probes rerun at this head: 31-second final delay denies; fresh evidence at close minus one second allows, exact close denies new and replay even at age one second | Pass for final evaluation time, not database commit time |
| Inclusive sticky -3R/-6R and correction | Accounting/F6/F11 cases rerun: exact boundaries, recovery/recreation, separate periods, rollover; corrections preserve denominators/locks and deny affected period; F14 correction-after-reservation denies replay | Pass for synthetic flow/accounting evidence |
| Atomic PG reservation / version / retry / replay | Full PG suite plus F14 post-write SQLSTATE recovery/exhaustion, rollback, observer isolation and concurrent replay; reviewer integrated theme case commits one reservation and replays identity, exhausted budget writes none, stale replay denies | Pass for bounded scenarios; injected SQLSTATE tests are distinguished from genuine helper-level contention tests |
| Conservative affordability | Current F15 behavior rerun: 500 ceiling permits 4 shares at 100.5, denies a second plan; a newer snapshot does not release old charges; two fills at 101 plus cancellation allow only two new shares | Pass for documented conservative harness ceiling |
| Disabled dispatch | Committed capability/invariant tests rerun; every additional integrated result remains execution-disabled; independent direct call to submit function raises ExecutionDisabledError | Pass; no broker execution enabled |

Same-theme boundary arithmetic independently checked: at current NLV 100000,
reference R=500 and 2R=1000. An existing 399 shares at 2.5 risk/share leaves 2.5,
so candidate quantity is one; 400 or 401 leaves zero admissible shares. Requested
quantity ten therefore resizes to one or denies. The same expected outcomes hold
with disjoint factors or three overlapping factors. Low Decimal precision with
Inexact/Rounded traps does not change the answers. Mixed remaining/fill probes
use fill price 15 and confirmed stop 9 rather than the reservation entry 10,
which would materially undercount risk if incorrectly substituted.

Two additional real-PG adapter cases bind receipt-qualified openings/accounting,
one current snapshot and a synthetic shared-theme/disjoint-factor taxonomy to
these 399/400-share positions. They observe one-share reservation/replay versus
zero-share denial, then stale replay/new evaluation denial with no extra row.

## Independent execution

Environment: Python 3.12.14, PostgreSQL 16.15 UTF8, SQLAlchemy 2.1.6, psycopg 3.3.6,
Pydantic 2.14.1, exchange-calendars 4.13.1, pytest 9.1.1, Ruff 0.17.2, Webull SDK
3.0.4 installed for offline imports/mocks only.

- Full committed suite: **838 passed, zero skipped**, 5 dependency warnings,
  68.39 seconds. All database-gated cases ran with `--fail-on-skip`.
- Additional external reviewer probes: **44 passed, zero skipped**, 4 dependency
  warnings, 10.50 seconds, another fresh disposable PostgreSQL cluster. These are
  36 new reviewer cases plus 8 earlier reviewer-designed scenarios rerun against
  this exact current tree; they are not included in the 838 committed cases.
- An earlier 27-case offline preview is a subset of the final 44, not additional
  coverage. The initial 37-case combined run passed before the seven isolated
  regime/NLV cases were added; the final 44-case run includes all of them.
- `python -m ruff check .`: passed. `python -m ruff format --check .`: passed,
  108 files. `git diff --check`: passed. No new defect required a runtime edit.

Dependency warnings concern pandas timezone conversion deprecation and Starlette
TestClient/httpx integration; no skipped case was hidden as a passing run.

The reviewer inspected and copied the existing scratch PostgreSQL runner to
accept explicit pytest arguments. Each database run initialized a fresh UTF8
cluster, created dedicated `ats_test`, and used the repository's per-case schema,
restricted runtime-role and Alembic upgrade/check fixtures through `20261020_06`.
The host maps only namespace UID/GID 0; a scratch-only initial-root-guard preload
was needed to start PostgreSQL, as documented in F15. The preload was unset before
Python tests, was never repository code, and does not establish normal host OS
user isolation. EXIT cleanup stopped and removed both test clusters.

No broker request, credentials, keyring lookup or submission path was used. No
CI, implementation-agent count or merge report was substituted for reviewer
execution. Parent-retrieved CI exists but is not part of this independent run's
proof. Local logs/JUnit were retained in scratch as supplementary session evidence;
this report preserves exact substantive commands, results and probe source.

Reproduce on a normal disposable PostgreSQL host with an admin test URL targeting
`ats_test`, never a real account database:

```text
python -m pip install -e ".[dev,webull]"
export ATS_TEST_DATABASE_URL=postgresql+psycopg://TEST_ADMIN@HOST:PORT/ats_test
python -m pytest -q --fail-on-skip --junitxml=/tmp/sprint3-independent-full.xml
python -m pytest -q -s -p tests.fail_on_skip /tmp/sprint3_reviewer_probes.py /tmp/reviewer_probes_revised.py /tmp/reviewer_additional_revised.py --fail-on-skip --junitxml=/tmp/sprint3-independent-probes.xml
python -m ruff check .
python -m ruff format --check .
git diff --check
```

Save the three source blocks below under the corresponding filenames. They
import only assessed repository code/fixture builders; they do not replace the
loss provider with a passing stub. Run from the assessed checkout with that
checkout on Python's import path. No application-source changes are needed.

## Findings, limits and acceptance boundary

**No new finding requiring changes within the approved simulation-only scope.**
The earlier F15 affordability/freshness findings remain corrected at this head.
The new same-theme/disjoint-factor defect is addressed in the assessed source
and independently checked through pure calculation and actual simulation gate.
Passing cases are evidence for the scenarios executed, not exhaustive proof.

The 2R checks constrain the candidate's own theme and each of its factors. This
review does not reinterpret them as a new global freeze/liquidation policy for
unrelated preexisting factor/theme heat breaches. The global total-heat check and
any-theme-over-15%-gross denial remain implemented. Adding an unrelated-breach
freeze or liquidation rule would be a different policy decision.

Synthetic principals, ledger completeness/seals, positions, buying power,
calendar attestations and source timestamps remain trusted harness inputs. An
accepted receipt attests the opening/candidate transaction's durable completion,
not the receipt transaction's commit time. Final authorization freshness is
checked after provider work; it is not a commit-time clock guarantee or global
monotonic-clock attestation across separate calls.

Buying power is a conservative ceiling before active ATS commitments: remaining
entry notional plus filled actual-price notional is charged under the account
lock. A newer snapshot does not release a charge. Broker-style net buying power
could double charge and underallocate; a complete filled-position-close/capital
release reconciliation is not implemented. Partial cancellation releases only
unfilled exposure. Gross theme convention uses entry/actual-fill prices rather
than authenticated broker marks or margin calculations.

Generic Layer3 continues to accept externally sized plans and synthetic provider
interfaces; it is not the accepted integrated sizing entry point. The reviewed
simulation path explicitly composes SimulationRiskService, receipt-aware loss
provider, durable synthetic accounting and PostgreSQL reservation. A future
enabled broker entry point must mandate its separately approved/authenticated
composition and reject simulation provenance.

Authenticated broker identity/source completeness, source-time and emergency
calendar authority, complete synchronized positions/orders/NLV, all settled flow
classes/reversals, real frozen openings and broker lifecycle remain excluded and
blocked. No simulated receipt or operator balance match is promoted into broker
acceptance. Enforcement and paper/live submission stay disabled.

## Probe source: sprint3_reviewer_probes.py

```python
"""Separate-review probes for d80f471, outside application checkout."""

from datetime import timedelta
from decimal import Decimal, Inexact, Rounded, localcontext
from uuid import uuid4

import pytest
from src.layer3 import ExecutionDisabledError, submit_order_to_broker
from src.portfolio import (
    CandidateExposure,
    FilledExposure,
    PendingExposure,
    PositionExposure,
    ReservationExposure,
    RiskClassification,
    RiskDecision,
    RiskTaxonomy,
    assess_portfolio_risk,
)
from src.simulation_risk import SimulationRiskService
from tests.test_portfolio import NOW, POLICY, make_snapshot
from tests import test_simulation_risk as f12

migrated_postgres_database = f12.migrated_postgres_database
migrated_postgres_engine = f12.migrated_postgres_engine
D = Decimal


def taxonomy(version="review-taxonomy", overlap=False):
    return RiskTaxonomy(
        version=version,
        classifications={
            "ABC": RiskClassification(
                theme="SHARED",
                factors=("COMMON", "SECOND", "THIRD") if overlap else ("NEW",),
            ),
            "OLD": RiskClassification(
                theme="SHARED",
                factors=("COMMON", "SECOND", "THIRD") if overlap else ("OLD",),
            ),
            "PENDING": RiskClassification(
                theme="SHARED",
                factors=("COMMON", "SECOND", "THIRD") if overlap else ("PENDING",),
            ),
            "RESERVE": RiskClassification(
                theme="SHARED",
                factors=("COMMON", "SECOND", "THIRD") if overlap else ("RESERVE",),
            ),
            "XYZ": RiskClassification(
                theme="SHARED",
                factors=("COMMON", "SECOND", "THIRD") if overlap else ("DIFFERENT",),
            ),
        },
    )


def assess(positions=(), pending=(), reservations=(), overlap=False, requested=10):
    tax = taxonomy(overlap=overlap)
    snap = make_snapshot(
        positions=positions, pending_orders=pending, taxonomy_version=tax.version
    )
    return assess_portfolio_risk(
        snapshot=snap,
        candidate=CandidateExposure("ABC", requested, D("100.5"), D(98)),
        reservations=reservations,
        taxonomy=tax,
        policy=POLICY,
        now=NOW,
    )


@pytest.mark.parametrize("source", ["position", "pending", "remaining", "fills"])
@pytest.mark.parametrize("quantity,expected", [(399, 1), (400, 0), (401, 0)])
@pytest.mark.parametrize("overlap", [False, True])
def test_theme_boundary_and_exhaustion(source, quantity, expected, overlap):
    # 2R=$1000. Each old share consumes $2.5. Candidate stop distance=$2.5.
    positions = ()
    pending = ()
    reservations = ()
    if source == "position":
        positions = (
            PositionExposure(
                ticker="OLD",
                quantity=quantity,
                average_entry=D(10),
                active_stop=D("7.5"),
                stop_confirmed=True,
            ),
        )
    elif source == "pending":
        pending = (
            PendingExposure(
                ticker="OLD",
                remaining_quantity=quantity,
                entry=D(10),
                stop=D("7.5"),
                status_confirmed=True,
            ),
        )
    else:
        reservations = (
            ReservationExposure(
                uuid4(),
                "OLD",
                quantity if source == "remaining" else 0,
                D(10),
                D("7.5"),
                ()
                if source == "remaining"
                else (FilledExposure(quantity, D("12.5"), D(10)),),
            ),
        )
    with localcontext() as ctx:
        ctx.prec = 2
        ctx.traps[Inexact] = True
        ctx.traps[Rounded] = True
        result = assess(positions, pending, reservations, overlap)
    assert result.quantity == expected
    assert result.decision is (RiskDecision.RESIZE if expected else RiskDecision.DENY)


@pytest.mark.parametrize("overlap", [False, True])
def test_mixed_exposures_actual_fill_prices_and_no_double_charge(overlap):
    # Position200 + pending300 + remaining100 + actual fill300=$900.
    # Theme headroom100 / candidate2.5 =40. Same theme+factor never sums into1800.
    positions = (
        PositionExposure(
            ticker="OLD",
            quantity=100,
            average_entry=D(10),
            active_stop=D(8),
            stop_confirmed=True,
        ),
    )
    pending = (
        PendingExposure(
            ticker="PENDING",
            remaining_quantity=150,
            entry=D(10),
            stop=D(8),
            status_confirmed=True,
        ),
    )
    reservations = (
        ReservationExposure(
            uuid4(), "RESERVE", 50, D(10), D(8), (FilledExposure(50, D(15), D(9)),)
        ),
    )
    result = assess(positions, pending, reservations, overlap, 100)
    assert (result.decision, result.quantity) == (RiskDecision.RESIZE, 40)


@pytest.mark.parametrize("quantity,expected", [(399, 1), (400, 0)])
def test_actual_receipted_simulation_theme_gate(
    migrated_postgres_engine, quantity, expected
):
    position = PositionExposure(
        ticker="XYZ",
        quantity=quantity,
        average_entry=D(10),
        active_stop=D("7.5"),
        stop_confirmed=True,
    )
    ctx = f12.setup(migrated_postgres_engine, quantity=500, positions=(position,))
    factory, old, plan, now, *_ = ctx
    service = SimulationRiskService(
        snapshot=old.snapshot,
        buying_power=old.buying_power,
        calendar=old._loss_locks.calendar,
        taxonomy=taxonomy(old.snapshot.taxonomy_version),
        principal_provider=old._principal_provider,
        clock=old._clock,
    )
    result = service.authorize_with_retry(
        factory, plan_id=plan, idempotency_key="review-theme"
    )
    assert result.quantity == expected and not result.execution_enabled
    assert f12.count(factory) == int(expected > 0)
    replay = service.authorize_with_retry(
        factory, plan_id=plan, idempotency_key="review-theme"
    )
    if expected:
        assert replay.replayed and replay.reservation_id == result.reservation_id
    now[0] += timedelta(seconds=31)
    assert (
        service.authorize_with_retry(
            factory, plan_id=plan, idempotency_key="review-theme"
        ).decision
        is RiskDecision.DENY
    )
    assert f12.count(factory) == int(expected > 0)


def test_dispatch_remains_disabled():
    with pytest.raises(ExecutionDisabledError):
        submit_order_to_broker()


@pytest.mark.parametrize(
    "regime_name,pnl,expected",
    [
        ("RISK_ON", "0", 100),
        ("RISK_ON_NARROW", "0", 75),
        ("NEUTRAL_CHOP", "0", 50),
        ("RISK_ON", "10000", 110),
        ("RISK_OFF", "0", 0),
        ("CAPITULATION_REVERSAL", "0", 0),
        (None, "0", 0),
    ],
)
def test_isolated_final_regime_sizing_current_nlv(
    migrated_postgres_engine, regime_name, pnl, expected
):
    from src.domain.models import Regime, RiskIntent

    regime = getattr(Regime, regime_name) if regime_name else None
    ctx = f12.setup(migrated_postgres_engine, pnl=pnl)
    entry = f12.pg.make_complete_trade()
    plan = entry.plan.model_copy(
        update={
            "account_id": ctx[1].snapshot.account_id,
            "stop": D("95.5"),
            "quantity": D(500),
            "risk_intent": RiskIntent(stop_risk_budget=D(2500)),
            "score_snapshot": entry.plan.score_snapshot.model_copy(
                update={"id": uuid4(), "regime": regime}
            ),
        }
    )
    with ctx[0].begin() as session:
        f12.pg.JournalRepository(session).save(entry.model_copy(update={"plan": plan}))
    result = ctx[1].authorize_with_retry(
        ctx[0], plan_id=plan.id, idempotency_key="isolated-sizing"
    )
    assert result.quantity == expected and not result.execution_enabled
    assert f12.count(ctx[0]) == int(expected > 0)
```

## Probe source: reviewer_probes_revised.py

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
    ctx = f12.setup(migrated_postgres_engine, buying_power="500")
    factory, service, first_id, *_ = ctx
    entry = pg.make_complete_trade()
    plan = entry.plan.model_copy(
        update={
            "account_id": service.snapshot.account_id,
            "quantity": Decimal(50),
            "score_snapshot": entry.plan.score_snapshot.model_copy(
                update={"regime": Regime.RISK_ON, "id": uuid4()}
            ),
            "risk_intent": RiskIntent(stop_risk_budget=Decimal(125)),
        }
    )
    with factory.begin() as session:
        pg.JournalRepository(session).save(entry.model_copy(update={"plan": plan}))
    first = f12.reserve(ctx, "review-bp-1")
    second = service.authorize_with_retry(
        factory, plan_id=plan.id, idempotency_key="review-bp-2"
    )
    with factory() as session:
        rows = session.scalars(select(RiskReservationEventRecord)).all()
        total = sum(
            Decimal(row.event_data["reservation"]["entry"])
            * row.event_data["reservation"]["remaining_quantity"]
            for row in rows
        )
    print(
        f"BP={service.buying_power}; quantities={first.quantity},{second.quantity}; total reserved notional={total}; events={len(rows)}"
    )
    assert first.quantity == 4 and second.quantity == 0
    assert total == Decimal("402.0") <= service.buying_power
    assert not first.execution_enabled and not second.execution_enabled


def test_reviewer_clock_advance_during_final_check(
    migrated_postgres_engine, monkeypatch
):
    ctx = f12.setup(migrated_postgres_engine)
    factory, service, _, now, *_ = ctx
    original = service._loss_binding_is_current

    def delayed(session, account_id, at, event_data):
        now[0] += timedelta(seconds=31)
        return original(session, account_id, at, event_data)

    monkeypatch.setattr(service, "_loss_binding_is_current", delayed)
    result = f12.reserve(ctx, "review-stale")
    print(
        f"decision={result.decision}; quantity={result.quantity}; actual model age={now[0] - service.snapshot.as_of}; events={f12.count(factory)}"
    )
    assert result.decision is RiskDecision.DENY
    assert f12.count(factory) == 0
    assert not result.execution_enabled
    # An immediate fresh evaluation observes the expiration and denies replay.
    monkeypatch.setattr(service, "_loss_binding_is_current", original)
    replay = f12.reserve(ctx, "review-stale")
    assert replay.decision is RiskDecision.DENY
```

## Probe source: reviewer_additional_revised.py

```python
from datetime import timedelta
from decimal import Decimal
from uuid import uuid4
import pytest
from src.domain.models import Regime
from src.layer3 import (
    BrokerOrderUpdate,
    BrokerOrderStatus,
    BrokerFillEvent,
    BrokerStopConfirmation,
)
from src.portfolio import RiskDecision
from src.simulation_ledger import SimulationLedger, SimulationEntry, EntryKind
from tests import test_simulation_risk as f12
from tests import test_simulation_risk_failure_verification as f14

migrated_postgres_database = f12.migrated_postgres_database
migrated_postgres_engine = f12.migrated_postgres_engine


def second_plan(ctx):
    entry = f12.pg.make_complete_trade()
    plan = entry.plan.model_copy(
        update={
            "account_id": ctx[1].snapshot.account_id,
            "score_snapshot": entry.plan.score_snapshot.model_copy(
                update={"id": uuid4(), "regime": Regime.RISK_ON}
            ),
        }
    )
    with ctx[0].begin() as session:
        f12.pg.JournalRepository(session).save(entry.model_copy(update={"plan": plan}))
    return plan


@pytest.mark.parametrize("replay", [False, True])
@pytest.mark.parametrize("delay,allowed", [(0, True), (1, False)])
def test_final_xnys_close(
    migrated_postgres_engine, monkeypatch, replay, delay, allowed
):
    ctx = f12.setup(migrated_postgres_engine)
    factory, original, plan, now, *_ = ctx
    calendar = original._loss_locks.calendar
    near = calendar.resolve(f12.f11.T).close_utc - timedelta(seconds=1)
    book = SimulationLedger(
        account_id=original.snapshot.account_id,
        opened_at=f12.f11.T,
        opening_nlv="100000",
    ).append(
        SimulationEntry(
            event_id="later-tick", occurred_at=near, kind=EntryKind.PNL, amount="1"
        )
    )
    f12.f6.store(factory, book, near)
    service = f14.adapter(
        original,
        original.snapshot.model_copy(
            update={"version": 3, "as_of": near, "equity": Decimal(100001)}
        ),
    )
    with factory.begin() as session:
        service.publish_snapshot(session, service.snapshot)
    now[0] = near
    if replay:
        assert (
            service.authorize_with_retry(
                factory, plan_id=plan, idempotency_key="close"
            ).quantity
            > 0
        )
    check = service._loss_binding_is_current

    def delayed(*args, **kwargs):
        result = check(*args, **kwargs)
        now[0] += timedelta(seconds=delay)
        return result

    monkeypatch.setattr(service, "_loss_binding_is_current", delayed)
    result = service.authorize_with_retry(
        factory, plan_id=plan, idempotency_key="close"
    )
    print(
        f"close replay={replay} delay={delay} result={result.decision} age={(now[0] - near).total_seconds()}"
    )
    assert (result.decision is not RiskDecision.DENY) == allowed
    assert f12.count(factory) == int(replay or allowed)
    assert not result.execution_enabled


def test_new_snapshot_keeps_active_affordability_charge(migrated_postgres_engine):
    ctx = f12.setup(migrated_postgres_engine, buying_power="500")
    assert f12.reserve(ctx).quantity == 4
    plan = second_plan(ctx)
    service = f14.adapter(ctx[1], ctx[1].snapshot.model_copy(update={"version": 3}))
    with ctx[0].begin() as session:
        service.publish_snapshot(session, service.snapshot)
    result = service.authorize_with_retry(
        ctx[0], plan_id=plan.id, idempotency_key="after-snapshot"
    )
    assert result.decision is RiskDecision.DENY
    assert f12.count(ctx[0]) == 1


def test_cancel_releases_only_unfilled_charge(migrated_postgres_engine):
    ctx = f12.setup(migrated_postgres_engine, buying_power="500")
    first = f12.reserve(ctx)
    service = ctx[1]
    service._broker_event_verifier = f12.pg._PostgresBrokerEventVerifier()
    with ctx[0].begin() as session:
        state = service._reservation_state(
            session, service.snapshot.account_id, first.reservation_id
        )
        update = BrokerOrderUpdate(
            event_id="review-fillcancel",
            account_id=state.account_id,
            order_attempt_id=state.order_attempt_id,
            broker_order_id="review-order",
            sequence=1,
            status=BrokerOrderStatus.CANCELLED,
            authorized_quantity=4,
            cumulative_filled_quantity=2,
            fills=(
                BrokerFillEvent(fill_id="review-fill", quantity=2, price=Decimal(101)),
            ),
        )
        state = service.process_broker_order_update(
            session, reservation_id=first.reservation_id, event=update
        )
        stop = BrokerStopConfirmation(
            event_id="review-stop",
            account_id=state.account_id,
            order_attempt_id=state.order_attempt_id,
            broker_order_id="review-order",
            sequence=2,
            covered_fill_ids=("review-fill",),
            active_stop=Decimal(98),
        )
        service.confirm_filled_stops(
            session, reservation_id=first.reservation_id, event=stop
        )
    plan = second_plan(ctx)
    result = service.authorize_with_retry(
        ctx[0], plan_id=plan.id, idempotency_key="after-fillcancel"
    )
    print(
        f"after partial fill/cancel newquantity={result.quantity}; fillednotional=202; total=403"
    )
    assert result.quantity == 2
    assert f12.count(ctx[0]) == 4
    assert not result.execution_enabled
```
