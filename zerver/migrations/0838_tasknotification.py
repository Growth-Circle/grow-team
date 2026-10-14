import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("zerver", "0837_tasknote")]

    operations = [
        migrations.CreateModel(
            name="TaskNotification",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True, primary_key=True, serialize=False, verbose_name="ID"
                    ),
                ),
                (
                    "kind",
                    models.CharField(
                        choices=[("mention", "mention"), ("assigned", "assigned")],
                        max_length=20,
                    ),
                ),
                ("date_created", models.DateTimeField(default=django.utils.timezone.now)),
                ("resolved_at", models.DateTimeField(default=None, null=True)),
                (
                    "actor",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="+",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "note",
                    models.ForeignKey(
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="notifications",
                        to="zerver.tasknote",
                    ),
                ),
                (
                    "task",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="notifications",
                        to="zerver.task",
                    ),
                ),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="+",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "ordering": ["-date_created", "-id"],
                "constraints": [
                    models.CheckConstraint(
                        condition=models.Q(("kind__in", ["mention", "assigned"])),
                        name="task_notification_kind_valid",
                    )
                ],
            },
        ),
    ]
