"""Audit, apply, or reverse the Grow Team system-bot email domain change."""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Iterable
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from zerver.models import Realm, UserProfile


class SystemBotMismatchError(RuntimeError):
    """The database does not contain the expected system-bot rows."""


EXPECTED_HOST = "team.growc.id"
EXPECTED_SYSTEM_REALM_ID = 1


def configured_system_bot_templates() -> tuple[dict[str, str], set[str]]:
    from django.conf import settings

    templates: dict[str, str] = {}
    required_settings: set[str] = set()
    for bot in settings.INTERNAL_BOTS:
        templates[bot["var_name"]] = bot["email_template"]
        required_settings.add(bot["var_name"])
    for bot in settings.DISABLED_REALM_INTERNAL_BOTS:
        templates[bot["var_name"]] = bot["email_template"]
    return templates, required_settings


def cache_keys_for_bots(
    bots: Iterable[UserProfile], emails: Iterable[str], realm_id: int
) -> list[str]:
    from zerver.lib.cache import (
        bot_profile_cache_key,
        get_cross_realm_dicts_key,
        user_profile_by_api_key_cache_key,
        user_profile_by_email_realm_id_cache_key,
        user_profile_by_id_cache_key,
        user_profile_delivery_email_cache_key,
        user_profile_narrow_by_id_cache_key,
    )

    keys = [get_cross_realm_dicts_key()]
    for bot in bots:
        keys.extend(
            [
                user_profile_by_id_cache_key(bot.id),
                user_profile_narrow_by_id_cache_key(bot.id),
                user_profile_by_api_key_cache_key(bot.api_key),
            ]
        )
    for email in emails:
        keys.extend(
            [
                user_profile_by_email_realm_id_cache_key(email, realm_id),
                user_profile_delivery_email_cache_key(email, realm_id),
                bot_profile_cache_key(email, realm_id),
            ]
        )
    return keys


def get_system_realm() -> Realm:
    from django.conf import settings

    from zerver.models import Realm

    realm = Realm.objects.select_for_update().filter(id=EXPECTED_SYSTEM_REALM_ID).first()
    if realm is None or realm.string_id != settings.SYSTEM_BOT_REALM:
        raise SystemBotMismatchError(
            "System bot realm id or string id differs from the expected value."
        )
    return realm


def classify_bot_rows(
    realm: Realm,
    source_emails: dict[str, str],
    target_emails: dict[str, str],
    required_settings: set[str],
) -> tuple[str, dict[str, UserProfile]]:
    from django.db.models import Q

    from zerver.models import UserProfile

    expected_emails = set(source_emails.values()) | set(target_emails.values())
    query = Q(email__in=expected_emails) | Q(delivery_email__in=expected_emails)
    rows = list(
        UserProfile.objects.select_for_update().filter(realm=realm).filter(query).order_by("id")
    )
    rows_by_email: dict[str, UserProfile] = {}
    for bot in rows:
        if bot.email != bot.delivery_email or bot.email not in expected_emails:
            raise SystemBotMismatchError("A system bot row has inconsistent email fields.")
        if bot.email in rows_by_email:
            raise SystemBotMismatchError(f"Found duplicate system bot rows for {bot.email}.")
        if (
            not bot.is_bot
            or not bot.is_active
            or bot.bot_type != UserProfile.DEFAULT_BOT
            or bot.bot_owner_id != bot.id
        ):
            raise SystemBotMismatchError(
                f"Expected an active self-owned default bot for {bot.email}."
            )
        rows_by_email[bot.email] = bot

    states: set[str] = set()
    bots: dict[str, UserProfile] = {}
    for setting_name, source_email in source_emails.items():
        target_email = target_emails[setting_name]
        source_bot = rows_by_email.get(source_email)
        target_bot = rows_by_email.get(target_email)
        if source_bot is not None and target_bot is not None:
            raise SystemBotMismatchError(f"System bot rows use mixed domains for {setting_name}.")
        bot = source_bot or target_bot
        if bot is None:
            if setting_name in required_settings:
                raise SystemBotMismatchError(f"Expected one row for {setting_name}.")
            continue
        states.add("source" if source_bot is not None else "target")
        bots[setting_name] = bot
    if len(states) != 1:
        raise SystemBotMismatchError("System bot rows use mixed source and target domains.")
    return states.pop(), bots


def validate_destination_collisions(
    bots: Iterable[UserProfile], destination_emails: Iterable[str]
) -> None:
    from zerver.models import UserProfile

    bot_ids = [bot.id for bot in bots]
    for email in destination_emails:
        collisions = UserProfile.objects.filter(email__iexact=email) | UserProfile.objects.filter(
            delivery_email__iexact=email
        )
        if collisions.exclude(id__in=bot_ids).exists():
            raise SystemBotMismatchError(f"Found target collision for {email}.")


def run(*, mode: str, source_domain: str, target_domain: str = "team.growc.id") -> str:
    from django.conf import settings
    from django.db import transaction
    from django.db.models import Case, CharField, Value, When

    from zerver.lib.cache import cache_delete_many
    from zerver.models import UserProfile

    if mode not in {"audit", "apply", "reverse"}:
        raise ValueError("mode must be audit, apply, or reverse")
    with transaction.atomic():
        source_domain = source_domain.lower()
        target_domain = target_domain.lower()
        if settings.EXTERNAL_HOST != EXPECTED_HOST:
            raise SystemBotMismatchError(f"EXTERNAL_HOST must be {EXPECTED_HOST}.")
        if source_domain == target_domain:
            raise SystemBotMismatchError("The source and target domains must differ.")
        templates, required_settings = configured_system_bot_templates()
        source_emails = {name: template % source_domain for name, template in templates.items()}
        target_emails = {name: template % target_domain for name, template in templates.items()}
        configured_emails = {name: getattr(settings, name) for name in templates}
        expected_configured_emails = (
            target_emails if target_domain == settings.INTERNAL_BOT_DOMAIN else source_emails
        )
        if configured_emails != expected_configured_emails:
            raise SystemBotMismatchError(
                "Named bot settings do not match the active transition domain."
            )
        realm = get_system_realm()
        state, bots = classify_bot_rows(realm, source_emails, target_emails, required_settings)
        destination_emails = target_emails if state == "source" else source_emails
        validate_destination_collisions(bots.values(), destination_emails.values())
        if mode == "audit":
            return f"Audit passed: {len(bots)} system bots use the {state} domain."
        required_state = "source" if mode == "apply" else "target"
        if state != required_state:
            raise SystemBotMismatchError(
                f"Cannot {mode} while system bot rows use the {state} domain."
            )
        new_emails = target_emails if mode == "apply" else source_emails
        old_emails = source_emails if mode == "apply" else target_emails
        updates = [When(id=bot.id, then=Value(new_emails[name])) for name, bot in bots.items()]
        UserProfile.objects.filter(id__in=[bot.id for bot in bots.values()]).update(
            email=Case(*updates, output_field=CharField()),
            delivery_email=Case(*updates, output_field=CharField()),
        )
        cache_delete_many(
            cache_keys_for_bots(
                bots.values(), [*old_emails.values(), *new_emails.values()], realm.id
            )
        )
    action = "Applied" if mode == "apply" else "Reversed"
    return f"{action} {len(bots)} system bot email addresses."


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--audit", action="store_true", help="Check rows without changing them.")
    mode.add_argument("--apply", action="store_true", help="Change rows to INTERNAL_BOT_DOMAIN.")
    mode.add_argument("--reverse", action="store_true", help="Restore rows to the source domain.")
    parser.add_argument(
        "--source-domain", default="zulip.com", help="Old domain. Default: zulip.com."
    )
    parser.add_argument(
        "--target-domain", default="team.growc.id", help="New domain. Default: team.growc.id."
    )
    args = parser.parse_args()

    sys.path.insert(0, str(Path("/home/zulip/deployments/current").resolve()))
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "zproject.settings")
    import django

    django.setup()
    mode_name = "audit" if args.audit else "apply" if args.apply else "reverse"
    print(run(mode=mode_name, source_domain=args.source_domain, target_domain=args.target_domain))


if __name__ == "__main__":
    main()
