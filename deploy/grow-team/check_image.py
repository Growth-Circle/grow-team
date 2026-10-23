"""Check image startup as the application user without production credentials."""

import importlib
import os
import pwd
import sys
from collections.abc import Iterable
from pathlib import Path
from typing import Any


# The thumbnail worker only touches libvips when someone uploads a picture, so
# a libvips without a saver would otherwise surface as 'Failed to thumbnail' in
# errors.log long after deploy.  Kept self-contained so tests can load it alone.
def check_thumbnail_formats(pyvips: Any, formats: Iterable[Any]) -> None:
    still = pyvips.Image.black(315, 480).bandjoin([pyvips.Image.black(315, 480)] * 2)
    still = still.copy(interpretation="srgb")
    # Two different frames, so the encoder cannot collapse them into a still.
    frames = pyvips.Image.arrayjoin([still, (still + 255).cast("uchar")], across=1).copy()
    frames.set_type(pyvips.GValue.gint_type, "page-height", 480)

    failures = []
    for thumbnail_format in formats:
        source = frames if thumbnail_format.animated else still
        try:
            resized = pyvips.Image.thumbnail_buffer(
                source.write_to_buffer(".webp" if thumbnail_format.animated else ".png"),
                thumbnail_format.max_width,
                height=thumbnail_format.max_height,
                option_string="n=-1" if thumbnail_format.animated else "",
                size=pyvips.Size.DOWN,
            )
            written = resized.write_to_buffer(
                f".{thumbnail_format.extension}[{thumbnail_format.opts}]"
            )
        except pyvips.Error as error:
            failures.append(f"{thumbnail_format}: {error}")
            continue
        if thumbnail_format.extension == "webp" and written[:4] + written[8:12] != b"RIFFWEBP":
            failures.append(f"{thumbnail_format}: output is not a WebP file")
    if failures:
        raise SystemExit("libvips cannot write Zulip thumbnails:\n" + "\n".join(failures))


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

# Importing zerver.lib.thumbnail applies the same libvips loader blocks that
# the production worker runs with.
import pyvips

from zerver.lib.thumbnail import THUMBNAIL_OUTPUT_FORMATS, TRANSCODED_IMAGE_FORMAT

check_thumbnail_formats(pyvips, (*THUMBNAIL_OUTPUT_FORMATS, TRANSCODED_IMAGE_FORMAT))

engines["Jinja2"].get_template("zerver/login.html")

# Loading a template does not run static(), so a path missing from the
# collected manifest only fails when a page renders; 12.2-grow-team.18
# shipped /login/ answering 500 that way.
import json
import re

manifest = json.loads((root / "staticfiles.json").read_text())["paths"]
missing = [
    f"{template.relative_to(root)}: {match[1]}"
    for template in sorted((root / "templates").rglob("*.html"))
    for match in re.finditer(r"""static\(\s*['"]([^'"]+)['"]\s*\)""", template.read_text())
    if match[1] not in manifest
]
if missing:
    raise SystemExit("Static paths missing from the manifest:\n" + "\n".join(missing))
print("Application user startup check: PASS")
