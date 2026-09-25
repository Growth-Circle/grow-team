import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("zerver", "0819_agent_instructions_and_followups"),
    ]

    operations = [
        migrations.CreateModel(
            name="RoomMeta",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True, primary_key=True, serialize=False, verbose_name="ID"
                    ),
                ),
                ("due_date", models.DateField(default=None, null=True)),
                ("summary_enabled", models.BooleanField(default=False)),
                ("quiet_notified_at", models.DateTimeField(default=None, null=True)),
                ("last_digest_at", models.DateTimeField(default=None, null=True)),
                (
                    "stream",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="room_meta",
                        to="zerver.stream",
                    ),
                ),
                (
                    "summary_enabled_by",
                    models.ForeignKey(
                        db_constraint=False,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="+",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
        ),
        migrations.CreateModel(
            name="RoomDigest",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True, primary_key=True, serialize=False, verbose_name="ID"
                    ),
                ),
                ("date", models.DateField()),
                ("decided", models.JSONField(default=list)),
                ("blocker", models.JSONField(default=list)),
                ("waiting", models.JSONField(default=list)),
                ("summary", models.TextField(default="")),
                ("message_count", models.PositiveIntegerField(default=0)),
                (
                    "stream",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="digests",
                        to="zerver.stream",
                    ),
                ),
                (
                    "source_message",
                    models.ForeignKey(
                        db_constraint=False,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="+",
                        to="zerver.message",
                    ),
                ),
                (
                    "job",
                    models.ForeignKey(
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="+",
                        to="zerver.agentjob",
                    ),
                ),
            ],
        ),
        migrations.AddConstraint(
            model_name="roomdigest",
            constraint=models.UniqueConstraint(
                fields=("stream", "date"), name="room_digest_stream_date_unique"
            ),
        ),
    ]
