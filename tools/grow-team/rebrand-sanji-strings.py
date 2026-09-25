#!/usr/bin/env python3
"""Rebrand "Grow Team" to "sanji" in this repo's user-visible strings.

This script audits by default and prints the files it would change.  Pass
--apply to write the changes.  It rewrites the directories in SCOPE_DIRS
below wholesale (every git-tracked file under them), plus the individual
files listed in SCOPE_FILES.  It never touches internal identifiers
(grow-team, grow_team, grow-agent), database data, or migrations, and it
skips the paths listed in EXCLUDE_PREFIXES because another change owns
them.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

OLD = "Grow Team"
NEW = "sanji"

# Directories rewritten wholesale: every git-tracked file under them.
SCOPE_DIRS = [
    "templates",
    "web/html",
    "web/templates",
    "locale",
    "zerver/tests",
    "web/tests",
]

# Individual files outside the directories above.
SCOPE_FILES = [
    "web/src/admin.ts",
    "web/src/billing/sponsorship.ts",
    "web/src/gear_menu_util.ts",
    "web/src/group_permission_settings.ts",
    "web/src/info_overlay.ts",
    "web/src/integration_url_modal.ts",
    "web/src/narrow_banner.ts",
    "web/src/narrow_title.ts",
    "web/src/navbar_alerts.ts",
    "web/src/onboarding_steps.ts",
    "web/src/popup_banners.ts",
    "web/src/portico/signup.ts",
    "web/src/settings_agents.ts",
    "web/src/settings_config.ts",
    "web/src/settings_notifications.ts",
    "web/src/tippyjs.ts",
    "web/src/ui_init.js",
    "zerver/lib/onboarding.py",
    "zerver/lib/send_email.py",
    "zerver/lib/email_notifications.py",
    "zerver/lib/recipient_users.py",
    "zerver/actions/create_realm.py",
    "zerver/actions/create_user.py",
    "zerver/actions/invites.py",
    "zerver/forms.py",
    "zerver/views/invite.py",
    "zerver/views/onboarding_steps.py",
    "zerver/views/user_settings.py",
    "zerver/views/video_calls.py",
    "web/e2e-tests/navigation.test.ts",
]

# Paths excluded even though they sit under a scope directory above:
# each one is owned and actively edited by a different change in flight,
# so a wholesale rewrite here would collide with that other work.
EXCLUDE_PREFIXES = [
    "web/templates/favicon.svg.hbs",  # dynamic favicon markup, rewritten separately
    "locale/id/pending",  # each pending translation file belongs to the change that adds it
    "zerver/tests/test_agent_realm_lock.py",
    "zerver/tests/test_workspace_models.py",
    "zerver/tests/test_role_permissions.py",
    "zerver/tests/test_openapi.py",
    "zerver/tests/test_openapi_fragments.py",
    "zerver/tests/test_oauth_callback.py",
    "zerver/tests/test_events.py",
    "zerver/tests/test_agent_events.py",
    "zerver/tests/test_agent_task_sync.py",
    "zerver/tests/test_tasks.py",
    "zerver/tests/test_agents_lifecycle.py",
    "zerver/tests/test_realm_logo_night.py",
    "web/tests/people.test.cjs",
    "web/tests/favicon.test.cjs",
]

# Literal replacement that is not the plain "Grow Team" -> "sanji" swap:
# the favicon/logo cache-busting query param, 17 places under templates/
# only (map-brand.md R-3).  zerver/lib/realm_logo.py has its own copy of
# this string, owned by WP07; do not touch it from here.
CACHE_BUST_OLD = "grow-team-1"
CACHE_BUST_NEW = "sanji-1"

# The Terms of Service checkbox used to build its link from a Jinja
# "{{ root_domain_url }}" variable, so every catalog carries a
# %(root_domain_url)s placeholder for it.  The template now points at
# the fixed sanji.space URL directly, so catalogs must drop the
# placeholder (and the now-stale "#, python-format" flag on that entry)
# or newstyle gettext raises KeyError when it formats the string.
TOS_LINK_OLD = "%(root_domain_url)s/policies/terms"
TOS_LINK_NEW = "https://sanji.space/syarat/"


def git_tracked_files(root: Path) -> list[Path]:
    out = subprocess.run(
        ["git", "-C", str(root), "ls-files"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    return [root / line for line in out.splitlines() if line]


def in_scope(rel: str) -> bool:
    if any(rel == ex or rel.startswith(ex + "/") for ex in EXCLUDE_PREFIXES):
        return False
    if rel in SCOPE_FILES:
        return True
    return any(rel == d or rel.startswith(d + "/") for d in SCOPE_DIRS)


def og_site_name_fixup(rel: str, text: str) -> str:
    """og:site_name must become "sanji.space", not the plain "sanji" swap."""
    if rel == "templates/zerver/meta_tags.html":
        text = text.replace('content="Grow Team"', 'content="sanji.space"')
    return text


def cache_bust_fixup(rel: str, text: str) -> str:
    if rel.startswith("templates/"):
        text = text.replace(CACHE_BUST_OLD, CACHE_BUST_NEW)
    return text


def tos_link_fixup(rel: str, text: str) -> str:
    if not rel.endswith("/LC_MESSAGES/django.po") or TOS_LINK_OLD not in text:
        return text
    text = text.replace(TOS_LINK_OLD, TOS_LINK_NEW)
    stale_flag = '#, python-format\nmsgid ""\n"I agree to the <a href=\\"' + TOS_LINK_NEW + '\\" "'
    return text.replace(stale_flag, 'msgid ""\n"I agree to the <a href=\\"' + TOS_LINK_NEW + '\\" "')


def rewrite(rel: str, text: str) -> str:
    text = og_site_name_fixup(rel, text)
    text = cache_bust_fixup(rel, text)
    text = tos_link_fixup(rel, text)
    return text.replace(OLD, NEW)


PO_MSGID_RE = re.compile(r'^msgid((?:\s*\n)?(?:\s*"(?:[^"\\]|\\.)*"\s*)+)', re.MULTILINE)


def po_msgids(text: str) -> list[str]:
    """Best-effort extraction of each msgid block's raw quoted content.

    Good enough to compare key counts and catch collisions before/after a
    rewrite; it does not need to fully unescape the string.
    """
    return [m.group(1) for m in PO_MSGID_RE.finditer(text)]


def catalog_key_check(rel: str, old_text: str, new_text: str) -> str | None:
    """Return an error message if a locale catalog rewrite changed key
    count or introduced a duplicate key; None when it is safe."""
    if rel.endswith("/LC_MESSAGES/django.po"):
        before, after = po_msgids(old_text), po_msgids(new_text)
        if len(before) != len(after):
            return f"msgid count changed: {len(before)} -> {len(after)}"
        if len(set(after)) != len(after):
            return "duplicate msgid after rewrite"
    elif rel.endswith("translations.json"):
        before = json.loads(old_text)
        after = json.loads(new_text)
        if len(before) != len(after):
            return f"key count changed: {len(before)} -> {len(after)}"
    return None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Write changes. Default only audits.")
    args = parser.parse_args()

    touched_files = 0
    occurrences = 0
    errors = 0
    for path in git_tracked_files(REPO_ROOT):
        rel = path.relative_to(REPO_ROOT).as_posix()
        if not in_scope(rel):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, FileNotFoundError):
            continue
        has_old = OLD in text
        has_cache_bust = rel.startswith("templates/") and CACHE_BUST_OLD in text
        if not has_old and not has_cache_bust:
            continue
        new_text = rewrite(rel, text)
        if new_text == text:
            continue
        error = catalog_key_check(rel, text, new_text)
        if error:
            errors += 1
            print(f"ERROR {rel}: {error}", file=sys.stderr)
            continue
        touched_files += 1
        occurrences += text.count(OLD)
        if args.apply:
            path.write_text(new_text, encoding="utf-8")
        else:
            print(rel)

    verb = "Rewrote" if args.apply else "Would rewrite"
    print(f'{verb} {touched_files} files, {occurrences} "{OLD}" occurrences.')
    if errors:
        print(
            f"{errors} files failed the catalog key check; nothing written for them.",
            file=sys.stderr,
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
