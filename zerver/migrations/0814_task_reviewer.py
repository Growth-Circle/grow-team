import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models
from django.db.backends.base.schema import BaseDatabaseSchemaEditor
from django.db.migrations.state import StateApps


def mark_default_review_columns(apps: StateApps, schema_editor: BaseDatabaseSchemaEditor) -> None:
    # Boards created by 0813 carry the default "Awaiting review" column;
    # it is the column whose cards count toward a reviewer's total.
    TaskBoardColumn = apps.get_model("zerver", "TaskBoardColumn")
    TaskBoardColumn.objects.filter(name="Awaiting review").update(is_review=True)


class Migration(migrations.Migration):
    dependencies = [
        ("zerver", "0813_task_board"),
    ]

    operations = [
        migrations.AddField(
            model_name="task",
            name="reviewer",
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="reviewed_tasks",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name="taskboardcolumn",
            name="is_review",
            field=models.BooleanField(default=False),
        ),
        migrations.RunPython(
            mark_default_review_columns,
            reverse_code=migrations.RunPython.noop,
            elidable=True,
        ),
    ]
