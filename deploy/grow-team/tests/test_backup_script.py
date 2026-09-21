"""Test deployment backups with temporary files and a local Compose substitute."""

import fcntl
import os
import subprocess
import tarfile
import tempfile
import unittest
from pathlib import Path

from typing_extensions import override

SCRIPT = Path(__file__).parents[1] / "backup.sh"
UNITS = (
    "grow-team.service",
    "grow-team.slice",
    "grow-team-docker.service",
    "grow-team-cloudflared.service",
    "grow-team-ai-tunnel.service",
    "grow-team-backup.service",
    "grow-team-backup.timer",
    "grow-team-agent-reconcile.service",
    "grow-team-agent-reconcile.timer",
)


class BackupScriptTest(unittest.TestCase):
    @override
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        for directory in (
            "etc/grow-team",
            "etc/systemd/system",
            "opt/grow-team/deploy",
            "tmp",
            "bin",
        ):
            (self.root / directory).mkdir(parents=True, exist_ok=True)
        for name in UNITS:
            (self.root / "etc/systemd/system" / name).write_text(name)
        (self.root / "etc/grow-team/settings").write_text("fixture-settings")
        self.backups = self.root / "backups"
        self.lock = self.root / "backup.lock"
        script = SCRIPT.read_text()
        # Redirect fixed deployment paths. Keep the backup commands unchanged.
        script = script.replace("/opt/grow-team", str(self.root / "opt/grow-team"))
        script = script.replace("/var/backups/grow-team", str(self.backups))
        script = script.replace("/run/grow-team-backup.lock", str(self.lock))
        script = script.replace("tar -C / ", f"tar -C '{self.root}' ")
        self.script = self.root / "opt/grow-team/deploy/backup.sh"
        self.script.write_text(script)
        self.script.chmod(0o700)
        self.write_executable("bin/date", "#!/bin/sh\nprintf '20260922T120000Z\\n'\n")
        self.write_executable(
            "manage.py",
            """#!/usr/bin/env python3
import io, os, pathlib, sys, tarfile
path = pathlib.Path(next(arg.split('=', 1)[1] for arg in sys.argv if arg.startswith('--output=')))
with open(os.environ['BACKUP_TEST_PATHS'], 'a') as output:
    print(path, file=output)
if os.environ.get('BACKUP_TEST_FAIL'):
    path.write_bytes(b'incomplete backup')
    raise SystemExit(23)
with tarfile.open(path, 'w:gz') as archive:
    content = b'fixture database and uploads'
    member = tarfile.TarInfo('zulip-backup/probe.txt')
    member.size = len(content)
    archive.addfile(member, io.BytesIO(content))
print('Fixture backup complete')
""",
        )
        self.write_executable(
            "opt/grow-team/deploy/compose.sh",
            """#!/usr/bin/env python3
import os, pathlib, shutil, subprocess, sys
root = pathlib.Path(os.environ['BACKUP_TEST_ROOT'])
args = sys.argv[1:]
if args[0] == 'exec':
    assert args[:8] == ['exec', '-T', '--interactive=false', '--user', 'zulip', 'zulip', 'sh', '-c']
    script = args[8].replace('/tmp/', str(root / 'tmp') + '/')
    script = script.replace('/home/zulip/deployments/current/manage.py', str(root / 'manage.py'))
    raise SystemExit(subprocess.call(['sh', '-c', script]))
if args[0] == 'cp':
    source = args[1].removeprefix('zulip:').replace('/tmp/', str(root / 'tmp') + '/')
    shutil.copyfile(source, args[2])
else:
    raise SystemExit(2)
""",
        )

    def write_executable(self, name: str, content: str) -> None:
        path = self.root / name
        path.write_text(content)
        path.chmod(0o700)

    def run_backup(self, *, fail: bool = False) -> subprocess.CompletedProcess[str]:
        env = {
            **os.environ,
            "PATH": f"{self.root / 'bin'}:{os.environ['PATH']}",
            "BACKUP_TEST_ROOT": str(self.root),
            "BACKUP_TEST_PATHS": str(self.root / "paths"),
        }
        if fail:
            env["BACKUP_TEST_FAIL"] = "1"
        return subprocess.run(
            [str(self.script)], check=False, capture_output=True, text=True, env=env, timeout=10
        )

    def verify_backup(self, directory: Path) -> None:
        self.assertEqual(directory.stat().st_mode & 0o777, 0o700)
        for name in ("zulip.tar.gz", "operations.tar.gz", "SHA256SUMS", "files.txt"):
            self.assertEqual((directory / name).stat().st_mode & 0o777, 0o600)
        result = subprocess.run(
            ["sha256sum", "--check", "SHA256SUMS"],
            cwd=directory,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        with tarfile.open(directory / "zulip.tar.gz") as archive:
            source = archive.extractfile("zulip-backup/probe.txt")
            assert source is not None
            self.assertEqual(source.read(), b"fixture database and uploads")
        with tarfile.open(directory / "operations.tar.gz") as archive:
            for name in UNITS:
                self.assertIn(f"etc/systemd/system/{name}", archive.getnames())

    def test_each_backup_has_private_unique_paths_and_all_units(self) -> None:
        for _ in range(2):
            result = self.run_backup()
            self.assertEqual(result.returncode, 0, result.stderr)
        directories = sorted(self.backups.iterdir())
        self.assertEqual(len(directories), 2)
        for directory in directories:
            self.verify_backup(directory)
        paths = (self.root / "paths").read_text().splitlines()
        self.assertEqual(len(set(paths)), 2)
        self.assertTrue(all(not Path(path).exists() for path in paths))

    def test_failure_preserves_previous_backup_and_has_no_success_manifest(self) -> None:
        result = self.run_backup()
        self.assertEqual(result.returncode, 0, result.stderr)
        original = next(self.backups.iterdir())
        original_bytes = (original / "zulip.tar.gz").read_bytes()
        failed = self.run_backup(fail=True)
        self.assertNotEqual(failed.returncode, 0)
        self.assertNotIn("Backup:", failed.stdout)
        self.assertEqual((original / "zulip.tar.gz").read_bytes(), original_bytes)
        directories = set(self.backups.iterdir()) - {original}
        self.assertEqual(len(directories), 1)
        self.assertFalse((directories.pop() / "SHA256SUMS").exists())
        self.verify_backup(original)
        self.assertTrue(
            all(not Path(path).exists() for path in (self.root / "paths").read_text().splitlines())
        )

    def test_existing_backup_lock_prevents_another_backup(self) -> None:
        with self.lock.open("w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            result = self.run_backup()
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.root / "paths").exists())


if __name__ == "__main__":
    unittest.main()
