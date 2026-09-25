import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("zerver", "0831_agent_audit_realm_index"),
    ]

    operations = [
        migrations.CreateModel(
            name="RolePermission",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True, primary_key=True, serialize=False, verbose_name="ID"
                    ),
                ),
                ("permission_key", models.CharField(max_length=40)),
                ("role", models.PositiveSmallIntegerField()),
                ("allowed", models.BooleanField(default=False)),
                (
                    "realm",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE, to="zerver.realm"
                    ),
                ),
            ],
        ),
        migrations.AddConstraint(
            model_name="rolepermission",
            constraint=models.UniqueConstraint(
                fields=("realm", "permission_key", "role"), name="role_permission_unique"
            ),
        ),
        migrations.AddConstraint(
            model_name="rolepermission",
            constraint=models.CheckConstraint(
                condition=models.Q(("role__in", [100, 200, 300, 400, 600])),
                name="role_permission_role_valid",
            ),
        ),
    ]
