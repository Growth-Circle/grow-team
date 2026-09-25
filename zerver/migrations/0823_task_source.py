from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("zerver", "0822_agent_realm_settings_workspace"),
    ]

    operations = [
        migrations.AddField(
            model_name="task",
            name="source",
            field=models.CharField(
                choices=[
                    ("manual", "manual"),
                    ("brief", "brief"),
                    ("meeting", "meeting"),
                    ("whatsapp", "whatsapp"),
                    ("agent", "agent"),
                ],
                db_default="manual",
                default="manual",
                max_length=20,
            ),
        ),
        migrations.AddConstraint(
            model_name="task",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    ("source__in", ["manual", "brief", "meeting", "whatsapp", "agent"])
                ),
                name="task_source_valid",
            ),
        ),
    ]
