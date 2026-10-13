from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from pydantic import ValidationError

from src.domain.models import (
    EvidenceRef,
    Fill,
    JournalEntry,
    Mood,
    OrderAction,
    OrderIntent,
    OrderType,
    Regime,
    RiskIntent,
    ScoreContribution,
    ScoreSnapshot,
    SecurityRef,
    SetupType,
    TradePlan,
    TradeResult,
    TradeSide,
    TradingStyle,
)

DECISION_TIME = datetime(2026, 1, 2, 15, 0, tzinfo=UTC)


def make_score_snapshot(**overrides) -> ScoreSnapshot:
    values = {
        "decided_at": DECISION_TIME,
        "market_opportunity": 80,
        "discovery": 75,
        "setup": 90,
        "regime_score": 3,
        "inflection_stage": 5,
    }
    values.update(overrides)
    return ScoreSnapshot(**values)


def make_plan(**overrides) -> TradePlan:
    values = {
        "security": SecurityRef(ticker="abc"),
        "side": TradeSide.LONG,
        "trading_style": TradingStyle.SWING,
        "setup_type": SetupType.BREAKOUT,
        "entry": Decimal(100),
        "stop": Decimal(98),
        "target": Decimal(104),
        "quantity": Decimal(50),
        "planned_at": DECISION_TIME + timedelta(minutes=1),
        "score_snapshot": make_score_snapshot(),
        "risk_intent": RiskIntent(stop_risk_budget=Decimal(100)),
    }
    values.update(overrides)
    return TradePlan(**values)


def test_score_ranges_and_positive_contributions_require_evidence():
    with pytest.raises(ValidationError):
        make_score_snapshot(market_opportunity=101)
    with pytest.raises(ValidationError):
        make_score_snapshot(regime_score=0)
    with pytest.raises(ValidationError):
        make_score_snapshot(inflection_stage=10)
    with pytest.raises(ValidationError, match="require evidence"):
        ScoreContribution(factor="demand", points=Decimal(1))


def test_score_snapshot_records_market_regime_and_mood():
    snapshot = make_score_snapshot(regime=Regime.RISK_ON, mood=Mood.CALM)
    assert snapshot.regime is Regime.RISK_ON
    assert snapshot.mood is Mood.CALM


def test_scores_and_decision_timestamps_require_utc():
    with pytest.raises(ValidationError, match="UTC"):
        make_score_snapshot(decided_at=DECISION_TIME.replace(tzinfo=None))


def test_evidence_must_be_available_by_score_decision_time():
    evidence = EvidenceRef(
        source="Company filing",
        observed_at=DECISION_TIME + timedelta(seconds=1),
        confidence=Decimal("0.9"),
    )
    contribution = ScoreContribution(
        factor="demand", points=Decimal(10), evidence=(evidence,)
    )
    with pytest.raises(ValidationError, match="observed after"):
        make_score_snapshot(contributions=(contribution,))


@pytest.mark.parametrize(
    ("side", "stop", "target"),
    [
        (TradeSide.LONG, Decimal(101), Decimal(104)),
        (TradeSide.LONG, Decimal(98), Decimal(99)),
        (TradeSide.SHORT, Decimal(99), Decimal(95)),
        (TradeSide.SHORT, Decimal(102), Decimal(104)),
    ],
)
def test_plan_prices_follow_trade_direction(side, stop, target):
    with pytest.raises(ValidationError):
        make_plan(side=side, stop=stop, target=target)


def test_plan_quantity_must_fit_stop_risk_budget():
    with pytest.raises(ValidationError, match="stop-risk budget"):
        make_plan(quantity=Decimal(51))


def test_trade_plan_is_immutable_and_amendments_are_versioned():
    plan = make_plan()
    with pytest.raises(ValidationError):
        plan.entry = Decimal(101)

    amended = make_plan(
        version=2,
        supersedes_plan_id=plan.id,
        entry=Decimal(101),
        risk_intent=RiskIntent(stop_risk_budget=Decimal(150)),
    )
    assert amended.id != plan.id
    assert plan.entry == Decimal(100)

    with pytest.raises(ValidationError, match="must identify"):
        make_plan(version=2)


def test_evidence_reference_may_be_unverified_but_is_not_unsupported():
    evidence = EvidenceRef(
        source="Market data",
        observed_at=DECISION_TIME,
        confidence=Decimal("0.6"),
    )
    snapshot = make_score_snapshot(
        contributions=(
            ScoreContribution(
                factor="momentum", points=Decimal(5), evidence=(evidence,)
            ),
        )
    )
    assert (
        snapshot.contributions[0].evidence[0].verification_status.value == "unverified"
    )


def test_fill_and_result_attach_without_changing_decision_time_plan():
    plan = make_plan()
    order = OrderIntent(
        plan_id=plan.id,
        action=OrderAction.SELL,
        order_type=OrderType.MARKET,
        quantity=plan.quantity,
        created_at=plan.planned_at + timedelta(hours=1),
    )
    fill = Fill(
        order_intent_id=order.id,
        filled_at=order.created_at + timedelta(seconds=1),
        quantity=plan.quantity,
        price=plan.target,
    )
    result = TradeResult(
        plan_id=plan.id,
        closed_at=fill.filled_at,
        realized_pnl=Decimal(200),
        r_multiple=Decimal(2),
        outcome="win",
    )
    journal = JournalEntry(
        plan=plan,
        orders=(order,),
        fills=(fill,),
        result=result,
        created_at=result.closed_at,
    )

    assert journal.plan == plan
    assert journal.plan.entry == Decimal(100)
    assert journal.plan.score_snapshot.decided_at == DECISION_TIME


def test_journal_rejects_orphaned_fill_or_wrong_plan_result():
    plan = make_plan()
    orphan_fill = Fill(
        order_intent_id=uuid4(),
        filled_at=plan.planned_at + timedelta(seconds=1),
        quantity=Decimal(1),
        price=Decimal(100),
    )
    with pytest.raises(ValidationError, match="reference a journal order"):
        JournalEntry(
            plan=plan,
            fills=(orphan_fill,),
            created_at=plan.planned_at + timedelta(seconds=2),
        )

    wrong_result = TradeResult(
        plan_id=uuid4(),
        closed_at=plan.planned_at + timedelta(minutes=1),
        realized_pnl=Decimal(0),
        r_multiple=Decimal(0),
        outcome="flat",
    )
    with pytest.raises(ValidationError, match="reference the journal plan"):
        JournalEntry(
            plan=plan,
            result=wrong_result,
            created_at=wrong_result.closed_at,
        )


def test_journal_rejects_fills_that_exceed_the_order_quantity():
    plan = make_plan()
    order = OrderIntent(
        plan_id=plan.id,
        action=OrderAction.BUY,
        order_type=OrderType.LIMIT,
        quantity=Decimal(5),
        limit_price=Decimal(100),
        created_at=plan.planned_at + timedelta(minutes=1),
    )
    fills = tuple(
        Fill(
            order_intent_id=order.id,
            filled_at=order.created_at + timedelta(seconds=seconds),
            quantity=Decimal(quantity),
            price=Decimal(100),
        )
        for seconds, quantity in ((1, 3), (2, 3))
    )
    with pytest.raises(ValidationError, match="cannot exceed the order quantity"):
        JournalEntry(
            plan=plan,
            orders=(order,),
            fills=fills,
            created_at=fills[-1].filled_at,
        )
