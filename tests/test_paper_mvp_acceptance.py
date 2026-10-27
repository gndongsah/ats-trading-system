import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from src.domain.models import (
    JournalEntry,
    OrderAction,
    OrderIntent,
    OrderType,
    PositionSnapshot,
    RiskIntent,
    ScoreSnapshot,
    SecurityRef,
    SetupType,
    TradePlan,
    TradeSide,
    TradingStyle,
)
from src.execution import ExecutionService, PaperBrokerAdapter, SubmissionStatus
from src.feedback import PaperFillJournalService
from src.operational_guards import OperationalGuard, OperationalGuardPolicy
from src.portfolio import RiskDecision
from src.proposals import (
    ApprovalDecision,
    HumanApproval,
    ProposalExecutionGate,
    ProposalRiskSnapshot,
    TradeProposal,
    execution_authorization_from_human_approval,
)
from src.run_type import RunType

DAY = datetime(2026, 10, 27, 14, 45, tzinfo=UTC)


class SequencedRiskChecker:
    def __init__(self, results):
        self._results = list(results)
        self.calls = 0

    def check(self, proposal, at):
        result = self._results[self.calls]
        self.calls += 1
        return result


class RecordingOperationalGuard:
    def __init__(self):
        self.calls = 0
        self._guard = OperationalGuard(
            policy=OperationalGuardPolicy(
                max_order_quantity=Decimal(100),
                max_order_notional=Decimal(25_000),
                max_orders_per_day=10,
                max_market_data_age=timedelta(seconds=30),
            )
        )

    def validate(self, *, request, context):
        self.calls += 1
        self._guard.validate(request=request, context=context)


class AcceptanceJournalRepository:
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


def _plan() -> TradePlan:
    return TradePlan(
        security=SecurityRef(ticker="ABC"),
        side=TradeSide.LONG,
        trading_style=TradingStyle.SWING,
        setup_type=SetupType.BREAKOUT,
        entry=Decimal(100),
        stop=Decimal(98),
        target=Decimal(106),
        quantity=Decimal(50),
        planned_at=DAY + timedelta(minutes=5),
        score_snapshot=ScoreSnapshot(
            decided_at=DAY,
            market_opportunity=82,
            discovery=76,
            setup=91,
            regime_score=2,
            inflection_stage=4,
        ),
        risk_intent=RiskIntent(stop_risk_budget=Decimal(100)),
    )


def _proposal(
    *,
    plan,
    intent,
    checked_at,
    created_at,
) -> TradeProposal:
    return TradeProposal(
        plan=plan,
        intent=intent,
        risk=ProposalRiskSnapshot(
            plan_id=plan.id,
            decision=RiskDecision.ALLOW,
            approved_quantity=50,
            portfolio_heat_r=Decimal("2.25"),
            checked_at=checked_at,
            policy_version="portfolio-v0.1",
            taxonomy_version="taxonomy-v1",
        ),
        created_at=created_at,
    )


def _approval(proposal, at):
    return HumanApproval(
        proposal_id=proposal.id,
        proposal_fingerprint=proposal.fingerprint,
        decision=ApprovalDecision.APPROVE,
        approver_identity="human:owner",
        decided_at=at,
    )


def test_complete_paper_day_requires_only_human_approval(record_property):
    plan = _plan()

    entry_intent = OrderIntent(
        plan_id=plan.id,
        action=OrderAction.BUY,
        order_type=OrderType.LIMIT,
        quantity=Decimal(50),
        limit_price=Decimal(101),
        created_at=DAY + timedelta(minutes=7),
    )
    entry_proposal = _proposal(
        plan=plan,
        intent=entry_intent,
        checked_at=DAY + timedelta(minutes=8),
        created_at=DAY + timedelta(minutes=9),
    )
    entry_approval = _approval(entry_proposal, DAY + timedelta(minutes=10))
    entry_authorization = execution_authorization_from_human_approval(entry_approval)

    exit_intent = OrderIntent(
        plan_id=plan.id,
        action=OrderAction.SELL,
        order_type=OrderType.MARKET,
        quantity=Decimal(50),
        created_at=DAY + timedelta(hours=4, minutes=55),
    )
    exit_proposal = _proposal(
        plan=plan,
        intent=exit_intent,
        checked_at=DAY + timedelta(hours=4, minutes=56),
        created_at=DAY + timedelta(hours=4, minutes=57),
    )
    exit_approval = _approval(
        exit_proposal,
        DAY + timedelta(hours=4, minutes=58),
    )
    exit_authorization = execution_authorization_from_human_approval(exit_approval)

    risk_checker = SequencedRiskChecker(
        (
            ProposalRiskSnapshot(
                plan_id=plan.id,
                decision=RiskDecision.ALLOW,
                approved_quantity=50,
                portfolio_heat_r=Decimal("2.50"),
                checked_at=DAY + timedelta(minutes=14),
                policy_version="portfolio-v0.1",
                taxonomy_version="taxonomy-v1",
            ),
            ProposalRiskSnapshot(
                plan_id=plan.id,
                decision=RiskDecision.ALLOW,
                approved_quantity=50,
                portfolio_heat_r=Decimal("2.00"),
                checked_at=DAY + timedelta(hours=4, minutes=59),
                policy_version="portfolio-v0.1",
                taxonomy_version="taxonomy-v1",
            ),
        )
    )
    guard = RecordingOperationalGuard()
    broker = PaperBrokerAdapter()
    gate = ProposalExecutionGate(
        risk_checker=risk_checker,
        execution_service=ExecutionService(broker),
        operational_guard=guard,
    )

    entry_time = DAY + timedelta(minutes=15)
    entry_receipt = gate.execute(
        proposal=entry_proposal,
        execution_authorization=entry_authorization,
        reference_price=Decimal(100),
        idempotency_key="mvp-day-entry",
        at=entry_time,
        market_data_observed_at=entry_time - timedelta(seconds=5),
        orders_submitted_today=0,
    )

    repository = AcceptanceJournalRepository()
    journal_service = PaperFillJournalService(repository)
    journal = JournalEntry(plan=plan, created_at=entry_proposal.created_at)
    journal, entry_revision = journal_service.capture(
        entry=journal,
        intent=entry_intent,
        receipt=entry_receipt,
        captured_at=entry_time + timedelta(seconds=1),
        expected_revision=0,
    )

    snapshots = (
        PositionSnapshot(
            plan_id=plan.id,
            as_of=DAY + timedelta(hours=1),
            side=TradeSide.LONG,
            quantity=Decimal(50),
            average_entry=Decimal(100),
            unrealized_pnl=Decimal(-25),
        ),
        PositionSnapshot(
            plan_id=plan.id,
            as_of=DAY + timedelta(hours=3),
            side=TradeSide.LONG,
            quantity=Decimal(50),
            average_entry=Decimal(100),
            unrealized_pnl=Decimal(150),
        ),
    )

    exit_time = DAY + timedelta(hours=5)
    exit_receipt = gate.execute(
        proposal=exit_proposal,
        execution_authorization=exit_authorization,
        reference_price=Decimal(104),
        idempotency_key="mvp-day-exit",
        at=exit_time,
        market_data_observed_at=exit_time - timedelta(seconds=5),
        orders_submitted_today=1,
    )
    journal, exit_revision = journal_service.capture(
        entry=journal,
        intent=exit_intent,
        receipt=exit_receipt,
        captured_at=exit_time + timedelta(seconds=1),
        expected_revision=entry_revision,
        position_snapshots=snapshots,
    )

    assert entry_receipt.status is SubmissionStatus.FILLED
    assert exit_receipt.status is SubmissionStatus.FILLED
    assert entry_receipt.run_type is RunType.PAPER
    assert exit_receipt.run_type is RunType.PAPER
    assert entry_receipt.adapter == exit_receipt.adapter == "paper"
    assert risk_checker.calls == 2
    assert guard.calls == 2
    assert entry_revision == 1
    assert exit_revision == 2
    assert len(repository.saved) == 2
    assert journal.result is not None
    assert journal.result.run_type is RunType.PAPER
    assert journal.result.realized_pnl == Decimal(200)
    assert journal.result.r_multiple == Decimal(2)
    assert journal.result.mae_r == Decimal("0.25")
    assert journal.result.mfe_r == Decimal("1.5")
    assert journal.plan_followed is True

    evidence = {
        "source": "SIMULATION-ONLY",
        "manual_step_types": ["HUMAN_APPROVAL"],
        "human_approvals": 2,
        "execution_authorization_sources": [
            entry_authorization.source.value,
            exit_authorization.source.value,
        ],
        "risk_checks": risk_checker.calls,
        "operational_guard_checks": guard.calls,
        "paper_fills": 2,
        "journal_revisions": len(repository.saved),
        "final_result": {
            "run_type": journal.result.run_type.value,
            "realized_pnl": str(journal.result.realized_pnl),
            "r_multiple": str(journal.result.r_multiple),
            "mae_r": str(journal.result.mae_r),
            "mfe_r": str(journal.result.mfe_r),
            "plan_followed": journal.plan_followed,
        },
        "live_execution_used": False,
        "autonomy_policy_used": False,
    }
    record_property("evidence", json.dumps(evidence, sort_keys=True))
