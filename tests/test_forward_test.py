from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from src.database import Base
from src.domain.models import (
    JournalEntry,
    OrderAction,
    OrderIntent,
    OrderType,
    RiskIntent,
    ScoreSnapshot,
    SecurityRef,
    SetupType,
    TradePlan,
    TradeSide,
    TradingStyle,
)
from src.execution import ExecutionService, PaperBrokerAdapter
from src.feedback import PaperFillJournalService
from src.forward_test import (
    ForwardTestOrchestrator,
    ForwardTestRunInput,
    ForwardTestRunStatus,
    playbook_digest,
)
from src.market_data import Bar, MarketEvent, Observation
from src.operational_guards import OperationalGuard, OperationalGuardPolicy
from src.persistence.forward_test_repository import ForwardTestRunRepository
from src.playbook import PLAYBOOK
from src.portfolio import RiskDecision
from src.proposals import (
    ApprovalDecision,
    HumanApproval,
    ProposalExecutionGate,
    ProposalRiskSnapshot,
    TradeProposal,
)

AT = datetime(2026, 10, 27, 15, 0, tzinfo=UTC)


class RiskChecker:
    def __init__(self, result):
        self.result = result
        self.calls = 0

    def check(self, proposal, at):
        self.calls += 1
        return self.result


class JournalRepository:
    def __init__(self):
        self.saved = []

    def save(self, entry, expected_revision=None):
        self.saved.append((entry, expected_revision))
        return len(self.saved)

    def get(self, journal_id):
        return None

    def get_revision(self, journal_id, revision):
        return None

    def get_by_trade_id(self, trade_id):
        return None


class RunRepository:
    def __init__(self):
        self.saved = []

    def save(self, evidence):
        self.saved.append(evidence)


def plan() -> TradePlan:
    return TradePlan(
        security=SecurityRef(ticker="ABC"),
        side=TradeSide.LONG,
        trading_style=TradingStyle.SWING,
        setup_type=SetupType.BREAKOUT,
        entry=Decimal(100),
        stop=Decimal(98),
        target=Decimal(106),
        quantity=Decimal(50),
        planned_at=AT - timedelta(minutes=10),
        score_snapshot=ScoreSnapshot(
            decided_at=AT - timedelta(minutes=12),
            market_opportunity=82,
            discovery=76,
            setup=91,
            regime_score=2,
            inflection_stage=4,
        ),
        risk_intent=RiskIntent(stop_risk_budget=Decimal(100)),
    )


def proposal() -> TradeProposal:
    trade_plan = plan()
    intent = OrderIntent(
        plan_id=trade_plan.id,
        action=OrderAction.BUY,
        order_type=OrderType.LIMIT,
        quantity=Decimal(50),
        limit_price=Decimal(101),
        created_at=AT - timedelta(minutes=8),
    )
    return TradeProposal(
        plan=trade_plan,
        intent=intent,
        risk=ProposalRiskSnapshot(
            plan_id=trade_plan.id,
            decision=RiskDecision.ALLOW,
            approved_quantity=50,
            portfolio_heat_r=Decimal("2.25"),
            checked_at=AT - timedelta(minutes=7),
            policy_version="portfolio-v0.1",
            taxonomy_version="taxonomy-v1",
        ),
        created_at=AT - timedelta(minutes=6),
    )


def approval(candidate: TradeProposal, *, decision=ApprovalDecision.APPROVE):
    return HumanApproval(
        proposal_id=candidate.id,
        proposal_fingerprint=candidate.fingerprint,
        decision=decision,
        approver_identity="human:owner",
        decided_at=AT - timedelta(minutes=5),
    )


def observation(candidate: TradeProposal, *, minutes_old=1) -> Observation:
    event_time = AT - timedelta(minutes=minutes_old)
    return Observation(
        event=MarketEvent(
            source="forward-fixture",
            symbol=candidate.plan.security.ticker,
            event_time=event_time,
            available_at=event_time + timedelta(seconds=1),
            revision=1,
            payload=Bar(
                interval="1m",
                open=Decimal(99),
                high=Decimal(101),
                low=Decimal(99),
                close=Decimal(100),
                volume=100_000,
            ),
        ),
        ingested_at=event_time + timedelta(seconds=2),
    )


def fresh_risk(candidate: TradeProposal):
    return ProposalRiskSnapshot(
        plan_id=candidate.plan.id,
        decision=RiskDecision.ALLOW,
        approved_quantity=50,
        portfolio_heat_r=Decimal("2.5"),
        checked_at=AT - timedelta(seconds=30),
        policy_version="portfolio-v0.1",
        taxonomy_version="taxonomy-v1",
    )


def orchestrator(candidate, *, max_age=timedelta(minutes=2), playbook=None):
    risk = RiskChecker(fresh_risk(candidate))
    journal_repo = JournalRepository()
    run_repo = RunRepository()
    guard = OperationalGuard(
        policy=OperationalGuardPolicy(
            max_order_quantity=Decimal(100),
            max_order_notional=Decimal(20_000),
            max_orders_per_day=10,
            max_market_data_age=max_age,
        )
    )
    service = ForwardTestOrchestrator(
        execution_gate=ProposalExecutionGate(
            risk_checker=risk,
            execution_service=ExecutionService(PaperBrokerAdapter()),
            operational_guard=guard,
        ),
        journal_service=PaperFillJournalService(journal_repo),
        run_repository=run_repo,
        playbook=playbook or PLAYBOOK,
    )
    return service, risk, journal_repo, run_repo


def request(candidate, *, market=None, human=None):
    return ForwardTestRunInput(
        proposal=candidate,
        human_approval=human or approval(candidate),
        market_observation=market or observation(candidate),
        as_of=AT,
        orders_submitted_today=0,
        idempotency_key="forward-abc-1",
        journal_entry=JournalEntry(
            plan=candidate.plan,
            created_at=candidate.created_at,
        ),
        expected_journal_revision=0,
    )


def test_forward_test_run_executes_paper_and_persists_trace_evidence():
    candidate = proposal()
    service, risk, journal_repo, run_repo = orchestrator(candidate)

    result = service.run(request(candidate))

    assert result.evidence.status is ForwardTestRunStatus.FILLED
    assert result.evidence.run_type.value == "PAPER"
    assert result.evidence.playbook_version == PLAYBOOK.version
    assert result.evidence.playbook_digest == playbook_digest(PLAYBOOK)
    assert result.evidence.market_source == "forward-fixture"
    assert result.evidence.market_revision == 1
    assert result.evidence.manual_step_types == ("HUMAN_APPROVAL",)
    assert result.evidence.live_execution_used is False
    assert result.evidence.autonomy_policy_used is False
    assert result.evidence.broker_adapter == "paper"
    assert result.evidence.journal_revision == 1
    assert result.broker_receipt is not None
    assert risk.calls == 1
    assert len(journal_repo.saved) == 1
    assert run_repo.saved == [result.evidence]


def test_rejected_human_approval_is_recorded_as_blocked_run():
    candidate = proposal()
    service, risk, journal_repo, run_repo = orchestrator(candidate)

    result = service.run(
        request(
            candidate,
            human=approval(candidate, decision=ApprovalDecision.REJECT),
        )
    )

    assert result.evidence.status is ForwardTestRunStatus.BLOCKED
    assert "cannot authorize execution" in result.evidence.blocked_reason
    assert result.broker_receipt is None
    assert risk.calls == 0
    assert journal_repo.saved == []
    assert run_repo.saved == [result.evidence]


def test_stale_market_observation_is_recorded_as_blocked_run():
    candidate = proposal()
    service, risk, journal_repo, run_repo = orchestrator(
        candidate,
        max_age=timedelta(seconds=30),
    )

    result = service.run(
        request(candidate, market=observation(candidate, minutes_old=1))
    )

    assert result.evidence.status is ForwardTestRunStatus.BLOCKED
    assert "market data is stale" in result.evidence.blocked_reason
    assert risk.calls == 1
    assert journal_repo.saved == []
    assert run_repo.saved == [result.evidence]


def test_playbook_policy_drift_blocks_before_risk_or_broker():
    candidate = proposal()
    mutable_playbook = PLAYBOOK.model_copy(deep=True)
    service, risk, journal_repo, run_repo = orchestrator(
        candidate,
        playbook=mutable_playbook,
    )
    mutable_playbook.version = "3.0-drifted"

    result = service.run(request(candidate))

    assert result.evidence.status is ForwardTestRunStatus.BLOCKED
    assert result.evidence.blocked_reason == "PLAYBOOK_POLICY_DRIFT"
    assert risk.calls == 0
    assert journal_repo.saved == []
    assert run_repo.saved == [result.evidence]


def test_future_market_observation_cannot_enter_forward_test():
    candidate = proposal()
    future = Observation(
        event=MarketEvent(
            source="forward-fixture",
            symbol="ABC",
            event_time=AT + timedelta(minutes=1),
            available_at=AT + timedelta(minutes=1, seconds=1),
            revision=1,
            payload=Bar(
                interval="1m",
                open=Decimal(100),
                high=Decimal(101),
                low=Decimal(99),
                close=Decimal(100),
                volume=100,
            ),
        ),
        ingested_at=AT + timedelta(minutes=1, seconds=2),
    )

    with pytest.raises(ValidationError, match="future"):
        request(candidate, market=future)


def test_forward_test_repository_round_trip_is_idempotent():
    candidate = proposal()
    service, _, _, run_repo = orchestrator(candidate)
    result = service.run(request(candidate))
    evidence = run_repo.saved[0]

    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        repository = ForwardTestRunRepository(session)
        repository.save(evidence)
        repository.save(evidence)
        session.commit()

        restored = repository.get(evidence.run_id)
        listed = repository.list_between(
            start=AT - timedelta(minutes=1),
            end=AT + timedelta(minutes=1),
        )

    assert restored == evidence
    assert listed == (evidence,)
    assert result.evidence == evidence
