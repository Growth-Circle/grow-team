import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("zerver", "0828_mcp"),
    ]

    operations = [
        migrations.CreateModel(
            name="RoomChannelLink",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True, primary_key=True, serialize=False, verbose_name="ID"
                    ),
                ),
                (
                    "provider",
                    models.CharField(
                        choices=[("whatsapp", "whatsapp")], default="whatsapp", max_length=20
                    ),
                ),
                ("external_id", models.CharField(max_length=255)),
                (
                    "direction",
                    models.CharField(
                        choices=[
                            ("inbound", "inbound"),
                            ("outbound", "outbound"),
                            ("both", "both"),
                        ],
                        default="both",
                        max_length=20,
                    ),
                ),
                ("created_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("removed_at", models.DateTimeField(default=None, null=True)),
                (
                    "created_by",
                    models.ForeignKey(
                        db_constraint=False,
                        on_delete=django.db.models.deletion.PROTECT,
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "realm",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE, to="zerver.realm"
                    ),
                ),
                (
                    "stream",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE, to="zerver.stream"
                    ),
                ),
            ],
        ),
        migrations.AddConstraint(
            model_name="roomchannellink",
            constraint=models.UniqueConstraint(
                condition=models.Q(removed_at__isnull=True),
                fields=("provider", "external_id"),
                name="room_channel_link_external_unique",
            ),
        ),
        migrations.AddConstraint(
            model_name="roomchannellink",
            constraint=models.CheckConstraint(
                condition=models.Q(("provider__in", ["whatsapp"])),
                name="room_channel_link_provider_valid",
            ),
        ),
        migrations.AddConstraint(
            model_name="roomchannellink",
            constraint=models.CheckConstraint(
                condition=models.Q(("direction__in", ["inbound", "outbound", "both"])),
                name="room_channel_link_direction_valid",
            ),
        ),
    ]
