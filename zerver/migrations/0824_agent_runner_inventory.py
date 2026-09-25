import uuid

import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("zerver", "0823_task_source"),
    ]

    operations = [
        migrations.AddField(
            model_name="agentrunner",
            name="runner_kind",
            field=models.CharField(
                choices=[
                    ("", ""),
                    ("local", "local"),
                    ("vps", "vps"),
                    ("cloud", "cloud"),
                ],
                db_default="",
                default="",
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="agentrunner",
            name="group",
            field=models.CharField(db_default="", default="", max_length=100),
        ),
        migrations.AddField(
            model_name="agentrunner",
            name="labels",
            field=models.JSONField(db_default=[], default=list),
        ),
        migrations.AddField(
            model_name="agentrunner",
            name="hidden_at",
            field=models.DateTimeField(default=None, null=True),
        ),
        migrations.AddField(
            model_name="agentrunner",
            name="offline_notified_at",
            field=models.DateTimeField(default=None, null=True),
        ),
        migrations.AddField(
            model_name="agentrunner",
            name="admin_offline_notified_at",
            field=models.DateTimeField(default=None, null=True),
        ),
        migrations.AddField(
            model_name="agentrunner",
            name="stale_notified_at",
            field=models.DateTimeField(default=None, null=True),
        ),
        migrations.AddField(
            model_name="agentrunner",
            name="rotate_requested_at",
            field=models.DateTimeField(default=None, null=True),
        ),
        migrations.AddField(
            model_name="agentrunner",
            name="region",
            field=models.CharField(db_default="", default="", max_length=40),
        ),
        migrations.AddField(
            model_name="agentrunner",
            name="size",
            field=models.CharField(db_default="", default="", max_length=40),
        ),
        migrations.AddConstraint(
            model_name="agentrunner",
            constraint=models.CheckConstraint(
                condition=models.Q(("runner_kind__in", ["", "local", "vps", "cloud"])),
                name="agent_runner_kind_valid",
            ),
        ),
        migrations.CreateModel(
            name="AgentRunnerRegistrationToken",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4, editable=False, primary_key=True, serialize=False
                    ),
                ),
                ("created_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("updated_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("token_hash", models.CharField(max_length=128, unique=True)),
                ("group", models.CharField(default="", max_length=100)),
                (
                    "runner_kind",
                    models.CharField(
                        choices=[("vps", "vps"), ("cloud", "cloud")],
                        default="vps",
                        max_length=20,
                    ),
                ),
                ("name", models.CharField(db_default="", default="", max_length=200)),
                ("sets_work_runner", models.BooleanField(db_default=False, default=False)),
                ("expires_at", models.DateTimeField()),
                ("used_at", models.DateTimeField(default=None, null=True)),
                (
                    "created_by",
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
                (
                    "runner",
                    models.ForeignKey(
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        to="zerver.agentrunner",
                    ),
                ),
            ],
        ),
        migrations.AddConstraint(
            model_name="agentrunnerregistrationtoken",
            constraint=models.CheckConstraint(
                condition=models.Q(("runner_kind__in", ["vps", "cloud"])),
                name="agent_runner_token_kind_valid",
            ),
        ),
    ]
