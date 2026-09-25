"""Reserved names and bot-email rules for sanji agent profiles (spec 11).

`create_profile` used to call `check_full_name(..., realm=None)`, which
skips Zulip's own uniqueness check entirely (map-backend 11-V1). The
helpers here are the replacement: a case- and form-insensitive check
against a fixed reserved list plus every active user and bot in the
workspace, and a bot email derived from a slug of the agent's name
instead of a random id (map-backend 11-V2).
"""

import secrets
import unicodedata

from zerver.lib.users import validate_short_name_and_construct_bot_email
from zerver.models import Realm, UserProfile

# Zulip's own special mention keywords, the sanji built-in agents, and the
# workspace-reserved words from the brand pack (internals/docs/pages/11).
RESERVED_AGENT_NAMES: frozenset[str] = frozenset(
    {
        "kaki",
        "ayame",
        "matcha",
        "admin",
        "sanji",
        "bot",
        "agen",
        "system",
        "owner",
        "kamu",
        "semua",
        "everyone",
        "here",
        "channel",
        "all",
        "stream",
        "topic",
    }
)


def _casefold(name: str) -> str:
    return unicodedata.normalize("NFKC", name).casefold()


def agent_name_available(
    name: str,
    realm: Realm,
    *,
    exclude_user_id: int | None = None,
    allow_reserved: bool = False,
) -> bool:
    """spec 11-V1: reject a reserved word or a clash, ignoring case and
    Unicode form, with an active user or bot in this workspace.

    Pass `exclude_user_id` (a profile's own bot user) when checking a
    rename, so a profile keeping its current name never collides with
    itself. Pass `allow_reserved=True` only for ensure_builtin_agents,
    which is the one caller allowed to create Kaki, Ayame, and Matcha
    under their own reserved names; every other caller keeps the
    reserved-word check, so no one else can claim those names.
    """
    normalized = _casefold(name)
    if not allow_reserved and normalized in RESERVED_AGENT_NAMES:
        return False
    existing = UserProfile.objects.filter(realm=realm, is_active=True)
    if exclude_user_id is not None:
        existing = existing.exclude(id=exclude_user_id)
    return not any(
        _casefold(other) == normalized for other in existing.values_list("full_name", flat=True)
    )


def _slugify(name: str) -> str:
    ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
    parts = "".join(character.lower() if character.isalnum() else "-" for character in ascii_name)
    slug = "-".join(part for part in parts.split("-") if part)
    return slug or "agent"


def bot_email_for_agent_name(name: str, realm: Realm) -> tuple[str, str]:
    """spec 11-V2: the bot's short name and email, built from a slug of
    the agent's display name. A 3-character suffix is added to the email
    only when that slug's email is already taken by a deactivated bot;
    the display name itself is never touched here."""
    slug = _slugify(name)
    short_name, email = validate_short_name_and_construct_bot_email(slug, realm)
    if not UserProfile.objects.filter(realm=realm, delivery_email=email).exists():
        return short_name, email
    suffix = secrets.token_hex(2)[:3]
    return validate_short_name_and_construct_bot_email(f"{slug}-{suffix}", realm)
