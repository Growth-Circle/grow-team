import base64
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

SCRIPT = Path(__file__).parents[1] / "init_agent_storage.py"


class AgentStorageTest(unittest.TestCase):
    def make_root(self, directory: Path, name: str = "private") -> Path:
        root = directory / name
        root.mkdir()
        root.chmod(0o700)
        return root

    def run_script(self, root: Path, *, timeout: float = 2) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(SCRIPT), "--root", str(root)],
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )

    def test_creates_private_storage_with_one_key(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = self.make_root(Path(temporary_directory))

            result = self.run_script(root)

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, "created=1 ready=1\n")
            artifacts = root / "artifacts"
            keyring = root / "keyring.json"
            self.assertTrue(artifacts.is_dir())
            self.assertEqual(stat.S_IMODE(artifacts.stat().st_mode), 0o700)
            self.assertEqual(stat.S_IMODE(keyring.stat().st_mode), 0o600)
            payload = json.loads(keyring.read_text())
            self.assertEqual(payload["current"], "v1")
            self.assertEqual(set(payload["keys"]), {"v1"})
            self.assertEqual(len(base64.b64decode(payload["keys"]["v1"], validate=True)), 32)
            self.assertNotIn(payload["keys"]["v1"], result.stdout + result.stderr)

    def test_preserves_existing_versions_and_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = self.make_root(Path(temporary_directory))
            (root / "artifacts").mkdir(mode=0o700)
            keyring = root / "keyring.json"
            original = json.dumps(
                {
                    "current": "v2",
                    "keys": {
                        "v1": base64.b64encode(b"a" * 32).decode(),
                        "v2": base64.b64encode(b"b" * 32).decode(),
                    },
                },
                separators=(",", ":"),
            ).encode()
            keyring.write_bytes(original)
            keyring.chmod(0o600)

            result = self.run_script(root)

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, "created=0 ready=1\n")
            self.assertEqual(keyring.read_bytes(), original)
            self.assertNotIn(base64.b64encode(b"a" * 32).decode(), result.stdout + result.stderr)

    def test_refuses_absent_unsafe_or_malformed_storage(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            base = Path(temporary_directory)
            absent = base / "absent"
            unsafe_mode = self.make_root(base, "unsafe-mode")
            unsafe_mode.chmod(0o750)
            malformed = self.make_root(base, "malformed")
            (malformed / "artifacts").mkdir(mode=0o700)
            (malformed / "keyring.json").write_text('{"current":"v1","keys":{"v1":"bad"}}')
            (malformed / "keyring.json").chmod(0o600)
            target = base / "target"
            target.write_text("not a keyring")
            target.chmod(0o600)
            symlinked = self.make_root(base, "symlinked")
            (symlinked / "keyring.json").symlink_to(target)
            unsafe_lock = self.make_root(base, "unsafe-lock")
            (unsafe_lock / ".init.lock").write_text("")
            (unsafe_lock / ".init.lock").chmod(0o644)

            for root in (absent, unsafe_mode, malformed, symlinked, unsafe_lock):
                with self.subTest(root=root.name):
                    result = self.run_script(root)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertEqual(result.stdout, "")
                    self.assertEqual(result.stderr, "Agent storage initialization failed.\n")

    def test_refuses_a_symlink_in_a_root_ancestor(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            base = Path(temporary_directory)
            backing = base / "backing"
            backing.mkdir()
            root = self.make_root(backing)
            alias = base / "alias"
            alias.symlink_to(backing, target_is_directory=True)

            result = self.run_script(alias / root.name)

            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(result.stderr, "Agent storage initialization failed.\n")

    def test_refuses_a_fifo_keyring_without_blocking(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = self.make_root(Path(temporary_directory))
            os.mkfifo(root / "keyring.json", 0o600)

            result = self.run_script(root)

            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(result.stdout, "")
            self.assertEqual(result.stderr, "Agent storage initialization failed.\n")

    def test_refuses_a_held_initialization_lock_without_blocking(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = self.make_root(Path(temporary_directory))
            lock = root / ".init.lock"
            holder = subprocess.Popen(
                [
                    sys.executable,
                    "-c",
                    (
                        "import fcntl, os, sys, time; "
                        "fd = os.open(sys.argv[1], os.O_RDWR | os.O_CREAT, 0o600); "
                        "fcntl.flock(fd, fcntl.LOCK_EX); "
                        "print('ready', flush=True); time.sleep(10)"
                    ),
                    str(lock),
                ],
                stdout=subprocess.PIPE,
                text=True,
            )
            stdout = holder.stdout
            assert stdout is not None
            try:
                self.assertEqual(stdout.readline(), "ready\n")
                result = self.run_script(root)
            finally:
                holder.terminate()
                holder.wait(timeout=2)
                stdout.close()

            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(result.stdout, "")
            self.assertEqual(result.stderr, "Agent storage initialization failed.\n")

    def test_concurrent_initialization_keeps_one_valid_keyring(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = self.make_root(Path(temporary_directory))

            with ThreadPoolExecutor(max_workers=8) as executor:
                results = list(executor.map(lambda _: self.run_script(root), range(8)))

            self.assertTrue(all(result.returncode == 0 for result in results))
            self.assertEqual(sum(result.stdout == "created=1 ready=1\n" for result in results), 1)
            self.assertTrue(
                all(
                    result.stdout in {"created=1 ready=1\n", "created=0 ready=1\n"}
                    for result in results
                )
            )
            payload = json.loads((root / "keyring.json").read_text())
            self.assertEqual(payload["current"], "v1")
            self.assertEqual(set(payload["keys"]), {"v1"})
            self.assertEqual(len(base64.b64decode(payload["keys"]["v1"], validate=True)), 32)


if __name__ == "__main__":
    unittest.main()
