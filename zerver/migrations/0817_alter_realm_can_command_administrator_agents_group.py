import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("zerver", "0816_set_default_value_for_can_command_administrator_agents_group"),
    ]

    operations = [
        migrations.AlterField(
            model_name="realm",
            name="can_command_administrator_agents_group",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.RESTRICT,
                related_name="+",
                to="zerver.usergroup",
            ),
        ),
    ]
