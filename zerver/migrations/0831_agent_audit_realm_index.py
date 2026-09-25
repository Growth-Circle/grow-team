from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("zerver", "0830_cloud_runner_instance"),
    ]

    operations = [
        migrations.AddIndex(
            model_name="agentauditevent",
            index=models.Index(
                fields=["realm", "occurred_at"], name="agent_audit_event_realm_occurred_idx"
            ),
        ),
    ]
