"""ATS-009A: non-enforcing, deterministic XNYS session resolution.

No broker orders, portfolio decisions, external I/O, or loss-lock state live
here. Unscheduled closures require a separately authenticated schedule source.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from importlib.metadata import version
from typing import Protocol
from zoneinfo import ZoneInfo

import exchange_calendars as xcals
import pandas as pd

SOURCE = "exchange-calendars"
PINNED_VERSION = "4.13.1"
EXCHANGE = "XNYS"
NEW_YORK = ZoneInfo("America/New_York")


class CalendarUnavailable(ValueError):
    """Calendar schedule or closure attestation could not be resolved safely."""


@dataclass(frozen=True)
class ScheduleAttestation:
    """Date-scoped output of a trusted schedule-attestation adapter.

    This value shape does not authenticate its contents. Production callers
    must only provide values returned by an independently authenticated
    authority. No such production authority is included in this module.
    """

    session_date: date
    calendar_version: str
    authority_revision: str
    verified_at: datetime
    valid_until: datetime
    emergency_closed: bool


class ScheduleAuthority(Protocol):
    """Trusted adapter contract for emergency-closure status."""

    def verified_status(
        self, session_date: date, calendar_version: str
    ) -> ScheduleAttestation | None: ...


@dataclass(frozen=True)
class ResolvedSession:
    exchange: str
    calendar_version: str
    session_id: str
    week_id: str
    local_date: date
    open_utc: datetime
    close_utc: datetime
    observed_at_utc: datetime
    is_regular_session: bool
    schedule_verified: bool
    authority_revision: str | None
    eligible_new_risk: bool


class XNYSCalendar:
    """Resolve regular XNYS sessions without granting execution permission."""

    def __init__(self, authority: ScheduleAuthority | None = None) -> None:
        installed_version = version(SOURCE)
        if installed_version != PINNED_VERSION:
            raise CalendarUnavailable(
                f"Unsupported {SOURCE} version: {installed_version}; "
                f"require {PINNED_VERSION}"
            )
        try:
            self.calendar = xcals.get_calendar(EXCHANGE)
        except (ValueError, KeyError) as error:
            raise CalendarUnavailable("Unable to load the XNYS calendar") from error
        self.authority = authority
        self.version = f"{SOURCE}:{PINNED_VERSION}:{EXCHANGE}"

    def resolve(self, at: datetime) -> ResolvedSession | None:
        try:
            if at.tzinfo is None or at.utcoffset() is None:
                raise CalendarUnavailable("Timestamp must be timezone-aware")
            utc = at.astimezone(UTC)
        except (OverflowError, ValueError) as error:
            if isinstance(error, CalendarUnavailable):
                raise
            raise CalendarUnavailable("Timestamp cannot be resolved safely") from error

        local_day = utc.astimezone(NEW_YORK).date()
        stamp = pd.Timestamp(local_day)
        try:
            if not self.calendar.is_session(stamp):
                return None
            opening = self.calendar.session_open(stamp).to_pydatetime().astimezone(UTC)
            closing = self.calendar.session_close(stamp).to_pydatetime().astimezone(UTC)
            monday = local_day - timedelta(days=local_day.weekday())
            week_sessions = self.calendar.sessions_in_range(
                pd.Timestamp(monday), pd.Timestamp(monday + timedelta(days=6))
            )
            if week_sessions.empty:
                raise CalendarUnavailable("Trading week has no supported XNYS session")
            first_session = week_sessions[0].date()
        except CalendarUnavailable:
            raise
        except (ValueError, IndexError, OverflowError) as error:
            raise CalendarUnavailable(
                "Session is beyond the supported XNYS calendar schedule"
            ) from error

        # The package schedule cannot attest that no emergency closure was
        # announced. Without a trusted authority, eligibility stays false.
        attestation = (
            self.authority.verified_status(local_day, self.version)
            if self.authority is not None
            else None
        )
        verified = self._attestation_is_valid(attestation, local_day, utc)
        revision = attestation.authority_revision if verified else None
        emergency_closed = attestation.emergency_closed if verified else True
        in_hours = opening <= utc < closing

        return ResolvedSession(
            exchange=EXCHANGE,
            calendar_version=self.version,
            session_id=f"{EXCHANGE}:{local_day.isoformat()}",
            week_id=f"{EXCHANGE}:week:{first_session.isoformat()}",
            local_date=local_day,
            open_utc=opening,
            close_utc=closing,
            observed_at_utc=utc,
            is_regular_session=in_hours,
            schedule_verified=verified,
            authority_revision=revision,
            eligible_new_risk=in_hours and verified and not emergency_closed,
        )

    def _attestation_is_valid(
        self,
        attestation: ScheduleAttestation | None,
        session_date: date,
        now: datetime,
    ) -> bool:
        if attestation is None:
            return False
        if (
            attestation.session_date != session_date
            or attestation.calendar_version != self.version
            or not isinstance(attestation.authority_revision, str)
            or not attestation.authority_revision.strip()
            or type(attestation.emergency_closed) is not bool
        ):
            return False
        try:
            verified_at = attestation.verified_at
            valid_until = attestation.valid_until
            if (
                verified_at.tzinfo is None
                or verified_at.utcoffset() is None
                or valid_until.tzinfo is None
                or valid_until.utcoffset() is None
            ):
                return False
            verified_at_utc = verified_at.astimezone(UTC)
            valid_until_utc = valid_until.astimezone(UTC)
        except (OverflowError, ValueError):
            return False
        return (
            verified_at_utc < valid_until_utc
            and verified_at_utc <= now < valid_until_utc
        )
