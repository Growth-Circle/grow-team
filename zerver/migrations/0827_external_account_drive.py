import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("zerver", "0826_web_push_subscription"),
    ]

    operations = [
        migrations.CreateModel(
            name="ExternalAccount",
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
                        choices=[("google", "google"), ("github", "github")], max_length=20
                    ),
                ),
                (
                    "purpose",
                    models.CharField(
                        choices=[
                            ("drive", "drive"),
                            ("calendar", "calendar"),
                            ("github", "github"),
                        ],
                        max_length=20,
                    ),
                ),
                ("account_label", models.CharField(default="", max_length=255)),
                ("scopes", models.JSONField(default=list)),
                ("external_id", models.CharField(default="", max_length=255)),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("active", "active"),
                            ("needs_reconnect", "needs_reconnect"),
                            ("revoked", "revoked"),
                        ],
                        default="active",
                        max_length=20,
                    ),
                ),
                ("created_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("revoked_at", models.DateTimeField(default=None, null=True)),
                (
                    "realm",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE, to="zerver.realm"
                    ),
                ),
                (
                    "secret",
                    models.ForeignKey(
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        to="zerver.agentsecret",
                    ),
                ),
                (
                    "user",
                    models.ForeignKey(
                        db_constraint=False,
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
        ),
        migrations.AddConstraint(
            model_name="externalaccount",
            constraint=models.CheckConstraint(
                condition=models.Q(("provider__in", ["google", "github"])),
                name="external_account_provider_valid",
            ),
        ),
        migrations.AddConstraint(
            model_name="externalaccount",
            constraint=models.CheckConstraint(
                condition=models.Q(("purpose__in", ["drive", "calendar", "github"])),
                name="external_account_purpose_valid",
            ),
        ),
        migrations.AddConstraint(
            model_name="externalaccount",
            constraint=models.CheckConstraint(
                condition=models.Q(("status__in", ["active", "needs_reconnect", "revoked"])),
                name="external_account_status_valid",
            ),
        ),
        migrations.CreateModel(
            name="DriveFolderLink",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True, primary_key=True, serialize=False, verbose_name="ID"
                    ),
                ),
                ("folder_id", models.CharField(max_length=255)),
                ("folder_name", models.CharField(default="", max_length=255)),
                (
                    "mode",
                    models.CharField(
                        choices=[("read", "read"), ("read_write", "read_write")],
                        default="read",
                        max_length=20,
                    ),
                ),
                ("created_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("removed_at", models.DateTimeField(default=None, null=True)),
                (
                    "account",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        to="zerver.externalaccount",
                    ),
                ),
                (
                    "linked_by",
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
                    "stream",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE, to="zerver.stream"
                    ),
                ),
            ],
        ),
        migrations.AddConstraint(
            model_name="drivefolderlink",
            constraint=models.CheckConstraint(
                condition=models.Q(("mode__in", ["read", "read_write"])),
                name="drive_folder_link_mode_valid",
            ),
        ),
    ]
