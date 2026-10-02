"""OAuth credentials are bound to one user, realm, and MCP resource."""

import base64
import hashlib
import re
import secrets
from datetime import timedelta
from urllib.parse import urlsplit

from django.http import HttpRequest
from django.utils.timezone import now
from django.views.decorators.debug import sensitive_variables

from zerver.lib.subdomains import get_subdomain
from zerver.models.mcp_access import MCPAccessGrant, MCPAccessToken
from zerver.models.realms import Realm, get_realm

SCOPES = ["team:read", "team:write"]
VERSIONS = ["2024-11-05", "2025-03-26", "2025-06-18", "2025-11-25"]


@sensitive_variables()
def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def request_realm(request: HttpRequest) -> Realm:
    return get_realm(get_subdomain(request))


def resource_url(request: HttpRequest) -> str:
    return request_realm(request).url.rstrip("/") + "/mcp"


def parse_scopes(value: str) -> list[str]:
    scopes = value.split() or ["team:read"]
    if not set(scopes) <= set(SCOPES) or "team:read" not in scopes:
        raise ValueError("invalid_scope")
    return sorted(set(scopes))


def valid_redirect(uri: object) -> bool:
    if not isinstance(uri, str) or len(uri) > 2048 or any(ord(c) < 33 for c in uri):
        return False
    try:
        parsed = urlsplit(uri)
        return bool(
            parsed.hostname
            and not parsed.username
            and not parsed.password
            and not parsed.fragment
            and (
                parsed.scheme == "https"
                or (
                    parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost", "::1"}
                )
            )
        )
    except ValueError:
        return False


def valid_grant(grant: MCPAccessGrant, request: HttpRequest) -> bool:
    return bool(
        grant.revoked_at is None
        and grant.expires_at > now()
        and grant.realm_id == request_realm(request).id
        and grant.resource == resource_url(request)
        and grant.user.realm_id == grant.realm_id
        and grant.user.is_active
        and not grant.user.is_bot
        and not grant.realm.deactivated
    )


@sensitive_variables()
def issue_token(grant: MCPAccessGrant, kind: str, lifetime: timedelta) -> str:
    token = "gtm_" + secrets.token_urlsafe(48)
    MCPAccessToken.objects.create(
        digest=digest(token),
        grant=grant,
        kind=kind,
        expires_at=min(now() + lifetime, grant.expires_at),
    )
    return token


@sensitive_variables()
def issue_pair(grant: MCPAccessGrant) -> dict[str, object]:
    return {
        "access_token": issue_token(grant, "access", timedelta(hours=1)),
        "refresh_token": issue_token(grant, "refresh", timedelta(days=30)),
        "token_type": "Bearer",
        "expires_in": min(3600, max(0, int((grant.expires_at - now()).total_seconds()))),
        "scope": " ".join(grant.scopes),
    }


@sensitive_variables()
def pkce_matches(verifier: str, challenge: str) -> bool:
    if re.fullmatch(r"[A-Za-z0-9._~-]{43,128}", verifier) is None:
        return False
    calculated = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    )
    return secrets.compare_digest(calculated, challenge)
