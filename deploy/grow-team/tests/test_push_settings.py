"""Test the mobile push settings of the deployment Compose files."""

import re
import unittest
from typing import Any

from tests.test_compose_isolation import SCRIPT, ComposeLoader


def load(name: str) -> dict[str, Any]:
    loader = ComposeLoader((SCRIPT.parent / name).read_text())
    try:
        return loader.get_single_data()
    finally:
        loader.dispose()


class PushSettingsTest(unittest.TestCase):
    def test_server_uses_the_zulip_push_service_without_sharing_usage_statistics(self) -> None:
        environment = load("compose.override.yaml")["services"]["zulip"]["environment"]

        self.assertEqual(environment["SETTING_ZULIP_SERVICE_PUSH_NOTIFICATIONS"], "True")
        # Push registration shares basic metadata only, never usage statistics.
        self.assertEqual(environment["SETTING_ZULIP_SERVICE_SUBMIT_USAGE_STATISTICS"], "False")

    def test_no_removed_or_secret_push_setting_is_configured(self) -> None:
        for name in ("compose.yaml", "compose.override.yaml", "zulip-settings.env.example"):
            text = (SCRIPT.parent / name).read_text()
            with self.subTest(name=name):
                # Replaced in Zulip 12 by the "require E2EE" organization setting.
                self.assertNotIn("PUSH_NOTIFICATION_REDACT_CONTENT", text)
                self.assertNotIn("PUSH_NOTIFICATION_BOUNCER_URL", text)
                # register_server writes these into the volume; they never go in Git.
                self.assertIsNone(re.search(r"ZULIP_ORG_(ID|KEY)", text, re.IGNORECASE))
