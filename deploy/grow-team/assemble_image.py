"""Assemble production files inside the pinned runtime image."""

import importlib
import json
import os
import re
import shutil
import sys
from pathlib import Path


def main() -> None:
    revision = sys.argv[1]
    if re.fullmatch(r"[0-9a-f]{40}", revision) is None:
        raise SystemExit("Pass the full source commit as GROW_TEAM_REVISION.")

    root = Path("/home/zulip/deployments/current").resolve()
    os.chdir(root)
    sys.path.insert(0, str(root))

    # Build settings exist only in this process. No production secrets are used.
    module = importlib.import_module("zproject.prod_settings_template")
    module.EXTERNAL_HOST = "build.invalid"
    module.ZULIP_ADMINISTRATOR = "noreply@build.invalid"
    sys.modules["zproject.prod_settings"] = module
    os.environ["DJANGO_SETTINGS_MODULE"] = "zproject.settings"
    os.environ["DISABLE_MANDATORY_SECRET_CHECK"] = "True"
    os.environ["ZULIP_COLLECTING_STATIC"] = "1"

    import django

    django.setup()
    from django.conf import settings
    from django.core.management import call_command

    # The base supplies integration assets. Fork assets take precedence.
    settings.STATICFILES_DIRS = [str(root / "static"), str(root / "prod-static/serve")]
    call_command("collectstatic", verbosity=0, interactive=False)
    call_command("compilemessages", verbosity=0, ignore=["*"])

    served = Path(settings.STATIC_ROOT)
    for name in (
        "images/logo/zulip-org-logo.svg",
        "images/favicon.svg",
        "images/logo/zulip-icon-128x128.png",
        "images/static_avatars/notification-bot.png",
        "images/static_avatars/notification-bot-medium.png",
    ):
        source = root / "static" / name
        if source.read_bytes() != (served / name).read_bytes():
            raise SystemExit(f"Static asset differs: {name}")

    manifest = json.loads((root / "staticfiles.json").read_text())["paths"]
    missing = [name for name in manifest.values() if not (served / name).is_file()]
    if missing:
        raise SystemExit(f"Missing static assets: {missing[:5]}")
    stats = json.loads((root / "webpack-stats-production.json").read_text())
    if stats["status"] != "done":
        raise SystemExit("The production webpack build is incomplete.")

    shutil.copytree(served, root / "prod-static/serve", dirs_exist_ok=True)
    (root / "build_id").write_text(revision + "\n")
    shutil.chown(root / "build_id", user="zulip", group="zulip")
    print(f"Grow Team {revision}: {len(manifest)} static paths verified.")


if __name__ == "__main__":
    main()
