"""Test the off-host backup with stub age and rclone binaries and a local fake R2."""

import base64
import configparser
import fcntl
import hashlib
import io
import os
import subprocess
import tarfile
import tempfile
import unittest
from pathlib import Path

from typing_extensions import override

DEPLOY = Path(__file__).parents[1]
SCRIPT = DEPLOY / "offsite-backup.sh"
BUCKET = "backups"
RECIPIENT = "age1testrecipient"

# Stand-ins for the real tools. The fake remote "r2:BUCKET/PATH" is a directory
# named by FAKE_R2, and "encryption" is base64 so plaintext cannot leak through.
AGE_STUB = """#!/usr/bin/env python3
import base64, sys
args = sys.argv[1:]
assert args[0] == '--encrypt' and args[1] == '--recipient', args
recipient, output = args[2], args[4]
if recipient == 'bad':
    sys.stderr.write('age: invalid recipient\\n')
    raise SystemExit(1)
with open(output, 'wb') as out:
    out.write(('AGE-FOR ' + recipient + '\\n').encode())
    out.write(base64.b64encode(sys.stdin.buffer.read()))
"""

RCLONE_STUB = """#!/usr/bin/env python3
import hashlib, json, os, pathlib, shutil, sys
root = pathlib.Path(os.environ['FAKE_R2'])
args = [a for a in sys.argv[1:] if not a.startswith('--') and a != 's']
command = args[0]

def local(remote):
    assert remote.startswith('r2:'), remote
    return root / remote[3:]

pathlib.Path(os.environ['FAKE_RCLONE_LOG']).open('a').write(
    json.dumps({'argv': sys.argv[1:], 'endpoint': os.environ['RCLONE_CONFIG_R2_ENDPOINT'],
                'key': os.environ['RCLONE_CONFIG_R2_ACCESS_KEY_ID'],
                'provider': os.environ['RCLONE_CONFIG_R2_PROVIDER']}) + '\\n')
if command == 'copyto':
    if os.environ.get('FAKE_UPLOAD_FAIL'):
        sys.stderr.write('rclone: 403 AccessDenied\\n')
        raise SystemExit(1)
    destination = local(args[2])
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(args[1], destination)
    if os.environ.get('FAKE_TRUNCATE') and destination.suffix == '.age':
        destination.write_bytes(destination.read_bytes()[:-1])
elif command == 'lsf':
    target = local(args[1])
    if target.is_file():
        print(target.stat().st_size)
    else:
        for item in sorted(target.iterdir()) if target.exists() else []:
            print(item.name)
elif command == 'md5sum':
    target = local(args[1])
    digest = hashlib.md5(target.read_bytes()).hexdigest()
    if os.environ.get('FAKE_NO_MD5'):
        digest = ''
    if os.environ.get('FAKE_BAD_MD5'):
        digest = '0' * 32
    print(f'{digest:>32}  {target.name}')
elif command == 'deletefile':
    local(args[1]).unlink()
else:
    raise SystemExit(f'unexpected rclone command {command}')
"""


class OffsiteBackupScriptTest(unittest.TestCase):
    @override
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.backups = self.root / "backups"
        self.backups.mkdir()
        self.remote = self.root / "r2"
        self.work = self.root / "work"
        self.work.mkdir()
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.install_stub("age", AGE_STUB)
        self.install_stub("rclone", RCLONE_STUB)
        self.env_file = self.root / "offsite-backup.env"
        self.write_env()
        self.rclone_log = self.root / "rclone.log"
        self.lock_file = self.root / "lock"
        self.script = self.root / "offsite-backup.sh"
        self.script.write_text(SCRIPT.read_text())
        self.script.chmod(0o700)

    def install_stub(self, name: str, content: str) -> None:
        path = self.bin / name
        path.write_text(content)
        path.chmod(0o700)

    def write_env(self, **overrides: str) -> None:
        values = {
            "R2_ENDPOINT": "https://account.r2.cloudflarestorage.com",
            "R2_BUCKET": BUCKET,
            "R2_ACCESS_KEY_ID": "key-id",
            "R2_SECRET_ACCESS_KEY": "secret-key",
            "AGE_RECIPIENT": RECIPIENT,
            **overrides,
        }
        self.env_file.write_text("".join(f"{key}={value}\n" for key, value in values.items()))
        self.env_file.chmod(0o600)

    def make_backup(self, name: str, *, complete: bool = True) -> Path:
        directory = self.backups / name
        directory.mkdir()
        (directory / "zulip.tar.gz").write_bytes(f"database of {name}".encode())
        if complete:
            digest = hashlib.sha256((directory / "zulip.tar.gz").read_bytes()).hexdigest()
            (directory / "SHA256SUMS").write_text(f"{digest}  zulip.tar.gz\n")
        return directory

    def snapshot(self) -> dict[str, tuple[int, str]]:
        return {
            str(path.relative_to(self.backups)): (
                path.stat().st_mode,
                hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else "",
            )
            for path in sorted(self.backups.rglob("*"))
        }

    def run_script(self, **extra_env: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [str(self.script)],
            check=False,
            capture_output=True,
            text=True,
            timeout=20,
            env={
                **os.environ,
                "PATH": f"{self.bin}:{os.environ['PATH']}",
                "OFFSITE_BACKUP_ENV": str(self.env_file),
                "BACKUP_ROOT": str(self.backups),
                "OFFSITE_WORK_DIR": str(self.work),
                "OFFSITE_LOCK_FILE": str(self.lock_file),
                "FAKE_R2": str(self.remote),
                "FAKE_RCLONE_LOG": str(self.rclone_log),
                **extra_env,
            },
        )

    def remote_names(self) -> list[str]:
        directory = self.remote / BUCKET / "zulip"
        return sorted(path.name for path in directory.iterdir()) if directory.exists() else []

    def seed_remote(self, names: list[str]) -> None:
        directory = self.remote / BUCKET / "zulip"
        directory.mkdir(parents=True, exist_ok=True)
        for name in names:
            (directory / f"{name}.tar.age").write_bytes(b"old")
            (directory / f"{name}.tar.age.sha256").write_text("old")

    def assert_failed_cleanly(
        self,
        result: subprocess.CompletedProcess[str],
        message: str,
        before: dict[str, tuple[int, str]],
    ) -> None:
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn(f"offsite-backup: {message}", result.stderr)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(list(self.work.iterdir()), [], "temporary encrypted copy was left behind")

    def test_encrypts_and_uploads_the_newest_complete_backup(self) -> None:
        self.make_backup("20261018T211500Z-zulip.AAAAAAAA")
        newest = self.make_backup("20261019T211500Z-zulip.BBBBBBBB")
        self.make_backup("20261020T211500Z-zulip.CCCCCCCC", complete=False)
        self.make_backup("20260921T114027Z-buzz-retired")
        before = self.snapshot()

        result = self.run_script()

        self.assertEqual(result.returncode, 0, result.stderr)
        name = f"{newest.name}.tar.age"
        self.assertEqual(self.remote_names(), [name, f"{name}.sha256"])
        uploaded = (self.remote / BUCKET / "zulip" / name).read_bytes()
        header, _, body = uploaded.partition(b"\n")
        self.assertEqual(header, f"AGE-FOR {RECIPIENT}".encode())
        self.assertNotIn(b"database of", uploaded)
        with tarfile.open(fileobj=io.BytesIO(base64.b64decode(body))) as archive:
            member = archive.extractfile(f"{newest.name}/zulip.tar.gz")
            assert member is not None
            self.assertEqual(member.read(), b"database of 20261019T211500Z-zulip.BBBBBBBB")
        sidecar = (self.remote / BUCKET / "zulip" / f"{name}.sha256").read_text()
        self.assertEqual(sidecar.split()[0], hashlib.sha256(uploaded).hexdigest())
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(list(self.work.iterdir()), [])
        calls = [line for line in self.rclone_log.read_text().splitlines() if "copyto" in line]
        self.assertIn('"provider": "Cloudflare"', calls[0])
        self.assertIn("account.r2.cloudflarestorage.com", calls[0])

    def test_keeps_only_the_newest_fourteen_copies(self) -> None:
        self.make_backup("20261019T211500Z-zulip.BBBBBBBB")
        self.seed_remote([f"202610{day:02d}T211500Z-zulip.OLD{day:02d}" for day in range(1, 17)])

        result = self.run_script()

        self.assertEqual(result.returncode, 0, result.stderr)
        archives = [name for name in self.remote_names() if name.endswith(".tar.age")]
        self.assertEqual(len(archives), 14)
        self.assertIn("20261019T211500Z-zulip.BBBBBBBB.tar.age", archives)
        self.assertNotIn("20261001T211500Z-zulip.OLD01.tar.age", archives)
        self.assertNotIn("20261003T211500Z-zulip.OLD03.tar.age", archives)
        self.assertIn("20261004T211500Z-zulip.OLD04.tar.age", archives)
        self.assertEqual(len(self.remote_names()), 28, "each kept copy keeps its checksum file")

    def test_a_failed_upload_prunes_nothing(self) -> None:
        self.make_backup("20261019T211500Z-zulip.BBBBBBBB")
        self.seed_remote([f"202610{day:02d}T211500Z-zulip.OLD{day:02d}" for day in range(1, 17)])
        before = self.snapshot()

        result = self.run_script(FAKE_UPLOAD_FAIL="1")

        self.assert_failed_cleanly(
            result, "upload of 20261019T211500Z-zulip.BBBBBBBB.tar.age failed", before
        )
        self.assertEqual(len(self.remote_names()), 32)

    def test_a_size_mismatch_fails_and_prunes_nothing(self) -> None:
        self.make_backup("20261019T211500Z-zulip.BBBBBBBB")
        self.seed_remote([f"202610{day:02d}T211500Z-zulip.OLD{day:02d}" for day in range(1, 17)])
        before = self.snapshot()

        result = self.run_script(FAKE_TRUNCATE="1")

        self.assert_failed_cleanly(
            result, "size mismatch for 20261019T211500Z-zulip.BBBBBBBB.tar.age", before
        )
        self.assertEqual(len(self.remote_names()), 34)

    def test_a_checksum_mismatch_fails(self) -> None:
        self.make_backup("20261019T211500Z-zulip.BBBBBBBB")
        before = self.snapshot()

        result = self.run_script(FAKE_BAD_MD5="1")

        self.assert_failed_cleanly(result, "checksum mismatch for", before)

    def test_size_alone_is_enough_when_r2_reports_no_md5(self) -> None:
        self.make_backup("20261019T211500Z-zulip.BBBBBBBB")

        result = self.run_script(FAKE_NO_MD5="1")

        self.assertEqual(result.returncode, 0, result.stderr)

    def test_missing_config_file_is_a_clear_error(self) -> None:
        self.make_backup("20261019T211500Z-zulip.BBBBBBBB")
        self.env_file.unlink()
        before = self.snapshot()

        result = self.run_script()

        self.assert_failed_cleanly(result, f"{self.env_file} is missing", before)
        self.assertEqual(self.remote_names(), [])

    def test_a_config_file_readable_by_others_is_refused(self) -> None:
        self.make_backup("20261019T211500Z-zulip.BBBBBBBB")
        self.env_file.chmod(0o644)
        before = self.snapshot()

        result = self.run_script()

        self.assert_failed_cleanly(result, f"{self.env_file} must have mode 0600", before)

    def test_every_setting_is_required(self) -> None:
        self.make_backup("20261019T211500Z-zulip.BBBBBBBB")
        for name in (
            "R2_ENDPOINT",
            "R2_BUCKET",
            "R2_ACCESS_KEY_ID",
            "R2_SECRET_ACCESS_KEY",
            "AGE_RECIPIENT",
        ):
            with self.subTest(name=name):
                self.write_env(**{name: ""})
                before = self.snapshot()

                result = self.run_script()

                self.assert_failed_cleanly(result, f"{name} is not set in {self.env_file}", before)

    def test_a_missing_tool_is_named(self) -> None:
        self.make_backup("20261019T211500Z-zulip.BBBBBBBB")
        (self.bin / "rclone").unlink()
        before = self.snapshot()

        result = self.run_script()

        self.assert_failed_cleanly(result, "required tool 'rclone' is not installed", before)

    def test_an_invalid_recipient_fails_without_uploading(self) -> None:
        self.make_backup("20261019T211500Z-zulip.BBBBBBBB")
        self.write_env(AGE_RECIPIENT="bad")
        before = self.snapshot()

        result = self.run_script()

        self.assert_failed_cleanly(result, "tar or age failed while encrypting", before)
        self.assertEqual(self.remote_names(), [])

    def test_no_complete_backup_is_an_error(self) -> None:
        self.make_backup("20261019T211500Z-zulip.BBBBBBBB", complete=False)
        before = self.snapshot()

        result = self.run_script()

        self.assert_failed_cleanly(result, f"no complete backup found in {self.backups}", before)

    def test_a_corrupt_local_backup_is_not_uploaded(self) -> None:
        backup = self.make_backup("20261019T211500Z-zulip.BBBBBBBB")
        (backup / "zulip.tar.gz").write_bytes(b"tampered")
        before = self.snapshot()

        result = self.run_script()

        self.assert_failed_cleanly(
            result,
            "local backup "
            + str(self.backups / "20261019T211500Z-zulip.BBBBBBBB")
            + " fails its own checksums",
            before,
        )
        self.assertEqual(self.remote_names(), [])

    def test_a_second_run_at_the_same_time_is_refused(self) -> None:
        self.make_backup("20261019T211500Z-zulip.BBBBBBBB")
        with self.lock_file.open("w") as held:
            fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)

            result = self.run_script()

        self.assertEqual(result.returncode, 1)
        self.assertIn("another offsite backup is already running", result.stderr)


def read_unit(name: str) -> configparser.ConfigParser:
    parser = configparser.ConfigParser(interpolation=None, strict=False)
    parser.optionxform = str  # type: ignore[assignment,method-assign]  # unit keys are case-sensitive
    parser.read(DEPLOY / "systemd" / name)
    return parser


class OffsiteBackupFilesTest(unittest.TestCase):
    def test_the_timer_runs_after_the_daily_backup_at_low_priority(self) -> None:
        service = read_unit("grow-team-offsite-backup.service")
        timer = read_unit("grow-team-offsite-backup.timer")
        backup_timer = read_unit("grow-team-backup.timer")

        self.assertEqual(service["Service"]["ExecStart"], "/opt/grow-team/deploy/offsite-backup.sh")
        self.assertIn("grow-team-backup.service", service["Unit"]["After"])
        self.assertEqual(service["Service"]["Slice"], "grow-team.slice")
        self.assertEqual(service["Service"]["IOSchedulingClass"], "idle")
        self.assertEqual(timer["Timer"]["Unit"], "grow-team-offsite-backup.service")
        offsite = timer["Timer"]["OnCalendar"]
        daily = backup_timer["Timer"]["OnCalendar"]
        self.assertTrue(offsite.endswith("UTC") and daily.endswith("UTC"))
        self.assertGreater(offsite.split()[1], daily.split()[1])

    def test_the_example_config_holds_no_values(self) -> None:
        lines = (DEPLOY / "offsite-backup.env.example").read_text().splitlines()
        settings = dict(
            line.split("=", 1) for line in lines if "=" in line and not line.startswith("#")
        )

        self.assertEqual(
            set(settings),
            {
                "R2_ENDPOINT",
                "R2_BUCKET",
                "R2_ACCESS_KEY_ID",
                "R2_SECRET_ACCESS_KEY",
                "AGE_RECIPIENT",
            },
        )
        self.assertEqual({value for value in settings.values() if value}, set())


if __name__ == "__main__":
    unittest.main()
