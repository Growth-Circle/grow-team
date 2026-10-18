from datetime import UTC, datetime, time, timedelta, timezone

from zerver.lib.exceptions import JsonableError
from zerver.lib.routine_schedule import (
    MAX_INTERVAL_HOURS,
    MAX_INTERVAL_MINUTES,
    MIN_INTERVAL_MINUTES,
    Schedule,
    next_run_after,
    validate_schedule,
)
from zerver.lib.test_classes import ZulipTestCase

JAKARTA = timezone(timedelta(hours=7))


def jakarta(day: int, hour: int, minute: int = 0, *, month: int = 10) -> datetime:
    return datetime(2026, month, day, hour, minute, tzinfo=JAKARTA)


class RoutineScheduleTest(ZulipTestCase):
    def schedule(self, pattern_kind: str, **kwargs: object) -> Schedule:
        kwargs.setdefault("timezone", "Asia/Jakarta")
        return Schedule(pattern_kind=pattern_kind, **kwargs)  # type: ignore[arg-type]

    def test_daily_runs_at_the_time_of_day_on_the_workspace_clock(self) -> None:
        schedule = self.schedule("daily", time_of_day=time(9, 0))

        # 16 Oct 2026 is a Friday.
        self.assertEqual(next_run_after(schedule, jakarta(16, 8, 59)), jakarta(16, 9))
        self.assertEqual(next_run_after(schedule, jakarta(16, 9)), jakarta(17, 9))
        self.assertEqual(next_run_after(schedule, jakarta(16, 23, 59)), jakarta(17, 9))
        self.assertEqual(next_run_after(schedule, jakarta(16, 9)).tzinfo, UTC)

    def test_weekdays_skip_the_weekend(self) -> None:
        schedule = self.schedule("weekdays", time_of_day=time(17, 30))

        self.assertEqual(next_run_after(schedule, jakarta(16, 17, 30)), jakarta(19, 17, 30))
        self.assertEqual(next_run_after(schedule, jakarta(17, 8)), jakarta(19, 17, 30))
        self.assertEqual(next_run_after(schedule, jakarta(19, 17, 30)), jakarta(20, 17, 30))

    def test_weekly_runs_on_the_chosen_days(self) -> None:
        schedule = self.schedule("weekly", time_of_day=time(16, 0), weekdays=(4, 0))

        self.assertEqual(next_run_after(schedule, jakarta(15, 12)), jakarta(16, 16))
        self.assertEqual(next_run_after(schedule, jakarta(16, 16)), jakarta(19, 16))
        self.assertEqual(next_run_after(schedule, jakarta(19, 16)), jakarta(23, 16))

    def test_monthly_runs_on_the_last_day_of_a_shorter_month(self) -> None:
        schedule = self.schedule("monthly", time_of_day=time(8, 0), month_day=31)

        self.assertEqual(next_run_after(schedule, jakarta(1, 0, month=1)), jakarta(31, 8, month=1))
        self.assertEqual(next_run_after(schedule, jakarta(31, 8, month=1)), jakarta(28, 8, month=2))
        self.assertEqual(next_run_after(schedule, jakarta(28, 8, month=2)), jakarta(31, 8, month=3))
        self.assertEqual(next_run_after(schedule, jakarta(31, 8, month=3)), jakarta(30, 8, month=4))

        leap = self.schedule("monthly", time_of_day=time(8, 0), month_day=30)
        february = datetime(2028, 2, 1, tzinfo=JAKARTA)
        self.assertEqual(
            next_run_after(leap, february), datetime(2028, 2, 29, 8, 0, tzinfo=JAKARTA)
        )

    def test_monthly_on_the_first_crosses_the_year(self) -> None:
        schedule = self.schedule("monthly", time_of_day=time(8, 0), month_day=1)

        self.assertEqual(
            next_run_after(schedule, jakarta(1, 8, month=12)),
            datetime(2027, 1, 1, 8, 0, tzinfo=JAKARTA),
        )

    def test_every_minutes_counts_from_midnight(self) -> None:
        schedule = self.schedule("every_minutes", interval=15)

        self.assertEqual(next_run_after(schedule, jakarta(16, 10, 1)), jakarta(16, 10, 15))
        self.assertEqual(next_run_after(schedule, jakarta(16, 10, 15)), jakarta(16, 10, 30))
        self.assertEqual(next_run_after(schedule, jakarta(16, 23, 50)), jakarta(17, 0))

        # 50 does not divide a day: the last gap of the day is shorter.
        uneven = self.schedule("every_minutes", interval=50)
        self.assertEqual(next_run_after(uneven, jakarta(16, 22, 30)), jakarta(16, 23, 20))
        self.assertEqual(next_run_after(uneven, jakarta(16, 23, 20)), jakarta(17, 0))

    def test_every_hours_counts_from_midnight(self) -> None:
        schedule = self.schedule("every_hours", interval=4)

        self.assertEqual(next_run_after(schedule, jakarta(16, 8)), jakarta(16, 12))
        self.assertEqual(next_run_after(schedule, jakarta(16, 21)), jakarta(17, 0))

    def test_a_timezone_that_changes_its_clock_keeps_the_wall_time(self) -> None:
        schedule = self.schedule("daily", time_of_day=time(9, 0), timezone="America/New_York")

        # New York changes to winter time on 1 Nov 2026, 06:00 UTC.
        before = datetime(2026, 10, 31, 12, 0, tzinfo=UTC)
        first = next_run_after(schedule, before)
        second = next_run_after(schedule, first)
        third = next_run_after(schedule, second)

        self.assertEqual(first, datetime(2026, 10, 31, 13, 0, tzinfo=UTC))
        self.assertEqual(second, datetime(2026, 11, 1, 14, 0, tzinfo=UTC))
        self.assertEqual(third, datetime(2026, 11, 2, 14, 0, tzinfo=UTC))

    def test_a_wall_time_that_does_not_exist_still_runs_once_that_day(self) -> None:
        schedule = self.schedule("daily", time_of_day=time(2, 30), timezone="America/New_York")

        # 8 Mar 2026: New York skips 02:00 to 03:00.
        run = next_run_after(schedule, datetime(2026, 3, 8, 0, 0, tzinfo=UTC))

        self.assertEqual(run.astimezone(timezone(timedelta(hours=-4))).date().day, 8)
        self.assertGreater(run, datetime(2026, 3, 8, 0, 0, tzinfo=UTC))
        self.assertEqual(next_run_after(schedule, run), datetime(2026, 3, 9, 6, 30, tzinfo=UTC))

    def test_interval_bounds_are_checked(self) -> None:
        validate_schedule(self.schedule("every_minutes", interval=MIN_INTERVAL_MINUTES))
        validate_schedule(self.schedule("every_minutes", interval=MAX_INTERVAL_MINUTES))
        validate_schedule(self.schedule("every_hours", interval=1))
        validate_schedule(self.schedule("every_hours", interval=MAX_INTERVAL_HOURS))
        for pattern_kind, interval in [
            ("every_minutes", MIN_INTERVAL_MINUTES - 1),
            ("every_minutes", MAX_INTERVAL_MINUTES + 1),
            ("every_minutes", None),
            ("every_hours", 0),
            ("every_hours", MAX_INTERVAL_HOURS + 1),
        ]:
            with self.assertRaises(JsonableError):
                validate_schedule(self.schedule(pattern_kind, interval=interval))

    def test_the_other_fields_are_checked(self) -> None:
        validate_schedule(self.schedule("weekly", time_of_day=time(9, 0), weekdays=(0, 6)))
        validate_schedule(self.schedule("monthly", time_of_day=time(9, 0), month_day=31))
        for schedule in [
            self.schedule("daily"),
            self.schedule("weekly", time_of_day=time(9, 0)),
            self.schedule("weekly", time_of_day=time(9, 0), weekdays=(7,)),
            self.schedule("weekly", time_of_day=time(9, 0), weekdays=(1, 1)),
            self.schedule("monthly", time_of_day=time(9, 0)),
            self.schedule("monthly", time_of_day=time(9, 0), month_day=32),
            self.schedule("fortnightly", time_of_day=time(9, 0)),
            self.schedule("daily", time_of_day=time(9, 0), timezone="Mars/Olympus"),
        ]:
            with self.assertRaises(JsonableError):
                validate_schedule(schedule)
