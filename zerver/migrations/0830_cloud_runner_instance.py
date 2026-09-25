import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("zerver", "0829_room_channel_link"),
    ]

    operations = [
        migrations.CreateModel(
            name="CloudRunnerInstance",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True, primary_key=True, serialize=False, verbose_name="ID"
                    ),
                ),
                (
                    "provider",
                    models.CharField(
                        choices=[("cloudflare", "cloudflare"), ("aws", "aws")],
                        default="cloudflare",
                        max_length=20,
                    ),
                ),
                ("provider_instance_id", models.CharField(default="", max_length=255)),
                ("region", models.CharField(default="", max_length=40)),
                ("size", models.CharField(default="", max_length=40)),
                (
                    "state",
                    models.CharField(
                        choices=[
                            ("pending", "pending"),
                            ("starting", "starting"),
                            ("running", "running"),
                            ("stopping", "stopping"),
                            ("stopped", "stopped"),
                            ("failed", "failed"),
                        ],
                        default="pending",
                        max_length=20,
                    ),
                ),
                (
                    "egress",
                    models.CharField(
                        choices=[("https_only", "https_only"), ("open", "open")],
                        db_default="https_only",
                        default="https_only",
                        max_length=20,
                    ),
                ),
                ("created_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("stopped_at", models.DateTimeField(default=None, null=True)),
                ("billing_seconds", models.PositiveBigIntegerField(default=0)),
                ("last_error", models.CharField(default="", max_length=500)),
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
            model_name="cloudrunnerinstance",
            constraint=models.CheckConstraint(
                condition=models.Q(("provider__in", ["cloudflare", "aws"])),
                name="cloud_runner_provider_valid",
            ),
        ),
        migrations.AddConstraint(
            model_name="cloudrunnerinstance",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    (
                        "state__in",
                        ["pending", "starting", "running", "stopping", "stopped", "failed"],
                    )
                ),
                name="cloud_runner_state_valid",
            ),
        ),
        migrations.AddConstraint(
            model_name="cloudrunnerinstance",
            constraint=models.CheckConstraint(
                condition=models.Q(("egress__in", ["https_only", "open"])),
                name="cloud_runner_egress_valid",
            ),
        ),
    ]
