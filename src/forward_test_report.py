"""ATS-034 reproducible PAPER forward-test weekly and campaign reporting."""

import json
from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from hashlib import sha256
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from src.domain.models import JournalEntry
from src.forward_test import ForwardTestRunEvidence, ForwardTestRunStatus
from src.forward_test_campaign import (
    ForwardTestCampaignManifest,
    campaign_manifest_digest,
)
from src.forward_test_scorecard import ForwardTestDailyScorecard
from src.run_type import RunType

ZERO = Decimal(0)


class ForwardTestReportKind(StrEnum):
    WEEKLY = "WEEKLY"
    CAMPAIGN = "CAMPAIGN"


class EvidenceIntegrityException(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    code: str = Field(min_length=1)
    detail: str = Field(min_length=1)


class CampaignPerformanceSlice(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    setup: str
    regime: int
    closed_trades: int = Field(ge=0)
    cumulative_r: Decimal
    expectancy_after_costs_r: Decimal | None = None
    max_drawdown_r: Decimal = Field(ge=ZERO)
    average_mae_r: Decimal | None = Field(default=None, ge=ZERO)
    average_mfe_r: Decimal | None = Field(default=None, ge=ZERO)
    adherence_observations: int = Field(ge=0)
    adherence_rate: float | None = Field(default=None, ge=0.0, le=1.0)


class ForwardTestEvidenceReport(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: ForwardTestReportKind
    run_type: RunType = RunType.PAPER
    campaign_id: UUID
    campaign_digest: str = Field(min_length=64, max_length=64)
    period_start: date
    period_end: date
    generated_as_of: datetime
    report_digest: str = Field(min_length=64, max_length=64)

    scorecard_days: int = Field(ge=0)
    proposals: int = Field(ge=0)
    approvals: int = Field(ge=0)
    rejections: int = Field(ge=0)
    blocked_runs: int = Field(ge=0)
    risk_denials: int = Field(ge=0)
    guard_denials: int = Field(ge=0)
    other_denials: int = Field(ge=0)
    fills: int = Field(ge=0)

    closed_trades: int = Field(ge=0)
    cumulative_r: Decimal
    expectancy_after_costs_r: Decimal | None = None
    max_drawdown_r: Decimal = Field(ge=ZERO)
    average_mae_r: Decimal | None = Field(default=None, ge=ZERO)
    average_mfe_r: Decimal | None = Field(default=None, ge=ZERO)
    adherence_observations: int = Field(ge=0)
    adherence_rate: float | None = Field(default=None, ge=0.0, le=1.0)

    risk_gate_breaches: int = Field(ge=0)
    evidence_integrity_exceptions: tuple[EvidenceIntegrityException, ...] = ()
    by_setup_regime: tuple[CampaignPerformanceSlice, ...] = ()


def _average(values: list[Decimal]) -> Decimal | None:
    if not values:
        return None
    return sum(values, ZERO) / Decimal(len(values))


def _max_drawdown(entries: tuple[JournalEntry, ...]) -> Decimal:
    equity = ZERO
    peak = ZERO
    maximum = ZERO
    for entry in entries:
        assert entry.result is not None
        equity += entry.result.r_multiple
        peak = max(peak, equity)
        maximum = max(maximum, peak - equity)
    return maximum


def _adherence(entries: tuple[JournalEntry, ...]) -> tuple[int, float | None]:
    values = [
        entry.plan_followed for entry in entries if entry.plan_followed is not None
    ]
    if not values:
        return 0, None
    return len(values), sum(bool(value) for value in values) / len(values)


def _performance_slice(
    entries: tuple[JournalEntry, ...], *, setup: str, regime: int
) -> CampaignPerformanceSlice:
    results = [entry.result for entry in entries if entry.result is not None]
    total_r = sum((result.r_multiple for result in results), ZERO)
    maes = [result.mae_r for result in results if result.mae_r is not None]
    mfes = [result.mfe_r for result in results if result.mfe_r is not None]
    adherence_count, adherence_rate = _adherence(entries)
    count = len(results)
    return CampaignPerformanceSlice(
        setup=setup,
        regime=regime,
        closed_trades=count,
        cumulative_r=total_r,
        expectancy_after_costs_r=total_r / Decimal(count) if count else None,
        max_drawdown_r=_max_drawdown(entries),
        average_mae_r=_average(maes),
        average_mfe_r=_average(mfes),
        adherence_observations=adherence_count,
        adherence_rate=adherence_rate,
    )


def _group_performance(
    entries: tuple[JournalEntry, ...],
) -> tuple[CampaignPerformanceSlice, ...]:
    grouped: dict[tuple[str, int], list[JournalEntry]] = defaultdict(list)
    for entry in entries:
        grouped[
            (entry.plan.setup_type.value, entry.plan.score_snapshot.regime_score)
        ].append(entry)
    return tuple(
        sorted(
            (
                _performance_slice(tuple(group), setup=setup, regime=regime)
                for (setup, regime), group in grouped.items()
            ),
            key=lambda item: (item.setup, item.regime),
        )
    )


def _risk_gate_breach(evidence: ForwardTestRunEvidence) -> bool:
    if evidence.status is not ForwardTestRunStatus.BLOCKED:
        return False
    reason = (evidence.blocked_reason or "").casefold()
    return any(
        token in reason
        for token in (
            "risk",
            "loss lock",
            "portfolio heat",
            "capacity",
            "reservation",
        )
    )


def _validate_and_select(
    *,
    campaign: ForwardTestCampaignManifest,
    scorecards: tuple[ForwardTestDailyScorecard, ...],
    run_evidence: tuple[ForwardTestRunEvidence, ...],
    journals: tuple[JournalEntry, ...],
    period_start: date,
    period_end: date,
    as_of: datetime,
) -> tuple[
    tuple[ForwardTestDailyScorecard, ...],
    tuple[ForwardTestRunEvidence, ...],
    tuple[JournalEntry, ...],
    tuple[EvidenceIntegrityException, ...],
]:
    if as_of.tzinfo is None or as_of.utcoffset() is None:
        raise ValueError("report as_of must be timezone-aware")
    as_of = as_of.astimezone(UTC)
    if period_end < period_start:
        raise ValueError("report period end must not precede start")
    if period_start < campaign.start_at.date() or period_end > campaign.end_at.date():
        raise ValueError("report period must remain inside campaign window")

    selected_scorecards = []
    seen_days: set[date] = set()
    exceptions: list[EvidenceIntegrityException] = []
    for scorecard in scorecards:
        if scorecard.run_type is not RunType.PAPER:
            raise ValueError("forward-test report accepts PAPER scorecards only")
        if scorecard.trading_day > as_of.date():
            raise ValueError("future scorecard evidence is not allowed")
        if period_start <= scorecard.trading_day <= period_end:
            if scorecard.trading_day in seen_days:
                exceptions.append(
                    EvidenceIntegrityException(
                        code="DUPLICATE_SCORECARD_DAY",
                        detail=scorecard.trading_day.isoformat(),
                    )
                )
                continue
            seen_days.add(scorecard.trading_day)
            selected_scorecards.append(scorecard)

    selected_runs = []
    seen_runs: set[UUID] = set()
    expected_digest = campaign_manifest_digest(campaign)
    for evidence in run_evidence:
        if evidence.run_type is not RunType.PAPER:
            raise ValueError("forward-test report accepts PAPER run evidence only")
        if evidence.as_of > as_of:
            raise ValueError("future run evidence is not allowed")
        if evidence.campaign_id != campaign.campaign_id:
            raise ValueError("run evidence belongs to a different campaign")
        if evidence.campaign_digest != expected_digest:
            raise ValueError("run evidence campaign digest mismatch")
        if period_start <= evidence.as_of.date() <= period_end:
            if evidence.run_id in seen_runs:
                exceptions.append(
                    EvidenceIntegrityException(
                        code="DUPLICATE_RUN_ID",
                        detail=str(evidence.run_id),
                    )
                )
                continue
            seen_runs.add(evidence.run_id)
            selected_runs.append(evidence)

    selected_journals = []
    seen_results: set[UUID] = set()
    for entry in journals:
        if entry.result is None:
            continue
        if entry.result.run_type is not RunType.PAPER:
            raise ValueError("forward-test report accepts PAPER journal outcomes only")
        if entry.result.closed_at > as_of:
            raise ValueError("future journal evidence is not allowed")
        if period_start <= entry.result.closed_at.date() <= period_end:
            if entry.result.id in seen_results:
                exceptions.append(
                    EvidenceIntegrityException(
                        code="DUPLICATE_TRADE_RESULT",
                        detail=str(entry.result.id),
                    )
                )
                continue
            seen_results.add(entry.result.id)
            selected_journals.append(entry)

    selected_journals.sort(key=lambda item: (item.result.closed_at, item.result.id))
    return (
        tuple(sorted(selected_scorecards, key=lambda item: item.trading_day)),
        tuple(sorted(selected_runs, key=lambda item: (item.as_of, item.run_id))),
        tuple(selected_journals),
        tuple(exceptions),
    )


def _report_digest(payload: dict[str, object]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return sha256(encoded.encode("utf-8")).hexdigest()


def build_forward_test_evidence_report(
    *,
    kind: ForwardTestReportKind,
    campaign: ForwardTestCampaignManifest,
    period_start: date,
    period_end: date,
    as_of: datetime,
    scorecards: tuple[ForwardTestDailyScorecard, ...],
    run_evidence: tuple[ForwardTestRunEvidence, ...],
    journals: tuple[JournalEntry, ...],
) -> ForwardTestEvidenceReport:
    """Build a deterministic PAPER-only report from accepted evidence domains."""

    scorecards, runs, journals, exceptions = _validate_and_select(
        campaign=campaign,
        scorecards=scorecards,
        run_evidence=run_evidence,
        journals=journals,
        period_start=period_start,
        period_end=period_end,
        as_of=as_of,
    )

    results = [entry.result for entry in journals if entry.result is not None]
    total_r = sum((result.r_multiple for result in results), ZERO)
    maes = [result.mae_r for result in results if result.mae_r is not None]
    mfes = [result.mfe_r for result in results if result.mfe_r is not None]
    adherence_count, adherence_rate = _adherence(journals)

    payload = {
        "kind": kind.value,
        "campaign_id": str(campaign.campaign_id),
        "campaign_digest": campaign_manifest_digest(campaign),
        "period_start": period_start.isoformat(),
        "period_end": period_end.isoformat(),
        "generated_as_of": as_of.astimezone(UTC).isoformat(),
        "scorecards": [item.model_dump(mode="json") for item in scorecards],
        "runs": [item.model_dump(mode="json") for item in runs],
        "journals": [item.model_dump(mode="json") for item in journals],
        "exceptions": [item.model_dump(mode="json") for item in exceptions],
    }

    return ForwardTestEvidenceReport(
        kind=kind,
        campaign_id=campaign.campaign_id,
        campaign_digest=campaign_manifest_digest(campaign),
        period_start=period_start,
        period_end=period_end,
        generated_as_of=as_of.astimezone(UTC),
        report_digest=_report_digest(payload),
        scorecard_days=len(scorecards),
        proposals=sum(item.proposals for item in scorecards),
        approvals=sum(item.approvals for item in scorecards),
        rejections=sum(item.rejections for item in scorecards),
        blocked_runs=sum(item.blocked_runs for item in scorecards),
        risk_denials=sum(item.risk_denials for item in scorecards),
        guard_denials=sum(item.guard_denials for item in scorecards),
        other_denials=sum(item.other_denials for item in scorecards),
        fills=sum(item.fills for item in scorecards),
        closed_trades=len(results),
        cumulative_r=total_r,
        expectancy_after_costs_r=total_r / Decimal(len(results)) if results else None,
        max_drawdown_r=_max_drawdown(journals),
        average_mae_r=_average(maes),
        average_mfe_r=_average(mfes),
        adherence_observations=adherence_count,
        adherence_rate=adherence_rate,
        risk_gate_breaches=sum(_risk_gate_breach(item) for item in runs),
        evidence_integrity_exceptions=exceptions,
        by_setup_regime=_group_performance(journals),
    )


def build_weekly_forward_test_report(
    *,
    campaign: ForwardTestCampaignManifest,
    week_start: date,
    as_of: datetime,
    scorecards: tuple[ForwardTestDailyScorecard, ...],
    run_evidence: tuple[ForwardTestRunEvidence, ...],
    journals: tuple[JournalEntry, ...],
) -> ForwardTestEvidenceReport:
    return build_forward_test_evidence_report(
        kind=ForwardTestReportKind.WEEKLY,
        campaign=campaign,
        period_start=week_start,
        period_end=week_start + timedelta(days=6),
        as_of=as_of,
        scorecards=scorecards,
        run_evidence=run_evidence,
        journals=journals,
    )


def build_campaign_forward_test_report(
    *,
    campaign: ForwardTestCampaignManifest,
    as_of: datetime,
    scorecards: tuple[ForwardTestDailyScorecard, ...],
    run_evidence: tuple[ForwardTestRunEvidence, ...],
    journals: tuple[JournalEntry, ...],
) -> ForwardTestEvidenceReport:
    campaign_end = min(campaign.end_at.date(), as_of.astimezone(UTC).date())
    return build_forward_test_evidence_report(
        kind=ForwardTestReportKind.CAMPAIGN,
        campaign=campaign,
        period_start=campaign.start_at.date(),
        period_end=campaign_end,
        as_of=as_of,
        scorecards=scorecards,
        run_evidence=run_evidence,
        journals=journals,
    )
