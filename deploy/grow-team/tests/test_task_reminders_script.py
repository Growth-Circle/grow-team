"""Test the task reminder timer with a local Compose substitute."""

import configparser
import os
import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path

from typing_extensions import override

DEPLOY = Path(__file__).parents[1]
SCRIPT = DEPLOY / "send-task-reminders.sh"
MANAGE = "/home/zulip/deployments/current/manage.py"
PYTHON = "/home/zulip/deployments/current/.venv/bin/python"
LOCK_DIRECTORY = "/data/grow-team-agent-private"


def read_unit(name: str) -> configparser.ConfigParser:
    parser = configparser.ConfigParser(interpolation=None, strict=False)
    parser.optionxform = str  # type: ignore[assignment,method-assign]  # unit keys are case-sensitive
    parser.read(DEPLOY / "systemd" / name)
    return parser


class TaskRemindersScriptTest(unittest.TestCase):
    @override
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.calls = self.root / "calls"
        self.deploy = self.root / "deploy"
        self.deploy.mkdir()
        self.script = self.deploy / "send-task-reminders.sh"
        self.script.write_text(SCRIPT.read_text())
        self.script.chmod(0o700)
        (self.root / "lock").mkdir()
        # The container runs the inner shell command. Here it runs on the
        # host, with the fixed container paths pointed at the fixture.
        compose = self.deploy / "compose.sh"
        compose.write_text(
            f"""#!/usr/bin/env python3
import os, subprocess, sys
args = sys.argv[1:]
assert args[:7] == ['exec', '-T', '--interactive=false', '--user', 'zulip', 'zulip', 'sh'], args
script = args[8]
script = script.replace({LOCK_DIRECTORY!r}, {str(self.root / "lock")!r})
script = script.replace({PYTHON!r} + ' -B', {str(self.root / "python")!r})
script = script.replace({MANAGE!r}, {str(self.root / "manage")!r})
script = script.replace('120s', os.environ.get('TASK_REMINDERS_TEST_DEADLINE', '120s'))
raise SystemExit(subprocess.call(['sh', '-c', script]))
"""
        )
        compose.chmod(0o700)
        (self.root / "python").write_text('#!/bin/sh\nexec "$@"\n')
        (self.root / "python").chmod(0o700)
        self.write_manage(f"echo \"$@\" >> '{self.calls}'")

    def write_manage(self, body: str) -> None:
        path = self.root / "manage"
        path.write_text(f"#!/bin/sh\n{body}\n")
        path.chmod(0o700)

    def run_script(self, deadline: str = "120s") -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [str(self.script)],
            check=False,
            capture_output=True,
            text=True,
            env={**os.environ, "TASK_REMINDERS_TEST_DEADLINE": deadline},
            timeout=20,
        )

    def test_runs_the_reminder_command_once(self) -> None:
        result = self.run_script()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.calls.read_text().strip(), "send_task_reminders")

    def test_a_run_that_overlaps_another_exits_75_without_running_the_command(self) -> None:
        ready = self.root / "ready"
        self.write_manage(f"touch '{ready}'; sleep 3")
        first = threading.Thread(target=self.run_script)
        first.start()
        for _ in range(100):
            if ready.exists():
                break
            time.sleep(0.05)
        self.write_manage(f"echo second >> '{self.calls}'")

        result = self.run_script()
        first.join()

        self.assertEqual(result.returncode, 75)
        self.assertFalse(self.calls.exists())

    def test_a_stuck_command_is_stopped_at_the_deadline(self) -> None:
        self.write_manage("sleep 30")

        started = time.monotonic()
        result = self.run_script(deadline="1s")

        self.assertEqual(result.returncode, 124)
        self.assertLess(time.monotonic() - started, 10)

    def test_the_script_keeps_lock_and_deadline_inside_the_container(self) -> None:
        text = SCRIPT.read_text()

        self.assertIn("/usr/bin/timeout", text)
        self.assertIn("/usr/bin/flock --no-fork --nonblock --conflict-exit-code 75", text)
        self.assertIn(f"{LOCK_DIRECTORY}/task-reminders.lock", text)
        self.assertEqual(SCRIPT.stat().st_mode & 0o777, 0o755)


class TaskRemindersUnitsTest(unittest.TestCase):
    def test_the_service_runs_the_script_at_low_priority_in_the_grow_team_slice(self) -> None:
        unit = read_unit("grow-team-task-reminders.service")

        self.assertEqual(unit["Service"]["Type"], "oneshot")
        self.assertEqual(unit["Service"]["Slice"], "grow-team.slice")
        self.assertEqual(
            unit["Service"]["ExecStart"], "/opt/grow-team/deploy/send-task-reminders.sh"
        )
        self.assertEqual(unit["Service"]["Nice"], "15")
        self.assertEqual(unit["Service"]["CPUWeight"], "10")
        self.assertEqual(unit["Service"]["SuccessExitStatus"], "75")
        self.assertEqual(unit["Unit"]["Requisite"], "grow-team.service")
        # A failure ends this oneshot and nothing else: nothing restarts or
        # depends on it.
        self.assertNotIn("Restart", unit["Service"])
        self.assertNotIn("RequiredBy", unit["Install"] if unit.has_section("Install") else {})

    def test_the_timer_fires_every_quarter_hour_and_skips_missed_runs(self) -> None:
        unit = read_unit("grow-team-task-reminders.timer")

        self.assertEqual(unit["Timer"]["OnCalendar"], "*:0/15")
        self.assertEqual(unit["Timer"]["Persistent"], "false")
        self.assertEqual(unit["Timer"]["Unit"], "grow-team-task-reminders.service")
        self.assertEqual(unit["Install"]["WantedBy"], "timers.target")


if __name__ == "__main__":
    unittest.main()
