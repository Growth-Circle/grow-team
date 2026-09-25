"""MCP tool catalog: a server a workspace connects to, the connections built
on it (workspace, room, or personal scope), the per-tool policy on each
connection, which agents a connection is granted to, and the tool-call plan
a job proposes for approval. WP44 builds the gateway that uses these tables.
"""

from django.db import models
from django.utils.timezone import now as timezone_now

from zerver.models.agents import AgentJob, AgentProfile, AgentSecret
from zerver.models.realms import Realm
from zerver.models.streams import Stream
from zerver.models.users import UserProfile

MCP_CONNECTION_SCOPES = ["workspace", "room", "personal"]
MCP_TOOL_POLICIES = ["allow", "approve", "block"]
MCP_JOB_PLAN_STATUSES = ["proposed", "approved", "rejected"]


MCP_AUTH_MODES = ["oauth", "header", "none"]
MCP_CONNECTION_STATUSES = ["pending", "active", "needs_review", "revoked"]


class McpServer(models.Model):
    """A server a workspace can connect to: a known catalog slug, or a
    custom self-hosted one."""

    realm = models.ForeignKey(Realm, on_delete=models.CASCADE)
    slug = models.CharField(max_length=60)
    name = models.CharField(max_length=100)
    base_url = models.URLField(max_length=2048, default="")
    auth_mode = models.CharField(
        max_length=20,
        choices=[(value, value) for value in MCP_AUTH_MODES],
        default="oauth",
    )
    # True for a catalog server sanji vetted; False for a custom server the
    # workspace pointed at itself (spec 16 T-10).
    verified = models.BooleanField(default=False, db_default=False)
    version_pin = models.CharField(max_length=40, default="", db_default="")
    supported_scopes = models.JSONField(default=list, db_default=[])
    # manage.py delete_realm removes UserProfile rows outright, and an old
    # image does not know this table exists, so this reference carries no
    # database-level constraint.
    added_by = models.ForeignKey(UserProfile, on_delete=models.PROTECT, db_constraint=False)
    created_at = models.DateTimeField(default=timezone_now)
    disabled_at = models.DateTimeField(null=True, default=None)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["realm", "slug"], name="mcp_server_realm_slug_unique"),
            models.CheckConstraint(
                condition=models.Q(auth_mode__in=MCP_AUTH_MODES),
                name="mcp_server_auth_mode_valid",
            ),
        ]


class McpConnection(models.Model):
    realm = models.ForeignKey(Realm, on_delete=models.CASCADE)
    server = models.ForeignKey(McpServer, on_delete=models.CASCADE, related_name="connections")
    scope = models.CharField(
        max_length=20,
        choices=[(value, value) for value in MCP_CONNECTION_SCOPES],
        default="workspace",
    )
    # Set only for scope="room".
    stream = models.ForeignKey(Stream, on_delete=models.CASCADE, null=True)
    # Set only for scope="personal". manage.py delete_realm removes
    # UserProfile rows outright, and an old image does not know this table
    # exists, so this reference carries no database-level constraint.
    user = models.ForeignKey(
        UserProfile,
        on_delete=models.CASCADE,
        null=True,
        db_constraint=False,
        related_name="mcp_connections",
    )
    credential = models.ForeignKey(AgentSecret, on_delete=models.PROTECT, null=True)
    # pending until WP44 verifies the tool manifest; needs_review when a
    # verified connection's manifest drifts (spec 16 step 7).
    status = models.CharField(
        max_length=20,
        choices=[(value, value) for value in MCP_CONNECTION_STATUSES],
        default="pending",
    )
    tool_manifest = models.JSONField(default=list)
    tool_manifest_hash = models.CharField(max_length=64, default="")
    rate_limit_per_minute = models.PositiveIntegerField(null=True, default=None)
    # manage.py delete_realm removes UserProfile rows outright, and an old
    # image does not know this table exists, so this reference carries no
    # database-level constraint.
    created_by = models.ForeignKey(
        UserProfile, on_delete=models.PROTECT, db_constraint=False, related_name="+"
    )
    created_at = models.DateTimeField(default=timezone_now)
    removed_at = models.DateTimeField(null=True, default=None)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(scope__in=MCP_CONNECTION_SCOPES),
                name="mcp_connection_scope_valid",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(scope="workspace", stream__isnull=True, user__isnull=True)
                    | models.Q(scope="room", stream__isnull=False, user__isnull=True)
                    | models.Q(scope="personal", stream__isnull=True, user__isnull=False)
                ),
                name="mcp_connection_scope_target_valid",
            ),
            models.CheckConstraint(
                condition=models.Q(status__in=MCP_CONNECTION_STATUSES),
                name="mcp_connection_status_valid",
            ),
        ]


class McpToolPolicy(models.Model):
    connection = models.ForeignKey(
        McpConnection, on_delete=models.CASCADE, related_name="tool_policies"
    )
    tool_name = models.CharField(max_length=200)
    policy = models.CharField(
        max_length=20, choices=[(value, value) for value in MCP_TOOL_POLICIES], default="block"
    )
    is_new = models.BooleanField(default=False)
    # Server-reported hints such as {"read_only": true, "destructive": false},
    # shown next to the tool so a reviewer can judge it.
    hints = models.JSONField(default=dict, db_default={})
    reviewed_at = models.DateTimeField(null=True, default=None)
    updated_at = models.DateTimeField(default=timezone_now)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["connection", "tool_name"], name="mcp_tool_policy_unique"
            ),
            models.CheckConstraint(
                condition=models.Q(policy__in=MCP_TOOL_POLICIES), name="mcp_tool_policy_valid"
            ),
        ]


class McpAgentGrant(models.Model):
    connection = models.ForeignKey(
        McpConnection, on_delete=models.CASCADE, related_name="agent_grants"
    )
    agent_profile = models.ForeignKey(
        AgentProfile, on_delete=models.CASCADE, related_name="mcp_grants"
    )
    created_at = models.DateTimeField(default=timezone_now)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["connection", "agent_profile"], name="mcp_agent_grant_unique"
            ),
        ]


class McpJobPlan(models.Model):
    """The tool-call budget a job proposes ("Rencana") for a human to
    approve. tools maps a tool name to the maximum number of calls."""

    job = models.OneToOneField(AgentJob, on_delete=models.CASCADE, related_name="mcp_plan")
    tools = models.JSONField(default=dict)
    status = models.CharField(
        max_length=20,
        choices=[(value, value) for value in MCP_JOB_PLAN_STATUSES],
        default="proposed",
    )
    # manage.py delete_realm removes UserProfile rows outright, and an old
    # image does not know this table exists, so this reference carries no
    # database-level constraint.
    approved_by = models.ForeignKey(
        UserProfile, on_delete=models.SET_NULL, null=True, db_constraint=False
    )
    approved_at = models.DateTimeField(null=True, default=None)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(status__in=MCP_JOB_PLAN_STATUSES),
                name="mcp_job_plan_status_valid",
            ),
        ]
