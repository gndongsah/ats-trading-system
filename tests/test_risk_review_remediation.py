"""Regressions for independent F15 timing and aggregate affordability findings."""

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from decimal import Decimal, localcontext
from fractions import Fraction
from threading import Barrier
from uuid import uuid4

import pytest

from src.domain.models import Regime, TradePlan
from src.persistence.models import TradePlanRecord
from src.portfolio import FilledExposure, ReservationExposure, RiskDecision
from tests import test_simulation_risk as f12

migrated_postgres_database = f12.migrated_postgres_database
migrated_postgres_engine = f12.migrated_postgres_engine


def second_plan(ctx):
    entry = f12.pg.make_complete_trade()
    plan = entry.plan.model_copy(
        update={
            "account_id": ctx[1].snapshot.account_id,
            "score_snapshot": entry.plan.score_snapshot.model_copy(
                update={
                    "id": uuid4(),
                    "regime": Regime.RISK_ON,
                }
            ),
        }
    )
    with ctx[0].begin() as session:
        f12.pg.JournalRepository(session).save(entry.model_copy(update={"plan": plan}))
    return plan


@pytest.mark.parametrize("delay,allowed", [(30, True), (30.000001, False), (-1, False)])
@pytest.mark.parametrize("replay", [False, True])
def test_final_clock_check_applies_after_provider_work(
    migrated_postgres_engine, monkeypatch, delay, allowed, replay
):
    ctx = f12.setup(migrated_postgres_engine)
    if replay:
        assert f12.reserve(ctx).quantity > 0
    original = ctx[1]._loss_binding_is_current

    def slow(*args, **kwargs):
        result = original(*args, **kwargs)
        ctx[3][0] += timedelta(seconds=delay)
        return result

    monkeypatch.setattr(ctx[1], "_loss_binding_is_current", slow)
    result = f12.reserve(ctx)
    assert (result.decision is not RiskDecision.DENY) == allowed
    assert not result.execution_enabled
    assert f12.count(ctx[0]) == int(replay or allowed)


@pytest.mark.parametrize("concurrent", [False, True])
def test_multiple_plans_cannot_reuse_buying_power(migrated_postgres_engine, concurrent):
    ctx = f12.setup(migrated_postgres_engine, buying_power="500")
    other = second_plan(ctx)
    barrier = Barrier(2)

    def reserve(pair):
        plan, key = pair
        if concurrent:
            barrier.wait(timeout=10)
        return ctx[1].authorize_with_retry(ctx[0], plan_id=plan, idempotency_key=key)

    pairs = [(ctx[2], "first"), (other.id, "second")]
    if concurrent:
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(reserve, pairs))
    else:
        results = [reserve(pair) for pair in pairs]
    assert sorted(r.quantity for r in results) == [0, 4]
    assert sum(Fraction(r.quantity) * Fraction("100.5") for r in results) <= 500
    assert f12.count(ctx[0]) == 1
    assert all(not r.execution_enabled for r in results)


def test_partial_fills_are_charged_at_actual_price_exactly(migrated_postgres_engine):
    ctx = f12.setup(migrated_postgres_engine, buying_power="500")
    with ctx[0]() as session:
        plan = TradePlan.model_validate(session.get(TradePlanRecord, ctx[2]).plan_data)
    exposure = ReservationExposure(
        reservation_id=uuid4(),
        ticker="ABC",
        remaining_quantity=2,
        entry=Decimal("100.5"),
        stop=Decimal(98),
        fills=(
            FilledExposure(quantity=1, price=Decimal(101), active_stop=Decimal(98)),
        ),
    )
    with localcontext() as context:
        context.prec = 2
        # 500 - (2*100.5 + 1*101) = 198: only one new share affordable.
        assert ctx[1]._sized_quantity(plan, ctx[1].snapshot, (exposure,)) == 1
