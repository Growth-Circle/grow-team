"""Calls to Ask first tools that wait for the owner of the grant.

A JSON-RPC call cannot wait, so the call is stored with its exact arguments,
the client is told to retry, and the owner decides in Needs you. Approving
runs the stored arguments, once, as the owner, inside the owner's request.
A retry with the same arguments gets what is stored: the pending notice, or
the receipt of the run.
"""

import hashlib
import json
from copy import copy
from datetime import timedelta
from typing import Any
from uuid import UUID

from django.db import IntegrityError, transaction
from django.http import HttpRequest
from django.utils.timezone import now
from django.utils.translation import gettext as _
from pydantic import ValidationError

from zerver.lib.exceptions import AccessDeniedError, JsonableError, ResourceNotFoundError
from zerver.lib.mcp_access import audit
from zerver.lib.mcp_policy import evaluate_grant
from zerver.lib.mcp_preview import build_preview
from zerver.lib.mcp_tools import TOOLS, execute_tool
from zerver.lib.request import RequestNotes
from zerver.models import AgentRealmSettings, MCPAccessGrant, MCPPendingCall, UserProfile
from zerver.models.clients import get_client

# A retry of a call that was decided this recently gets the stored answer. A
# later call with the same arguments is a new request: sending "ok" twice an
# hour apart is two messages, not a replay.
RECEIPT_WINDOW = timedelta(minutes=10)
# Distinct requests that may wait at once for one grant, so a client cannot
# fill Needs you with them.
MAX_PENDING_PER_GRANT = 50
DEFAULT_APPROVAL_TTL_MINUTES = 120
DIGEST_SHORT_LENGTH = 12


class MCPApprovalConflictError(JsonableError):
    http_status_code = 409


def argument_digest(tool_name: str, args: dict[str, object]) -> str:
    canonical = json.dumps(
        {"tool": tool_name, "arguments": args},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


def approval_ttl(grant: MCPAccessGrant) -> timedelta:
    minutes = (
        AgentRealmSettings.objects.filter(realm_id=grant.realm_id)
        .values_list("approval_ttl_minutes", flat=True)
        .first()
    )
    return timedelta(minutes=minutes or DEFAULT_APPROVAL_TTL_MINUTES)


def _is_current(call: MCPPendingCall) -> bool:
    """Whether a retry should be answered with this call instead of a new one."""
    if call.status == "pending":
        return call.expires_at > now()
    if call.status == "expired":
        return False
    return (call.decided_at or call.created_at) >= now() - RECEIPT_WINDOW


def request_approval(
    grant: MCPAccessGrant, tool_name: str, args: dict[str, object]
) -> MCPPendingCall:
    """The pending call for these arguments: the one already waiting or
    recently decided, else a new one. Raises when the target is not
    reachable by the account, or when too many calls already wait."""
    digest = argument_digest(tool_name, args)
    with transaction.atomic():
        grant = MCPAccessGrant.objects.select_for_update(no_key=True).get(id=grant.id)
        existing = (
            MCPPendingCall.objects.filter(grant=grant, argument_digest=digest)
            .order_by("-created_at")
            .first()
        )
        if existing is not None and _is_current(existing):
            return existing
        if existing is not None and existing.status == "pending":
            existing.status = "expired"
            existing.save(update_fields=["status"])
        waiting = MCPPendingCall.objects.filter(
            grant=grant, status="pending", expires_at__gt=now()
        ).count()
        if waiting >= MAX_PENDING_PER_GRANT:
            raise JsonableError(_("Too many requests are waiting for approval."))
        preview = build_preview(grant.user, tool_name, args)
        try:
            with transaction.atomic():
                call = MCPPendingCall.objects.create(
                    grant=grant,
                    tool_name=tool_name,
                    arguments=args,
                    argument_digest=digest,
                    preview=preview,
                    expires_at=now() + approval_ttl(grant),
                )
        except IntegrityError:
            return MCPPendingCall.objects.get(grant=grant, argument_digest=digest, status="pending")
        audit(
            grant,
            tool_name,
            "asked",
            argument_digest=digest,
            actor=grant.user,
            detail={"pending_call_id": str(call.id)},
        )
        return call


def call_result(call: MCPPendingCall) -> dict[str, Any]:
    """The MCP tool result for a call in its current state."""
    if call.status == "executed":
        return {
            "content": [{"type": "text", "text": json.dumps(call.result, ensure_ascii=False)}],
            "isError": False,
        }
    if call.status == "approved":
        text = (
            "The account owner approved this action and it is running. Retry the same call shortly."
        )
    elif call.status == "pending":
        text = (
            "This action is waiting for the account owner to approve it in Sanji. "
            f"Retry the same call later to get the result. Reference: {call.argument_digest[:DIGEST_SHORT_LENGTH]}."
        )
    elif call.status == "rejected":
        text = "The account owner declined this action."
    else:
        text = "The approved action failed or the account lacks permission."
    return {"content": [{"type": "text", "text": text}], "isError": True}


def _grant_request(request: HttpRequest) -> HttpRequest:
    """A copy of the owner's request that is recorded as the MCP client, so
    what the call sends names Grow Team MCP, as a direct call would."""
    grant_request = copy(request)
    notes = copy(RequestNotes.get_notes(request))
    notes.client = get_client("Grow Team MCP")
    RequestNotes.set_notes(grant_request, notes)
    return grant_request


def decide(
    request: HttpRequest,
    actor: UserProfile,
    call_id: UUID,
    *,
    decision: str,
    digest: str,
    nonce: str,
) -> MCPPendingCall:
    """Approve or reject a pending call. Only the grant's owner decides.

    Approving first claims the call under a row lock, after every check has
    been made again (the digest, the nonce, the expiry, the grant, the
    tool's policy), so a call that changed or lost its permission since it
    was shown does not run, and a second decision finds it already claimed.
    The tool then runs with the stored arguments outside that transaction,
    because the workspace's own views open transactions of their own, and
    the outcome is saved afterwards. A crash in between leaves the call
    approved and never runs it a second time."""
    conflict: str | None = None
    with transaction.atomic():
        call = (
            MCPPendingCall.objects.select_for_update(no_key=True, of=("self",))
            .select_related("grant", "grant__user")
            .filter(id=call_id, grant__realm=actor.realm)
            .first()
        )
        if call is None:
            raise ResourceNotFoundError(_("Approval does not exist."))
        grant = call.grant
        if grant.user_id != actor.id:
            raise AccessDeniedError
        if call.status != "pending":
            raise MCPApprovalConflictError(_("This request was already decided."))
        detail = {"pending_call_id": str(call.id)}
        if call.expires_at <= now():
            call.status = "expired"
            call.decided_at = now()
            call.save(update_fields=["status", "decided_at"])
            conflict = _("This request has expired.")
        elif (
            digest != call.argument_digest
            or nonce != str(call.nonce)
            or argument_digest(call.tool_name, call.arguments) != call.argument_digest
        ):
            raise MCPApprovalConflictError(_("This request changed. Reload and review it again."))
        elif decision == "reject":
            call.status = "rejected"
            call.decided_at = now()
            call.save(update_fields=["status", "decided_at"])
            audit(
                grant,
                call.tool_name,
                "declined",
                argument_digest=call.argument_digest,
                actor=actor,
                detail=detail,
            )
        elif (
            grant.revoked_at is not None
            or grant.expires_at <= now()
            or not grant.user.is_active
            or call.tool_name not in TOOLS
            or evaluate_grant(grant, call.tool_name) == "deny"
        ):
            call.status = "rejected"
            call.decided_at = now()
            call.save(update_fields=["status", "decided_at"])
            audit(
                grant,
                call.tool_name,
                "blocked",
                argument_digest=call.argument_digest,
                actor=actor,
                detail={**detail, "reason": "no_longer_allowed"},
            )
            conflict = _("This connection can no longer run this tool.")
        else:
            call.status = "approved"
            call.decided_at = now()
            call.save(update_fields=["status", "decided_at"])
            audit(
                grant,
                call.tool_name,
                "approved",
                argument_digest=call.argument_digest,
                actor=actor,
                detail=detail,
            )
    if conflict is not None:
        raise MCPApprovalConflictError(conflict)
    if call.status != "approved":
        return call
    try:
        output = execute_tool(_grant_request(request), grant.user, call.tool_name, call.arguments)
    except (JsonableError, ValueError, ValidationError):
        status, result, outcome = "failed", {"error": True}, "rejected"
    else:
        status, result, outcome = "executed", output, "executed"
    with transaction.atomic():
        MCPPendingCall.objects.filter(id=call.id, status="approved").update(
            status=status, result=result
        )
        audit(
            grant,
            call.tool_name,
            outcome,
            argument_digest=call.argument_digest,
            actor=actor,
            detail=detail,
        )
    call.refresh_from_db()
    return call
