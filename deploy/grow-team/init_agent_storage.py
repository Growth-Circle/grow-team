#!/usr/bin/env python3
"""Initialize private agent artifact and key storage."""

import argparse
import base64
import fcntl
import json
import os
import re
import stat
import sys
import time
from pathlib import Path
from typing import Any

DEFAULT_ROOT = Path("/data/grow-team-agent-private")
KEYRING_NAME = "keyring.json"
LOCK_NAME = ".init.lock"
ARTIFACTS_NAME = "artifacts"
KEY_BYTES = 32
MAX_KEYRING_BYTES = 64 * 1024
MAX_KEYS = 128
LOCK_TIMEOUT_SECONDS = 1.0
LOCK_RETRY_SECONDS = 0.05
KEY_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")


class StorageError(Exception):
    pass


def _mode(st: os.stat_result) -> int:
    return stat.S_IMODE(st.st_mode)


def _validate_directory(st: os.stat_result, *, require_owner: bool) -> None:
    if not stat.S_ISDIR(st.st_mode):
        raise StorageError
    if require_owner and st.st_uid != os.geteuid():
        raise StorageError
    if _mode(st) != 0o700 or st.st_mode & 0o7000:
        raise StorageError


def _validate_file(st: os.stat_result) -> None:
    if not stat.S_ISREG(st.st_mode):
        raise StorageError
    if st.st_uid != os.geteuid() or st.st_nlink != 1:
        raise StorageError
    if _mode(st) != 0o600 or st.st_mode & 0o7000:
        raise StorageError


def _open_root(root: Path) -> int:
    root_text = os.path.abspath(os.fspath(root))
    if not os.path.isabs(root_text):
        raise StorageError
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    fd = os.open("/", flags)
    try:
        for component in Path(root_text).parts[1:]:
            next_fd = os.open(component, flags, dir_fd=fd)
            os.close(fd)
            fd = next_fd
        _validate_directory(os.fstat(fd), require_owner=True)
        return fd
    except Exception:
        os.close(fd)
        raise


def _open_directory(name: str, root_fd: int) -> int:
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    fd = os.open(name, flags, dir_fd=root_fd)
    try:
        _validate_directory(os.fstat(fd), require_owner=True)
        return fd
    except Exception:
        os.close(fd)
        raise


def _open_file(name: str, root_fd: int) -> int:
    flags = os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW | os.O_CLOEXEC
    fd = os.open(name, flags, dir_fd=root_fd)
    try:
        _validate_file(os.fstat(fd))
        return fd
    except Exception:
        os.close(fd)
        raise


def _read_file(fd: int) -> bytes:
    chunks: list[bytes] = []
    remaining = MAX_KEYRING_BYTES + 1
    while remaining:
        chunk = os.read(fd, min(8192, remaining))
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    content = b"".join(chunks)
    if len(content) > MAX_KEYRING_BYTES:
        raise StorageError
    return content


def _no_duplicate_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError
        result[key] = value
    return result


def _validate_keyring(content: bytes) -> None:
    try:
        payload = json.loads(content.decode("utf-8"), object_pairs_hook=_no_duplicate_object)
        if not isinstance(payload, dict) or set(payload) != {"current", "keys"}:
            raise ValueError
        current = payload["current"]
        keys = payload["keys"]
        if not isinstance(current, str) or not KEY_ID_RE.fullmatch(current):
            raise ValueError
        if not isinstance(keys, dict) or not 1 <= len(keys) <= MAX_KEYS or current not in keys:
            raise ValueError
        for key_id, encoded_key in keys.items():
            if not isinstance(key_id, str) or not KEY_ID_RE.fullmatch(key_id):
                raise ValueError
            if not isinstance(encoded_key, str):
                raise ValueError
            decoded_key = base64.b64decode(encoded_key, validate=True)
            if len(decoded_key) != KEY_BYTES:
                raise ValueError
            if base64.b64encode(decoded_key).decode("ascii") != encoded_key:
                raise ValueError
    except (UnicodeDecodeError, TypeError, ValueError, json.JSONDecodeError):
        raise StorageError from None


def _write_all(fd: int, content: bytes) -> None:
    view = memoryview(content)
    while view:
        written = os.write(fd, view)
        if written <= 0:
            raise OSError("incomplete write")
        view = view[written:]


def _create_keyring(root_fd: int) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC
    fd = os.open(KEYRING_NAME, flags, 0o600, dir_fd=root_fd)
    try:
        os.fchmod(fd, 0o600)
        payload = {
            "current": "v1",
            "keys": {"v1": base64.b64encode(os.urandom(KEY_BYTES)).decode("ascii")},
        }
        content = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("ascii") + b"\n"
        _write_all(fd, content)
        os.fsync(fd)
    finally:
        os.close(fd)
    os.fsync(root_fd)


def _lock(root_fd: int) -> int:
    create_flags = os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC
    try:
        fd = os.open(LOCK_NAME, create_flags, 0o600, dir_fd=root_fd)
        created = True
    except FileExistsError:
        fd = os.open(LOCK_NAME, os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=root_fd)
        created = False
    try:
        if created:
            os.fchmod(fd, 0o600)
            os.fsync(fd)
            os.fsync(root_fd)
        _validate_file(os.fstat(fd))
        deadline = time.monotonic() + LOCK_TIMEOUT_SECONDS
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise StorageError from None
                time.sleep(LOCK_RETRY_SECONDS)
        return fd
    except Exception:
        os.close(fd)
        raise


def initialize(root: Path) -> bool:
    root_fd = _open_root(root)
    lock_fd = -1
    artifacts_fd = -1
    try:
        lock_fd = _lock(root_fd)
        created = False
        try:
            os.mkdir(ARTIFACTS_NAME, 0o700, dir_fd=root_fd)
            os.fsync(root_fd)
            created = True
        except FileExistsError:
            pass
        artifacts_fd = _open_directory(ARTIFACTS_NAME, root_fd)
        try:
            keyring_fd = _open_file(KEYRING_NAME, root_fd)
        except FileNotFoundError:
            _create_keyring(root_fd)
            created = True
        else:
            try:
                _validate_keyring(_read_file(keyring_fd))
            finally:
                os.close(keyring_fd)
        return created
    finally:
        if artifacts_fd != -1:
            os.close(artifacts_fd)
        if lock_fd != -1:
            os.close(lock_fd)
        os.close(root_fd)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    args = parser.parse_args(argv)
    try:
        created = initialize(args.root)
    except Exception:
        print("Agent storage initialization failed.", file=sys.stderr)
        return 1
    print(f"created={int(created)} ready=1")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
