"""Tests for the command that registers routines that run outside Sanji."""

import json
import tempfile
from datetime import timedelta
from io import StringIO
from typing import Any

import time_machine
from django.core.management import call_command
from django.core.management.base import CommandError

from zerver.lib.routines import run_due_routines
from zerver.models import AgentRoutine, AgentRoutineRun
from zerver.tests.test_routines_api import EIGHT, RoutineAPITestCase


class ImportExternalRoutinesTest(RoutineAPITestCase):
    def import_file(self, items: object, *args: str) -> str:
        with tempfile.NamedTemporaryFile("w", suffix=".json") as file:
            json.dump(items, file)
            file.flush()
            out = StringIO()
            call_command(
                "import_external_routines",
                file.name,
                "--realm",
                self.realm.string_id,
                *args,
                stdout=out,
            )
        return out.getvalue()

    def item(self, **fields: Any) -> dict[str, Any]:
        return {
            "title": "Cek pagi",
            "owner_email": self.hamlet.delivery_email,
            "agent_name": "Opus",
            "pattern_kind": "daily",
            "time_of_day": "08:00",
            "stream": "routines",
            "topic": "Cek",
            **fields,
        }

    def test_an_outside_routine_is_registered_and_only_shown(self) -> None:
        with time_machine.travel(EIGHT, tick=False):
            output = self.import_file([self.item(), self.item(title="Rekap", stream=None)])

        self.assertIn("2 routine(s) registered.", output)
        routine = AgentRoutine.objects.get(title="Cek pagi")
        self.assertEqual(routine.executor, "external")
        self.assertIsNone(routine.agent_profile)
        self.assertEqual(routine.external_agent_name, "Opus")
        self.assertEqual(routine.stream_id, self.stream.id)
        self.assertEqual(routine.next_run_at, EIGHT + timedelta(days=1))

        # The scheduler never runs it, and nobody can change it here.
        with time_machine.travel(EIGHT + timedelta(days=1, minutes=5), tick=False):
            self.assertEqual(run_due_routines(self.realm), 0)
        self.assertFalse(AgentRoutineRun.objects.exists())
        self.login_user(self.hamlet)
        data = self.assert_json_success(self.client_get("/json/routines"))
        self.assertEqual({item["title"] for item in data["routines"]}, {"Cek pagi", "Rekap"})
        self.assertEqual({item["can_edit"] for item in data["routines"]}, {False})

    def test_a_second_import_adds_nothing_and_a_dry_run_saves_nothing(self) -> None:
        self.assertIn("1 routine(s) to register.", self.import_file([self.item()], "--dry-run"))
        self.assertFalse(AgentRoutine.objects.exists())

        self.import_file([self.item()])
        output = self.import_file([self.item()])

        self.assertIn("Cek pagi: already registered.", output)
        self.assertEqual(AgentRoutine.objects.count(), 1)

    def test_a_mistake_in_one_item_registers_none(self) -> None:
        for bad in [
            self.item(owner_email="nobody@zulip.com"),
            self.item(stream="no-such-channel"),
            self.item(pattern_kind="every_minutes", interval=5, time_of_day=None),
            self.item(time_of_day="8am"),
            {"title": "No schedule"},
        ]:
            with self.assertRaises(CommandError):
                self.import_file([self.item(title="Fine"), bad])
        with self.assertRaises(CommandError):
            self.import_file({"title": "Not a list"})
        self.assertFalse(AgentRoutine.objects.exists())
