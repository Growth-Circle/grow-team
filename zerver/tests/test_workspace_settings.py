"""Tests for zerver/lib/workspace_settings.py and
zerver/views/workspace_settings.py: workspace settings."""

from unittest import mock

from django.utils.timezone import now as timezone_now
from typing_extensions import override

from zerver.lib.test_classes import ZulipTestCase
from zerver.lib.workspace_settings import BRAND_COLORS, capabilities
from zerver.models import AgentProvider, AgentRealmSettings, AgentRunner, RealmAuditLog, UserProfile
from zerver.models.realm_audit_logs import AuditLogEventType
from zerver.models.realms import get_realm
from zerver.models.users import get_user_by_delivery_email


class RealmSettingsTests(ZulipTestCase):
    @override
    def setUp(self) -> None:
        super().setUp()
        self.realm = get_realm("zulip")

    def make_runner(self, owner: UserProfile, name: str) -> AgentRunner:
        return AgentRunner.objects.create(
            realm=owner.realm, owner=owner, name=name, fingerprint=name.ljust(64, "0")
        )

    def make_provider(self, runner: AgentRunner, name: str) -> AgentProvider:
        return AgentProvider.objects.create(
            realm=runner.realm,
            owner=runner.owner,
            runner=runner,
            name=name,
            base_url="https://provider.example.com/v1",
            model_id="anthropic/claude-sonnet",
            context_window_tokens=200000,
            max_output_tokens=8192,
        )

    def test_get_defaults_and_capabilities_shape(self) -> None:
        self.login("hamlet")  # A Member: GET needs no special permission.
        rows_before = AgentRealmSettings.objects.count()
        result = self.client_get("/json/agent/realm-settings")
        payload = self.assert_json_success(result)
        self.assertEqual(payload["timezone"], "Asia/Jakarta")
        self.assertEqual(payload["approval_ttl_minutes"], 120)
        self.assertEqual(payload["invite_expiry_days"], 7)
        self.assertEqual(payload["agent_language"], "id")
        self.assertIsNone(payload["work_runner"])
        self.assertIsNone(payload["work_provider"])
        self.assertFalse(payload["can_create_workspace"])
        self.assertEqual(set(payload["capabilities"]), set(capabilities(self.realm)))
        # A read never writes.
        self.assertEqual(AgentRealmSettings.objects.count(), rows_before)

    def test_patch_requires_ws_settings_permission(self) -> None:
        self.login("hamlet")  # Member: ws_settings defaults to False.
        result = self.client_patch("/json/agent/realm-settings", {"timezone": "Asia/Makassar"})
        self.assert_json_error(result, "You do not have permission to do this.")

    def test_patch_as_admin_updates_fields_and_logs_audit(self) -> None:
        self.login("iago")  # Admin: ws_settings is True by default.
        result = self.client_patch(
            "/json/agent/realm-settings",
            {
                "timezone": "Asia/Makassar",
                "agent_language": "en",
                "task_id_prefix": "KS",
                "task_status_notices": "true",
            },
        )
        payload = self.assert_json_success(result)
        self.assertEqual(payload["timezone"], "Asia/Makassar")
        self.assertEqual(payload["agent_language"], "en")
        self.assertEqual(payload["task_id_prefix"], "KS")
        self.assertTrue(payload["task_status_notices"])

        row = AgentRealmSettings.objects.get(realm=self.realm)
        self.assertEqual(row.timezone, "Asia/Makassar")
        self.assertEqual(row.agent_language, "en")

        entry = RealmAuditLog.objects.filter(
            realm=self.realm, event_type=AuditLogEventType.WORKSPACE_SETTINGS_CHANGED
        ).latest("id")
        self.assertEqual(entry.acting_user_id, self.example_user("iago").id)
        self.assertEqual(
            entry.extra_data["changed"],
            [
                {"property": "agent_language", "old_value": "id", "new_value": "en"},
                {"property": "task_id_prefix", "old_value": "", "new_value": "KS"},
                {"property": "task_status_notices", "old_value": False, "new_value": True},
                {
                    "property": "timezone",
                    "old_value": "Asia/Jakarta",
                    "new_value": "Asia/Makassar",
                },
            ],
        )

    def test_patch_sends_event_only_after_commit(self) -> None:
        self.login("iago")
        with mock.patch("zerver.tornado.event_queue.process_notification") as notify:
            with self.captureOnCommitCallbacks(execute=False) as callbacks:
                result = self.client_patch(
                    "/json/agent/realm-settings", {"timezone": "Asia/Makassar"}
                )
            self.assert_json_success(result)
            notify.assert_not_called()
            for callback in callbacks:
                callback()
            notify.assert_called_once()
        event = notify.call_args.args[0]["event"]
        self.assertEqual(event["type"], "agent_realm_settings")
        self.assertEqual(event["op"], "update")
        self.assertEqual(event["data"], {"timezone": "Asia/Makassar"})

    def test_patch_with_no_real_change_writes_nothing(self) -> None:
        self.login("iago")
        audit_rows = RealmAuditLog.objects.filter(
            realm=self.realm, event_type=AuditLogEventType.WORKSPACE_SETTINGS_CHANGED
        )
        count_before = audit_rows.count()
        with self.capture_send_event_calls(expected_num_events=0):
            # Both are the defaults already.
            result = self.client_patch(
                "/json/agent/realm-settings",
                {"timezone": "Asia/Jakarta", "invite_expiry_days": "7"},
            )
        self.assert_json_success(result)
        self.assertEqual(audit_rows.count(), count_before)

    def test_patch_rejects_a_bad_timezone(self) -> None:
        self.login("iago")
        result = self.client_patch("/json/agent/realm-settings", {"timezone": "Mars/Olympus_Mons"})
        self.assert_json_error(result, "Not a recognized time zone")

    def test_patch_rejects_a_color_outside_the_four(self) -> None:
        self.login("iago")
        result = self.client_patch("/json/agent/realm-settings", {"brand_color": "#000000"})
        self.assert_json_error(result, "That color is not available.")

    def test_patch_accepts_every_brand_color(self) -> None:
        self.login("iago")
        for color in BRAND_COLORS:
            result = self.client_patch("/json/agent/realm-settings", {"brand_color": color})
            payload = self.assert_json_success(result)
            self.assertEqual(payload["brand_color"], color)

    def test_patch_other_fields(self) -> None:
        self.login("iago")
        result = self.client_patch(
            "/json/agent/realm-settings",
            {
                "summary_default": "true",
                "approval_ttl_minutes": "10080",
                "require_2fa": "true",
                "mcp_default_mode": "direct",
            },
        )
        payload = self.assert_json_success(result)
        self.assertTrue(payload["summary_default"])
        self.assertEqual(payload["approval_ttl_minutes"], 10080)
        self.assertTrue(payload["require_2fa"])
        self.assertEqual(payload["mcp_default_mode"], "direct")

        result = self.client_patch("/json/agent/realm-settings", {"mcp_default_mode": "later"})
        self.assert_json_error(result, "That mode is not available.")
        result = self.client_patch("/json/agent/realm-settings", {"agent_language": "fr"})
        self.assert_json_error(result, "Choose Indonesian or English.")
        result = self.client_patch("/json/agent/realm-settings", {"summary_default": "maybe"})
        self.assert_json_error(result, "summary_default is not valid JSON")
        result = self.client_patch("/json/agent/realm-settings", {"approval_ttl_minutes": "0"})
        self.assert_json_error(result, "approval_ttl_minutes is too small")
        result = self.client_patch("/json/agent/realm-settings", {"approval_ttl_minutes": "10081"})
        self.assert_json_error(result, "approval_ttl_minutes is too large")
        result = self.client_patch(
            "/json/agent/realm-settings", {"approval_ttl_minutes": str(2**31)}
        )
        self.assert_json_error(result, "approval_ttl_minutes is too large")

    def test_patch_task_id_prefix_must_fit(self) -> None:
        self.login("iago")
        for bad_prefix in ["ks", "K-S", "ABCDEFGHIJK"]:
            result = self.client_patch("/json/agent/realm-settings", {"task_id_prefix": bad_prefix})
            self.assert_json_error(result, "Use up to 10 capital letters or digits.")
        payload = self.assert_json_success(
            self.client_patch("/json/agent/realm-settings", {"task_id_prefix": "ABCDEFGHI0"})
        )
        self.assertEqual(payload["task_id_prefix"], "ABCDEFGHI0")
        payload = self.assert_json_success(
            self.client_patch("/json/agent/realm-settings", {"task_id_prefix": ""})
        )
        self.assertEqual(payload["task_id_prefix"], "")

    def test_patch_invite_expiry_days_choices(self) -> None:
        self.login("iago")
        for wire_value, expected in [("30", 30), ('"unlimited"', None), ("7", 7)]:
            result = self.client_patch(
                "/json/agent/realm-settings", {"invite_expiry_days": wire_value}
            )
            payload = self.assert_json_success(result)
            self.assertEqual(payload["invite_expiry_days"], expected)

        result = self.client_patch("/json/agent/realm-settings", {"invite_expiry_days": "14"})
        self.assert_json_error(result, "Choose 7 days, 30 days, or no limit.")

    def test_patch_budget_needs_the_owner(self) -> None:
        self.login("iago")  # Admin: has ws_settings, but is not the Owner.
        result = self.client_patch(
            "/json/agent/realm-settings", {"monthly_budget_microunits": "1000000"}
        )
        self.assert_json_error(result, "Must be an organization owner")
        self.assertFalse(
            AgentRealmSettings.objects.filter(
                realm=self.realm, monthly_budget_microunits__isnull=False
            ).exists()
        )

        self.login("desdemona")  # Owner.
        result = self.client_patch(
            "/json/agent/realm-settings", {"monthly_budget_microunits": "1000000"}
        )
        payload = self.assert_json_success(result)
        self.assertEqual(payload["monthly_budget_microunits"], 1000000)

        result = self.client_patch(
            "/json/agent/realm-settings", {"monthly_budget_microunits": '"unlimited"'}
        )
        payload = self.assert_json_success(result)
        self.assertIsNone(payload["monthly_budget_microunits"])

    def test_patch_budget_must_fit_its_column(self) -> None:
        self.login("desdemona")
        result = self.client_patch(
            "/json/agent/realm-settings", {"monthly_budget_microunits": str(2**63)}
        )
        self.assert_json_error(
            result, "That budget is not possible. Choose another amount or no limit."
        )
        for bad_value in ["-1", '"none"']:
            result = self.client_patch(
                "/json/agent/realm-settings", {"monthly_budget_microunits": bad_value}
            )
            self.assert_json_error(
                result, "That budget is not possible. Choose another amount or no limit."
            )
        payload = self.assert_json_success(
            self.client_patch(
                "/json/agent/realm-settings", {"monthly_budget_microunits": str(2**63 - 1)}
            )
        )
        self.assertEqual(payload["monthly_budget_microunits"], 2**63 - 1)

    def test_patch_work_runner_must_belong_to_this_workspace(self) -> None:
        other_realm = get_realm("lear")
        other_owner = get_user_by_delivery_email("cordelia@zulip.com", other_realm)
        foreign_runner = self.make_runner(other_owner, "Foreign")
        foreign_provider = self.make_provider(foreign_runner, "Foreign provider")
        self.login("desdemona")
        result = self.client_patch(
            "/json/agent/realm-settings", {"work_runner_id": str(foreign_runner.id)}
        )
        self.assert_json_error(result, "That runner is not part of this workspace.")
        result = self.client_patch("/json/agent/realm-settings", {"work_runner_id": "not-an-id"})
        self.assert_json_error(result, "That runner is not part of this workspace.")
        result = self.client_patch(
            "/json/agent/realm-settings", {"work_provider_id": str(foreign_provider.id)}
        )
        self.assert_json_error(result, "That model provider is not part of this workspace.")

    def test_patch_work_runner_must_be_my_own(self) -> None:
        owner = self.example_user("desdemona")
        admin_runner = self.make_runner(self.example_user("iago"), "Iago's laptop")
        admin_provider = self.make_provider(admin_runner, "Iago's key")
        revoked_runner = self.make_runner(owner, "Old laptop")
        revoked_runner.revoked_at = timezone_now()
        revoked_runner.save(update_fields=["revoked_at"])
        disabled_provider = self.make_provider(self.make_runner(owner, "Spare"), "Old key")
        disabled_provider.disabled_at = timezone_now()
        disabled_provider.save(update_fields=["disabled_at"])

        self.login("desdemona")
        for runner in [admin_runner, revoked_runner]:
            result = self.client_patch(
                "/json/agent/realm-settings", {"work_runner_id": str(runner.id)}
            )
            self.assert_json_error(result, "Choose one of your own runners that still has access.")
        for provider in [admin_provider, disabled_provider]:
            result = self.client_patch(
                "/json/agent/realm-settings", {"work_provider_id": str(provider.id)}
            )
            self.assert_json_error(
                result, "Choose one of your own model providers that is turned on."
            )
        self.assertFalse(
            AgentRealmSettings.objects.filter(realm=self.realm, work_runner__isnull=False).exists()
        )

    def test_patch_work_provider_must_run_on_the_work_runner(self) -> None:
        owner = self.example_user("desdemona")
        laptop = self.make_runner(owner, "Laptop")
        server = self.make_runner(owner, "Server")
        server_provider = self.make_provider(server, "Server key")
        self.login("desdemona")
        result = self.client_patch(
            "/json/agent/realm-settings",
            {"work_runner_id": str(laptop.id), "work_provider_id": str(server_provider.id)},
        )
        self.assert_json_error(result, "Choose a model provider that runs on the chosen runner.")
        self.assertFalse(
            AgentRealmSettings.objects.filter(realm=self.realm, work_runner__isnull=False).exists()
        )

    def test_patch_sets_and_clears_the_work_runner_and_provider(self) -> None:
        owner = self.example_user("desdemona")
        runner = self.make_runner(owner, "Own")
        provider = self.make_provider(runner, "Own provider")
        self.login("desdemona")
        result = self.client_patch(
            "/json/agent/realm-settings",
            {"work_runner_id": str(runner.id), "work_provider_id": str(provider.id)},
        )
        payload = self.assert_json_success(result)
        self.assertEqual(
            payload["work_runner"],
            {"id": str(runner.id), "name": "Own", "status": "unknown", "model": None},
        )
        self.assertEqual(
            payload["work_provider"],
            {
                "id": str(provider.id),
                "name": "Own provider",
                "status": "active",
                "model": "anthropic/claude-sonnet",
            },
        )
        entry = RealmAuditLog.objects.filter(
            realm=self.realm, event_type=AuditLogEventType.WORKSPACE_SETTINGS_CHANGED
        ).latest("id")
        self.assertEqual(
            entry.extra_data["changed"],
            [
                {"property": "work_provider", "old_value": None, "new_value": str(provider.id)},
                {"property": "work_runner", "old_value": None, "new_value": str(runner.id)},
            ],
        )

        # Clearing only the runner would leave a provider on no runner.
        result = self.client_patch("/json/agent/realm-settings", {"work_runner_id": ""})
        self.assert_json_error(result, "Choose a model provider that runs on the chosen runner.")
        result = self.client_patch(
            "/json/agent/realm-settings", {"work_runner_id": "", "work_provider_id": ""}
        )
        payload = self.assert_json_success(result)
        self.assertIsNone(payload["work_runner"])
        self.assertIsNone(payload["work_provider"])

    def test_can_create_workspace_needs_both_role_and_setting(self) -> None:
        with self.settings(WORKSPACE_CREATION_ENABLED=False):
            self.login("desdemona")
            payload = self.assert_json_success(self.client_get("/json/agent/realm-settings"))
            self.assertFalse(payload["can_create_workspace"])

        with self.settings(WORKSPACE_CREATION_ENABLED=True):
            self.login("hamlet")  # Member, not Owner or Admin.
            payload = self.assert_json_success(self.client_get("/json/agent/realm-settings"))
            self.assertFalse(payload["can_create_workspace"])

            self.login("iago")  # Admin.
            payload = self.assert_json_success(self.client_get("/json/agent/realm-settings"))
            self.assertTrue(payload["can_create_workspace"])

    def test_capabilities_follow_server_settings(self) -> None:
        with self.settings(VAPID_PUBLIC_KEY=None, WHATSAPP_PHONE_NUMBER_ID=None):
            data = capabilities(self.realm)
            self.assertFalse(data["web_push"])
            self.assertEqual(data["vapid_public_key"], "")
            self.assertFalse(data["whatsapp"])
        with self.settings(VAPID_PUBLIC_KEY="a-test-key", WHATSAPP_PHONE_NUMBER_ID="123"):
            data = capabilities(self.realm)
            self.assertTrue(data["web_push"])
            self.assertEqual(data["vapid_public_key"], "a-test-key")
            self.assertTrue(data["whatsapp"])
