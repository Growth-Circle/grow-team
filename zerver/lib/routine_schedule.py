"""When a routine runs next. The pattern is read on the clock of the
routine's own timezone, so "every day at 09:00" stays at 09:00 on the wall
through a change of daylight saving time."""

import calendar
import zoneinfo
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta

from django.utils.translation import gettext as _

from zerver.lib.exceptions import JsonableError
from zerver.lib.timezone import canonicalize_timezone

PATTERN_KINDS = ("every_minutes", "every_hours", "daily", "weekdays", "weekly", "monthly")

# Below this, a routine is a stream of messages, not a routine.
MIN_INTERVAL_MINUTES = 15

# Past these bounds, a longer wait is "every hours", "daily" or "weekly".
MAX_INTERVAL_MINUTES = 59
MAX_INTERVAL_HOURS = 23

# Monday is 0, as in datetime.weekday().
WORKDAYS = (0, 1, 2, 3, 4)

# Every valid pattern has a run within this many days (a monthly one needs
# at most 31), so the search below ends.
_SEARCH_DAYS = 70


@dataclass(frozen=True)
class Schedule:
    pattern_kind: str
    timezone: str
    interval: int | None = None
    time_of_day: time | None = None
    weekdays: tuple[int, ...] = ()
    month_day: int | None = None


def validate_schedule(schedule: Schedule) -> None:
    """Raise JsonableError for a schedule that cannot run."""
    if schedule.pattern_kind not in PATTERN_KINDS:
        raise JsonableError(_("Choose how often the routine runs."))
    try:
        zoneinfo.ZoneInfo(canonicalize_timezone(schedule.timezone))
    except (zoneinfo.ZoneInfoNotFoundError, ValueError):
        raise JsonableError(_("Unknown timezone."))

    if schedule.pattern_kind == "every_minutes":
        if schedule.interval is None or not (
            MIN_INTERVAL_MINUTES <= schedule.interval <= MAX_INTERVAL_MINUTES
        ):
            raise JsonableError(
                _("Minutes between runs must be from {minimum} to {maximum}.").format(
                    minimum=MIN_INTERVAL_MINUTES, maximum=MAX_INTERVAL_MINUTES
                )
            )
    elif schedule.pattern_kind == "every_hours":
        if schedule.interval is None or not (1 <= schedule.interval <= MAX_INTERVAL_HOURS):
            raise JsonableError(
                _("Hours between runs must be from 1 to {maximum}.").format(
                    maximum=MAX_INTERVAL_HOURS
                )
            )
    elif schedule.time_of_day is None:
        raise JsonableError(_("Choose the time of day."))

    if schedule.pattern_kind == "weekly" and (
        not schedule.weekdays
        or len(set(schedule.weekdays)) != len(schedule.weekdays)
        or any(day not in range(7) for day in schedule.weekdays)
    ):
        raise JsonableError(_("Choose at least one day of the week."))
    if schedule.pattern_kind == "monthly" and (
        schedule.month_day is None or not 1 <= schedule.month_day <= 31
    ):
        raise JsonableError(_("Choose a day of the month from 1 to 31."))


def _runs_on(schedule: Schedule, day: date) -> bool:
    if schedule.pattern_kind == "weekdays":
        return day.weekday() in WORKDAYS
    if schedule.pattern_kind == "weekly":
        return day.weekday() in schedule.weekdays
    if schedule.pattern_kind == "monthly":
        assert schedule.month_day is not None
        # A month without that day runs on its last day.
        return day.day == min(schedule.month_day, calendar.monthrange(day.year, day.month)[1])
    return True


def _times_on(schedule: Schedule) -> list[time]:
    if schedule.pattern_kind in ("every_minutes", "every_hours"):
        assert schedule.interval is not None
        step = schedule.interval * (60 if schedule.pattern_kind == "every_hours" else 1)
        # The slots count from midnight, so a run does not depend on when
        # the routine was made, and the last gap of a day may be shorter.
        return [time(minute // 60, minute % 60) for minute in range(0, 24 * 60, step)]
    assert schedule.time_of_day is not None
    return [schedule.time_of_day.replace(second=0, microsecond=0, tzinfo=None)]


def next_run_after(schedule: Schedule, after: datetime) -> datetime:
    """The first run strictly after `after`, as a UTC moment."""
    zone = zoneinfo.ZoneInfo(canonicalize_timezone(schedule.timezone))
    first_day = after.astimezone(zone).date()
    times = _times_on(schedule)
    for offset in range(_SEARCH_DAYS):
        day = first_day + timedelta(days=offset)
        if not _runs_on(schedule, day):
            continue
        for slot in times:
            moment = datetime.combine(day, slot, tzinfo=zone).astimezone(UTC)
            if moment > after:
                return moment
    raise AssertionError("A validated schedule always has a next run.")
