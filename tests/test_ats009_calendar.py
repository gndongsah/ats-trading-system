from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

import src.ats009_calendar as calendar_module
from src.ats009_calendar import (
    CalendarUnavailable,
    ScheduleAttestation,
    XNYSCalendar,
)

NEW_YORK = ZoneInfo("America/New_York")


def ny(year: int, month: int, day: int, hour: int = 12, minute: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=NEW_YORK)


class UnitTestScheduleAuthority:
    """Synthetic verifier for unit tests; not a production trust provider."""

    def __init__(
        self,
        *,
        emergency_closed: bool = False,
        stale: bool = False,
        wrong_version: bool = False,
    ) -> None:
        self.emergency_closed = emergency_closed
        self.stale = stale
        self.wrong_version = wrong_version

    def verified_status(
        self, session_date: date, calendar_version: str
    ) -> ScheduleAttestation:
        noon = datetime.combine(session_date, datetime.min.time(), NEW_YORK).replace(
            hour=12
        )
        return ScheduleAttestation(
            session_date=session_date,
            calendar_version="invalid" if self.wrong_version else calendar_version,
            authority_revision="TEST-ONLY-REVISION",
            verified_at=noon - timedelta(days=2),
            valid_until=(
                noon - timedelta(days=1) if self.stale else noon + timedelta(days=2)
            ),
            emergency_closed=self.emergency_closed,
        )


def test_no_production_attestation_never_marks_risk_eligible() -> None:
    result = XNYSCalendar().resolve(ny(2026, 10, 20, 10))

    assert result is not None
    assert result.is_regular_session
    assert not result.schedule_verified
    assert not result.eligible_new_risk


@pytest.mark.parametrize(
    ("hour", "minute", "eligible"),
    [(9, 29, False), (9, 30, True), (12, 0, True), (15, 59, True), (16, 0, False)],
)
def test_regular_session_boundaries(hour: int, minute: int, eligible: bool) -> None:
    result = XNYSCalendar(UnitTestScheduleAuthority()).resolve(
        ny(2026, 10, 20, hour, minute)
    )

    assert result is not None
    assert result.eligible_new_risk is eligible
    assert result.open_utc.hour == 13
    assert result.close_utc.hour == 20


@pytest.mark.parametrize(
    "ymd",
    [
        (2026, 10, 17),
        (2026, 10, 18),
        (2026, 12, 25),
        (2026, 11, 26),
        (2026, 1, 1),
    ],
)
def test_weekends_and_holidays_have_no_session(ymd: tuple[int, int, int]) -> None:
    assert XNYSCalendar(UnitTestScheduleAuthority()).resolve(ny(*ymd)) is None


def test_thanksgiving_friday_early_close() -> None:
    calendar = XNYSCalendar(UnitTestScheduleAuthority())
    before_close = calendar.resolve(ny(2026, 11, 27, 12, 59))
    at_close = calendar.resolve(ny(2026, 11, 27, 13, 0))

    assert before_close is not None and before_close.eligible_new_risk
    assert at_close is not None
    assert at_close.close_utc.astimezone(NEW_YORK).hour == 13
    assert not at_close.eligible_new_risk


def test_dst_changes_utc_open_without_changing_local_session() -> None:
    calendar = XNYSCalendar(UnitTestScheduleAuthority())
    winter = calendar.resolve(ny(2026, 3, 6, 10))
    summer = calendar.resolve(ny(2026, 3, 9, 10))

    assert winter is not None and summer is not None
    assert winter.open_utc.hour == 14
    assert summer.open_utc.hour == 13
    assert winter.eligible_new_risk and summer.eligible_new_risk


def test_holiday_monday_week_begins_on_first_valid_session() -> None:
    calendar = XNYSCalendar(UnitTestScheduleAuthority())
    tuesday = calendar.resolve(ny(2026, 9, 8, 10))
    friday = calendar.resolve(ny(2026, 9, 11, 10))

    assert tuesday is not None and friday is not None
    assert tuesday.week_id == "XNYS:week:2026-09-08"
    assert friday.week_id == tuesday.week_id


def test_regular_monday_is_week_identity() -> None:
    result = XNYSCalendar(UnitTestScheduleAuthority()).resolve(ny(2026, 10, 20, 10))

    assert result is not None
    assert result.week_id == "XNYS:week:2026-10-19"


@pytest.mark.parametrize(
    "kwargs",
    [
        {"stale": True},
        {"wrong_version": True},
        {"emergency_closed": True},
    ],
)
def test_invalid_or_closed_attestation_never_allows_new_risk(
    kwargs: dict[str, bool],
) -> None:
    result = XNYSCalendar(UnitTestScheduleAuthority(**kwargs)).resolve(
        ny(2026, 10, 20, 10)
    )

    assert result is not None
    assert not result.eligible_new_risk


def test_malformed_attestation_fails_closed() -> None:
    class MalformedAuthority:
        def verified_status(
            self, session_date: date, calendar_version: str
        ) -> ScheduleAttestation:
            return ScheduleAttestation(
                session_date=session_date,
                calendar_version=calendar_version,
                authority_revision="TEST-ONLY-REVISION",
                verified_at=datetime(2026, 10, 18, tzinfo=UTC),
                valid_until=datetime(2026, 10, 22),
                emergency_closed=False,
            )

    result = XNYSCalendar(MalformedAuthority()).resolve(ny(2026, 10, 20, 10))

    assert result is not None
    assert not result.schedule_verified
    assert not result.eligible_new_risk


def test_naive_timestamp_is_rejected() -> None:
    with pytest.raises(CalendarUnavailable, match="timezone-aware"):
        XNYSCalendar().resolve(datetime(2026, 10, 20, 10))


def test_calendar_version_is_pinned(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(calendar_module, "version", lambda _name: "4.13.0")

    with pytest.raises(CalendarUnavailable, match="Unsupported"):
        XNYSCalendar()


def test_calendar_date_outside_supported_schedule_fails_safely() -> None:
    with pytest.raises(CalendarUnavailable):
        XNYSCalendar().resolve(ny(1900, 1, 2, 10))


def test_session_and_week_period_ids_are_distinct() -> None:
    calendar = XNYSCalendar(UnitTestScheduleAuthority())
    tuesday = calendar.resolve(ny(2026, 10, 20, 10))
    wednesday = calendar.resolve(ny(2026, 10, 21, 10))

    assert tuesday is not None and wednesday is not None
    assert tuesday.session_id != wednesday.session_id
    assert tuesday.week_id == wednesday.week_id
