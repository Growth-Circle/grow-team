from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("zerver", "0834_meeting"),
    ]

    operations = [
        migrations.AlterField(
            model_name="realm",
            name="logo_source",
            field=models.CharField(
                choices=[("D", "Default to sanji"), ("U", "Uploaded by administrator")],
                default="D",
                max_length=1,
            ),
        ),
        migrations.AlterField(
            model_name="realm",
            name="night_logo_source",
            field=models.CharField(
                choices=[("D", "Default to sanji"), ("U", "Uploaded by administrator")],
                default="D",
                max_length=1,
            ),
        ),
    ]
