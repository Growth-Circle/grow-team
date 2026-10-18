"""Credentials for external clients that access this workspace."""

from uuid import uuid4

from django.conf import settings
from django.db import models
from django.utils.timezone import now

from zerver.models.mcp import MCP_TOOL_POLICIES
from zerver.models.realms import Realm
from zerver.models.users import UserProfile

MCP_ACCESS_POLICY_MODES = ["managed", "legacy"]
MCP_PENDING_CALL_STATUSES = ["pending", "approved", "executed", "failed", "rejected", "expired"]


class MCPClient(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid4, editable=False)
    realm = models.ForeignKey(Realm, on_delete=models.CASCADE)
    name = models.CharField(max_length=100)
    redirect_uris = models.JSONField(default=list)
    created_at = models.DateTimeField(default=now)


class MCPAccessGrant(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid4, editable=False)
    realm = models.ForeignKey(Realm, on_delete=models.CASCADE)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    client = models.ForeignKey(MCPClient, null=True, on_delete=models.CASCADE)
    name = models.CharField(max_length=100)
    scopes = models.JSONField(default=list)
    resource = models.URLField(max_length=2048)
    created_at = models.DateTimeField(default=now)
    expires_at = models.DateTimeField()
    revoked_at = models.DateTimeField(null=True, default=None)
    last_used_at = models.DateTimeField(null=True, default=None)
    # A grant that exists when per-tool policies ship is "legacy" and keeps
    # the access its scopes gave it; the database default is what marks
    # those rows, so a new grant is "managed".
    policy_mode = models.CharField(
        max_length=10,
        choices=[(value, value) for value in MCP_ACCESS_POLICY_MODES],
        default="managed",
        db_default="legacy",
    )

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(policy_mode__in=MCP_ACCESS_POLICY_MODES),
                name="mcp_access_grant_policy_mode_valid",
            ),
        ]


class MCPAccessToolPolicy(models.Model):
    """What one tool may do for a managed grant: allow (Auto), approve (Ask
    first), or block (Off). A tool with no row has its class default."""

    grant = models.ForeignKey(
        MCPAccessGrant, on_delete=models.CASCADE, related_name="tool_policies"
    )
    tool_name = models.CharField(max_length=100)
    policy = models.CharField(
        max_length=20, choices=[(value, value) for value in MCP_TOOL_POLICIES]
    )
    # manage.py delete_realm removes UserProfile rows outright, and an old
    # image does not know this table exists, so this reference carries no
    # database-level constraint.
    updated_by = models.ForeignKey(
        UserProfile, on_delete=models.SET_NULL, null=True, db_constraint=False, related_name="+"
    )
    updated_at = models.DateTimeField(default=now)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["grant", "tool_name"], name="mcp_access_tool_policy_unique"
            ),
            models.CheckConstraint(
                condition=models.Q(policy__in=MCP_TOOL_POLICIES),
                name="mcp_access_tool_policy_valid",
            ),
        ]


class MCPAuthorizationCode(models.Model):
    digest = models.CharField(max_length=64, primary_key=True)
    grant = models.ForeignKey(MCPAccessGrant, on_delete=models.CASCADE)
    redirect_uri = models.URLField(max_length=2048)
    challenge = models.CharField(max_length=128)
    expires_at = models.DateTimeField()
    consumed_at = models.DateTimeField(null=True, default=None)


class MCPAccessToken(models.Model):
    digest = models.CharField(max_length=64, primary_key=True)
    grant = models.ForeignKey(MCPAccessGrant, on_delete=models.CASCADE)
    kind = models.CharField(max_length=10, choices=[("access", "access"), ("refresh", "refresh")])
    expires_at = models.DateTimeField()
    consumed_at = models.DateTimeField(null=True, default=None)


class MCPAccessAudit(models.Model):
    grant = models.ForeignKey(MCPAccessGrant, on_delete=models.CASCADE)
    tool_name = models.CharField(max_length=100)
    outcome = models.CharField(max_length=20)
    created_at = models.DateTimeField(default=now)
    # A hash of the call's arguments, never the arguments themselves.
    argument_digest = models.CharField(max_length=64, default="", db_default="")
    # The person who decided, when a person did. See MCPAccessToolPolicy.updated_by
    # for why this reference has no constraint.
    actor = models.ForeignKey(
        UserProfile, on_delete=models.SET_NULL, null=True, db_constraint=False, related_name="+"
    )
    # Facts about the entry that are not content, such as the pending call id.
    detail = models.JSONField(default=dict, db_default={})


class MCPPendingCall(models.Model):
    """A call to an Ask first tool that waits for the grant's owner. It holds
    the exact arguments, so what the owner approves is what runs."""

    id = models.UUIDField(primary_key=True, default=uuid4, editable=False)
    grant = models.ForeignKey(
        MCPAccessGrant, on_delete=models.CASCADE, related_name="pending_calls"
    )
    tool_name = models.CharField(max_length=100)
    arguments = models.JSONField()
    argument_digest = models.CharField(max_length=64)
    preview = models.JSONField(default=dict)
    status = models.CharField(
        max_length=20,
        choices=[(value, value) for value in MCP_PENDING_CALL_STATUSES],
        default="pending",
    )
    # What the tool returned, or {"error": true} when it failed. A client
    # that retries the call gets this back instead of a second run.
    result = models.JSONField(null=True, default=None)
    nonce = models.UUIDField(default=uuid4, editable=False)
    created_at = models.DateTimeField(default=now)
    expires_at = models.DateTimeField()
    decided_at = models.DateTimeField(null=True, default=None)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["grant", "argument_digest"],
                condition=models.Q(status="pending"),
                name="mcp_pending_call_one_open_per_digest",
            ),
            models.CheckConstraint(
                condition=models.Q(status__in=MCP_PENDING_CALL_STATUSES),
                name="mcp_pending_call_status_valid",
            ),
        ]
