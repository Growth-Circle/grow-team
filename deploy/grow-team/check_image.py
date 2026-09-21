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
sys.modules["zproject.prod_settings"] = module
os.environ["DJANGO_SETTINGS_MODULE"] = "zproject.settings"
os.environ["DISABLE_MANDATORY_SECRET_CHECK"] = "True"

import django

django.setup()
from django.core.management import call_command
from django.template import engines

call_command("check", verbosity=0)
engines["Jinja2"].get_template("zerver/login.html")
print("Application user startup check: PASS")
