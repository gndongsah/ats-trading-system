from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

from sqlalchemy.orm import sessionmaker

from src.backtest import (
    BacktestCostModel,
    BacktestSizingInputs,
    DeterministicBacktestHarness,
    HistoricalExitObservation,
)
from src.domain.evidence import SourceDocument
from src.domain.models import Regime, TradeSide, TradingStyle
from src.historical_replay import HistoricalReplayService, ResearchReplayRequirement
from src.market_data import Bar, MarketDataStore, MarketEvent, ReplayRequirement
from src.persistence.evidence_repository import EvidenceRepository
from src.regime import RegimeClassification
from src.run_type import RunType
from src.setup_score import (
    SetupScoreComponents,
    SwingLongSetupScreen,
    score_setup,
)
from src.swing_triggers import BreakoutRetestInput, evaluate_breakout_retest
from tests import test_postgres_persistence as pg

migrated_postgres_engine = pg.migrated_postgres_engine


def test_postgres_point_in_time_replay_flows_into_deterministic_backtest(
    migrated_postgres_engine,
):
    as_of = datetime(2026, 10, 27, 14, 0, tzinfo=UTC)
    suffix = uuid4().hex[:8]
    market_source = f"ats016-postgres-{suffix}"
    source_id = f"sec:ABC:ats016:{suffix}"
    factory = sessionmaker(bind=migrated_postgres_engine)

    event = MarketEvent(
        source=market_source,
        symbol="ABC",
        event_time=as_of - timedelta(minutes=1),
        available_at=as_of - timedelta(seconds=50),
        revision=1,
        payload=Bar(
            interval="1m",
            open=Decimal(99),
            high=Decimal(101),
            low=Decimal(98),
            close=Decimal(100),
            volume=1000,
        ),
    )
    filing = SourceDocument(
        source_id=source_id,
        version=1,
        source_type="regulatory_filing",
        publisher="SEC",
        subject="ABC",
        published_at=as_of - timedelta(hours=2),
        observed_at=as_of - timedelta(hours=1),
        available_at=as_of - timedelta(hours=1),
        content_hash="a" * 64,
    )

    with factory.begin() as session:
        MarketDataStore(
            session,
            clock=lambda: as_of - timedelta(seconds=40),
        ).ingest(event)
        EvidenceRepository(session).ingest_source(
            filing,
            ingested_at=as_of - timedelta(minutes=50),
        )

    with factory() as session:
        context = HistoricalReplayService(session).replay(
            as_of=as_of,
            market_requirements=(
                ReplayRequirement(
                    source=market_source,
                    symbol="ABC",
                    stream="bar:1m",
                    max_age=timedelta(minutes=2),
                ),
            ),
            research_requirements=(
                ResearchReplayRequirement(
                    subject="ABC",
                    source_type="regulatory_filing",
                    max_publication_age=timedelta(hours=4),
                ),
            ),
        )

    regime = RegimeClassification(
        as_of=as_of,
        regime=Regime.RISK_ON,
        rule_name="risk_on",
        rule_version="1.0",
        playbook_version="3.0",
        matched_signals=(
            "spy_above_50dma",
            "dma50_above_200dma",
            "dma50_rising",
            "dma200_rising",
            "breadth_healthy",
            "advancers_leading",
            "new_highs_expanding",
        ),
        active_signals=(
            "advancers_leading",
            "breadth_healthy",
            "dma200_rising",
            "dma50_above_200dma",
            "dma50_rising",
            "new_highs_expanding",
            "spy_above_50dma",
        ),
        fixed_risk_multiplier=Decimal("1.00"),
    )
    setup = score_setup(
        as_of=as_of,
        components=SetupScoreComponents(
            trend_alignment=Decimal(25),
            relative_strength_vs_spy=Decimal(25),
            structure_quality=Decimal(20),
            liquidity_volume_confirmation=Decimal(15),
            earnings_distance_safety=Decimal(10),
        ),
        regime=regime,
        trading_style=TradingStyle.SWING,
        side=TradeSide.LONG,
        swing_long_screen=SwingLongSetupScreen(
            above_50dma=True,
            dma50_above_200dma=True,
            rs_3m_outperforming_spy=True,
            rs_6m_outperforming_spy=True,
            avg_daily_volume=1_000_000,
            extension_pct_50dma=Decimal(5),
            days_to_earnings=30,
        ),
    )
    trigger = evaluate_breakout_retest(
        BreakoutRetestInput(
            as_of=as_of,
            cleared_flat_base=True,
            rising_volume=True,
            retest_holds=True,
            breakout_level=Decimal(100),
            base_low=Decimal(95),
            stop_pct_below_base=Decimal(4),
        ),
        setup=setup,
    )

    outcome = DeterministicBacktestHarness().run(
        symbol="ABC",
        context=context,
        trigger=trigger,
        sizing=BacktestSizingInputs(
            equity=Decimal(100000),
            risk_fraction=Decimal("0.005"),
            available_buying_power=Decimal(100000),
        ),
        costs=BacktestCostModel(
            entry_slippage_bps=Decimal(0),
            exit_slippage_bps=Decimal(0),
            fee_per_share_per_side=Decimal(0),
            fixed_fee_per_side=Decimal(0),
        ),
        exit_observation=HistoricalExitObservation(
            source="postgres-history-fixture",
            exit_at=as_of + timedelta(days=2),
            exit_price=Decimal(110),
        ),
    )

    assert outcome.context_digest == context.digest
    assert outcome.sizing.quantity == 56
    assert outcome.net_pnl == Decimal(560)
    assert outcome.trade_result.run_type is RunType.REPLAY
    assert outcome.trade_result.outcome == "win"
