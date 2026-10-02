"""OAuth consent and connection management for external MCP clients."""

import json
import re
import secrets
from datetime import timedelta
from typing import Annotated, Literal
from urllib.parse import urlencode
from uuid import UUID

from django.db import transaction
from django.db.models import Q
from django.http import HttpRequest, HttpResponse, HttpResponseRedirect, JsonResponse
from django.shortcuts import render
from django.utils.timezone import now
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.debug import sensitive_post_parameters, sensitive_variables
from django.views.decorators.http import require_GET, require_http_methods, require_POST
from pydantic import StringConstraints

from zerver.decorator import zulip_login_required
from zerver.lib.mcp_access import (
    SCOPES,
    digest,
    issue_pair,
    issue_token,
    parse_scopes,
    pkce_matches,
    request_realm,
    resource_url,
    valid_grant,
    valid_redirect,
)
from zerver.lib.rate_limiter import rate_limit_request_by_ip
from zerver.lib.response import json_success
from zerver.lib.typed_endpoint import typed_endpoint
from zerver.models import UserProfile
from zerver.models.mcp_access import (
    MCPAccessAudit,
    MCPAccessGrant,
    MCPAccessToken,
    MCPAuthorizationCode,
    MCPClient,
)


def oauth_json(data: dict[str, object], status: int = 200) -> JsonResponse:
    response = JsonResponse(data, status=status)
    response["Cache-Control"] = "no-store"
    response["Pragma"] = "no-cache"
    response["Access-Control-Allow-Origin"] = "*"
    return response


@require_GET
def protected_metadata(request: HttpRequest) -> HttpResponse:
    resource = resource_url(request)
    return oauth_json(
        {
            "resource": resource,
            "authorization_servers": [resource.removesuffix("/mcp")],
            "scopes_supported": SCOPES,
            "bearer_methods_supported": ["header"],
            "resource_name": "Grow Team",
        }
    )


@require_GET
def authorization_metadata(request: HttpRequest) -> HttpResponse:
    origin = resource_url(request).removesuffix("/mcp")
    return oauth_json(
        {
            "issuer": origin,
            "authorization_endpoint": origin + "/mcp/authorize",
            "token_endpoint": origin + "/mcp/token",
            "registration_endpoint": origin + "/mcp/register",
            "revocation_endpoint": origin + "/mcp/revoke",
            "response_types_supported": ["code"],
            "grant_types_supported": ["authorization_code", "refresh_token"],
            "token_endpoint_auth_methods_supported": ["none"],
            "code_challenge_methods_supported": ["S256"],
            "scopes_supported": SCOPES,
        }
    )


@csrf_exempt
@require_POST
def register_client(request: HttpRequest) -> HttpResponse:
    rate_limit_request_by_ip(request, "agent_pairing_by_ip")
    if len(request.body) > 16384:
        return oauth_json({"error": "invalid_client_metadata"}, 400)
    try:
        data = json.loads(request.body)
        if not isinstance(data, dict):
            raise ValueError
        redirects = data.get("redirect_uris")
        name = data.get("client_name", "MCP client")
        if (
            not isinstance(redirects, list)
            or not 1 <= len(redirects) <= 10
            or not all(valid_redirect(uri) for uri in redirects)
            or not isinstance(name, str)
            or not 1 <= len(name.strip()) <= 100
            or data.get("token_endpoint_auth_method", "none") != "none"
            or not set(data.get("grant_types", ["authorization_code", "refresh_token"]))
            <= {"authorization_code", "refresh_token"}
            or data.get("response_types", ["code"]) != ["code"]
        ):
            raise ValueError
    except (ValueError, TypeError, UnicodeDecodeError):
        return oauth_json({"error": "invalid_client_metadata"}, 400)
    client = MCPClient.objects.create(
        realm=request_realm(request), name=name.strip(), redirect_uris=redirects
    )
    return oauth_json(
        {
            "client_id": str(client.id),
            "client_name": client.name,
            "redirect_uris": redirects,
            "token_endpoint_auth_method": "none",
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
            "client_id_issued_at": int(now().timestamp()),
        },
        201,
    )


@sensitive_variables()
def authorization_request(request: HttpRequest) -> tuple[MCPClient, dict[str, str], list[str]]:
    data = request.POST if request.method == "POST" else request.GET
    if any(len(data.getlist(key)) != 1 for key in data):
        raise ValueError("invalid_request")
    params = {
        key: data.get(key, "")
        for key in [
            "client_id",
            "redirect_uri",
            "response_type",
            "code_challenge",
            "code_challenge_method",
            "scope",
            "state",
            "resource",
        ]
    }
    try:
        client = MCPClient.objects.get(id=UUID(params["client_id"]), realm=request_realm(request))
    except (ValueError, MCPClient.DoesNotExist):
        raise ValueError("invalid_client") from None
    if params["redirect_uri"] not in client.redirect_uris or not valid_redirect(
        params["redirect_uri"]
    ):
        raise ValueError("invalid_redirect_uri")
    if (
        params["response_type"] != "code"
        or params["code_challenge_method"] != "S256"
        or re.fullmatch(r"[A-Za-z0-9_-]{43}", params["code_challenge"]) is None
    ):
        raise ValueError("invalid_request")
    if params["resource"] and params["resource"] != resource_url(request):
        raise ValueError("invalid_target")
    if len(params["state"]) > 2048:
        raise ValueError("invalid_request")
    return client, params, parse_scopes(params["scope"])


@zulip_login_required
@require_http_methods(["GET", "POST"])
@sensitive_variables()
def authorize(request: HttpRequest) -> HttpResponse:
    try:
        client, params, scopes = authorization_request(request)
    except ValueError as error:
        return oauth_json({"error": str(error)}, 400)
    user = request.user
    assert isinstance(user, UserProfile)
    if user.realm_id != client.realm_id or user.is_bot or user.is_guest:
        return oauth_json({"error": "access_denied"}, 403)
    if request.method == "GET":
        response = render(
            request,
            "zerver/mcp_consent.html",
            {
                "client_name": client.name,
                "callback_host": params["redirect_uri"].split("/")[2],
                "params": params,
                "can_write": "team:write" in scopes,
                "account_name": user.full_name,
                "workspace_name": user.realm.name,
            },
        )
        response["Cache-Control"] = "no-store"
        response["Referrer-Policy"] = "no-referrer"
        return response
    decision = request.POST.get("decision")
    if decision not in {"approve", "deny"}:
        return oauth_json({"error": "invalid_request"}, 400)
    query = {"state": params["state"]}
    if decision == "deny":
        query["error"] = "access_denied"
    else:
        code = secrets.token_urlsafe(48)
        with transaction.atomic():
            grant = MCPAccessGrant.objects.create(
                realm=user.realm,
                user=user,
                client=client,
                name=client.name,
                scopes=scopes,
                resource=resource_url(request),
                expires_at=now() + timedelta(days=90),
            )
            MCPAuthorizationCode.objects.create(
                digest=digest(code),
                grant=grant,
                redirect_uri=params["redirect_uri"],
                challenge=params["code_challenge"],
                expires_at=now() + timedelta(minutes=5),
            )
            MCPAccessAudit.objects.create(grant=grant, tool_name="consent", outcome="approved")
        query["code"] = code
    separator = "&" if "?" in params["redirect_uri"] else "?"
    response = HttpResponseRedirect(params["redirect_uri"] + separator + urlencode(query))
    response["Cache-Control"] = "no-store"
    response["Referrer-Policy"] = "no-referrer"
    return response


@csrf_exempt
@require_POST
@sensitive_post_parameters("code", "code_verifier", "refresh_token")
@sensitive_variables()
def token(request: HttpRequest) -> HttpResponse:
    rate_limit_request_by_ip(request, "agent_pairing_by_ip")
    data = request.POST
    if len(request.body) > 16384 or any(len(data.getlist(key)) != 1 for key in data):
        return oauth_json({"error": "invalid_request"}, 400)
    try:
        client_id = UUID(data.get("client_id", ""))
    except ValueError:
        return oauth_json({"error": "invalid_client"}, 400)
    if data.get("resource") and data["resource"] != resource_url(request):
        return oauth_json({"error": "invalid_target"}, 400)
    with transaction.atomic():
        if data.get("grant_type") == "authorization_code":
            code = MCPAuthorizationCode.objects.filter(digest=digest(data.get("code", ""))).first()
            if code is None:
                return oauth_json({"error": "invalid_grant"}, 400)
            grant = (
                MCPAccessGrant.objects.select_for_update()
                .select_related("user", "realm")
                .get(id=code.grant_id)
            )
            code.refresh_from_db()
            if (
                not valid_grant(grant, request)
                or grant.client_id != client_id
                or code.consumed_at is not None
                or code.expires_at <= now()
                or code.redirect_uri != data.get("redirect_uri")
                or not pkce_matches(data.get("code_verifier", ""), code.challenge)
            ):
                return oauth_json({"error": "invalid_grant"}, 400)
            code.consumed_at = now()
            code.save(update_fields=["consumed_at"])
        elif data.get("grant_type") == "refresh_token":
            refresh = MCPAccessToken.objects.filter(
                digest=digest(data.get("refresh_token", "")), kind="refresh"
            ).first()
            if refresh is None:
                return oauth_json({"error": "invalid_grant"}, 400)
            grant = (
                MCPAccessGrant.objects.select_for_update()
                .select_related("user", "realm")
                .get(id=refresh.grant_id)
            )
            refresh.refresh_from_db()
            if (
                not valid_grant(grant, request)
                or grant.client_id != client_id
                or refresh.expires_at <= now()
            ):
                return oauth_json({"error": "invalid_grant"}, 400)
            if refresh.consumed_at is not None:
                grant.revoked_at = now()
                grant.save(update_fields=["revoked_at"])
                return oauth_json({"error": "invalid_grant"}, 400)
            if data.get("scope") and set(data["scope"].split()) != set(grant.scopes):
                return oauth_json({"error": "invalid_scope"}, 400)
            refresh.consumed_at = now()
            refresh.save(update_fields=["consumed_at"])
        else:
            return oauth_json({"error": "unsupported_grant_type"}, 400)
        return oauth_json(issue_pair(grant))


@csrf_exempt
@require_POST
@sensitive_post_parameters("token")
@sensitive_variables()
def revoke_token(request: HttpRequest) -> HttpResponse:
    rate_limit_request_by_ip(request, "agent_pairing_by_ip")
    value = MCPAccessToken.objects.filter(digest=digest(request.POST.get("token", ""))).first()
    if value is not None:
        with transaction.atomic():
            grant = (
                MCPAccessGrant.objects.select_for_update()
                .select_related("user", "realm")
                .get(id=value.grant_id)
            )
            if valid_grant(grant, request) and str(grant.client_id) == request.POST.get(
                "client_id"
            ):
                grant.revoked_at = now()
                grant.save(update_fields=["revoked_at"])
    return oauth_json({})


def list_access(request: HttpRequest, user_profile: UserProfile) -> HttpResponse:
    personal = MCPAccessGrant.objects.filter(realm=user_profile.realm, user=user_profile)
    recent = personal.order_by("-created_at").values("id")[:100]
    connections = personal.filter(
        Q(revoked_at__isnull=True, expires_at__gt=now()) | Q(id__in=recent)
    ).order_by("revoked_at", "-created_at")
    return json_success(
        request,
        data={
            "url": resource_url(request),
            "connections": [
                {
                    "id": str(grant.id),
                    "name": grant.name,
                    "method": "oauth" if grant.client_id else "token",
                    "scopes": grant.scopes,
                    "created_at": grant.created_at.isoformat(),
                    "expires_at": grant.expires_at.isoformat(),
                    "last_used_at": grant.last_used_at.isoformat() if grant.last_used_at else None,
                    "revoked": grant.revoked_at is not None,
                    "expired": grant.expires_at <= now(),
                }
                for grant in connections
            ],
        },
    )


@typed_endpoint
@sensitive_variables()
def create_access(
    request: HttpRequest,
    user_profile: UserProfile,
    *,
    name: Annotated[str, StringConstraints(min_length=1, max_length=100, strip_whitespace=True)],
    write: Literal["true", "false"] = "false",
) -> HttpResponse:
    if user_profile.is_bot or user_profile.is_guest:
        return oauth_json({"error": "access_denied"}, 403)
    if not name or len(name) > 100 or write not in {"true", "false"}:
        return oauth_json({"error": "invalid_request"}, 400)
    with transaction.atomic():
        grant = MCPAccessGrant.objects.create(
            realm=user_profile.realm,
            user=user_profile,
            name=name,
            scopes=SCOPES if write == "true" else ["team:read"],
            resource=resource_url(request),
            expires_at=now() + timedelta(days=90),
        )
        credential = issue_token(grant, "access", timedelta(days=90))
        MCPAccessAudit.objects.create(grant=grant, tool_name="token.create", outcome="approved")
    response = json_success(
        request,
        data={"id": str(grant.id), "token": credential, "expires_at": grant.expires_at.isoformat()},
    )
    response["Cache-Control"] = "no-store"
    return response


def revoke_access(request: HttpRequest, user_profile: UserProfile, grant_id: UUID) -> HttpResponse:
    with transaction.atomic():
        grant = (
            MCPAccessGrant.objects.select_for_update()
            .filter(id=grant_id, user=user_profile, realm=user_profile.realm)
            .first()
        )
        if grant is None:
            return oauth_json({"error": "not_found"}, 404)
        grant.revoked_at = now()
        grant.save(update_fields=["revoked_at"])
        MCPAccessAudit.objects.create(grant=grant, tool_name="token.revoke", outcome="revoked")
    return json_success(request)
