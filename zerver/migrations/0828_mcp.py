import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("zerver", "0827_external_account_drive"),
    ]

    operations = [
        migrations.CreateModel(
            name="McpServer",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True, primary_key=True, serialize=False, verbose_name="ID"
                    ),
                ),
                ("slug", models.CharField(max_length=60)),
                ("name", models.CharField(max_length=100)),
                ("base_url", models.URLField(default="", max_length=2048)),
                (
                    "auth_mode",
                    models.CharField(
                        choices=[("oauth", "oauth"), ("header", "header"), ("none", "none")],
                        default="oauth",
                        max_length=20,
                    ),
                ),
                ("verified", models.BooleanField(db_default=False, default=False)),
                ("version_pin", models.CharField(db_default="", default="", max_length=40)),
                ("supported_scopes", models.JSONField(db_default=[], default=list)),
                ("created_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("disabled_at", models.DateTimeField(default=None, null=True)),
                (
                    "added_by",
                    models.ForeignKey(
                        db_constraint=False,
                        on_delete=django.db.models.deletion.PROTECT,
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "realm",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE, to="zerver.realm"
                    ),
                ),
            ],
        ),
        migrations.AddConstraint(
            model_name="mcpserver",
            constraint=models.UniqueConstraint(
                fields=("realm", "slug"), name="mcp_server_realm_slug_unique"
            ),
        ),
        migrations.AddConstraint(
            model_name="mcpserver",
            constraint=models.CheckConstraint(
                condition=models.Q(("auth_mode__in", ["oauth", "header", "none"])),
                name="mcp_server_auth_mode_valid",
            ),
        ),
        migrations.CreateModel(
            name="McpConnection",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True, primary_key=True, serialize=False, verbose_name="ID"
                    ),
                ),
                (
                    "scope",
                    models.CharField(
                        choices=[
                            ("workspace", "workspace"),
                            ("room", "room"),
                            ("personal", "personal"),
                        ],
                        default="workspace",
                        max_length=20,
                    ),
                ),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("pending", "pending"),
                            ("active", "active"),
                            ("needs_review", "needs_review"),
                            ("revoked", "revoked"),
                        ],
                        default="pending",
                        max_length=20,
                    ),
                ),
                ("tool_manifest", models.JSONField(default=list)),
                ("tool_manifest_hash", models.CharField(default="", max_length=64)),
                ("rate_limit_per_minute", models.PositiveIntegerField(default=None, null=True)),
                ("created_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("removed_at", models.DateTimeField(default=None, null=True)),
                (
                    "created_by",
                    models.ForeignKey(
                        db_constraint=False,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="+",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "credential",
                    models.ForeignKey(
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        to="zerver.agentsecret",
                    ),
                ),
                (
                    "realm",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE, to="zerver.realm"
                    ),
                ),
                (
                    "server",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="connections",
                        to="zerver.mcpserver",
                    ),
                ),
                (
                    "stream",
                    models.ForeignKey(
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        to="zerver.stream",
                    ),
                ),
                (
                    "user",
                    models.ForeignKey(
                        db_constraint=False,
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="mcp_connections",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
        ),
        migrations.AddConstraint(
            model_name="mcpconnection",
            constraint=models.CheckConstraint(
                condition=models.Q(("scope__in", ["workspace", "room", "personal"])),
                name="mcp_connection_scope_valid",
            ),
        ),
        migrations.AddConstraint(
            model_name="mcpconnection",
            constraint=models.CheckConstraint(
                condition=(
                    models.Q(scope="workspace", stream__isnull=True, user__isnull=True)
                    | models.Q(scope="room", stream__isnull=False, user__isnull=True)
                    | models.Q(scope="personal", stream__isnull=True, user__isnull=False)
                ),
                name="mcp_connection_scope_target_valid",
            ),
        ),
        migrations.AddConstraint(
            model_name="mcpconnection",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    ("status__in", ["pending", "active", "needs_review", "revoked"])
                ),
                name="mcp_connection_status_valid",
            ),
        ),
        migrations.CreateModel(
            name="McpToolPolicy",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True, primary_key=True, serialize=False, verbose_name="ID"
                    ),
                ),
                ("tool_name", models.CharField(max_length=200)),
                (
                    "policy",
                    models.CharField(
                        choices=[
                            ("allow", "allow"),
                            ("approve", "approve"),
                            ("block", "block"),
                        ],
                        default="block",
                        max_length=20,
                    ),
                ),
                ("is_new", models.BooleanField(default=False)),
                ("hints", models.JSONField(db_default={}, default=dict)),
                ("reviewed_at", models.DateTimeField(default=None, null=True)),
                ("updated_at", models.DateTimeField(default=django.utils.timezone.now)),
                (
                    "connection",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="tool_policies",
                        to="zerver.mcpconnection",
                    ),
                ),
            ],
        ),
        migrations.AddConstraint(
            model_name="mcptoolpolicy",
            constraint=models.UniqueConstraint(
                fields=("connection", "tool_name"), name="mcp_tool_policy_unique"
            ),
        ),
        migrations.AddConstraint(
            model_name="mcptoolpolicy",
            constraint=models.CheckConstraint(
                condition=models.Q(("policy__in", ["allow", "approve", "block"])),
                name="mcp_tool_policy_valid",
            ),
        ),
        migrations.CreateModel(
            name="McpAgentGrant",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True, primary_key=True, serialize=False, verbose_name="ID"
                    ),
                ),
                ("created_at", models.DateTimeField(default=django.utils.timezone.now)),
                (
                    "agent_profile",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="mcp_grants",
                        to="zerver.agentprofile",
                    ),
                ),
                (
                    "connection",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="agent_grants",
                        to="zerver.mcpconnection",
                    ),
                ),
            ],
        ),
        migrations.AddConstraint(
            model_name="mcpagentgrant",
            constraint=models.UniqueConstraint(
                fields=("connection", "agent_profile"), name="mcp_agent_grant_unique"
            ),
        ),
        migrations.CreateModel(
            name="McpJobPlan",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True, primary_key=True, serialize=False, verbose_name="ID"
                    ),
                ),
                ("tools", models.JSONField(default=dict)),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("proposed", "proposed"),
                            ("approved", "approved"),
                            ("rejected", "rejected"),
                        ],
                        default="proposed",
                        max_length=20,
                    ),
                ),
                ("approved_at", models.DateTimeField(default=None, null=True)),
                (
                    "approved_by",
                    models.ForeignKey(
                        db_constraint=False,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "job",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="mcp_plan",
                        to="zerver.agentjob",
                    ),
                ),
            ],
        ),
        migrations.AddConstraint(
            model_name="mcpjobplan",
            constraint=models.CheckConstraint(
                condition=models.Q(("status__in", ["proposed", "approved", "rejected"])),
                name="mcp_job_plan_status_valid",
            ),
        ),
    ]
