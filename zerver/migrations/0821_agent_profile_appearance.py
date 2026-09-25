from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("zerver", "0820_room_meta"),
    ]

    operations = [
        migrations.AddField(
            model_name="agentprofile",
            name="agent_role",
            field=models.CharField(
                choices=[
                    ("planner", "planner"),
                    ("builder", "builder"),
                    ("reviewer", "reviewer"),
                    ("custom", "custom"),
                ],
                db_default="custom",
                default="custom",
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="agentprofile",
            name="avatar_shape",
            field=models.CharField(
                choices=[("circle", "circle"), ("ring", "ring"), ("box", "box")],
                db_default="circle",
                default="circle",
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="agentprofile",
            name="avatar_color",
            field=models.CharField(db_default="", default="", max_length=20),
        ),
        migrations.AddField(
            model_name="agentprofile",
            name="model_preset",
            field=models.CharField(
                choices=[("", ""), ("fast", "fast"), ("balanced", "balanced"), ("best", "best")],
                db_default="",
                default="",
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="agentprofile",
            name="monthly_budget_microunits",
            field=models.PositiveBigIntegerField(default=None, null=True),
        ),
        migrations.AddField(
            model_name="agentprofile",
            name="model_key_hash",
            field=models.CharField(db_default="", default="", max_length=128),
        ),
        migrations.AddField(
            model_name="agentprofile",
            name="model_key_limit_microunits",
            field=models.PositiveBigIntegerField(default=None, null=True),
        ),
        migrations.AddField(
            model_name="agentprofile",
            name="model_key_rotated_at",
            field=models.DateTimeField(default=None, null=True),
        ),
        migrations.AddField(
            model_name="agentprofile",
            name="is_builtin",
            field=models.BooleanField(db_default=False, default=False),
        ),
        migrations.AddField(
            model_name="agentprofile",
            name="work_skills",
            field=models.JSONField(db_default=[], default=list),
        ),
        migrations.AddField(
            model_name="agentprofile",
            name="work_tools",
            field=models.JSONField(db_default=[], default=list),
        ),
        migrations.AddConstraint(
            model_name="agentprofile",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    ("agent_role__in", ["planner", "builder", "reviewer", "custom"])
                ),
                name="agent_profile_role_valid",
            ),
        ),
        migrations.AddConstraint(
            model_name="agentprofile",
            constraint=models.CheckConstraint(
                condition=models.Q(("avatar_shape__in", ["circle", "ring", "box"])),
                name="agent_profile_avatar_shape_valid",
            ),
        ),
        migrations.AddConstraint(
            model_name="agentprofile",
            constraint=models.CheckConstraint(
                condition=models.Q(("model_preset__in", ["", "fast", "balanced", "best"])),
                name="agent_profile_model_preset_valid",
            ),
        ),
    ]
