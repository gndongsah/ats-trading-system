"""ATS-036C deterministic XNYS campaign schedule."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from enum import StrEnum

import exchange_calendars as xcals
from pydantic import BaseModel, ConfigDict, Field

from src.forward_test_campaign import ForwardTestCampaignManifest


class CampaignJobKind(StrEnum):
    MARKET_WINDOW = "MARKET_WINDOW"
    DAILY_SCORECARD = "DAILY_SCORECARD"
    WEEKLY_REPORT = "WEEKLY_REPORT"
    READINESS = "READINESS"


class CampaignSchedulePolicy(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    version: str = "campaign-schedule-v1"
    execution_interval_minutes: int = Field(default=15, ge=1, le=120)
    first_window_after_open_minutes: int = Field(default=15, ge=1, le=120)
    stop_before_close_minutes: int = Field(default=15, ge=1, le=120)
    scorecard_after_close_minutes: int = Field(default=15, ge=1, le=180)
    weekly_report_after_close_minutes: int = Field(default=30, ge=1, le=240)


class CampaignScheduledJob(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: CampaignJobKind
    scheduled_at: datetime
    trading_day: date


class CampaignScheduler:
    """Produce reproducible PAPER jobs from the XNYS session calendar."""

    def __init__(
        self,
        *,
        policy: CampaignSchedulePolicy | None = None,
    ) -> None:
        self._policy = policy or CampaignSchedulePolicy()
        self._calendar = xcals.get_calendar("XNYS")

    @property
    def policy(self) -> CampaignSchedulePolicy:
        return self._policy

    def next_start_at(self, after: datetime) -> datetime:
        after = self._utc(after)
        session = self._calendar.minute_to_session(after, direction="next")
        session_open = self._utc(self._calendar.session_open(session).to_pydatetime())
        first = session_open + timedelta(
            minutes=self._policy.first_window_after_open_minutes
        )
        if first <= after:
            next_session = self._calendar.next_session(session)
            session_open = self._utc(
                self._calendar.session_open(next_session).to_pydatetime()
            )
            first = session_open + timedelta(
                minutes=self._policy.first_window_after_open_minutes
            )
        return first

    def jobs_for_day(
        self,
        *,
        campaign: ForwardTestCampaignManifest,
        trading_day: date,
    ) -> tuple[CampaignScheduledJob, ...]:
        session = self._calendar.date_to_session(trading_day, direction="none")
        session_open = self._utc(self._calendar.session_open(session).to_pydatetime())
        session_close = self._utc(self._calendar.session_close(session).to_pydatetime())

        jobs: list[CampaignScheduledJob] = []
        current = session_open + timedelta(
            minutes=self._policy.first_window_after_open_minutes
        )
        last = session_close - timedelta(minutes=self._policy.stop_before_close_minutes)
        interval = timedelta(minutes=self._policy.execution_interval_minutes)
        while current <= last:
            if campaign.start_at <= current <= campaign.end_at:
                jobs.append(
                    CampaignScheduledJob(
                        kind=CampaignJobKind.MARKET_WINDOW,
                        scheduled_at=current,
                        trading_day=trading_day,
                    )
                )
            current += interval

        scorecard_at = session_close + timedelta(
            minutes=self._policy.scorecard_after_close_minutes
        )
        if campaign.start_at <= scorecard_at <= campaign.end_at:
            jobs.append(
                CampaignScheduledJob(
                    kind=CampaignJobKind.DAILY_SCORECARD,
                    scheduled_at=scorecard_at,
                    trading_day=trading_day,
                )
            )

        if trading_day.weekday() == 4:
            weekly_at = session_close + timedelta(
                minutes=self._policy.weekly_report_after_close_minutes
            )
            if campaign.start_at <= weekly_at <= campaign.end_at:
                jobs.append(
                    CampaignScheduledJob(
                        kind=CampaignJobKind.WEEKLY_REPORT,
                        scheduled_at=weekly_at,
                        trading_day=trading_day,
                    )
                )
                if weekly_at >= campaign.start_at + timedelta(weeks=8):
                    jobs.append(
                        CampaignScheduledJob(
                            kind=CampaignJobKind.READINESS,
                            scheduled_at=weekly_at + timedelta(minutes=1),
                            trading_day=trading_day,
                        )
                    )
        return tuple(sorted(jobs, key=lambda job: (job.scheduled_at, job.kind.value)))

    @staticmethod
    def _utc(value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("scheduler timestamp must be timezone-aware")
        return value.astimezone(UTC)
