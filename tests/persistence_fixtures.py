from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

from src.domain.models import (
    EvidenceRef,
    Fill,
    JournalEntry,
    Mood,
    OrderAction,
    OrderIntent,
    OrderType,
    PositionSnapshot,
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
from src.run_type import RunType

DECISION_TIME = datetime(2026, 1, 2, 15, 0, tzinfo=UTC)


def make_complete_trade() -> JournalEntry:
    evidence = EvidenceRef(
        source="Company filing",
        source_url="https://example.test/filing",
        published_at=DECISION_TIME - timedelta(hours=1),
        observed_at=DECISION_TIME - timedelta(minutes=30),
        metric="revenue_growth",
        excerpt="Revenue growth accelerated.",
        confidence=Decimal("0.95"),
    )
    snapshot = ScoreSnapshot(
        decided_at=DECISION_TIME,
        market_opportunity=82,
        discovery=77,
        setup=88,
        regime_score=2,
        inflection_stage=5,
        mood=Mood.CONFIDENT,
        contributions=(
            ScoreContribution(
                factor="demand",
                points=Decimal("8.5"),
                evidence=(evidence,),
            ),
        ),
    )
    trade_id = uuid4()
    original_plan = TradePlan(
        trade_id=trade_id,
        version=1,
        security=SecurityRef(ticker="abc", sector="technology"),
        side=TradeSide.LONG,
        trading_style=TradingStyle.SWING,
        setup_type=SetupType.BREAKOUT,
        entry=Decimal(100),
        stop=Decimal(98),
        target=Decimal(106),
        quantity=Decimal(50),
        planned_at=DECISION_TIME + timedelta(minutes=1),
        score_snapshot=snapshot,
        risk_intent=RiskIntent(
            stop_risk_budget=Decimal(100),
            portfolio_risk_fraction=Decimal("0.02"),
            rationale="Original decision risk budget",
        ),
    )
    plan = TradePlan(
        trade_id=trade_id,
        version=2,
        supersedes_plan_id=original_plan.id,
        security=original_plan.security,
        side=original_plan.side,
        trading_style=original_plan.trading_style,
        setup_type=original_plan.setup_type,
        entry=Decimal("100.5"),
        stop=original_plan.stop,
        target=original_plan.target,
        quantity=original_plan.quantity,
        planned_at=DECISION_TIME + timedelta(minutes=2),
        score_snapshot=snapshot,
        risk_intent=RiskIntent(
            stop_risk_budget=Decimal(125),
            portfolio_risk_fraction=Decimal("0.025"),
            rationale="Amended after a confirmed retest",
        ),
    )
    order = OrderIntent(
        plan_id=plan.id,
        action=OrderAction.BUY,
        order_type=OrderType.LIMIT,
        quantity=plan.quantity,
        limit_price=plan.entry,
        created_at=plan.planned_at + timedelta(minutes=1),
    )
    fill = Fill(
        order_intent_id=order.id,
        filled_at=order.created_at + timedelta(seconds=3),
        quantity=plan.quantity,
        price=plan.entry,
        fees=Decimal("1.25"),
    )
    position = PositionSnapshot(
        plan_id=plan.id,
        as_of=fill.filled_at + timedelta(hours=2),
        side=TradeSide.LONG,
        quantity=Decimal(50),
        average_entry=plan.entry,
        unrealized_pnl=Decimal(75),
    )
    result = TradeResult(
        plan_id=plan.id,
        run_type=RunType.PAPER,
        closed_at=position.as_of + timedelta(hours=4),
        realized_pnl=Decimal(250),
        r_multiple=Decimal("2.5"),
        outcome="win",
        mae_r=Decimal("0.25"),
        mfe_r=Decimal("3.1"),
        excursion_source="consolidated hourly bars",
    )
    return JournalEntry(
        id=uuid4(),
        plan=plan,
        prior_plans=(original_plan,),
        orders=(order,),
        fills=(fill,),
        position_snapshots=(position,),
        result=result,
        plan_followed=True,
        mood=Mood.CALM,
        thesis="Demand and breakout structure remained intact.",
        lesson="Wait for a confirmed retest before sizing.",
        created_at=result.closed_at,
    )
