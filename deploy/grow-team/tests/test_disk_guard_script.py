"""Test the disk guard with a local Compose substitute."""

import configparser
import os
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

from typing_extensions import override

DEPLOY = Path(__file__).parents[1]
SCRIPT = DEPLOY / "disk-guard.sh"


def read_unit(name: str) -> configparser.ConfigParser:
    parser = configparser.ConfigParser(interpolation=None, strict=False)
    parser.optionxform = str  # type: ignore[assignment,method-assign]  # unit keys are case-sensitive
    parser.read(DEPLOY / "systemd" / name)
    return parser


class DiskGuardScriptTest(unittest.TestCase):
    @override
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.calls = self.root / "calls"
        self.deploy = self.root / "deploy"
        self.deploy.mkdir()
        self.script = self.deploy / "disk-guard.sh"
        self.script.write_text(SCRIPT.read_text())
        self.script.chmod(0o700)
        compose = self.deploy / "compose.sh"
        compose.write_text(f"#!/bin/sh\nprintf '%s\\n' \"$@\" >> '{self.calls}'\n")
        compose.chmod(0o700)
        self.go_cache = self.root / "go-build"
        self.state = self.root / "state"

    def run_script(self, mode: str, alert_percent: int) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [str(self.script), mode],
            check=False,
            capture_output=True,
            text=True,
            env={
                **os.environ,
                "GO_CACHE_DIR": str(self.go_cache),
                "DISK_GUARD_STATE_DIR": str(self.state),
                "DISK_ALERT_PERCENT": str(alert_percent),
            },
            timeout=20,
        )

    def test_check_below_the_threshold_sends_nothing(self) -> None:
        result = self.run_script("check", alert_percent=101)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(self.calls.exists())

    def test_check_over_the_threshold_messages_owners_once_per_window(self) -> None:
        first = self.run_script("check", alert_percent=0)
        second = self.run_script("check", alert_percent=0)

        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(second.returncode, 0, second.stderr)
        calls = self.calls.read_text()
        self.assertEqual(calls.count("exec\n"), 1)
        self.assertIn("ROLE_REALM_OWNER", calls)
        self.assertIn("NOTIFICATION_BOT", calls)
        self.assertIn("alert already sent", second.stdout)

    def test_check_alerts_again_after_the_window(self) -> None:
        self.state.mkdir()
        (self.state / "disk-alert.last").write_text(f"{int(time.time()) - 13 * 3600}\n")

        result = self.run_script("check", alert_percent=0)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.calls.read_text().count("exec\n"), 1)

    def test_trim_drops_only_cache_entries_untouched_for_a_day(self) -> None:
        bucket = self.go_cache / "ab"
        bucket.mkdir(parents=True)
        old = time.time() - 3 * 86400
        for name in ("old-a", "old-d", "trim.txt", "README"):
            (bucket / name).write_text("x")
            os.utime(bucket / name, (old, old))
        (bucket / "fresh-d").write_text("x")

        result = self.run_script("trim-go-cache", alert_percent=80)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            sorted(path.name for path in bucket.iterdir()), ["README", "fresh-d", "trim.txt"]
        )

    def test_trim_without_a_cache_is_a_no_op(self) -> None:
        result = self.run_script("trim-go-cache", alert_percent=80)

        self.assertEqual(result.returncode, 0, result.stderr)

    def test_an_unknown_mode_fails(self) -> None:
        result = self.run_script("clean-everything", alert_percent=80)

        self.assertEqual(result.returncode, 2)


class DeployServerScriptTest(unittest.TestCase):
    def test_the_deploy_stops_before_build_and_swap_when_disk_is_low(self) -> None:
        text = (DEPLOY / "deploy-server.sh").read_text()

        self.assertIn('if [ "$f" -lt 20 ]', text)
        self.assertLess(text.index("guard buildx"), text.index("buildx build"))
        self.assertLess(text.index("guard swap"), text.index('sed -i "s#${old_tag}'))
        self.assertEqual((DEPLOY / "deploy-server.sh").stat().st_mode & 0o777, 0o755)

    def test_the_deploy_prunes_old_builds_after_verify(self) -> None:
        text = (DEPLOY / "deploy-server.sh").read_text()

        self.assertLess(text.index('step "verify"'), text.index('prune_builds "$build"'))
        self.assertLess(text.index('step "verify"'), text.index('step "prune old builds"'))


class PruneBuildsTest(unittest.TestCase):
    @override
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.builds = self.root / "builds"
        self.builds.mkdir()
        # The real script runs find and rm through sudo; the substitute just runs them.
        bin_dir = self.root / "bin"
        bin_dir.mkdir()
        sudo = bin_dir / "sudo"
        sudo.write_text('#!/bin/sh\nexec "$@"\n')
        sudo.chmod(0o755)
        self.bin_dir = bin_dir

    def make_build(self, name: str, age_days: int) -> Path:
        path = self.builds / name
        (path / "nested").mkdir(parents=True)
        (path / "nested" / "file").write_text("x")
        stamp = time.time() - age_days * 86400
        os.utime(path, (stamp, stamp))
        return path

    def prune(self, keep: Path) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [str(DEPLOY / "deploy-server.sh"), "prune-builds", str(keep)],
            check=False,
            capture_output=True,
            text=True,
            env={
                **os.environ,
                "PATH": f"{self.bin_dir}:{os.environ['PATH']}",
                "BUILDS_DIR": str(self.builds),
            },
            timeout=20,
        )

    def test_keeps_the_new_build_and_the_previous_one(self) -> None:
        for name, age in (("aaa", 9), ("bbb", 7), ("ccc with space", 5), ("ddd", 3)):
            self.make_build(name, age)
        new = self.make_build("eee", 0)

        result = self.prune(new)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(sorted(path.name for path in self.builds.iterdir()), ["ddd", "eee"])

    def test_keeps_the_new_build_even_when_it_looks_older(self) -> None:
        # tar can restore an old mtime on the build directory it extracts into.
        self.make_build("old-a", 2)
        self.make_build("old-b", 4)
        new = self.make_build("new", 30)

        result = self.prune(new)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(sorted(path.name for path in self.builds.iterdir()), ["new", "old-a"])

    def test_leaves_files_and_symlink_targets_alone(self) -> None:
        outside = self.root / "outside"
        outside.mkdir()
        (outside / "keep.txt").write_text("x")
        (self.builds / "link").symlink_to(outside)
        (self.builds / "stray-file").write_text("x")
        for name, age in (("aaa", 9), ("bbb", 7)):
            self.make_build(name, age)
        new = self.make_build("ccc", 0)

        result = self.prune(new)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            sorted(path.name for path in self.builds.iterdir()),
            ["bbb", "ccc", "link", "stray-file"],
        )
        self.assertTrue((outside / "keep.txt").exists())

    def test_a_single_build_is_kept(self) -> None:
        new = self.make_build("only", 0)

        result = self.prune(new)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(new.exists())


class DiskGuardUnitsTest(unittest.TestCase):
    def test_the_alert_runs_hourly_at_low_priority(self) -> None:
        service = read_unit("grow-team-disk-alert.service")
        timer = read_unit("grow-team-disk-alert.timer")

        self.assertEqual(
            service["Service"]["ExecStart"], "/opt/grow-team/deploy/disk-guard.sh check"
        )
        self.assertEqual(service["Service"]["Slice"], "grow-team.slice")
        self.assertEqual(timer["Timer"]["OnCalendar"], "hourly")
        self.assertEqual(timer["Timer"]["Unit"], "grow-team-disk-alert.service")

    def test_the_trim_runs_daily_as_the_cache_owner(self) -> None:
        service = read_unit("grow-team-go-cache-trim.service")
        timer = read_unit("grow-team-go-cache-trim.timer")

        self.assertEqual(
            service["Service"]["ExecStart"], "/opt/grow-team/deploy/disk-guard.sh trim-go-cache"
        )
        self.assertEqual(service["Service"]["User"], "hermesadmin")
        self.assertEqual(timer["Timer"]["OnCalendar"], "*-*-* 20:00:00 UTC")
        self.assertEqual(timer["Timer"]["Persistent"], "yes")


if __name__ == "__main__":
    unittest.main()
