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
    resolve_regime_risk_multiplier,
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


@pytest.mark.parametrize(
    ("regime", "expected"),
    [
        (Regime.RISK_ON, Decimal("1.00")),
        (Regime.RISK_ON_NARROW, Decimal("0.75")),
        (Regime.NEUTRAL_CHOP, Decimal("0.50")),
    ],
)
def test_fixed_regime_risk_multipliers(regime, expected):
    assert resolve_regime_risk_multiplier(regime) == expected


def test_risk_off_requires_explicit_long_side_policy_in_range():
    with pytest.raises(ValueError, match="only to the long side"):
        resolve_regime_risk_multiplier(
            Regime.RISK_OFF, explicit_multiplier=Decimal("0.1")
        )
    with pytest.raises(ValueError, match="only to the long side"):
        resolve_regime_risk_multiplier(
            Regime.RISK_OFF,
            side=TradeSide.SHORT,
            explicit_multiplier=Decimal("0.1"),
        )
    with pytest.raises(ValueError, match="requires an explicit"):
        resolve_regime_risk_multiplier(Regime.RISK_OFF, side=TradeSide.LONG)

    assert resolve_regime_risk_multiplier(
        Regime.RISK_OFF,
        side=TradeSide.LONG,
        explicit_multiplier=Decimal(0),
    ) == Decimal(0)
    assert resolve_regime_risk_multiplier(
        Regime.RISK_OFF,
        side=TradeSide.LONG,
        explicit_multiplier=Decimal("0.25"),
    ) == Decimal("0.25")

    for multiplier in (Decimal("-0.01"), Decimal("0.26"), Decimal("NaN"), False):
        with pytest.raises(ValueError):
            resolve_regime_risk_multiplier(
                Regime.RISK_OFF,
                side=TradeSide.LONG,
                explicit_multiplier=multiplier,
            )


def test_capitulation_regime_requires_authorized_special_setup_or_no_trade():
    with pytest.raises(ValueError, match="separately authorized"):
        resolve_regime_risk_multiplier(Regime.CAPITULATION_REVERSAL)

    assert (
        resolve_regime_risk_multiplier(
            Regime.CAPITULATION_REVERSAL,
            special_setup=True,
        )
        is None
    )
    assert resolve_regime_risk_multiplier(
        Regime.CAPITULATION_REVERSAL,
        special_setup=True,
        explicit_multiplier=Decimal("0.1"),
    ) == Decimal("0.1")
    for multiplier in (Decimal("-0.1"), Decimal("Infinity"), True):
        with pytest.raises(ValueError):
            resolve_regime_risk_multiplier(
                Regime.CAPITULATION_REVERSAL,
                special_setup=True,
                explicit_multiplier=multiplier,
            )


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
        trade_id=plan.trade_id,
        version=2,
        supersedes_plan_id=plan.id,
        entry=Decimal(101),
        risk_intent=RiskIntent(stop_risk_budget=Decimal(150)),
    )
    assert amended.id != plan.id
    assert amended.trade_id == plan.trade_id
    assert plan.entry == Decimal(100)

    with pytest.raises(ValidationError, match="must identify"):
        make_plan(version=2)


@pytest.mark.parametrize(
    ("prior_version", "amended_version", "same_trade", "message"),
    [
        (1, 3, True, "immediately follow"),
        (1, 2, False, "same trade_id"),
    ],
)
def test_journal_validates_amendment_lineage_when_prior_plans_are_available(
    prior_version, amended_version, same_trade, message
):
    prior = make_plan(version=prior_version)
    amended = make_plan(
        trade_id=prior.trade_id if same_trade else uuid4(),
        version=amended_version,
        supersedes_plan_id=prior.id,
        planned_at=prior.planned_at + timedelta(minutes=1),
        risk_intent=RiskIntent(stop_risk_budget=Decimal(150)),
    )

    with pytest.raises(ValidationError, match=message):
        JournalEntry(
            plan=amended,
            prior_plans=(prior,),
            created_at=amended.planned_at,
        )


def test_journal_retains_a_valid_plan_lineage_and_immutable_decision_snapshot():
    snapshot = make_score_snapshot(
        regime=Regime.RISK_ON_NARROW,
        mood=Mood.CONFIDENT,
    )
    original = make_plan(score_snapshot=snapshot)
    amended = make_plan(
        trade_id=original.trade_id,
        version=2,
        supersedes_plan_id=original.id,
        planned_at=original.planned_at + timedelta(minutes=1),
        entry=Decimal(101),
        risk_intent=RiskIntent(
            stop_risk_budget=Decimal(150),
            portfolio_risk_fraction=Decimal("0.05"),
            rationale="Amended risk assessment",
        ),
    )
    journal = JournalEntry(
        plan=amended,
        prior_plans=(original,),
        plan_followed=False,
        mood=Mood.CAUTIOUS,
        thesis="The original breakout thesis weakened.",
        lesson="Reassess after a failed retest.",
        created_at=amended.planned_at,
    )

    assert journal.plan.trade_id == original.trade_id
    assert journal.prior_plans == (original,)
    assert journal.prior_plans[0].entry == Decimal(100)
    assert journal.prior_plans[0].score_snapshot == snapshot
    assert journal.prior_plans[0].risk_intent.stop_risk_budget == Decimal(100)
    assert journal.prior_plans[0].score_snapshot.mood is Mood.CONFIDENT
    assert journal.plan_followed is False
    assert journal.mood is Mood.CAUTIOUS
    assert journal.thesis == "The original breakout thesis weakened."
    assert journal.lesson == "Reassess after a failed retest."


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
        mae_r=Decimal("0.5"),
        mfe_r=Decimal("2.5"),
        excursion_source="  consolidated minute bars  ",
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
    assert journal.plan.score_snapshot.market_opportunity == 80
    assert journal.plan.risk_intent.stop_risk_budget == Decimal(100)
    assert journal.result is not None
    assert journal.result.mae_r == Decimal("0.5")
    assert journal.result.mfe_r == Decimal("2.5")
    assert journal.result.excursion_source == "consolidated minute bars"


def test_trade_result_excursions_are_optional_nonnegative_and_provenanced():
    plan = make_plan()
    result_values = {
        "plan_id": plan.id,
        "closed_at": plan.planned_at + timedelta(minutes=1),
        "realized_pnl": Decimal(0),
        "r_multiple": Decimal(0),
        "outcome": "flat",
    }
    assert TradeResult(**result_values).mae_r is None
    assert (
        TradeResult(**result_values, excursion_source="history unavailable").mfe_r
        is None
    )

    with pytest.raises(ValidationError, match="greater than or equal to 0"):
        TradeResult(
            **result_values,
            mae_r=Decimal("-0.1"),
            excursion_source="minute bars",
        )
    with pytest.raises(ValidationError, match="greater than or equal to 0"):
        TradeResult(
            **result_values,
            mfe_r=Decimal("-0.1"),
            excursion_source="minute bars",
        )
    with pytest.raises(ValidationError, match="require an excursion source"):
        TradeResult(**result_values, mae_r=Decimal("0.5"))
    with pytest.raises(ValidationError, match="must not be blank"):
        TradeResult(**result_values, excursion_source="  ")


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
