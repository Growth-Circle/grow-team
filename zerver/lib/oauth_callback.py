# Helper for third-party OAuth connect flows (Google, GitHub, OpenRouter,
# MCP) that must complete on a single fixed host (OAUTH_CALLBACK_HOST)
# regardless of which realm host started the flow. A workspace can live on
# any host, but each provider is registered with one fixed redirect URI, and
# a session-bound `state` value cannot survive the hop between two hosts.
#
# `state` is instead a signed, stateless token carrying everything the
# callback needs (realm, user, provider, and where to send the browser
# back), plus a nonce that a cache entry marks used on first exchange.
import secrets
from collections.abc import Callable
from dataclasses import dataclass

from django.conf import settings
from django.core.cache import cache
from django.core.signing import BadSignature, SignatureExpired, dumps, loads
from django.http import HttpRequest

from zerver.models import Realm, UserProfile

STATE_SALT = "zerver.lib.oauth_callback.state"
STATE_MAX_AGE_SECONDS = 10 * 60
NONCE_CACHE_PREFIX = "oauth_callback_nonce:"
CODE_VERIFIER_CACHE_PREFIX = "oauth_callback_code_verifier:"


class OAuthStateError(Exception):
    """The `state` query parameter could not be verified."""


@dataclass(frozen=True)
class OAuthState:
    realm_id: int
    user_id: int
    provider: str
    return_hash: str
    nonce: str
    # Set only when the connect flow started with PKCE (sign_state's
    # code_verifier argument); an exchanger that needs it for the token
    # request reads it from here instead of threading it through `state`
    # itself, which stays a fixed-shape, provider-agnostic token.
    code_verifier: str | None


def callback_url(realm: Realm, provider: str) -> str:
    """The redirect URI to register with `provider`: this always points at
    the fixed callback host (or, with no such host configured, at the
    calling realm's own host, for a single-host dev setup)."""
    host = settings.OAUTH_CALLBACK_HOST or realm.host
    return f"{settings.EXTERNAL_URI_SCHEME}{host}/oauth/callback/{provider}"


def sign_state(
    realm: Realm,
    user: UserProfile,
    provider: str,
    return_hash: str,
    *,
    code_verifier: str | None = None,
) -> str:
    """Build the signed `state` value a connect button sends the provider.

    `return_hash` must be a URL fragment (starting with "#"): the callback
    appends it directly to the trusted realm URL to build the redirect
    target, and only a fragment cannot change which host or path that
    redirect lands on.
    """
    if not return_hash.startswith("#"):
        raise ValueError("return_hash must be a URL fragment starting with '#'.")
    nonce = secrets.token_urlsafe(18)
    if code_verifier is not None:
        cache.set(CODE_VERIFIER_CACHE_PREFIX + nonce, code_verifier, STATE_MAX_AGE_SECONDS)
    payload = {
        "realm_id": realm.id,
        "user_id": user.id,
        "provider": provider,
        "return_hash": return_hash,
        "nonce": nonce,
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
            nonce=nonce,
            code_verifier=cache.get(CODE_VERIFIER_CACHE_PREFIX + nonce),
        )
    except KeyError:
        raise OAuthStateError("invalid")


ExchangeCallback = Callable[[HttpRequest, str, UserProfile, OAuthState], None]

_exchangers: dict[str, ExchangeCallback] = {}


def register(provider: str, exchange: ExchangeCallback) -> None:
    """Register the function that turns an OAuth `code` for `provider`
    into a stored credential. Called once at import time by the feature
    that owns that provider."""
    assert provider not in _exchangers, f"OAuth provider already registered: {provider}"
    _exchangers[provider] = exchange


def get_exchanger(provider: str) -> ExchangeCallback | None:
    return _exchangers.get(provider)
