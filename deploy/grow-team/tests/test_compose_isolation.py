"""Test the deployment Compose wrapper with a hostile inherited Docker environment."""

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from typing_extensions import override

SCRIPT = Path(__file__).parents[1] / "compose.sh"
DEDICATED_SOCKET = "unix:///run/grow-team-docker/docker.sock"
DEDICATED_CONFIG = "/opt/grow-team/lib/docker"


class ComposeIsolationTest(unittest.TestCase):
    @override
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.capture = self.root / "capture.json"
        self.fake_docker = self.root / "fake-docker.py"
        self.fake_docker.write_text("""#!/usr/bin/env python3
import json
import os
import pathlib
import sys
pathlib.Path(os.environ['COMPOSE_TEST_CAPTURE']).write_text(json.dumps({
    'argv': sys.argv[1:],
    'context': os.environ.get('DOCKER_CONTEXT'),
    'host': os.environ.get('DOCKER_HOST'),
    'config': os.environ.get('DOCKER_CONFIG'),
}))
raise SystemExit(37)
""")
        self.fake_docker.chmod(0o700)
        self.script = self.root / "compose.sh"
        self.script.write_text(
            SCRIPT.read_text().replace("/opt/grow-team/bin/docker", str(self.fake_docker))
        )
        self.script.chmod(0o700)

    def test_uses_dedicated_socket_and_ignores_inherited_context(self) -> None:
        result = subprocess.run(
            [str(self.script), "config", "--quiet"],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
            env={
                **os.environ,
                "COMPOSE_TEST_CAPTURE": str(self.capture),
                "DOCKER_CONTEXT": "hostile-context",
                "DOCKER_HOST": "tcp://hostile.invalid:2375",
                "DOCKER_CONFIG": "/hostile/docker-config",
            },
        )

        self.assertEqual(result.returncode, 37)
        captured = json.loads(self.capture.read_text())
        self.assertIsNone(captured["context"])
        self.assertEqual(captured["host"], DEDICATED_SOCKET)
        self.assertEqual(captured["config"], DEDICATED_CONFIG)
        self.assertEqual(captured["argv"][0], f"--host={DEDICATED_SOCKET}")
        self.assertEqual(captured["argv"][1:4], ["compose", "--project-name", "grow-team"])
        self.assertEqual(captured["argv"][-2:], ["config", "--quiet"])
