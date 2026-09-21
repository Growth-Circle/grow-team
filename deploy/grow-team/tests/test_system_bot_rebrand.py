import importlib.util
import sys
import types
import unittest
from pathlib import Path

from django.conf import settings

if not settings.configured:
    settings.configure(
        SECRET_KEY="test",
        DATABASES={"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}},
        INSTALLED_APPS=[],
        USE_TZ=True,
    )

import django

django.setup()

from django.db import connection, models
from django.test import override_settings


class Realm(models.Model):
    string_id = models.CharField(max_length=100)

    class Meta:
        app_label = "test_bot_rebrand"


class UserProfile(models.Model):
    realm = models.ForeignKey(Realm, on_delete=models.CASCADE)
    email = models.CharField(max_length=200)
    delivery_email = models.CharField(max_length=200)
    is_bot = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    bot_type = models.IntegerField(default=1)
    bot_owner = models.ForeignKey("self", null=True, on_delete=models.CASCADE)
    api_key = models.CharField(max_length=100)
    role = models.IntegerField(default=400)

    DEFAULT_BOT = 1

    class Meta:
        app_label = "test_bot_rebrand"


cache_module = types.ModuleType("zerver.lib.cache")
cache_module.bot_profile_cache_key = lambda email, realm_id: f"bot:{email}:{realm_id}"
cache_module.get_cross_realm_dicts_key = lambda: "cross-realm"
cache_module.user_profile_by_api_key_cache_key = lambda api_key: f"key:{api_key}"
cache_module.user_profile_by_email_realm_id_cache_key = lambda email, realm_id: (
    f"email:{email}:{realm_id}"
)
cache_module.user_profile_by_id_cache_key = lambda user_id: f"id:{user_id}"
cache_module.user_profile_delivery_email_cache_key = lambda email, realm_id: (
    f"delivery:{email}:{realm_id}"
)
cache_module.user_profile_narrow_by_id_cache_key = lambda user_id: f"narrow:{user_id}"
cache_module.cache_delete_many = lambda keys: None
sys.modules["zerver.models"] = types.SimpleNamespace(Realm=Realm, UserProfile=UserProfile)
sys.modules["zerver.lib.cache"] = cache_module

script_path = Path(__file__).parents[1] / "rebrand_system_bots.py"
script_spec = importlib.util.spec_from_file_location("system_bot_rebrand", script_path)
assert script_spec is not None and script_spec.loader is not None
script = importlib.util.module_from_spec(script_spec)
script_spec.loader.exec_module(script)


BOT_LOCAL_PARTS = {
    "NOTIFICATION_BOT": "notification-bot",
    "EMAIL_GATEWAY_BOT": "emailgateway",
    "NAGIOS_SEND_BOT": "nagios-send-bot",
    "NAGIOS_RECEIVE_BOT": "nagios-receive-bot",
    "WELCOME_BOT": "welcome-bot",
    "NAGIOS_STAGING_SEND_BOT": "nagios-staging-send-bot",
    "NAGIOS_STAGING_RECEIVE_BOT": "nagios-staging-receive-bot",
    "REMINDER_BOT": "reminder-bot",
}
REQUIRED_BOTS = tuple(name for name in BOT_LOCAL_PARTS if name != "REMINDER_BOT")


def configured_settings(domain: str, host: str = "team.growc.id") -> dict[str, object]:
    emails = {name: f"{local_part}@{domain}" for name, local_part in BOT_LOCAL_PARTS.items()}
    internal_bots = [
        {"var_name": name, "email_template": f"{BOT_LOCAL_PARTS[name]}@%s"}
        for name in REQUIRED_BOTS
    ]
    return {
        "EXTERNAL_HOST": host,
        "INTERNAL_BOT_DOMAIN": domain,
        "SYSTEM_BOT_REALM": "zulipinternal",
        "INTERNAL_BOTS": internal_bots,
        "DISABLED_REALM_INTERNAL_BOTS": [
            {"var_name": "REMINDER_BOT", "email_template": "reminder-bot@%s"}
        ],
        "CROSS_REALM_BOT_EMAILS": {
            emails["NOTIFICATION_BOT"],
            emails["EMAIL_GATEWAY_BOT"],
            emails["WELCOME_BOT"],
        },
        **emails,
    }


class SystemBotRebrandTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        with connection.schema_editor() as editor:
            editor.create_model(Realm)
            editor.create_model(UserProfile)

    @classmethod
    def tearDownClass(cls) -> None:
        with connection.schema_editor() as editor:
            editor.delete_model(UserProfile)
            editor.delete_model(Realm)
        super().tearDownClass()

    def setUp(self) -> None:
        UserProfile.objects.all().delete()
        Realm.objects.all().delete()
        self.system_realm = Realm.objects.create(id=1, string_id="zulipinternal")
        self.other_realm = Realm.objects.create(id=2, string_id="other")
        self.seed_source_bots()

    def seed_source_bots(self) -> None:
        for user_id, name in enumerate(REQUIRED_BOTS, start=1):
            email = f"{BOT_LOCAL_PARTS[name]}@zulip.com"
            bot = UserProfile.objects.create(
                id=user_id,
                realm=self.system_realm,
                email=email,
                delivery_email=email,
                is_bot=True,
                is_active=True,
                bot_type=UserProfile.DEFAULT_BOT,
                api_key=f"key-{user_id}",
                role=400 + user_id,
            )
            bot.bot_owner = bot
            bot.save(update_fields=["bot_owner"])

    def snapshot(self) -> list[tuple[int, str, str, int | None, str, int, int]]:
        return list(
            UserProfile.objects.order_by("id").values_list(
                "id", "email", "delivery_email", "bot_owner_id", "api_key", "role", "bot_type"
            )
        )

    def execute(self, mode: str, domain: str = "zulip.com", host: str = "team.growc.id") -> str:
        with override_settings(**configured_settings(domain, host)):
            return script.run(mode=mode, source_domain="zulip.com", target_domain="team.growc.id")

    def test_source_audit_does_not_write(self) -> None:
        before = self.snapshot()
        self.assertIn("source domain", self.execute("audit"))
        self.assertEqual(self.snapshot(), before)

    def test_apply_target_audit_and_reverse_preserve_identity(self) -> None:
        before = self.snapshot()
        self.assertIn("Applied 7", self.execute("apply"))
        self.assertTrue(all(email.endswith("@team.growc.id") for _, email, *_ in self.snapshot()))
        self.assertIn("target domain", self.execute("audit", domain="team.growc.id"))
        self.assertIn("Reversed 7", self.execute("reverse", domain="team.growc.id"))
        self.assertEqual(self.snapshot(), before)

    def test_invalid_rows_reject_without_writes(self) -> None:
        cases = {
            "missing": lambda: UserProfile.objects.filter(id=1).delete(),
            "wrong-type": lambda: UserProfile.objects.filter(id=1).update(is_bot=False),
            "mixed": lambda: UserProfile.objects.filter(id=1).update(
                email="notification-bot@team.growc.id",
                delivery_email="notification-bot@team.growc.id",
            ),
            "inconsistent": lambda: UserProfile.objects.filter(id=1).update(
                delivery_email="notification-bot@team.growc.id"
            ),
        }
        for name, mutate in cases.items():
            with self.subTest(name=name):
                self.setUp()
                mutate()
                before = self.snapshot()
                with self.assertRaises(script.SystemBotMismatchError):
                    self.execute("apply")
                self.assertEqual(self.snapshot(), before)

    def test_forward_collisions_reject_without_writes(self) -> None:
        for field in ("email", "delivery_email"):
            with self.subTest(field=field):
                self.setUp()
                collision = UserProfile.objects.create(
                    realm=self.other_realm,
                    email="person@example.com",
                    delivery_email="person@example.com",
                    api_key="person-key",
                )
                setattr(collision, field, "NOTIFICATION-BOT@TEAM.GROWC.ID")
                collision.save(update_fields=[field])
                before = self.snapshot()
                with self.assertRaisesRegex(script.SystemBotMismatchError, "target collision"):
                    self.execute("apply")
                self.assertEqual(self.snapshot(), before)

    def test_reverse_source_collision_rejects_without_writes(self) -> None:
        self.execute("apply")
        UserProfile.objects.create(
            realm=self.other_realm,
            email="NOTIFICATION-BOT@ZULIP.COM",
            delivery_email="person@example.com",
            api_key="person-key",
        )
        before = self.snapshot()
        with self.assertRaisesRegex(script.SystemBotMismatchError, "target collision"):
            self.execute("reverse", domain="team.growc.id")
        self.assertEqual(self.snapshot(), before)

    def test_wrong_host_realm_and_mode_reject_without_writes(self) -> None:
        before = self.snapshot()
        with self.assertRaises(script.SystemBotMismatchError):
            self.execute("audit", host="wrong.example")
        self.assertEqual(self.snapshot(), before)
        self.system_realm.string_id = "wrong"
        self.system_realm.save(update_fields=["string_id"])
        before = self.snapshot()
        with self.assertRaises(script.SystemBotMismatchError):
            self.execute("audit")
        self.assertEqual(self.snapshot(), before)
        self.system_realm.string_id = "zulipinternal"
        self.system_realm.save(update_fields=["string_id"])
        before = self.snapshot()
        with self.assertRaises(ValueError):
            self.execute("unknown")
        self.assertEqual(self.snapshot(), before)


if __name__ == "__main__":
    unittest.main()
