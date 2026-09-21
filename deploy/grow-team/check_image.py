"""Check image startup as the application user without production credentials."""

import importlib
import os
import pwd
import sys
from pathlib import Path

root = Path("/home/zulip/deployments/current").resolve()
os.chdir(root)
sys.path.insert(0, str(root))
if pwd.getpwuid(os.geteuid()).pw_name != "zulip":
    raise SystemExit("Run this check with --user zulip.")

module = importlib.import_module("zproject.prod_settings_template")
module.EXTERNAL_HOST = "build.invalid"
module.ZULIP_ADMINISTRATOR = "noreply@build.invalid"
module.INTERNAL_BOT_DOMAIN = "bots.build.invalid"
module.AUTHENTICATION_BACKENDS = ("zproject.backends.EmailAuthBackend",)
sys.modules["zproject.prod_settings"] = module
config = importlib.import_module("zproject.config")
config.secrets_file.read_dict(
    {"secrets": dict.fromkeys(("secret_key", "shared_secret", "avatar_salt"), "image-check-only")}
)
os.environ["DJANGO_SETTINGS_MODULE"] = "zproject.settings"
os.environ["DISABLE_MANDATORY_SECRET_CHECK"] = "True"

import django

django.setup()
from django.conf import settings
from django.core.management import call_command
from django.template import engines

expected_bot_settings = {
    "NOTIFICATION_BOT": "notification-bot@bots.build.invalid",
    "EMAIL_GATEWAY_BOT": "emailgateway@bots.build.invalid",
    "NAGIOS_SEND_BOT": "nagios-send-bot@bots.build.invalid",
    "NAGIOS_RECEIVE_BOT": "nagios-receive-bot@bots.build.invalid",
    "WELCOME_BOT": "welcome-bot@bots.build.invalid",
    "REMINDER_BOT": "reminder-bot@bots.build.invalid",
    "NAGIOS_STAGING_SEND_BOT": "nagios-staging-send-bot@bots.build.invalid",
    "NAGIOS_STAGING_RECEIVE_BOT": "nagios-staging-receive-bot@bots.build.invalid",
}
assert {name: getattr(settings, name) for name in expected_bot_settings} == expected_bot_settings
assert {
    expected_bot_settings["NOTIFICATION_BOT"],
    expected_bot_settings["EMAIL_GATEWAY_BOT"],
    expected_bot_settings["WELCOME_BOT"],
} == settings.CROSS_REALM_BOT_EMAILS

call_command("check", verbosity=0)
engines["Jinja2"].get_template("zerver/login.html")
print("Application user startup check: PASS")
