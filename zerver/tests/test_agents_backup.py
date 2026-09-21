import hashlib
import json
import os
import stat
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from typing_extensions import override

from zerver.lib.agent_backup import AgentBackupError, stage_agent_backup, verify_agent_backup


class AgentBackupTest(unittest.TestCase):
    @override
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="grow-team-backup-test-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.artifacts = self.root / "private-artifacts"
        self.artifacts.mkdir()
        (self.artifacts / "job").mkdir()
        (self.artifacts / "job" / "diff.patch").write_bytes(b"synthetic diff\n")
        self.keyring = self.root / "keys.json"
        self.keyring.write_text(
            json.dumps({"current": "v2", "keys": {"v1": "synthetic-old", "v2": "synthetic-new"}})
        )
        self.staging = self.root / "backup"
        self.staging.mkdir()

    def stage(self) -> Path:
        result = stage_agent_backup(
            self.staging,
            artifact_root=self.artifacts,
            keyring_file=self.keyring,
            require_artifacts=True,
            require_keyring=True,
        )
        assert result is not None
        return result

    def test_archive_restore_preserves_artifacts_and_every_key_version(self) -> None:
        staged = self.stage()
        archive_path = self.root / "backup.tar.gz"
        with tarfile.open(archive_path, "x:gz") as archive:
            archive.add(staged, arcname="agent")
        restored = self.root / "restored"
        previous_umask = os.umask(0o077)
        try:
            with tarfile.open(archive_path) as archive:
                archive.extractall(restored, filter="data")
        finally:
            os.umask(previous_umask)
        manifest = verify_agent_backup(restored / "agent")
        self.assertEqual((restored / "agent/keyring.json").read_bytes(), self.keyring.read_bytes())
        self.assertEqual(
            (restored / "agent/artifacts/job/diff.patch").read_bytes(), b"synthetic diff\n"
        )
        self.assertEqual(len(manifest["files"]), 2)
        manifest_text = (staged / "manifest.json").read_text()
        self.assertNotIn("synthetic-old", manifest_text)
        self.assertNotIn("synthetic-new", manifest_text)
        self.assertNotIn(str(self.root), manifest_text)

    def test_staged_files_and_directories_are_private(self) -> None:
        staged = self.stage()
        for path in [staged, *staged.rglob("*")]:
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o700 if path.is_dir() else 0o600)

    def test_unconfigured_empty_feature_does_not_add_an_archive(self) -> None:
        self.assertIsNone(stage_agent_backup(self.staging, artifact_root=None, keyring_file=None))
        self.assertEqual(list(self.staging.iterdir()), [])

    def test_required_configuration_cannot_be_silently_omitted(self) -> None:
        for required in ("require_artifacts", "require_keyring"):
            with self.subTest(required=required), self.assertRaises(AgentBackupError):
                stage_agent_backup(
                    self.staging, artifact_root=None, keyring_file=None, **{required: True}
                )

    def test_missing_configured_source_fails(self) -> None:
        with self.assertRaises(AgentBackupError):
            stage_agent_backup(self.staging, artifact_root=self.root / "absent", keyring_file=None)

    def test_symlink_outside_artifacts_is_rejected(self) -> None:
        (self.artifacts / "escape").symlink_to(self.keyring)
        with self.assertRaises(AgentBackupError):
            self.stage()
        self.assertFalse((self.staging / "agent/manifest.json").exists())

    def test_directory_symlink_is_rejected(self) -> None:
        (self.artifacts / "outside").symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(AgentBackupError):
            self.stage()

    def test_hard_link_is_rejected(self) -> None:
        os.link(self.keyring, self.artifacts / "hard-link")
        with self.assertRaises(AgentBackupError):
            self.stage()

    def test_fifo_is_rejected_without_reading(self) -> None:
        os.mkfifo(self.artifacts / "pipe")
        with self.assertRaises(AgentBackupError):
            self.stage()

    def test_existing_stage_is_not_overwritten(self) -> None:
        staged = self.stage()
        before = (staged / "manifest.json").read_bytes()
        with self.assertRaises(AgentBackupError):
            self.stage()
        self.assertEqual((staged / "manifest.json").read_bytes(), before)

    def test_restore_detects_corrupt_artifact(self) -> None:
        staged = self.stage()
        (staged / "artifacts/job/diff.patch").write_bytes(b"corruption")
        with self.assertRaises(AgentBackupError):
            verify_agent_backup(staged)

    def test_restore_rejects_path_escape_even_with_matching_checksum(self) -> None:
        staged = self.stage()
        manifest = json.loads((staged / "manifest.json").read_text())
        content = self.keyring.read_bytes()
        manifest["files"].append(
            {
                "path": "../../keys.json",
                "size": len(content),
                "sha256": hashlib.sha256(content).hexdigest(),
            }
        )
        (staged / "manifest.json").write_text(json.dumps(manifest))
        with self.assertRaises(AgentBackupError):
            verify_agent_backup(staged)

    def test_restore_rejects_missing_keyring(self) -> None:
        staged = self.stage()
        (staged / "keyring.json").rename(staged / "keyring.moved")
        with self.assertRaises(AgentBackupError):
            verify_agent_backup(staged)

    def test_keyring_only_backup_keeps_retained_versions(self) -> None:
        staged = stage_agent_backup(self.staging, artifact_root=None, keyring_file=self.keyring)
        assert staged is not None
        manifest = verify_agent_backup(staged)
        self.assertFalse(manifest["artifacts"])
        self.assertTrue(manifest["keyring"])
        self.assertEqual((staged / "keyring.json").read_bytes(), self.keyring.read_bytes())

    def test_empty_artifact_directory_is_preserved(self) -> None:
        empty = self.root / "empty"
        empty.mkdir()
        staged = stage_agent_backup(self.staging, artifact_root=empty, keyring_file=None)
        assert staged is not None
        manifest = verify_agent_backup(staged)
        self.assertTrue(manifest["artifacts"])
        self.assertEqual(manifest["files"], [])

    def test_staging_inside_artifacts_is_rejected(self) -> None:
        with self.assertRaises(AgentBackupError):
            stage_agent_backup(self.artifacts, artifact_root=self.artifacts, keyring_file=None)

    def test_keyring_inside_artifacts_is_rejected(self) -> None:
        with self.assertRaises(AgentBackupError):
            stage_agent_backup(
                self.staging, artifact_root=self.artifacts, keyring_file=self.artifacts / "keys"
            )

    def test_changed_file_during_copy_leaves_no_success_manifest(self) -> None:
        original_write = os.write
        changed = False

        def change_source(fd: int, data: bytes) -> int:
            nonlocal changed
            if not changed:
                with (self.artifacts / "job/diff.patch").open("ab") as source:
                    source.write(b"changed during copy")
                changed = True
            return original_write(fd, data)

        with (
            mock.patch("zerver.lib.agent_backup.os.write", side_effect=change_source),
            self.assertRaises(AgentBackupError),
        ):
            self.stage()
        self.assertFalse((self.staging / "agent/manifest.json").exists())

    def test_restore_rejects_unlisted_file(self) -> None:
        staged = self.stage()
        (staged / "artifacts/unlisted").write_text("unexpected")
        with self.assertRaises(AgentBackupError):
            verify_agent_backup(staged)

    def test_restore_rejects_symlink_even_when_content_matches(self) -> None:
        staged = self.stage()
        (staged / "artifacts/job/diff.patch").rename(staged / "artifacts/job/moved.patch")
        (staged / "artifacts/job/diff.patch").symlink_to(self.artifacts / "job/diff.patch")
        with self.assertRaises(AgentBackupError):
            verify_agent_backup(staged)

    def test_artifact_ancestor_symlink_is_rejected(self) -> None:
        alias = self.root / "alias"
        alias.symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(AgentBackupError):
            stage_agent_backup(
                self.staging, artifact_root=alias / self.artifacts.name, keyring_file=None
            )

    def test_keyring_ancestor_symlink_is_rejected(self) -> None:
        alias = self.root / "alias"
        alias.symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(AgentBackupError):
            stage_agent_backup(
                self.staging, artifact_root=None, keyring_file=alias / self.keyring.name
            )

    def test_restore_ancestor_symlink_is_rejected(self) -> None:
        staged = self.stage()
        alias = self.root / "alias"
        alias.symlink_to(self.staging, target_is_directory=True)
        with self.assertRaises(AgentBackupError):
            verify_agent_backup(alias / staged.name)

    def test_restore_rejects_widened_file_or_directory_permissions(self) -> None:
        staged = self.stage()
        for path, mode in (
            (staged, 0o755),
            (staged / "keyring.json", 0o640),
            (staged / "manifest.json", 0o644),
            (staged / "artifacts/job", 0o750),
            (staged / "artifacts/job/diff.patch", 0o644),
        ):
            with self.subTest(path=path.name):
                original = stat.S_IMODE(path.stat().st_mode)
                path.chmod(mode)
                try:
                    with self.assertRaises(AgentBackupError):
                        verify_agent_backup(staged)
                finally:
                    path.chmod(original)
