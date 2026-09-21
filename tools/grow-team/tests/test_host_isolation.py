"""DB-free tests for the Grow Team host-isolation evidence tool."""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from typing import Any, ClassVar

from typing_extensions import override

ROOT = Path(__file__).resolve().parents[3]
SCRIPT_PATH = ROOT / "tools/grow-team/host-isolation.py"


def load_module() -> Any:
    spec = importlib.util.spec_from_file_location("host_isolation", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class HostIsolationTest(unittest.TestCase):
    tool: ClassVar[Any]

    @classmethod
    @override
    def setUpClass(cls) -> None:
        cls.tool = load_module()

    def baseline(self) -> dict[str, Any]:
        return {
            "captured_at": "2026-09-21T15:57:22+00:00",
            "units": {
                "/etc/systemd/system/hermestrading-one.timer": "a" * 64,
                "/etc/systemd/system/timers.target.wants/hermestrading-one.timer": "a" * 64,
            },
            "timers": [
                {"unit": "hermestrading-one.timer", "enabled": "disabled", "active": "inactive"},
                {"unit": "hermestrading-template@.timer", "enabled": "disabled", "active": ""},
            ],
            "containers": [
                {
                    "Names": "grow-team-zulip-1",
                    "ID": "app-old",
                    "Image": "grow-team/server:old",
                    "State": "running",
                    "HealthStatus": "healthy",
                },
                {
                    "Names": "grow-team-redis-1",
                    "ID": "redis-id",
                    "Image": "redis:alpine",
                    "State": "running",
                    "HealthStatus": "none",
                },
            ],
        }

    def snapshot(self) -> dict[str, Any]:
        return {
            "captured_at": "2026-09-22T00:00:00+00:00",
            "units": {
                "/etc/systemd/system/hermestrading-one.timer": "a" * 64,
                "/etc/systemd/system/timers.target.wants/hermestrading-one.timer": "a" * 64,
            },
            "timers": [
                {"unit": "hermestrading-one.timer", "enabled": "disabled", "active": "inactive"},
                {"unit": "hermestrading-template@.timer", "enabled": "disabled", "active": ""},
            ],
            "containers": [
                {
                    "name": "grow-team-zulip-1",
                    "id": "app-old",
                    "image": "grow-team/server:old",
                    "state": "running",
                    "health": "healthy",
                },
                {
                    "name": "grow-team-redis-1",
                    "id": "redis-id",
                    "image": "redis:alpine",
                    "state": "running",
                    "health": "none",
                },
            ],
        }

    def test_compare_accepts_disabled_and_template_timer_states(self) -> None:
        self.assertEqual(self.tool.compare(self.baseline(), self.snapshot()), [])

    def test_expanded_baseline_accepts_56_paths_but_compare_requires_the_exact_set(self) -> None:
        baseline_path = (
            ROOT / ".superpowers/sdd/2026-09-21-grow-team-agent-platform/production-baseline.json"
        )
        original = json.loads(baseline_path.read_text())
        expanded = deepcopy(original)
        for number in range(15):
            expanded["units"][
                f"/etc/systemd/system/hermestrading-extra-{number}.service.d/onfailure.conf"
            ] = f"{number:064x}"
        self.assertEqual(len(expanded["units"]), 56)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "expanded-baseline.json"
            path.write_text(json.dumps(expanded))
            loaded = self.tool.load_baseline(path)
        self.assertEqual(len(loaded["units"]), 56)
        self.assertEqual(self.tool.compare(expanded, expanded), [])
        missing = deepcopy(expanded)
        missing["units"].pop(next(iter(missing["units"])))
        self.assertIn("unit paths differ", self.tool.compare(expanded, missing))
        self.assertIn("unit paths differ", self.tool.compare(original, expanded))

    def test_compare_rejects_duplicate_records(self) -> None:
        snapshot = self.snapshot()
        snapshot["timers"] = [snapshot["timers"][0], snapshot["timers"][0]]
        errors = self.tool.compare(self.baseline(), snapshot)
        self.assertTrue(any("duplicate timer" in error for error in errors))

    def test_compare_rejects_missing_and_extra_records(self) -> None:
        snapshot = self.snapshot()
        snapshot["units"] = {"/etc/systemd/system/hermestrading-one.timer": "a" * 64}
        snapshot["containers"].append(
            {
                "name": "grow-team-extra-1",
                "id": "extra",
                "image": "x",
                "state": "running",
                "health": "none",
            }
        )
        errors = self.tool.compare(self.baseline(), snapshot)
        self.assertTrue(any("unit paths differ" in error for error in errors))
        self.assertTrue(any("container names differ" in error for error in errors))

    def test_compare_rejects_app_replacement_without_explicit_flag(self) -> None:
        snapshot = self.snapshot()
        snapshot["containers"][0].update(id="app-new", image="grow-team/server:new")
        errors = self.tool.compare(self.baseline(), snapshot)
        self.assertTrue(any("grow-team-zulip-1 was replaced" in error for error in errors))

    def test_compare_allows_only_running_healthy_app_replacement_when_requested(self) -> None:
        snapshot = self.snapshot()
        snapshot["containers"][0].update(id="app-new", image="grow-team/server:new")
        self.assertEqual(
            self.tool.compare(self.baseline(), snapshot, allow_app_replacement=True), []
        )

    def test_compare_rejects_unhealthy_or_replaced_supporting_container(self) -> None:
        snapshot = self.snapshot()
        snapshot["containers"][1].update(id="other-redis", health="unhealthy")
        errors = self.tool.compare(self.baseline(), snapshot, allow_app_replacement=True)
        self.assertTrue(any("grow-team-redis-1 was replaced" in error for error in errors))
        self.assertTrue(any("grow-team-redis-1 is unhealthy" in error for error in errors))

    def test_capture_fails_without_writing_evidence_when_remote_collection_fails(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output_dir = Path(directory)

            def failed_remote(_command: list[str], _input: str) -> subprocess.CompletedProcess[str]:
                return subprocess.CompletedProcess(_command, 255, "", "connection failed")

            with self.assertRaisesRegex(RuntimeError, "remote collection failed"):
                self.tool.capture(self.baseline(), output_dir, run_remote=failed_remote)
            self.assertEqual(list(output_dir.iterdir()), [])

    def test_capture_rejects_duplicate_remote_unit_records(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output_dir = Path(directory)
            output = "\n".join(
                [
                    "UNIT\t/etc/systemd/system/hermestrading-one.timer\t" + "a" * 64,
                    "UNIT\t/etc/systemd/system/hermestrading-one.timer\t" + "a" * 64,
                ]
            )

            def successful_remote(
                command: list[str], _input: str
            ) -> subprocess.CompletedProcess[str]:
                return subprocess.CompletedProcess(command, 0, output, "")

            with self.assertRaisesRegex(RuntimeError, "duplicate unit"):
                self.tool.capture(self.baseline(), output_dir, run_remote=successful_remote)
            self.assertEqual(list(output_dir.iterdir()), [])

    def test_generated_discovery_includes_extra_paths_symlinks_and_override_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            timer = root / "hermestrading-one.timer"
            timer.write_text("[Timer]\n")
            wants = root / "timers.target.wants"
            wants.mkdir()
            wants.joinpath("hermestrading-one.timer").symlink_to(timer)
            override = root / "hermestrading-one.service.d"
            override.mkdir()
            override.joinpath("override.conf").write_text("[Service]\n")
            root.joinpath("unrelated.service").write_text("[Service]\n")

            command = (
                "set -euo pipefail\n"
                + self.tool.discovery_script(str(root))
                + "\npaths=$(discover_hermes_paths)\nprintf '%s\\n' \"$paths\""
            )
            result = subprocess.run(
                ["bash", "-c", command], text=True, capture_output=True, timeout=10, check=False
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                result.stdout.splitlines(),
                [
                    str(override / "override.conf"),
                    str(timer),
                    str(wants / "hermestrading-one.timer"),
                ],
            )

    def test_generated_discovery_fails_on_partial_find_output(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "systemd"
            root.mkdir()
            fake_bin = Path(directory) / "bin"
            fake_bin.mkdir()
            fake_find = fake_bin / "find"
            fake_find.write_text("#!/bin/sh\nprintf 'partial-path\\n'\nexit 42\n")
            fake_find.chmod(0o700)
            command = (
                "set -euo pipefail\n"
                + self.tool.discovery_script(str(root))
                + "\npaths=$(discover_hermes_paths)\nprintf 'must-not-run:%s\\n' \"$paths\""
            )
            environment = os.environ | {"PATH": str(fake_bin) + ":" + os.environ["PATH"]}
            result = subprocess.run(
                ["bash", "-c", command],
                text=True,
                capture_output=True,
                timeout=10,
                check=False,
                env=environment,
            )

            self.assertEqual(result.returncode, 42)
            self.assertNotIn("must-not-run", result.stdout)

    def test_capture_rejects_empty_successful_remote_output_without_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output_dir = Path(directory) / "evidence"

            def successful_remote(
                command: list[str], _input: str
            ) -> subprocess.CompletedProcess[str]:
                return subprocess.CompletedProcess(command, 0, "", "")

            with self.assertRaisesRegex(RuntimeError, "remote output is empty"):
                self.tool.capture(self.baseline(), output_dir, run_remote=successful_remote)
            self.assertFalse(output_dir.exists())

    def test_capture_timeout_does_not_create_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output_dir = Path(directory) / "evidence"

            def timed_out_remote(
                command: list[str], _input: str
            ) -> subprocess.CompletedProcess[str]:
                raise subprocess.TimeoutExpired(command, 30)

            with self.assertRaisesRegex(RuntimeError, "timed out"):
                self.tool.capture(self.baseline(), output_dir, run_remote=timed_out_remote)
            self.assertFalse(output_dir.exists())

    def test_capture_parses_read_only_remote_records_and_uses_new_output_path(self) -> None:
        baseline = self.baseline()
        with tempfile.TemporaryDirectory() as directory:
            output_dir = Path(directory)
            output_dir.joinpath("prior.json").write_text("prior evidence")
            output = "\n".join(
                [
                    "UNIT\t/etc/systemd/system/hermestrading-one.timer\t" + "a" * 64,
                    "UNIT\t/etc/systemd/system/timers.target.wants/hermestrading-one.timer\t"
                    + "a" * 64,
                    "TIMER\thermestrading-one.timer\tdisabled\tinactive",
                    "TIMER\thermestrading-template@.timer\tdisabled\t",
                    "CONTAINER\tgrow-team-zulip-1\tapp-old\tgrow-team/server:old\trunning\thealthy",
                    "CONTAINER\tgrow-team-redis-1\tredis-id\tredis:alpine\trunning\tnone",
                ]
            )

            def successful_remote(
                command: list[str], remote_script: str
            ) -> subprocess.CompletedProcess[str]:
                self.assertIn("BatchMode=yes", command)
                self.assertIn("server-gteam", command)
                self.assertIn("env -u DOCKER_CONTEXT", remote_script)
                self.assertIn("--host=unix:///run/grow-team-docker/docker.sock", remote_script)
                self.assertIn("/opt/grow-team/bin/docker", remote_script)
                return subprocess.CompletedProcess(command, 0, output, "")

            evidence_path = self.tool.capture(baseline, output_dir, run_remote=successful_remote)
            self.assertNotEqual(evidence_path.name, "prior.json")
            self.assertEqual(os.stat(evidence_path).st_mode & 0o777, 0o600)
            evidence = json.loads(evidence_path.read_text())
            self.assertEqual(self.tool.compare(baseline, evidence), [])


if __name__ == "__main__":
    unittest.main()
