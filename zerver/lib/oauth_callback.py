# Helper for third-party OAuth connect flows (Google, GitHub, OpenRouter,
# MCP) that must complete on a single fixed host (OAUTH_CALLBACK_HOST)
# regardless of which realm host started the flow. A workspace can live on
# any host, but each provider is registered with one fixed redirect URI, and
# a session-bound `state` value cannot survive the hop between two hosts.
#
# `state` is instead a signed, stateless token carrying everything the
# callback needs (realm, user, provider, and where to send the browser
# back), plus a nonce that a cache entry marks used on first exchange. See
# PLAN.md Sanji WP04 and critic finding 44.
import secrets
from collections.abc import Callable
from dataclasses import dataclass

from django.core.cache import cache
from django.core.signing import BadSignature, SignatureExpired, dumps, loads
from django.http import HttpRequest

from zerver.models import Realm, UserProfile

STATE_SALT = "sanji.oauth_callback.state"
STATE_MAX_AGE_SECONDS = 10 * 60
NONCE_CACHE_PREFIX = "sanji_oauth_callback_nonce:"


class OAuthStateError(Exception):
    """The `state` query parameter could not be verified."""


@dataclass(frozen=True)
class OAuthState:
    realm_id: int
    user_id: int
    provider: str
    return_hash: str


def sign_state(realm: Realm, user: UserProfile, provider: str, return_hash: str) -> str:
    """Build the signed `state` value a connect button sends the provider."""
    payload = {
        "realm_id": realm.id,
        "user_id": user.id,
        "provider": provider,
        "return_hash": return_hash,
        "nonce": secrets.token_urlsafe(18),
    }
    return dumps(payload, salt=STATE_SALT)


def verify_state(state: str, *, provider: str) -> OAuthState:
    """Decode and consume a `state` value. Raises OAuthStateError if the
    signature is invalid, the token expired, the provider does not match
    the one it was signed for, or the nonce was already used."""
    try:
        payload = loads(state, salt=STATE_SALT, max_age=STATE_MAX_AGE_SECONDS)
    except SignatureExpired:
        raise OAuthStateError("expired")
    except BadSignature:
        raise OAuthStateError("invalid")

    if not isinstance(payload, dict) or payload.get("provider") != provider:
        raise OAuthStateError("invalid")

    nonce = payload.get("nonce")
    if not isinstance(nonce, str) or not nonce:
        raise OAuthStateError("invalid")
    # cache.add only succeeds the first time a key is set, so a replayed
    # nonce (still within its expiry window) is rejected here.
    if not cache.add(NONCE_CACHE_PREFIX + nonce, True, STATE_MAX_AGE_SECONDS):
        raise OAuthStateError("reused")

    try:
        return OAuthState(
            realm_id=payload["realm_id"],
            user_id=payload["user_id"],
            provider=payload["provider"],
            return_hash=payload["return_hash"],
        )
    except KeyError:
        raise OAuthStateError("invalid")


ExchangeCallback = Callable[[HttpRequest, str, OAuthState], None]

_exchangers: dict[str, ExchangeCallback] = {}


def register(provider: str, exchange: ExchangeCallback) -> None:
    """Register the function that turns an OAuth `code` for `provider`
    into a stored credential. Called once at import time by the feature
    that owns that provider (WP25, WP36, WP44)."""
    assert provider not in _exchangers, f"OAuth provider already registered: {provider}"
    _exchangers[provider] = exchange


def get_exchanger(provider: str) -> ExchangeCallback | None:
    return _exchangers.get(provider)
