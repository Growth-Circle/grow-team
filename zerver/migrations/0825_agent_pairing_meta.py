from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("zerver", "0824_agent_runner_inventory"),
    ]

    operations = [
        migrations.AddField(
            model_name="agentpairing",
            name="requested_meta",
            field=models.JSONField(db_default={}, default=dict),
        ),
        migrations.AddField(
            model_name="agentpairing",
            name="denied_at",
            field=models.DateTimeField(default=None, null=True),
        ),
    ]
