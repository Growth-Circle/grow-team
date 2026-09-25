import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("zerver", "0832_role_permission"),
    ]

    operations = [
        migrations.AddField(
            model_name="agentapproval",
            name="email_reminder_sent_at",
            field=models.DateTimeField(default=None, null=True),
        ),
        migrations.CreateModel(
            name="NeedResolution",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True, primary_key=True, serialize=False, verbose_name="ID"
                    ),
                ),
                (
                    "action",
                    models.CharField(
                        choices=[("done", "done"), ("dismissed", "dismissed")],
                        default="done",
                        max_length=20,
                    ),
                ),
                ("resolved_at", models.DateTimeField(default=django.utils.timezone.now)),
                (
                    "message",
                    models.ForeignKey(
                        db_constraint=False,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="+",
                        to="zerver.message",
                    ),
                ),
                (
                    "realm",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE, to="zerver.realm"
                    ),
                ),
                (
                    "user",
                    models.ForeignKey(
                        db_constraint=False,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="+",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
        ),
        migrations.AddConstraint(
            model_name="needresolution",
            constraint=models.UniqueConstraint(
                fields=("user", "message"), name="need_resolution_user_message_unique"
            ),
        ),
        migrations.AddConstraint(
            model_name="needresolution",
            constraint=models.CheckConstraint(
                condition=models.Q(("action__in", ["done", "dismissed"])),
                name="need_resolution_action_valid",
            ),
        ),
    ]
