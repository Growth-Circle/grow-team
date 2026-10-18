"""Tests for the scheduler of routines and the history of their runs."""

from datetime import UTC, datetime, time, timedelta
from io import StringIO
from typing import Any
from unittest import mock

import time_machine
from django.conf import settings
from django.core.management import call_command
from typing_extensions import override

from zerver.actions.tasks import do_create_task
from zerver.lib.agent_context import AgentBusy
from zerver.lib.exceptions import JsonableError
from zerver.lib.routines import (
    FAILURES_BEFORE_PAUSE,
    GRACE,
    MAX_MISSED_RECORDED,
    board_summary,
    reconcile_routine_runs,
    run_due_routines,
    run_routine_now,
)
from zerver.lib.tasks import get_or_create_default_board
from zerver.lib.test_classes import ZulipTestCase
from zerver.lib.test_helpers import most_recent_message
from zerver.models import AgentRealmSettings, AgentRoutine, AgentRoutineRun, Message, Task, agents
from zerver.models.clients import get_client
from zerver.models.users import get_system_bot
from zerver.tests.test_room_digests import _ready_kaki_profile

# 09:00 on Friday 16 Oct 2026 in Jakarta (UTC+7).
NINE = datetime(2026, 10, 16, 2, 0, tzinfo=UTC)


class RoutineTestCase(ZulipTestCase):
    @override
    def setUp(self) -> None:
        super().setUp()
        self.owner = self.example_user("hamlet")
        self.realm = self.owner.realm
        AgentRealmSettings.objects.update_or_create(
            realm=self.realm,
            defaults={"enabled": True, "timezone": "Asia/Jakarta", "agent_language": "en"},
        )
        self.profile = _ready_kaki_profile(self.owner, name="Sanji Agent")
        self.stream = self.subscribe(self.owner, "routines")
        self.subscribe(self.example_user("cordelia"), "routines")
        # A client made inside a rolled-back run stays in the client cache
        # without its row, so the tests that roll a run back need the row first.
        get_client("Grow Agent")

    def make_routine(self, **fields: Any) -> AgentRoutine:
        values: dict[str, Any] = {
            "realm": self.realm,
            "title": "Morning brief",
            "instructions": "Summarize what matters today.",
            "agent_profile": self.profile,
            "owner": self.owner,
            "stream": self.stream,
            "topic": "Brief",
            "pattern_kind": "daily",
            "time_of_day": time(9, 0),
            "timezone": "Asia/Jakarta",
            "next_run_at": NINE,
        }
        values.update(fields)
        return AgentRoutine.objects.create(**values)

    def scheduler(self, at: datetime) -> int:
        with time_machine.travel(at, tick=False):
            return run_due_routines(self.realm)

    def reconcile(self, at: datetime) -> int:
        with time_machine.travel(at, tick=False):
            return reconcile_routine_runs(self.realm)

    def notices(self) -> list[str]:
        bot = get_system_bot(settings.NOTIFICATION_BOT, self.realm.id)
        return list(
            Message.objects.filter(
                realm=self.realm, sender=bot, usermessage__user_profile=self.owner
            )
            .order_by("id")
            .values_list("content", flat=True)
        )

    def stream_message_count(self) -> int:
        assert self.stream.recipient_id is not None
        return Message.objects.filter(recipient_id=self.stream.recipient_id).count()

    def end_job(self, run: AgentRoutineRun, status: str, **fields: Any) -> None:
        assert run.job_id is not None
        agents.AgentJob.objects.filter(id=run.job_id).update(status=status, **fields)


class RoutineSchedulerTest(RoutineTestCase):
    def test_a_due_routine_starts_one_job_and_its_bot_posts(self) -> None:
        routine = self.make_routine()
        other = self.example_user("cordelia")
        earlier = self.send_stream_message(other, "routines", "We shipped the fix.")
        own = self.send_stream_message(self.profile.bot_user, "routines", "An earlier summary.")
        Message.objects.filter(id__in=[earlier, own]).update(date_sent=NINE - timedelta(hours=3))

        self.assertEqual(self.scheduler(NINE + timedelta(seconds=30)), 1)

        run = AgentRoutineRun.objects.get(routine=routine)
        self.assertEqual(run.status, "running")
        self.assertEqual(run.trigger, "schedule")
        self.assertEqual(run.scheduled_for, NINE)
        job = run.job
        assert job is not None
        self.assertEqual(job.requester_id, self.owner.id)
        self.assertEqual(job.profile_id, self.profile.id)
        self.assertEqual(job.trigger_kind, "manual")
        self.assertTrue(job.request.startswith("Morning brief\n\nSummarize what matters today."))
        # The post that starts the job is the bot's, never a person's.
        source = job.source_message
        assert source is not None
        self.assertEqual(source.sender_id, self.profile.bot_user_id)
        self.assertEqual(source.content, "Morning brief")
        self.assertEqual(source.topic_name(), "Brief")
        self.assertEqual(source.recipient_id, self.stream.recipient_id)
        context = set(
            agents.AgentContextRef.objects.filter(job=job, kind="message").values_list(
                "message_id", flat=True
            )
        )
        self.assertIn(earlier, context)
        # The agent does not read its own earlier messages.
        self.assertNotIn(own, context)

        routine.refresh_from_db()
        self.assertEqual(routine.next_run_at, NINE + timedelta(days=1))
        self.assertEqual(routine.last_run_at, NINE + timedelta(seconds=30))
        self.assertEqual(routine.last_status, "running")

    def test_only_enabled_routines_that_are_due_run(self) -> None:
        future = self.make_routine(title="Later", next_run_at=NINE + timedelta(hours=1))
        paused = self.make_routine(title="Paused", enabled=False, paused_reason="user")
        external = self.make_routine(
            title="Outside",
            executor="external",
            agent_profile=None,
            external_agent_name="Opus",
            stream=None,
        )

        self.assertEqual(self.scheduler(NINE + timedelta(seconds=30)), 0)

        self.assertFalse(AgentRoutineRun.objects.exists())
        for routine, expected in [
            (future, NINE + timedelta(hours=1)),
            (paused, NINE),
            (external, NINE),
        ]:
            routine.refresh_from_db()
            self.assertEqual(routine.next_run_at, expected)

    def test_a_workspace_with_agents_off_runs_nothing(self) -> None:
        self.make_routine()
        AgentRealmSettings.objects.filter(realm=self.realm).update(enabled=False)

        self.assertEqual(self.scheduler(NINE), 0)

        self.assertFalse(AgentRoutineRun.objects.exists())

    def test_running_the_scheduler_twice_makes_one_run_and_one_job(self) -> None:
        routine = self.make_routine()

        self.assertEqual(self.scheduler(NINE + timedelta(seconds=30)), 1)
        self.assertEqual(self.scheduler(NINE + timedelta(seconds=40)), 0)
        # Even a routine that is put back to the same time, as an overlap
        # of two instances would leave it, makes no second run.
        AgentRoutine.objects.filter(id=routine.id).update(next_run_at=NINE)
        self.assertEqual(self.scheduler(NINE + timedelta(seconds=50)), 0)

        self.assertEqual(AgentRoutineRun.objects.filter(routine=routine).count(), 1)
        self.assertEqual(agents.AgentJob.objects.filter(profile=self.profile).count(), 1)

    def test_a_busy_agent_lock_leaves_the_routine_for_the_next_minute(self) -> None:
        routine = self.make_routine()
        before = self.stream_message_count()

        with mock.patch("zerver.lib.routines.create_job", side_effect=AgentBusy("busy")):
            self.assertEqual(self.scheduler(NINE + timedelta(seconds=30)), 0)

        routine.refresh_from_db()
        self.assertEqual(routine.next_run_at, NINE)
        self.assertFalse(AgentRoutineRun.objects.exists())
        # Nothing was posted either: the source message rolled back too.
        self.assertEqual(self.stream_message_count(), before)

        self.assertEqual(self.scheduler(NINE + timedelta(minutes=1)), 1)

    def test_a_host_that_was_down_runs_the_latest_occurrence_only(self) -> None:
        routine = self.make_routine(next_run_at=NINE - timedelta(days=3))

        self.assertEqual(self.scheduler(NINE + timedelta(minutes=30)), 1)

        runs = AgentRoutineRun.objects.filter(routine=routine).order_by("scheduled_for")
        self.assertEqual(
            [(run.scheduled_for, run.status) for run in runs],
            [
                (NINE - timedelta(days=3), "skipped_missed"),
                (NINE - timedelta(days=2), "skipped_missed"),
                (NINE - timedelta(days=1), "skipped_missed"),
                (NINE, "running"),
            ],
        )
        self.assertEqual(agents.AgentJob.objects.count(), 1)
        routine.refresh_from_db()
        self.assertEqual(routine.next_run_at, NINE + timedelta(days=1))
        # A missed run tells nobody: it is not a failure.
        self.assertEqual(self.notices(), [])

    def test_an_occurrence_past_the_grace_is_skipped(self) -> None:
        routine = self.make_routine(next_run_at=NINE - timedelta(days=1))

        self.assertEqual(self.scheduler(NINE + GRACE + timedelta(minutes=1)), 0)

        runs = AgentRoutineRun.objects.filter(routine=routine)
        self.assertEqual({run.status for run in runs}, {"skipped_missed"})
        self.assertEqual(runs.count(), 2)
        self.assertFalse(agents.AgentJob.objects.exists())
        routine.refresh_from_db()
        self.assertEqual(routine.next_run_at, NINE + timedelta(days=1))

        # The occurrence on the edge of the grace still runs.
        again = self.make_routine(title="Edge", next_run_at=NINE)
        self.assertEqual(self.scheduler(NINE + GRACE), 1)
        self.assertEqual(AgentRoutineRun.objects.get(routine=again).status, "running")

    def test_a_long_stop_does_not_make_a_storm(self) -> None:
        routine = self.make_routine(
            pattern_kind="every_minutes",
            interval=15,
            time_of_day=None,
            next_run_at=NINE - timedelta(days=40),
        )

        self.assertEqual(self.scheduler(NINE + timedelta(minutes=5)), 1)

        statuses = list(
            AgentRoutineRun.objects.filter(routine=routine).values_list("status", flat=True)
        )
        self.assertEqual(statuses.count("running"), 1)
        self.assertEqual(statuses.count("skipped_missed"), MAX_MISSED_RECORDED)
        self.assertEqual(agents.AgentJob.objects.count(), 1)
        routine.refresh_from_db()
        assert routine.next_run_at is not None
        self.assertEqual(routine.next_run_at, NINE + timedelta(minutes=15))

    def test_command_runs_the_scheduler_and_the_reconciliation(self) -> None:
        self.make_routine()
        out = StringIO()

        with time_machine.travel(NINE + timedelta(seconds=30), tick=False):
            call_command("run_scheduled_routines", stdout=out)

        self.assertEqual(out.getvalue().strip(), "Started 1 routine(s), closed 0 run(s).")
        self.assertEqual(AgentRoutineRun.objects.count(), 1)


class RoutineIdentityTest(RoutineTestCase):
    def test_an_agent_that_is_archived_skips_the_run_and_tells_the_owner(self) -> None:
        routine = self.make_routine()
        agents.AgentProfile.objects.filter(id=self.profile.id).update(
            desired_state="archived", archived_at=NINE
        )
        before = self.stream_message_count()

        self.assertEqual(self.scheduler(NINE + timedelta(seconds=30)), 0)

        run = AgentRoutineRun.objects.get(routine=routine)
        self.assertEqual((run.status, run.error_code), ("skipped_unavailable", "agent_unavailable"))
        self.assertIsNone(run.job)
        self.assertEqual(self.stream_message_count(), before)
        routine.refresh_from_db()
        self.assertFalse(routine.enabled)
        self.assertEqual(routine.paused_reason, "unavailable")
        (notice,) = self.notices()
        self.assertIn("did not run", notice)
        self.assertIn("The agent is archived, paused or not ready.", notice)
        self.assertIn("`Morning brief`", notice)

    def test_a_paused_agent_or_one_that_is_not_ready_skips_the_run(self) -> None:
        for fields in [{"desired_state": "paused"}, {"readiness_state": "needs_action"}]:
            routine = self.make_routine(title=str(fields))
            agents.AgentProfile.objects.filter(id=self.profile.id).update(**fields)

            self.scheduler(NINE + timedelta(seconds=30))

            run = AgentRoutineRun.objects.get(routine=routine)
            self.assertEqual(run.error_code, "agent_unavailable", fields)
            agents.AgentProfile.objects.filter(id=self.profile.id).update(
                desired_state="enabled", readiness_state="ready"
            )

    def test_an_owner_who_lost_the_agent_or_the_channel_skips_the_run(self) -> None:
        for patched, error_code in [
            ("zerver.lib.routines.check_agent_access", "agent_access"),
            ("zerver.lib.routines.access_stream_by_id", "channel_unavailable"),
        ]:
            routine = self.make_routine(title=error_code)
            with mock.patch(patched, side_effect=JsonableError("no")):
                self.scheduler(NINE + timedelta(seconds=30))

            run = AgentRoutineRun.objects.get(routine=routine)
            self.assertEqual((run.status, run.error_code), ("skipped_unavailable", error_code))
        self.assertFalse(agents.AgentJob.objects.exists())

    def test_every_post_of_a_routine_is_the_bot_s_and_never_a_person_s(self) -> None:
        self.make_routine()
        before = set(Message.objects.values_list("id", flat=True))

        self.scheduler(NINE + timedelta(seconds=30))

        senders = {
            message.sender_id
            for message in Message.objects.exclude(id__in=before)
            if message.recipient_id == self.stream.recipient_id
        }
        self.assertEqual(senders, {self.profile.bot_user_id})
        self.assertNotIn(self.owner.id, senders)


class RoutineHistoryTest(RoutineTestCase):
    def start_run(self, at: datetime = NINE) -> AgentRoutineRun:
        self.assertEqual(self.scheduler(at + timedelta(seconds=30)), 1)
        return AgentRoutineRun.objects.order_by("-scheduled_for").first()  # type: ignore[return-value]

    def test_a_run_shows_running_until_its_job_ends(self) -> None:
        routine = self.make_routine()
        run = self.start_run()

        for status in ["queued", "running", "waiting_for_input", "verifying"]:
            self.end_job(run, status)
            self.assertEqual(self.reconcile(NINE + timedelta(minutes=5)), 0)
            run.refresh_from_db()
            self.assertEqual(run.status, "running", status)

        result = Message.objects.get(id=self.send_stream_message(self.owner, "routines", "Done."))
        finished = NINE + timedelta(minutes=9)
        self.end_job(run, "completed", result_message=result, completed_at=finished)
        self.assertEqual(self.reconcile(NINE + timedelta(minutes=10)), 1)

        run.refresh_from_db()
        self.assertEqual(run.status, "succeeded")
        self.assertEqual(run.finished_at, finished)
        self.assertEqual(run.result_message_id, result.id)
        # Nobody measured the cost, so it stays unknown and is never zero.
        self.assertIsNone(run.cost_microunits)
        self.assertIsNone(run.to_api_dict()["cost_microunits"])
        routine.refresh_from_db()
        self.assertEqual(routine.last_status, "succeeded")
        self.assertEqual(self.notices(), [])

    def test_a_failed_run_tells_the_owner_once(self) -> None:
        routine = self.make_routine()
        run = self.start_run()
        self.end_job(run, "failed")

        self.assertEqual(self.reconcile(NINE + timedelta(minutes=5)), 1)
        self.assertEqual(self.reconcile(NINE + timedelta(minutes=6)), 0)

        run.refresh_from_db()
        self.assertEqual((run.status, run.error_code), ("failed", "job_failed"))
        self.assertIsNotNone(run.notified_at)
        (notice,) = self.notices()
        self.assertIn("did not run well", notice)
        self.assertIn("The agent's job failed.", notice)
        self.assertIn("/#routines", notice)
        self.assertEqual(most_recent_message(self.owner).content, notice)
        routine.refresh_from_db()
        self.assertTrue(routine.enabled)
        self.assertEqual(routine.last_status, "failed")

    def test_three_failures_in_a_row_pause_the_routine(self) -> None:
        routine = self.make_routine()
        for day in range(FAILURES_BEFORE_PAUSE):
            run = self.start_run(NINE + timedelta(days=day))
            self.end_job(run, "failed")
            self.reconcile(NINE + timedelta(days=day, minutes=5))
            routine.refresh_from_db()
            self.assertEqual(routine.enabled, day < FAILURES_BEFORE_PAUSE - 1)

        self.assertEqual(routine.paused_reason, "failures")
        self.assertEqual(len(self.notices()), FAILURES_BEFORE_PAUSE)
        self.assertIn("The routine is paused.", self.notices()[-1])
        self.assertNotIn("The routine is paused.", self.notices()[0])
        # A paused routine does not run again.
        self.assertEqual(self.scheduler(NINE + timedelta(days=5)), 0)

    def test_a_success_between_failures_restarts_the_count(self) -> None:
        routine = self.make_routine()
        for day, status in enumerate(["failed", "failed", "completed", "failed", "failed"]):
            run = self.start_run(NINE + timedelta(days=day))
            self.end_job(run, status, completed_at=NINE + timedelta(days=day, minutes=1))
            self.reconcile(NINE + timedelta(days=day, minutes=5))

        routine.refresh_from_db()
        self.assertTrue(routine.enabled)

    def test_a_job_that_was_deleted_fails_the_run(self) -> None:
        self.make_routine()
        run = self.start_run()
        AgentRoutineRun.objects.filter(id=run.id).update(job=None)

        self.reconcile(NINE + timedelta(minutes=5))

        run.refresh_from_db()
        self.assertEqual((run.status, run.error_code), ("failed", "job_missing"))

    def test_a_job_that_could_not_start_fails_the_run(self) -> None:
        routine = self.make_routine()
        before = self.stream_message_count()

        with (
            mock.patch("zerver.lib.routines.create_job", side_effect=ValueError("Queue is full.")),
            self.assertLogs("zerver.lib.routines", "WARNING") as logs,
        ):
            self.assertEqual(self.scheduler(NINE + timedelta(seconds=30)), 0)
        self.assertIn("Queue is full.", logs.output[0])

        run = AgentRoutineRun.objects.get(routine=routine)
        self.assertEqual((run.status, run.error_code), ("failed", "job_not_created"))
        self.assertEqual(self.stream_message_count(), before)
        self.assertEqual(len(self.notices()), 1)


class RoutineBudgetTest(RoutineTestCase):
    def test_an_exceeded_budget_skips_the_run_pauses_and_tells_once(self) -> None:
        routine = self.make_routine()

        with mock.patch("zerver.lib.routine_budget.model_budget_state", return_value="exceeded"):
            self.assertEqual(self.scheduler(NINE + timedelta(seconds=30)), 0)
            self.assertEqual(self.scheduler(NINE + timedelta(days=1, seconds=30)), 0)

        run = AgentRoutineRun.objects.get(routine=routine)
        self.assertEqual((run.status, run.error_code), ("skipped_budget", "budget_exceeded"))
        self.assertFalse(agents.AgentJob.objects.exists())
        routine.refresh_from_db()
        self.assertFalse(routine.enabled)
        self.assertEqual(routine.paused_reason, "budget")
        (notice,) = self.notices()
        self.assertIn("The budget for this month is used up.", notice)

    def test_a_warning_lets_the_run_go_and_tells_once_a_month(self) -> None:
        routine = self.make_routine()

        with mock.patch("zerver.lib.routine_budget.model_budget_state", return_value="warn"):
            self.assertEqual(self.scheduler(NINE + timedelta(seconds=30)), 1)
            self.assertEqual(self.scheduler(NINE + timedelta(days=1, seconds=30)), 1)
            self.assertEqual(len(self.notices()), 1)
            # The routine moved to 17 Nov, the next month.
            AgentRoutine.objects.filter(id=routine.id).update(next_run_at=NINE + timedelta(days=30))
            self.assertEqual(self.scheduler(NINE + timedelta(days=30, seconds=30)), 1)

        self.assertEqual(AgentRoutineRun.objects.filter(routine=routine).count(), 3)
        notices = self.notices()
        self.assertEqual(len(notices), 2)
        self.assertIn("almost used up", notices[0])


class RoutineRunNowTest(RoutineTestCase):
    def test_run_now_starts_one_job_and_keeps_the_next_run(self) -> None:
        routine = self.make_routine(next_run_at=NINE + timedelta(days=1))
        now = NINE - timedelta(hours=4)

        run = run_routine_now(routine, now)

        self.assertEqual((run.trigger, run.status, run.scheduled_for), ("manual", "running", now))
        assert run.job is not None
        self.assertEqual(run.job.source_message.sender_id, self.profile.bot_user_id)  # type: ignore[union-attr]
        routine.refresh_from_db()
        self.assertEqual(routine.next_run_at, NINE + timedelta(days=1))

    def test_run_now_is_limited_to_once_a_minute_with_a_fresh_key_each_time(self) -> None:
        routine = self.make_routine(next_run_at=NINE + timedelta(days=1))
        now = NINE - timedelta(hours=4)
        first = run_routine_now(routine, now)

        with self.assertRaisesRegex(JsonableError, "ran a moment ago"):
            run_routine_now(routine, now + timedelta(seconds=59))
        second = run_routine_now(routine, now + timedelta(seconds=61))

        assert first.job is not None and second.job is not None
        self.assertNotEqual(first.job.id, second.job.id)
        self.assertNotEqual(first.job.idempotency_key, second.job.idempotency_key)
        self.assertEqual(AgentRoutineRun.objects.filter(routine=routine).count(), 2)

    def test_run_now_works_for_a_paused_routine_and_not_an_outside_one(self) -> None:
        paused = self.make_routine(enabled=False, paused_reason="user")
        self.assertEqual(run_routine_now(paused, NINE).status, "running")

        external = self.make_routine(
            executor="external", agent_profile=None, external_agent_name="Opus", stream=None
        )
        with self.assertRaisesRegex(JsonableError, "outside Sanji"):
            run_routine_now(external, NINE)


class RoutineBoardSummaryTest(RoutineTestCase):
    @override
    def setUp(self) -> None:
        super().setUp()
        self.board = get_or_create_default_board(self.realm)
        column = self.board.columns.order_by("order").first()
        assert column is not None
        self.column = column

    def card(self, title: str, due_at: datetime | None, **fields: Any) -> Task:
        return do_create_task(
            user_profile=self.owner,
            board=self.board,
            column=self.column,
            title=title,
            assignee=fields.pop("assignee", self.owner),
            due_at=due_at,
            **fields,
        )

    def test_the_summary_lists_overdue_due_today_and_blocked_cards(self) -> None:
        overdue = self.card("Send the invoice", NINE - timedelta(days=2))
        today = self.card("Review the brief", NINE + timedelta(hours=9))
        blocked = self.card("Wait for legal", None)
        Task.objects.filter(id=blocked.id).update(blocked=True)
        self.card("Next week", NINE + timedelta(days=5))
        finished = self.card("Already done", NINE - timedelta(days=1))
        Task.objects.filter(id=finished.id).update(completed_at=NINE)
        private = self.make_stream("hidden", invite_only=True)
        self.card("Secret plan", NINE - timedelta(days=1), stream_id=private.id)

        with time_machine.travel(NINE, tick=False):
            text = board_summary(self.owner)

        self.assertIn("Task board on Oct 16:", text)
        self.assertIn(f"Overdue cards (1):\n- GT-{overdue.counter} Send the invoice", text)
        self.assertIn(f"Cards due today (1):\n- GT-{today.counter} Review the brief", text)
        self.assertIn(f"Blocked cards (1):\n- GT-{blocked.counter} Wait for legal", text)
        for left_out in ["Next week", "Already done", "Secret plan"]:
            self.assertNotIn(left_out, text)

    def test_the_summary_is_empty_without_anything_to_report(self) -> None:
        self.card("Someday", None)

        self.assertEqual(board_summary(self.owner), "")

    def test_a_long_list_is_cut_and_a_title_stays_on_one_line(self) -> None:
        for number in range(12):
            self.card(f"Card {number}\nsecond line", NINE - timedelta(days=1))

        with time_machine.travel(NINE, tick=False):
            text = board_summary(self.owner)

        self.assertIn("Overdue cards (12):", text)
        self.assertIn("And 4 more.", text)
        self.assertNotIn("\nsecond line", text)

    def test_the_job_request_carries_the_summary_after_the_instructions(self) -> None:
        self.card("Send the invoice", NINE - timedelta(days=2))
        self.make_routine()

        self.scheduler(NINE + timedelta(seconds=30))

        job = agents.AgentJob.objects.get(profile=self.profile)
        self.assertRegex(
            job.request,
            r"^Morning brief\n\nSummarize what matters today\.\n\nTask board on Oct 16:\n\n"
            r"Overdue cards \(1\):\n- GT-\d+ Send the invoice",
        )
