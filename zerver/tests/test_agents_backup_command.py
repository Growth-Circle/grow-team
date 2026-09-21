import base64
import contextlib
import hashlib
import io
import json
import os
import subprocess
import tarfile
import tempfile
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest import mock

from django.test import SimpleTestCase, override_settings
from typing_extensions import override

from zerver.lib.agent_backup import AgentBackupError, verify_agent_backup
from zerver.management.commands import backup
from zerver.models import agents


class AgentBackupCommandTest(SimpleTestCase):
    @override
    def setUp(self) -> None:
        super().setUp()
        self.temporary = tempfile.TemporaryDirectory(prefix="grow-team-backup-command-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        (self.root / "zproject").mkdir()
        (self.root / "zproject/dev-secrets.conf").write_text("fixture-configuration")
        self.artifacts = self.root / "artifacts"
        self.artifacts.mkdir(mode=0o700)
        (self.artifacts / "result.data").write_bytes(b"fixture-diff")
        (self.artifacts / "result.data").chmod(0o600)
        self.keyring = self.root / "keyring.json"
        self.keyring.write_text(
            json.dumps(
                {
                    "current": "v2",
                    "keys": {
                        "v1": base64.b64encode(b"a" * 32).decode(),
                        "v2": base64.b64encode(b"b" * 32).decode(),
                    },
                }
            )
        )
        self.keyring.chmod(0o600)
        self.output = self.root / "backup.tar.gz"
        self.calls: list[str] = []

    def run_command(
        self,
        *,
        configured: bool = True,
        required_artifacts: bool = True,
        required_keyring: bool = True,
        skip_db: bool = False,
        tar_action: Callable[[], None] | None = None,
    ) -> str:
        def run(command: list[str], *, cwd: str | None = None, **kwargs: Any) -> None:
            self.calls.append(command[0])
            if command[0].endswith("/pg_dump"):
                directory = Path(
                    next(arg.split("=", 1)[1] for arg in command if arg.startswith("--file="))
                )
                directory.mkdir()
                (directory / "toc.dat").write_bytes(b"fixture-database-dump")
                # Files committed before the dump completes must enter the archive.
                (self.artifacts / "after-dump.data").write_bytes(b"fixture-late-artifact")
                (self.artifacts / "after-dump.data").chmod(0o600)
            else:
                if tar_action is not None:
                    tar_action()
                subprocess.run(command, cwd=cwd, check=True, timeout=10, **kwargs)

        stream = io.StringIO()
        with (
            override_settings(
                DEVELOPMENT=True,
                DEPLOY_ROOT=str(self.root),
                LOCAL_UPLOADS_DIR=None,
                AGENT_ARTIFACT_ROOT=str(self.artifacts) if configured else None,
                AGENT_SECRET_MASTER_KEY_FILE=str(self.keyring) if configured else "",
            ),
            mock.patch.object(backup, "run", side_effect=run),
            mock.patch.object(backup, "try_git_describe", return_value="fixture-source"),
            mock.patch.object(backup, "connection") as database,
            mock.patch.object(agents.AgentArtifact.objects, "filter") as artifacts,
            mock.patch.object(agents.AgentSecret.objects, "values_list") as key_ids,
            mock.patch.dict(os.environ),
            contextlib.redirect_stdout(stream),
        ):
            database.cursor.return_value.connection.server_version = 140017
            artifacts.return_value.values_list.return_value = (
                [("result.data", len(b"fixture-diff"), hashlib.sha256(b"fixture-diff").hexdigest())]
                if required_artifacts
                else []
            )
            key_ids.return_value = ["v1", "v2"] if required_keyring else []
            backup.Command().handle(output=str(self.output), skip_db=skip_db, skip_uploads=True)
        return stream.getvalue()

    def extract(self) -> Path:
        destination = self.root / "restored"
        previous_umask = os.umask(0o077)
        try:
            with tarfile.open(self.output) as archive:
                archive.extractall(destination, filter="data")
        finally:
            os.umask(previous_umask)
        return destination / "zulip-backup"

    def test_database_archive_contains_artifacts_and_every_key_version(self) -> None:
        output = self.run_command()
        restored = self.extract()
        self.assertEqual((restored / "database/toc.dat").read_bytes(), b"fixture-database-dump")
        manifest = verify_agent_backup(restored / "agent")
        self.assertTrue(manifest["artifacts"])
        self.assertTrue(manifest["keyring"])
        self.assertEqual((restored / "agent/artifacts/result.data").read_bytes(), b"fixture-diff")
        self.assertEqual(
            (restored / "agent/artifacts/after-dump.data").read_bytes(), b"fixture-late-artifact"
        )
        self.assertEqual((restored / "agent/keyring.json").read_bytes(), self.keyring.read_bytes())
        self.assertNotIn(base64.b64encode(b"a" * 32).decode(), output)
        self.assertTrue(self.calls[0].endswith("/pg_dump"))
        self.assertEqual(self.calls[-1], "tar")

    def test_required_sources_cannot_be_omitted(self) -> None:
        for artifacts, keyring in ((True, False), (False, True)):
            with self.subTest(artifacts=artifacts), self.assertRaises(AgentBackupError):
                self.run_command(
                    configured=False,
                    required_artifacts=artifacts,
                    required_keyring=keyring,
                )
            self.assertFalse(self.output.exists())

    def test_missing_configured_source_fails_before_archive_success(self) -> None:
        self.keyring.rename(self.root / "keyring.saved")
        with self.assertRaises(AgentBackupError):
            self.run_command(required_artifacts=False, required_keyring=False)
        self.assertFalse(self.output.exists())

    def test_skip_database_and_uploads_keeps_private_agent_files(self) -> None:
        self.run_command(skip_db=True)
        restored = self.extract()
        self.assertFalse((restored / "database").exists())
        self.assertTrue(verify_agent_backup(restored / "agent")["keyring"])

    def test_unconfigured_empty_feature_preserves_legacy_archive(self) -> None:
        self.run_command(configured=False, required_artifacts=False, required_keyring=False)
        restored = self.extract()
        self.assertTrue((restored / "database/toc.dat").is_file())
        self.assertFalse((restored / "agent").exists())

    def test_explicit_output_stays_private_with_permissive_umask(self) -> None:
        previous_umask = os.umask(0o022)
        try:
            self.run_command()
        finally:
            os.umask(previous_umask)
        self.assertEqual(self.output.stat().st_mode & 0o777, 0o600)

    def test_existing_output_is_not_overwritten(self) -> None:
        self.output.write_bytes(b"irreplaceable-earlier-backup")
        with self.assertRaises(FileExistsError):
            self.run_command()
        self.assertEqual(self.output.read_bytes(), b"irreplaceable-earlier-backup")

    def test_output_created_during_archive_is_not_overwritten(self) -> None:
        def create_destination() -> None:
            self.output.write_bytes(b"concurrent-backup")

        with self.assertRaises(FileExistsError):
            self.run_command(tar_action=create_destination)
        self.assertEqual(self.output.read_bytes(), b"concurrent-backup")
        self.assertEqual(list(self.root.glob(".backup.tar.gz.*.partial")), [])

    def test_failed_archive_removes_only_its_private_staging_file(self) -> None:
        def fail_archive() -> None:
            raise subprocess.CalledProcessError(1, ["tar"])

        with self.assertRaises(subprocess.CalledProcessError):
            self.run_command(tar_action=fail_archive)
        self.assertFalse(self.output.exists())
        self.assertEqual(list(self.root.glob(".backup.tar.gz.*.partial")), [])

    def test_replaced_output_staging_cannot_redirect_tar(self) -> None:
        victim = self.root / "victim"
        victim.write_bytes(b"unchanged-victim")
        moved_staging = self.root / "moved-staging"

        def replace_staging() -> None:
            [staging] = self.root.glob(".backup.tar.gz.*.partial")
            staging.rename(moved_staging)
            staging.symlink_to(victim)

        self.run_command(tar_action=replace_staging)

        self.assertEqual(victim.read_bytes(), b"unchanged-victim")
        self.assertTrue(self.output.is_file())
        self.assertTrue(moved_staging.is_dir())
        self.assertTrue(next(self.root.glob(".backup.tar.gz.*.partial")).is_symlink())

    def test_absent_output_staging_does_not_mask_a_published_archive(self) -> None:
        moved_staging = self.root / "moved-staging"

        def remove_staging_name() -> None:
            [staging] = self.root.glob(".backup.tar.gz.*.partial")
            staging.rename(moved_staging)

        self.run_command(tar_action=remove_staging_name)

        self.assertTrue(self.output.is_file())
        self.assertTrue(moved_staging.is_dir())

    def test_unsafe_output_staging_mode_is_rejected(self) -> None:
        original_mkdir = os.mkdir

        def widen_staging(
            path: str | Path, mode: int = 0o777, *, dir_fd: int | None = None
        ) -> None:
            original_mkdir(path, mode, dir_fd=dir_fd)
            if os.fspath(path).startswith(".backup.tar.gz."):
                os.chmod(path, 0o777, dir_fd=dir_fd)  # noqa: S103

        with (
            mock.patch.object(os, "mkdir", side_effect=widen_staging),
            self.assertRaises(PermissionError),
        ):
            self.run_command()
        self.assertFalse(self.output.exists())

    def test_wrong_owner_output_staging_is_rejected(self) -> None:
        original_fstat = os.fstat

        def report_wrong_owner(fd: int) -> SimpleNamespace:
            info = original_fstat(fd)
            return SimpleNamespace(
                st_dev=info.st_dev,
                st_ino=info.st_ino,
                st_mode=info.st_mode,
                st_uid=info.st_uid + 1,
            )

        staging = None
        with mock.patch.object(os, "fstat", side_effect=report_wrong_owner):
            try:
                staging = backup._make_private_output_staging(str(self.output))
            except PermissionError:
                pass
            else:
                backup._remove_owned_output_staging(staging)
                self.fail("Wrong-owner staging directory was accepted.")
        self.assertFalse(self.output.exists())

    def test_output_staging_cleanup_is_idempotent(self) -> None:
        staging = backup._make_private_output_staging(str(self.output))

        backup._remove_owned_output_staging(staging)
        backup._remove_owned_output_staging(staging)

        self.assertEqual(list(self.root.glob(".backup.tar.gz.*.partial")), [])

    def test_missing_referenced_artifact_cannot_make_a_complete_backup(self) -> None:
        (self.artifacts / "result.data").rename(self.root / "result.saved")
        with self.assertRaises(AgentBackupError):
            self.run_command()
        self.assertFalse(self.output.exists())

    def test_changed_referenced_artifact_cannot_make_a_complete_backup(self) -> None:
        (self.artifacts / "result.data").write_bytes(b"different-result")
        with self.assertRaises(AgentBackupError):
            self.run_command()
        self.assertFalse(self.output.exists())

    def test_missing_retained_key_cannot_make_a_complete_backup(self) -> None:
        payload = json.loads(self.keyring.read_text())
        del payload["keys"]["v1"]
        self.keyring.write_text(json.dumps(payload))
        with self.assertRaises(AgentBackupError):
            self.run_command()
        self.assertFalse(self.output.exists())

    def test_invalid_keyring_cannot_make_a_complete_backup(self) -> None:
        self.keyring.write_text('{"current":"v2","keys":{"v1":"bad","v2":"bad"}}')
        with self.assertRaises(AgentBackupError):
            self.run_command()
        self.assertFalse(self.output.exists())
