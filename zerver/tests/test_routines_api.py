"""Tests for the routines API: access, validation, pausing, running now."""

from datetime import UTC, datetime, timedelta
from typing import Any
from unittest import mock
from uuid import uuid4

import orjson
import time_machine
from typing_extensions import override

from zerver.lib.event_schema import check_routine_add, check_routine_remove, check_routine_update
from zerver.lib.routine_schedule import Schedule, validate_schedule
from zerver.lib.routine_templates import ROUTINE_TEMPLATES
from zerver.lib.routines import run_due_routines
from zerver.lib.test_classes import ZulipTestCase
from zerver.models import AgentRealmSettings, AgentRoutine, AgentRoutineRun, UserProfile, agents
from zerver.tests.test_room_digests import _ready_kaki_profile

# 08:00 on Friday 16 Oct 2026 in Jakarta (UTC+7).
EIGHT = datetime(2026, 10, 16, 1, 0, tzinfo=UTC)


class RoutineAPITestCase(ZulipTestCase):
    @override
    def setUp(self) -> None:
        super().setUp()
        self.hamlet = self.example_user("hamlet")
        self.cordelia = self.example_user("cordelia")
        self.iago = self.example_user("iago")
        self.guest = self.example_user("polonius")
        self.realm = self.hamlet.realm
        AgentRealmSettings.objects.update_or_create(
            realm=self.realm,
            defaults={"enabled": True, "timezone": "Asia/Jakarta", "agent_language": "en"},
        )
        self.agent = _ready_kaki_profile(self.hamlet, name="Sanji Agent")
        agents.AgentProfile.objects.filter(id=self.agent.id).update(agent_role="custom")
        self.stream = self.subscribe(self.hamlet, "routines")
        self.subscribe(self.cordelia, "routines")
        self.subscribe(self.guest, "routines")

    def params(self, **overrides: Any) -> dict[str, Any]:
        values: dict[str, Any] = {
            "title": "Morning brief",
            "instructions": "Summarize what matters today.",
            "stream_id": self.stream.id,
            "topic": "Brief",
            "pattern_kind": "daily",
            "time_of_day": "09:00",
        }
        values.update(overrides)
        for name in ("weekdays",):
            if name in values:
                values[name] = orjson.dumps(values[name]).decode()
        return {name: str(value) for name, value in values.items()}

    def create(self, user: UserProfile | None = None, **overrides: Any) -> Any:
        self.login_user(user or self.hamlet)
        return self.client_post("/json/routines", self.params(**overrides))

    def make_routine(self, **fields: Any) -> AgentRoutine:
        values: dict[str, Any] = {
            "realm": self.realm,
            "title": "Evening wrap",
            "instructions": "Write the wrap.",
            "agent_profile": self.agent,
            "owner": self.hamlet,
            "stream": self.stream,
            "topic": "Wrap",
            "pattern_kind": "weekdays",
            "time_of_day": "17:30",
            "next_run_at": EIGHT + timedelta(hours=9),
        }
        values.update(fields)
        return AgentRoutine.objects.create(**values)


class RoutineCreateTest(RoutineAPITestCase):
    def test_a_member_creates_a_routine_with_its_next_run_on_the_workspace_clock(self) -> None:
        with time_machine.travel(EIGHT, tick=False):
            data = self.assert_json_success(self.create())

        routine = AgentRoutine.objects.get()
        self.assertEqual(data["routine"]["id"], str(routine.id))
        self.assertEqual(routine.owner_id, self.hamlet.id)
        self.assertEqual(routine.agent_profile_id, self.agent.id)
        self.assertEqual(routine.timezone, "Asia/Jakarta")
        self.assertEqual(routine.executor, "agent")
        self.assertTrue(routine.enabled)
        self.assertEqual(routine.next_run_at, EIGHT + timedelta(hours=1))
        self.assertEqual(
            data["routine"]["next_run_at"], int((EIGHT + timedelta(hours=1)).timestamp())
        )
        self.assertEqual(data["routine"]["time_of_day"], "09:00")
        self.assertTrue(data["routine"]["can_edit"])
        self.assertEqual(data["routine"]["agent_name"], "Sanji Agent")

    def test_a_chosen_agent_timezone_and_pattern_are_kept(self) -> None:
        with time_machine.travel(EIGHT, tick=False):
            data = self.assert_json_success(
                self.create(
                    agent_profile_id=self.agent.id,
                    pattern_kind="weekly",
                    weekdays=[4, 0],
                    time_of_day="16:00",
                    timezone="America/New_York",
                    model_preset="fast",
                )
            )

        routine = AgentRoutine.objects.get(id=data["routine"]["id"])
        self.assertEqual(routine.weekdays, [0, 4])
        self.assertEqual(routine.timezone, "America/New_York")
        self.assertEqual(routine.model_preset, "fast")
        # 16:00 in New York (UTC-4) on Friday 16 Oct.
        self.assertEqual(routine.next_run_at, datetime(2026, 10, 16, 20, 0, tzinfo=UTC))

    def test_an_interval_pattern_keeps_no_time_of_day(self) -> None:
        with time_machine.travel(EIGHT, tick=False):
            data = self.assert_json_success(
                self.create(pattern_kind="every_minutes", interval=15, time_of_day="09:00")
            )

        self.assertIsNone(data["routine"]["time_of_day"])
        self.assertEqual(data["routine"]["interval"], 15)
        self.assertEqual(AgentRoutine.objects.get().next_run_at, EIGHT + timedelta(minutes=15))

    def test_a_schedule_that_cannot_run_is_rejected_and_nothing_is_saved(self) -> None:
        bad_schedules: list[dict[str, Any]] = [
            {"pattern_kind": "every_minutes", "interval": 14},
            {"pattern_kind": "every_minutes", "interval": 60},
            {"pattern_kind": "every_hours", "interval": 24},
            {"pattern_kind": "weekly", "weekdays": []},
            {"pattern_kind": "weekly", "weekdays": [7]},
            {"pattern_kind": "monthly", "month_day": 32},
            {"pattern_kind": "monthly"},
            {"time_of_day": "9:00"},
            {"time_of_day": "24:00"},
            {"pattern_kind": "yearly"},
            {"timezone": "Mars/Olympus"},
            {"topic": ""},
            {"title": "x" * 101},
            {"instructions": "x" * 4001},
        ]
        for overrides in bad_schedules:
            result = self.create(**overrides)
            self.assertEqual(result.status_code, 400, overrides)
            self.assertEqual(orjson.loads(result.content)["result"], "error", overrides)
        self.assertFalse(AgentRoutine.objects.exists())

    def test_an_interval_error_names_the_bounds(self) -> None:
        self.assert_json_error(
            self.create(pattern_kind="every_minutes", interval=5),
            "Minutes between runs must be from 15 to 59.",
        )

    def test_a_guest_cannot_use_routines(self) -> None:
        self.assert_json_error(
            self.create(self.guest), "Scheduled routines are for members, not guests."
        )
        self.login_user(self.guest)
        self.assert_json_error(
            self.client_get("/json/routines"), "Scheduled routines are for members, not guests."
        )
        self.assert_json_error(
            self.client_get("/json/routines/templates"),
            "Scheduled routines are for members, not guests.",
        )
        self.assertFalse(AgentRoutine.objects.exists())

    def test_a_channel_that_the_member_cannot_read_is_rejected(self) -> None:
        private = self.make_stream("hidden-routines", invite_only=True)
        self.subscribe(self.iago, "hidden-routines")

        result = self.create(stream_id=private.id)

        self.assert_json_error(result, "Invalid channel ID")
        self.assertFalse(AgentRoutine.objects.exists())

    def test_an_agent_that_the_member_cannot_use_is_rejected(self) -> None:
        other = _ready_kaki_profile(self.iago, name="Iago Agent")

        result = self.create(self.cordelia, agent_profile_id=other.id)

        self.assert_json_error(result, "Agent access denied.")
        self.assertFalse(AgentRoutine.objects.exists())

    def test_an_agent_that_is_archived_or_paused_is_rejected(self) -> None:
        agents.AgentProfile.objects.filter(id=self.agent.id).update(
            desired_state="archived", archived_at=EIGHT
        )
        self.assert_json_error(self.create(agent_profile_id=self.agent.id), "Agent access denied.")

        agents.AgentProfile.objects.filter(id=self.agent.id).update(
            desired_state="paused", archived_at=None
        )
        self.assert_json_error(
            self.create(agent_profile_id=self.agent.id), "This agent is not available."
        )
        self.assertFalse(AgentRoutine.objects.exists())

    def test_the_default_agent_is_required_when_none_is_given(self) -> None:
        agents.AgentProfile.objects.filter(id=self.agent.id).update(agent_role="planner")

        result = self.create()

        self.assert_json_error(result, "No agent is available to post routines. Ask an admin.")

    def test_an_unknown_agent_is_rejected(self) -> None:
        self.assert_json_error(self.create(agent_profile_id=uuid4()), "Agent does not exist.")

    def test_a_member_without_the_agent_task_permission_cannot_give_a_routine_to_an_agent(
        self,
    ) -> None:
        with mock.patch("zerver.lib.routine_access.has_role_permission", return_value=False):
            result = self.create()

        self.assert_json_error(result, "Agent access denied.")
        self.assertFalse(AgentRoutine.objects.exists())


class RoutineListTest(RoutineAPITestCase):
    def test_the_list_has_every_routine_the_member_can_read_with_outside_ones(self) -> None:
        mine = self.make_routine()
        outside = self.make_routine(
            title="Rekap harian",
            executor="external",
            agent_profile=None,
            external_agent_name="Opus",
            stream=None,
            topic="",
        )
        private = self.make_stream("hidden-routines", invite_only=True)
        self.subscribe(self.iago, "hidden-routines")
        self.make_routine(title="Secret", stream=private)
        AgentRoutineRun.objects.create(
            realm=self.realm,
            routine=mine,
            scheduled_for=EIGHT,
            status="succeeded",
        )

        self.login_user(self.cordelia)
        data = self.assert_json_success(self.client_get("/json/routines"))

        titles = [routine["title"] for routine in data["routines"]]
        self.assertEqual(titles, ["Evening wrap", "Rekap harian"])
        by_id = {routine["id"]: routine for routine in data["routines"]}
        self.assertFalse(by_id[str(mine.id)]["can_edit"])
        self.assertEqual(by_id[str(mine.id)]["last_run"]["status"], "succeeded")
        outside_data = by_id[str(outside.id)]
        self.assertEqual(outside_data["executor"], "external")
        self.assertEqual(outside_data["agent_name"], "Opus")
        self.assertFalse(outside_data["can_edit"])
        self.assertIsNone(outside_data["last_run"])

    def test_a_routine_in_a_channel_that_the_member_cannot_read_does_not_exist_for_them(
        self,
    ) -> None:
        private = self.make_stream("hidden-routines", invite_only=True)
        self.subscribe(self.iago, "hidden-routines")
        secret = self.make_routine(title="Secret", stream=private)

        self.login_user(self.cordelia)
        for response in [
            self.client_get(f"/json/routines/{secret.id}"),
            self.client_get(f"/json/routines/{secret.id}/runs"),
            self.client_post(f"/json/routines/{secret.id}/pause"),
            self.client_post(f"/json/routines/{secret.id}/run"),
            self.client_delete(f"/json/routines/{secret.id}"),
            self.client_patch(f"/json/routines/{secret.id}", {"title": "Mine"}),
        ]:
            self.assert_json_error(response, "Routine does not exist.")

        secret.refresh_from_db()
        self.assertEqual(secret.title, "Secret")
        self.assertTrue(secret.enabled)

    def test_an_unknown_routine_does_not_exist(self) -> None:
        self.login_user(self.hamlet)
        self.assert_json_error(
            self.client_get(f"/json/routines/{uuid4()}"), "Routine does not exist."
        )

    def test_the_agents_to_choose_from_are_those_the_member_can_use(self) -> None:
        _ready_kaki_profile(self.iago, name="Iago Agent")

        self.login_user(self.cordelia)
        data = self.assert_json_success(self.client_get("/json/routines/agents"))
        self.assertEqual(data["agents"], [])
        self.assertEqual(data["default_agent_id"], str(self.agent.id))
        self.assertEqual(data["default_timezone"], "Asia/Jakarta")

        self.login_user(self.hamlet)
        data = self.assert_json_success(self.client_get("/json/routines/agents"))
        self.assertEqual(data["agents"], [{"id": str(self.agent.id), "name": "Sanji Agent"}])

    def test_templates_fill_the_form_and_create_nothing(self) -> None:
        self.login_user(self.hamlet)

        data = self.assert_json_success(self.client_get("/json/routines/templates"))

        self.assertEqual(
            [template["id"] for template in data["templates"]],
            ["morning_brief", "end_of_day_summary", "weekly_team_digest", "client_check"],
        )
        brief = data["templates"][0]
        self.assertEqual(
            (brief["title"], brief["pattern_kind"], brief["time_of_day"]),
            ("Morning brief", "daily", "08:00"),
        )
        digest = data["templates"][2]
        self.assertEqual((digest["pattern_kind"], digest["weekdays"]), ("weekly", [4]))
        self.assertFalse(AgentRoutine.objects.exists())

    def test_every_template_is_a_schedule_that_can_run(self) -> None:
        for template in ROUTINE_TEMPLATES:
            validate_schedule(
                Schedule(
                    pattern_kind=template.pattern_kind,
                    timezone="Asia/Jakarta",
                    time_of_day=template.time_of_day,
                    weekdays=template.weekdays,
                )
            )
            self.assertLessEqual(len(str(template.title)), AgentRoutine.MAX_TITLE_LENGTH)
            self.assertLessEqual(
                len(str(template.instructions)), AgentRoutine.MAX_INSTRUCTIONS_LENGTH
            )


class RoutineEditTest(RoutineAPITestCase):
    def test_the_owner_and_an_administrator_can_change_it_and_another_member_cannot(self) -> None:
        routine = self.make_routine()

        self.login_user(self.cordelia)
        self.assert_json_error(
            self.client_patch(f"/json/routines/{routine.id}", {"title": "Mine"}),
            "Only the owner of this routine or an administrator can change it.",
        )
        self.assert_json_error(
            self.client_post(f"/json/routines/{routine.id}/pause"),
            "Only the owner of this routine or an administrator can change it.",
        )
        self.assert_json_error(
            self.client_delete(f"/json/routines/{routine.id}"),
            "Only the owner of this routine or an administrator can change it.",
        )
        self.assert_json_error(
            self.client_post(f"/json/routines/{routine.id}/run"),
            "Only the owner of this routine or an administrator can change it.",
        )
        routine.refresh_from_db()
        self.assertEqual(routine.title, "Evening wrap")

        for user, title in [(self.hamlet, "By owner"), (self.iago, "By admin")]:
            self.login_user(user)
            data = self.assert_json_success(
                self.client_patch(f"/json/routines/{routine.id}", {"title": title})
            )
            self.assertEqual(data["routine"]["title"], title)
        routine.refresh_from_db()
        self.assertEqual(routine.title, "By admin")
        # An administrator's change keeps the owner.
        self.assertEqual(routine.owner_id, self.hamlet.id)

    def test_a_routine_that_runs_outside_sanji_cannot_be_changed(self) -> None:
        outside = self.make_routine(
            executor="external", agent_profile=None, external_agent_name="Opus", stream=None
        )

        self.login_user(self.iago)
        for response in [
            self.client_patch(f"/json/routines/{outside.id}", {"title": "Mine"}),
            self.client_post(f"/json/routines/{outside.id}/pause"),
            self.client_post(f"/json/routines/{outside.id}/run"),
            self.client_delete(f"/json/routines/{outside.id}"),
        ]:
            self.assert_json_error(
                response, "This routine runs outside Sanji and cannot be changed here."
            )
        self.assertTrue(AgentRoutine.objects.filter(id=outside.id).exists())

    def test_a_new_schedule_works_out_the_next_run_again_and_a_new_title_does_not(self) -> None:
        with time_machine.travel(EIGHT, tick=False):
            routine = AgentRoutine.objects.get(
                id=self.assert_json_success(self.create())["routine"]["id"]
            )
            original = routine.next_run_at

            self.assert_json_success(
                self.client_patch(f"/json/routines/{routine.id}", {"title": "Renamed"})
            )
            routine.refresh_from_db()
            self.assertEqual(routine.next_run_at, original)

            self.assert_json_success(
                self.client_patch(f"/json/routines/{routine.id}", {"time_of_day": "08:30"})
            )
            routine.refresh_from_db()
            self.assertEqual(routine.next_run_at, EIGHT + timedelta(minutes=30))

    def test_a_change_of_pattern_leaves_no_field_of_the_old_one_behind(self) -> None:
        with time_machine.travel(EIGHT, tick=False):
            routine = AgentRoutine.objects.get(
                id=self.assert_json_success(self.create(pattern_kind="weekly", weekdays=[1, 3]))[
                    "routine"
                ]["id"]
            )
            self.assert_json_success(
                self.client_patch(f"/json/routines/{routine.id}", {"pattern_kind": "daily"})
            )
            routine.refresh_from_db()
            self.assertEqual((routine.pattern_kind, routine.weekdays), ("daily", []))

            self.assert_json_success(
                self.client_patch(
                    f"/json/routines/{routine.id}",
                    {"pattern_kind": "every_hours", "interval": "2"},
                )
            )
            routine.refresh_from_db()
            self.assertEqual((routine.interval, routine.time_of_day), (2, None))

            self.assert_json_error(
                self.client_patch(
                    f"/json/routines/{routine.id}",
                    {"pattern_kind": "monthly", "time_of_day": "09:00"},
                ),
                "Choose a day of the month from 1 to 31.",
            )
            routine.refresh_from_db()
            self.assertEqual(routine.pattern_kind, "every_hours")

    def test_moving_to_a_channel_that_the_owner_cannot_read_is_rejected(self) -> None:
        routine = self.make_routine()
        private = self.make_stream("hidden-routines", invite_only=True)
        self.subscribe(self.iago, "hidden-routines")

        # An administrator can read it, but the job runs for the owner.
        self.login_user(self.iago)
        self.assert_json_error(
            self.client_patch(f"/json/routines/{routine.id}", {"stream_id": private.id}),
            "Invalid channel ID",
        )
        routine.refresh_from_db()
        self.assertEqual(routine.stream_id, self.stream.id)

    def test_moving_to_an_agent_that_the_owner_cannot_use_is_rejected(self) -> None:
        routine = self.make_routine()
        other = _ready_kaki_profile(self.iago, name="Iago Agent")

        self.login_user(self.hamlet)
        self.assert_json_error(
            self.client_patch(f"/json/routines/{routine.id}", {"agent_profile_id": str(other.id)}),
            "Agent access denied.",
        )
        routine.refresh_from_db()
        self.assertEqual(routine.agent_profile_id, self.agent.id)

    def test_delete_removes_the_routine_and_its_runs(self) -> None:
        routine = self.make_routine()
        AgentRoutineRun.objects.create(
            realm=self.realm, routine=routine, scheduled_for=EIGHT, status="succeeded"
        )

        self.login_user(self.hamlet)
        self.assert_json_success(self.client_delete(f"/json/routines/{routine.id}"))

        self.assertFalse(AgentRoutine.objects.exists())
        self.assertFalse(AgentRoutineRun.objects.exists())


class RoutinePauseTest(RoutineAPITestCase):
    def test_pause_stops_the_routine_and_resume_counts_from_now(self) -> None:
        routine = self.make_routine(next_run_at=EIGHT)
        self.login_user(self.hamlet)

        data = self.assert_json_success(self.client_post(f"/json/routines/{routine.id}/pause"))
        self.assertFalse(data["routine"]["enabled"])
        self.assertEqual(data["routine"]["paused_reason"], "user")
        with time_machine.travel(EIGHT + timedelta(minutes=5), tick=False):
            self.assertEqual(run_due_routines(self.realm), 0)
        self.assertFalse(AgentRoutineRun.objects.exists())

        # A day and a half later, on a Sunday, the missed run is not made up for.
        with time_machine.travel(EIGHT + timedelta(days=1, hours=10), tick=False):
            data = self.assert_json_success(self.client_post(f"/json/routines/{routine.id}/resume"))
            self.assertTrue(data["routine"]["enabled"])
            self.assertEqual(data["routine"]["paused_reason"], "")
        routine.refresh_from_db()
        self.assertEqual(routine.next_run_at, datetime(2026, 10, 19, 10, 30, tzinfo=UTC))
        self.assertTrue(routine.enabled)

    def test_the_state_survives_a_reload(self) -> None:
        routine = self.make_routine()
        self.login_user(self.hamlet)
        self.assert_json_success(self.client_post(f"/json/routines/{routine.id}/pause"))

        data = self.assert_json_success(self.client_get("/json/routines"))

        self.assertEqual(
            [(item["enabled"], item["paused_reason"]) for item in data["routines"]],
            [(False, "user")],
        )

    def test_resume_starts_a_streak_of_failures_over(self) -> None:
        routine = self.make_routine(enabled=False, paused_reason="failures", failure_streak=3)
        self.login_user(self.hamlet)

        self.assert_json_success(self.client_post(f"/json/routines/{routine.id}/resume"))

        routine.refresh_from_db()
        self.assertEqual((routine.enabled, routine.failure_streak), (True, 0))


class RoutineRunNowTest(RoutineAPITestCase):
    def test_run_now_starts_one_job_without_moving_the_next_run(self) -> None:
        routine = self.make_routine(next_run_at=EIGHT + timedelta(days=1))
        self.login_user(self.hamlet)

        with time_machine.travel(EIGHT, tick=False):
            data = self.assert_json_success(self.client_post(f"/json/routines/{routine.id}/run"))
            again = self.client_post(f"/json/routines/{routine.id}/run")

        self.assertEqual((data["run"]["trigger"], data["run"]["status"]), ("manual", "running"))
        self.assert_json_error(
            again, "This routine ran a moment ago. Wait a minute to run it again."
        )
        self.assertEqual(agents.AgentJob.objects.count(), 1)
        routine.refresh_from_db()
        self.assertEqual(routine.next_run_at, EIGHT + timedelta(days=1))

    def test_history_lists_the_runs_newest_first_with_unknown_cost(self) -> None:
        routine = self.make_routine()
        for hours in range(3):
            AgentRoutineRun.objects.create(
                realm=self.realm,
                routine=routine,
                scheduled_for=EIGHT + timedelta(hours=hours),
                status="failed" if hours == 1 else "succeeded",
                error_code="job_failed" if hours == 1 else "",
            )

        self.login_user(self.cordelia)
        data = self.assert_json_success(self.client_get(f"/json/routines/{routine.id}/runs"))

        self.assertEqual(
            [run["scheduled_for"] for run in data["runs"]],
            [int((EIGHT + timedelta(hours=hours)).timestamp()) for hours in (2, 1, 0)],
        )
        self.assertEqual(data["runs"][1]["error_code"], "job_failed")
        self.assertEqual({run["cost_microunits"] for run in data["runs"]}, {None})


class RoutineEventTest(RoutineAPITestCase):
    def test_events_reach_the_members_who_can_read_the_channel_and_never_a_guest(self) -> None:
        self.login_user(self.hamlet)
        with self.capture_send_event_calls(expected_num_events=1) as events:
            result = self.client_post("/json/routines", self.params())
        routine_id = self.assert_json_success(result)["routine"]["id"]
        check_routine_add("events[0]", {**events[0]["event"], "id": 0})
        self.assertIn(self.cordelia.id, set(events[0]["users"]))
        self.assertNotIn(self.guest.id, set(events[0]["users"]))

        with self.capture_send_event_calls(expected_num_events=1) as events:
            self.assert_json_success(self.client_post(f"/json/routines/{routine_id}/pause"))
        check_routine_update("events[0]", {**events[0]["event"], "id": 0})
        self.assertFalse(events[0]["event"]["routine"]["enabled"])

        with self.capture_send_event_calls(expected_num_events=1) as events:
            self.assert_json_success(self.client_delete(f"/json/routines/{routine_id}"))
        check_routine_remove("events[0]", {**events[0]["event"], "id": 0})
        self.assertEqual(events[0]["event"]["routine_id"], routine_id)

    def test_a_routine_of_a_private_channel_reaches_only_its_readers(self) -> None:
        private = self.make_stream("hidden-routines", invite_only=True)
        self.subscribe(self.hamlet, "hidden-routines")

        self.login_user(self.hamlet)
        with self.capture_send_event_calls(expected_num_events=1) as events:
            self.assert_json_success(
                self.client_post("/json/routines", self.params(stream_id=private.id))
            )

        self.assertEqual(set(events[0]["users"]), {self.hamlet.id})
