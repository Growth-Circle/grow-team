"""Stage and verify private agent files for database backup recovery."""

import hashlib
import json
import os
import re
import stat
from pathlib import Path, PurePosixPath
from typing import Literal, TypedDict


class AgentBackupError(ValueError):
    pass


class BackupFile(TypedDict):
    path: str
    size: int
    sha256: str


class AgentBackupManifest(TypedDict):
    schema_version: Literal[1]
    artifacts: bool
    keyring: bool
    files: list[BackupFile]


_DIRECTORY_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
_FILE_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK


def _open_path(path: Path, flags: int) -> int:
    parts = path.absolute().parts
    if ".." in parts:
        raise AgentBackupError("Agent backup paths cannot contain parent traversal.")
    parent_fd = os.open("/", _DIRECTORY_FLAGS)
    try:
        for component in parts[1:-1]:
            child_fd = os.open(component, _DIRECTORY_FLAGS, dir_fd=parent_fd)
            os.close(parent_fd)
            parent_fd = child_fd
        return os.open(parts[-1] if len(parts) > 1 else ".", flags, dir_fd=parent_fd)
    finally:
        os.close(parent_fd)


def _require_private(info: os.stat_result) -> None:
    if info.st_mode & 0o077:
        raise AgentBackupError("Agent backup files and directories must be private.")


def _file_record(
    fd: int, relative: str, destination: Path | None = None, *, private: bool = False
) -> BackupFile:
    with os.fdopen(fd, "rb") as source:
        before = os.fstat(source.fileno())
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
            raise AgentBackupError("Agent backup requires regular files without hard links.")
        if private:
            _require_private(before)
        target_fd = (
            os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            if destination is not None
            else None
        )
        digest = hashlib.sha256()
        size = 0
        try:
            while size < before.st_size:
                chunk = source.read(min(1024 * 1024, before.st_size - size))
                if not chunk:
                    break
                digest.update(chunk)
                size += len(chunk)
                if target_fd is not None:
                    pending = memoryview(chunk)
                    while pending:
                        count = os.write(target_fd, pending)
                        if count == 0:
                            raise AgentBackupError("Agent backup cannot write the staged file.")
                        pending = pending[count:]
            if target_fd is not None:
                os.fsync(target_fd)
        finally:
            if target_fd is not None:
                os.close(target_fd)
        after = os.fstat(source.fileno())
        if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        ) or size != before.st_size:
            raise AgentBackupError("An agent backup source changed during the copy.")
        return {"path": relative, "size": size, "sha256": digest.hexdigest()}


def _directory_records(
    source_fd: int, relative: str, destination: Path | None = None, *, private: bool = False
) -> list[BackupFile]:
    if private:
        _require_private(os.fstat(source_fd))
    records = []
    for name in sorted(os.listdir(source_fd)):
        info = os.stat(name, dir_fd=source_fd, follow_symlinks=False)
        target = destination / name if destination is not None else None
        child_relative = f"{relative}/{name}"
        if stat.S_ISDIR(info.st_mode):
            child_fd = os.open(name, _DIRECTORY_FLAGS, dir_fd=source_fd)
            try:
                if target is not None:
                    target.mkdir(mode=0o700)
                records.extend(
                    _directory_records(child_fd, child_relative, target, private=private)
                )
            finally:
                os.close(child_fd)
        else:
            records.append(
                _file_record(
                    os.open(name, _FILE_FLAGS, dir_fd=source_fd),
                    child_relative,
                    target,
                    private=private,
                )
            )
    return records


def stage_agent_backup(
    backup_root: Path,
    *,
    artifact_root: Path | None,
    keyring_file: Path | None,
    require_artifacts: bool = False,
    require_keyring: bool = False,
) -> Path | None:
    """Copy immutable files after the database dump and before retention resumes."""
    if require_artifacts and artifact_root is None:
        raise AgentBackupError("Agent artifacts require a configured backup source.")
    if require_keyring and keyring_file is None:
        raise AgentBackupError("Agent secrets require a configured keyring backup source.")
    if artifact_root is None and keyring_file is None:
        return None
    if artifact_root is not None and backup_root.absolute().is_relative_to(
        artifact_root.absolute()
    ):
        raise AgentBackupError("Agent backup staging must be outside the artifact directory.")
    if (
        artifact_root is not None
        and keyring_file is not None
        and keyring_file.absolute().is_relative_to(artifact_root.absolute())
    ):
        raise AgentBackupError("The agent keyring must be outside the artifact directory.")

    staged = backup_root / "agent"
    manifest: AgentBackupManifest = {
        "schema_version": 1,
        "artifacts": artifact_root is not None,
        "keyring": keyring_file is not None,
        "files": [],
    }
    try:
        backup_fd = _open_path(backup_root, _DIRECTORY_FLAGS)
        os.close(backup_fd)
        staged.mkdir(mode=0o700)
        if artifact_root is not None:
            source_fd = _open_path(artifact_root, _DIRECTORY_FLAGS)
            try:
                destination = staged / "artifacts"
                destination.mkdir(mode=0o700)
                manifest["files"].extend(_directory_records(source_fd, "artifacts", destination))
            finally:
                os.close(source_fd)
        if keyring_file is not None:
            manifest["files"].append(
                _file_record(
                    _open_path(keyring_file, _FILE_FLAGS), "keyring.json", staged / "keyring.json"
                )
            )
        manifest["files"].sort(key=lambda record: record["path"])
        fd = os.open(staged / "manifest.json", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as output:
            json.dump(manifest, output, sort_keys=True, indent=2)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        return staged
    except OSError:
        raise AgentBackupError("Agent backup cannot read or stage a configured source.") from None


def verify_agent_backup(staged: Path) -> AgentBackupManifest:
    """Verify the exact restored file set before application configuration changes."""
    root_fd: int | None = None
    try:
        root_fd = _open_path(staged, _DIRECTORY_FLAGS)
        _require_private(os.fstat(root_fd))
        fd = os.open("manifest.json", _FILE_FLAGS, dir_fd=root_fd)
        with os.fdopen(fd, "rb") as source:
            info = os.fstat(source.fileno())
            _require_private(info)
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_nlink != 1
                or info.st_size > 64 * 1024 * 1024
            ):
                raise AgentBackupError("The agent backup manifest is invalid.")
            content = source.read(64 * 1024 * 1024 + 1)
            if len(content) > 64 * 1024 * 1024:
                raise AgentBackupError("The agent backup manifest is invalid.")
            payload = json.loads(content)
        if (
            not isinstance(payload, dict)
            or set(payload) != {"schema_version", "artifacts", "keyring", "files"}
            or type(payload["schema_version"]) is not int
            or payload["schema_version"] != 1
            or type(payload["artifacts"]) is not bool
            or type(payload["keyring"]) is not bool
            or not isinstance(payload["files"], list)
        ):
            raise AgentBackupError("The agent backup manifest is invalid.")

        expected: list[BackupFile] = []
        paths: set[str] = set()
        for record in payload["files"]:
            if not isinstance(record, dict) or set(record) != {"path", "size", "sha256"}:
                raise AgentBackupError("An agent backup file record is invalid.")
            name, size, digest = record["path"], record["size"], record["sha256"]
            if (
                not isinstance(name, str)
                or PurePosixPath(name).as_posix() != name
                or PurePosixPath(name).is_absolute()
                or ".." in PurePosixPath(name).parts
                or name in paths
                or not (name == "keyring.json" or name.startswith("artifacts/"))
                or type(size) is not int
                or size < 0
                or not isinstance(digest, str)
                or re.fullmatch(r"[0-9a-f]{64}", digest) is None
            ):
                raise AgentBackupError("An agent backup file record is invalid.")
            paths.add(name)
            expected.append({"path": name, "size": size, "sha256": digest})

        root_names = {"manifest.json"}
        actual: list[BackupFile] = []
        if payload["artifacts"]:
            root_names.add("artifacts")
            artifacts_fd = os.open("artifacts", _DIRECTORY_FLAGS, dir_fd=root_fd)
            try:
                actual.extend(_directory_records(artifacts_fd, "artifacts", private=True))
            finally:
                os.close(artifacts_fd)
        if payload["keyring"]:
            root_names.add("keyring.json")
            actual.append(
                _file_record(
                    os.open("keyring.json", _FILE_FLAGS, dir_fd=root_fd),
                    "keyring.json",
                    private=True,
                )
            )
        if set(os.listdir(root_fd)) != root_names or sorted(
            actual, key=lambda x: x["path"]
        ) != sorted(expected, key=lambda x: x["path"]):
            raise AgentBackupError("The agent backup file set or checksum does not match.")
        return {
            "schema_version": 1,
            "artifacts": payload["artifacts"],
            "keyring": payload["keyring"],
            "files": actual,
        }
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise AgentBackupError("Agent backup cannot read a valid manifest and file set.") from None
    finally:
        if root_fd is not None:
            os.close(root_fd)
