"""Credentials for external clients that access this workspace."""

from uuid import uuid4

from django.conf import settings
from django.db import models
from django.utils.timezone import now

from zerver.models.mcp import MCP_TOOL_POLICIES
from zerver.models.realms import Realm
from zerver.models.users import UserProfile

MCP_ACCESS_POLICY_MODES = ["managed", "legacy"]


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
