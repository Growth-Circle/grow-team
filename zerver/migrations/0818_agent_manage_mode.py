from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("zerver", "0817_alter_realm_can_command_administrator_agents_group"),
    ]

    operations = [
        migrations.AlterField(
            model_name="agentprofile",
            name="default_mode",
            field=models.CharField(
                choices=[("answer", "answer"), ("code", "code"), ("manage", "manage")],
                default="answer",
                max_length=20,
            ),
        ),
        migrations.AlterField(
            model_name="agentjob",
            name="job_kind",
            field=models.CharField(
                choices=[("answer", "answer"), ("code", "code"), ("manage", "manage")],
                default="answer",
                max_length=20,
            ),
        ),
        migrations.RemoveConstraint(
            model_name="agentprofile",
            name="agent_profile_job_kind_valid",
        ),
        migrations.AddConstraint(
            model_name="agentprofile",
            constraint=models.CheckConstraint(
                condition=models.Q(("default_mode__in", ("answer", "code", "manage"))),
                name="agent_profile_job_kind_valid",
            ),
        ),
        migrations.RemoveConstraint(
            model_name="agentjob",
            name="agent_job_kind_target_valid",
        ),
        migrations.AddConstraint(
            model_name="agentjob",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    models.Q(("delivery_target", "answer"), ("job_kind", "answer")),
                    models.Q(("delivery_target__in", ["patch", "draft_pr"]), ("job_kind", "code")),
                    models.Q(("delivery_target", "answer"), ("job_kind", "manage")),
                    _connector="OR",
                ),
                name="agent_job_kind_target_valid",
            ),
        ),
        migrations.AddField(
            model_name="agentoperation",
            name="server_receipt",
            field=models.JSONField(default=None, null=True),
        ),
    ]
