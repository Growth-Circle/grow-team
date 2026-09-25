import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("zerver", "0821_agent_profile_appearance"),
    ]

    operations = [
        migrations.AddField(
            model_name="agentrealmsettings",
            name="brand_color",
            field=models.CharField(db_default="", default="", max_length=20),
        ),
        migrations.AddField(
            model_name="agentrealmsettings",
            name="timezone",
            field=models.CharField(
                db_default="Asia/Jakarta", default="Asia/Jakarta", max_length=40
            ),
        ),
        migrations.AddField(
            model_name="agentrealmsettings",
            name="summary_default",
            field=models.BooleanField(db_default=False, default=False),
        ),
        migrations.AddField(
            model_name="agentrealmsettings",
            name="monthly_budget_microunits",
            field=models.PositiveBigIntegerField(default=None, null=True),
        ),
        migrations.AddField(
            model_name="agentrealmsettings",
            name="approval_ttl_minutes",
            field=models.PositiveIntegerField(db_default=120, default=120),
        ),
        migrations.AddField(
            model_name="agentrealmsettings",
            name="invite_expiry_days",
            field=models.PositiveIntegerField(db_default=7, default=7, null=True),
        ),
        migrations.AddField(
            model_name="agentrealmsettings",
            name="task_status_notices",
            field=models.BooleanField(db_default=False, default=False),
        ),
        migrations.AddField(
            model_name="agentrealmsettings",
            name="require_2fa",
            field=models.BooleanField(db_default=False, default=False),
        ),
        migrations.AddField(
            model_name="agentrealmsettings",
            name="task_id_prefix",
            field=models.CharField(db_default="", default="", max_length=10),
        ),
        migrations.AddField(
            model_name="agentrealmsettings",
            name="model_source",
            field=models.JSONField(db_default={}, default=dict),
        ),
        migrations.AddField(
            model_name="agentrealmsettings",
            name="model_presets",
            field=models.JSONField(db_default={}, default=dict),
        ),
        migrations.AddField(
            model_name="agentrealmsettings",
            name="model_guardrails",
            field=models.JSONField(db_default={}, default=dict),
        ),
        migrations.AddField(
            model_name="agentrealmsettings",
            name="work_runner",
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="+",
                to="zerver.agentrunner",
            ),
        ),
        migrations.AddField(
            model_name="agentrealmsettings",
            name="work_provider",
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="+",
                to="zerver.agentprovider",
            ),
        ),
        migrations.AddField(
            model_name="agentrealmsettings",
            name="agent_language",
            field=models.CharField(
                choices=[("id", "id"), ("en", "en")],
                db_default="id",
                default="id",
                max_length=5,
            ),
        ),
        migrations.AddField(
            model_name="agentrealmsettings",
            name="budget_alert_month",
            field=models.CharField(db_default="", default="", max_length=7),
        ),
        migrations.AddField(
            model_name="agentrealmsettings",
            name="mcp_default_mode",
            field=models.CharField(
                db_default="research_first", default="research_first", max_length=20
            ),
        ),
        migrations.AddConstraint(
            model_name="agentrealmsettings",
            constraint=models.CheckConstraint(
                condition=models.Q(("agent_language__in", ["id", "en"])),
                name="agent_realm_settings_language_valid",
            ),
        ),
        migrations.AddConstraint(
            model_name="agentrealmsettings",
            constraint=models.CheckConstraint(
                condition=models.Q(("mcp_default_mode__in", ["research_first", "direct"])),
                name="agent_realm_settings_mcp_mode_valid",
            ),
        ),
    ]
