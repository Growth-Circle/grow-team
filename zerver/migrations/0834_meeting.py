import django.db.models.deletion
import django.utils.timezone
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("zerver", "0833_need_resolution"),
    ]

    operations = [
        migrations.CreateModel(
            name="Meeting",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True, primary_key=True, serialize=False, verbose_name="ID"
                    ),
                ),
                ("provider", models.CharField(default="google_calendar", max_length=20)),
                ("external_id", models.CharField(max_length=255)),
                ("title", models.CharField(default="", max_length=255)),
                ("starts_at", models.DateTimeField()),
                ("ends_at", models.DateTimeField(default=None, null=True)),
                ("platform", models.CharField(default="", max_length=40)),
                ("join_url", models.URLField(default="", max_length=2048)),
                ("recording_url", models.URLField(default="", max_length=2048)),
                ("summary", models.TextField(default="")),
                ("action_items", models.JSONField(default=list)),
                ("topic", models.TextField(default="")),
                ("fathom_join", models.BooleanField(default=False)),
                ("created_task_ids", models.JSONField(default=list)),
                ("done", models.BooleanField(default=False)),
                ("updated_at", models.DateTimeField(default=django.utils.timezone.now)),
                (
                    "realm",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE, to="zerver.realm"
                    ),
                ),
                (
                    "stream",
                    models.ForeignKey(
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        to="zerver.stream",
                    ),
                ),
            ],
        ),
        migrations.AddConstraint(
            model_name="meeting",
            constraint=models.UniqueConstraint(
                fields=("realm", "provider", "external_id"),
                name="meeting_realm_provider_external_unique",
            ),
        ),
    ]
