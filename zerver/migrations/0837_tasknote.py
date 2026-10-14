import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("zerver", "0836_mcp_access")]

    operations = [
        migrations.CreateModel(
            name="TaskNote",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True, primary_key=True, serialize=False, verbose_name="ID"
                    ),
                ),
                ("content", models.TextField(max_length=10000)),
                ("rendered_content", models.TextField()),
                ("date_created", models.DateTimeField(default=django.utils.timezone.now)),
                (
                    "author",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="+",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "task",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="notes",
                        to="zerver.task",
                    ),
                ),
            ],
            options={
                "ordering": ["date_created", "id"],
            },
        ),
        migrations.RemoveConstraint(
            model_name="taskhistory",
            name="task_history_kind_valid",
        ),
        migrations.AlterField(
            model_name="taskhistory",
            name="kind",
            field=models.CharField(
                choices=[
                    ("created", "created"),
                    ("moved", "moved"),
                    ("edited", "edited"),
                    ("assigned", "assigned"),
                    ("note_added", "note_added"),
                ],
                max_length=20,
            ),
        ),
        migrations.AddConstraint(
            model_name="taskhistory",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    ("kind__in", ["created", "moved", "edited", "assigned", "note_added"])
                ),
                name="task_history_kind_valid",
            ),
        ),
    ]
