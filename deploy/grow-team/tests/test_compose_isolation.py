"""Test the deployment Compose wrapper with a hostile inherited Docker environment."""

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from typing import Any

import yaml
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


class ComposeLoader(yaml.SafeLoader):
    """Read Compose files, whose merge tags (!reset, !override) are not plain YAML."""


def keep_tagged_value(loader: yaml.SafeLoader, tag_suffix: str, node: yaml.Node) -> object:
    if isinstance(node, yaml.ScalarNode):
        return loader.construct_scalar(node)
    if isinstance(node, yaml.SequenceNode):
        return loader.construct_sequence(node, deep=True)
    assert isinstance(node, yaml.MappingNode)
    return loader.construct_mapping(node, deep=True)


ComposeLoader.add_multi_constructor("!", keep_tagged_value)


class ComposeVolumesTest(unittest.TestCase):
    def load(self, name: str) -> dict[str, Any]:
        loader = ComposeLoader((SCRIPT.parent / name).read_text())
        try:
            return loader.get_single_data()
        finally:
            loader.dispose()

    def test_zulip_logs_live_on_a_named_volume_that_survives_recreation(self) -> None:
        base = self.load("compose.yaml")
        override = self.load("compose.override.yaml")

        mounts = base["services"]["zulip"]["volumes"]
        self.assertIn("zulip-logs:/var/log/zulip:rw", mounts)
        self.assertIn("zulip-logs", base["volumes"])
        override_mounts = override["services"]["zulip"].get("volumes", [])
        # An override that remounts the path would shadow the named volume.
        self.assertEqual([mount for mount in override_mounts if "/var/log/zulip" in mount], [])
