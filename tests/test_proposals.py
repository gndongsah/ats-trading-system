from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from pydantic import ValidationError

from src.domain.models import (
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
from src.execution import ExecutionService, PaperBrokerAdapter, SubmissionStatus
from src.portfolio import RiskDecision
from src.proposals import (
    ApprovalDecision,
    HumanApproval,
    ProposalExecutionGate,
    ProposalRiskSnapshot,
    TradeProposal,
)

NOW = datetime(2026, 10, 27, 20, 0, tzinfo=UTC)


def make_plan() -> TradePlan:
    score = ScoreSnapshot(
        decided_at=NOW,
        market_opportunity=82,
        discovery=76,
        setup=91,
        regime_score=2,
        inflection_stage=4,
    )
    return TradePlan(
        security=SecurityRef(ticker="abc"),
        side=TradeSide.LONG,
        trading_style=TradingStyle.SWING,
        setup_type=SetupType.BREAKOUT,
        entry=Decimal(100),
        stop=Decimal(98),
        target=Decimal(106),
        quantity=Decimal(50),
        planned_at=NOW + timedelta(minutes=1),
        score_snapshot=score,
        risk_intent=RiskIntent(stop_risk_budget=Decimal(100)),
    )


def make_proposal() -> TradeProposal:
    plan = make_plan()
    intent = OrderIntent(
        plan_id=plan.id,
        action=OrderAction.BUY,
        order_type=OrderType.LIMIT,
        quantity=Decimal(50),
        limit_price=Decimal(101),
        created_at=NOW + timedelta(minutes=2),
    )
    risk = ProposalRiskSnapshot(
        plan_id=plan.id,
        decision=RiskDecision.ALLOW,
        approved_quantity=50,
        portfolio_heat_r=Decimal("2.25"),
        checked_at=NOW + timedelta(minutes=2),
        policy_version="portfolio-v0.1",
        taxonomy_version="taxonomy-v1",
    )
    return TradeProposal(
        plan=plan,
        intent=intent,
        risk=risk,
        created_at=NOW + timedelta(minutes=3),
    )


def approve(proposal: TradeProposal) -> HumanApproval:
    return HumanApproval(
        proposal_id=proposal.id,
        proposal_fingerprint=proposal.fingerprint,
        decision=ApprovalDecision.APPROVE,
        approver_identity="human:owner",
        decided_at=NOW + timedelta(minutes=4),
    )


class RiskChecker:
    def __init__(self, result: ProposalRiskSnapshot):
        self.result = result
        self.calls = 0

    def check(self, proposal, at):
        self.calls += 1
        return self.result


def fresh_risk(proposal: TradeProposal, **overrides) -> ProposalRiskSnapshot:
    values = {
        "plan_id": proposal.plan.id,
        "decision": RiskDecision.ALLOW,
        "approved_quantity": 50,
        "portfolio_heat_r": Decimal("2.50"),
        "checked_at": NOW + timedelta(minutes=5),
        "policy_version": "portfolio-v0.1",
        "taxonomy_version": "taxonomy-v1",
    }
    values.update(overrides)
    return ProposalRiskSnapshot(**values)


def test_proposal_summary_exposes_scores_levels_size_risk_and_heat():
    proposal = make_proposal()

    summary = proposal.display_summary()

    assert summary["market_opportunity_score"] == 82
    assert summary["discovery_score"] == 76
    assert summary["setup_score"] == 91
    assert summary["entry"] == Decimal(100)
    assert summary["stop"] == Decimal(98)
    assert summary["target"] == Decimal(106)
    assert summary["size"] == Decimal(50)
    assert summary["stop_risk_budget"] == Decimal(100)
    assert summary["portfolio_heat_r"] == Decimal("2.25")


def test_exact_human_approval_and_fresh_risk_can_reach_paper_adapter():
    proposal = make_proposal()
    checker = RiskChecker(fresh_risk(proposal))
    gate = ProposalExecutionGate(
        risk_checker=checker,
        execution_service=ExecutionService(PaperBrokerAdapter()),
    )

    receipt = gate.execute(
        proposal=proposal,
        approval=approve(proposal),
        reference_price=Decimal(100),
        idempotency_key="proposal-1",
        at=NOW + timedelta(minutes=6),
    )

    assert checker.calls == 1
    assert receipt.status is SubmissionStatus.FILLED
    assert receipt.symbol == "ABC"


def test_rejected_proposal_never_reaches_risk_or_broker():
    proposal = make_proposal()
    checker = RiskChecker(fresh_risk(proposal))
    rejection = approve(proposal).model_copy(
        update={"decision": ApprovalDecision.REJECT}
    )
    gate = ProposalExecutionGate(
        risk_checker=checker,
        execution_service=ExecutionService(PaperBrokerAdapter()),
    )

    with pytest.raises(PermissionError, match="not approved"):
        gate.execute(
            proposal=proposal,
            approval=rejection,
            reference_price=Decimal(100),
            idempotency_key="proposal-rejected",
            at=NOW + timedelta(minutes=6),
        )
    assert checker.calls == 0


def test_approval_is_bound_to_exact_proposal_fingerprint():
    proposal = make_proposal()
    checker = RiskChecker(fresh_risk(proposal))
    bad = approve(proposal).model_copy(update={"proposal_fingerprint": "0" * 64})
    gate = ProposalExecutionGate(
        risk_checker=checker,
        execution_service=ExecutionService(PaperBrokerAdapter()),
    )

    with pytest.raises(PermissionError, match="does not match"):
        gate.execute(
            proposal=proposal,
            approval=bad,
            reference_price=Decimal(100),
            idempotency_key="proposal-mutated",
            at=NOW + timedelta(minutes=6),
        )


def test_risk_must_be_rechecked_after_approval():
    proposal = make_proposal()
    checker = RiskChecker(
        fresh_risk(proposal, checked_at=NOW + timedelta(minutes=3, seconds=30))
    )
    gate = ProposalExecutionGate(
        risk_checker=checker,
        execution_service=ExecutionService(PaperBrokerAdapter()),
    )

    with pytest.raises(PermissionError, match="after human approval"):
        gate.execute(
            proposal=proposal,
            approval=approve(proposal),
            reference_price=Decimal(100),
            idempotency_key="stale-risk",
            at=NOW + timedelta(minutes=6),
        )


def test_fresh_risk_denial_blocks_paper_execution():
    proposal = make_proposal()
    checker = RiskChecker(
        fresh_risk(
            proposal,
            decision=RiskDecision.DENY,
            approved_quantity=0,
        )
    )
    gate = ProposalExecutionGate(
        risk_checker=checker,
        execution_service=ExecutionService(PaperBrokerAdapter()),
    )

    with pytest.raises(PermissionError, match="denied"):
        gate.execute(
            proposal=proposal,
            approval=approve(proposal),
            reference_price=Decimal(100),
            idempotency_key="risk-denied",
            at=NOW + timedelta(minutes=6),
        )


def test_fresh_resize_below_human_approved_size_fails_closed():
    proposal = make_proposal()
    checker = RiskChecker(
        fresh_risk(
            proposal,
            decision=RiskDecision.RESIZE,
            approved_quantity=40,
        )
    )
    gate = ProposalExecutionGate(
        risk_checker=checker,
        execution_service=ExecutionService(PaperBrokerAdapter()),
    )

    with pytest.raises(PermissionError, match="approved size"):
        gate.execute(
            proposal=proposal,
            approval=approve(proposal),
            reference_price=Decimal(100),
            idempotency_key="resize",
            at=NOW + timedelta(minutes=6),
        )


def test_proposal_rejects_cross_plan_intent():
    proposal = make_proposal()
    wrong_intent = proposal.intent.model_copy(update={"plan_id": make_plan().id})

    with pytest.raises(ValidationError, match="intent must reference"):
        proposal.model_copy(update={"intent": wrong_intent})


def test_whole_share_gate_rejects_fractional_paper_quantity():
    proposal = make_proposal()
    fractional = proposal.model_copy(
        update={
            "intent": proposal.intent.model_copy(update={"quantity": Decimal("49.5")})
        }
    )
    checker = RiskChecker(fresh_risk(fractional))
    gate = ProposalExecutionGate(
        risk_checker=checker,
        execution_service=ExecutionService(PaperBrokerAdapter()),
    )

    with pytest.raises(PermissionError, match="whole-share"):
        gate.execute(
            proposal=fractional,
            approval=approve(fractional),
            reference_price=Decimal(100),
            idempotency_key="fractional",
            at=NOW + timedelta(minutes=6),
        )
