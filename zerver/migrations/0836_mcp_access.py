import uuid

import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("zerver", "0835_alter_realm_logo_source_label")]
    operations = [
        migrations.CreateModel(
            name="MCPClient",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4, editable=False, primary_key=True, serialize=False
                    ),
                ),
                (
                    "realm",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        to="zerver.realm",
                    ),
                ),
                ("name", models.CharField(max_length=100)),
                ("redirect_uris", models.JSONField(default=list)),
                ("created_at", models.DateTimeField(default=django.utils.timezone.now)),
            ],
        ),
        migrations.CreateModel(
            name="MCPAccessGrant",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4, editable=False, primary_key=True, serialize=False
                    ),
                ),
                (
                    "realm",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        to="zerver.realm",
                    ),
                ),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "client",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        to="zerver.mcpclient",
                        null=True,
                    ),
                ),
                ("name", models.CharField(max_length=100)),
                ("scopes", models.JSONField(default=list)),
                ("resource", models.URLField(max_length=2048)),
                ("created_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("expires_at", models.DateTimeField()),
                ("revoked_at", models.DateTimeField(default=None, null=True)),
                ("last_used_at", models.DateTimeField(default=None, null=True)),
            ],
        ),
        migrations.CreateModel(
            name="MCPAuthorizationCode",
            fields=[
                ("digest", models.CharField(max_length=64, primary_key=True, serialize=False)),
                (
                    "grant",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        to="zerver.mcpaccessgrant",
                    ),
                ),
                ("redirect_uri", models.URLField(max_length=2048)),
                ("challenge", models.CharField(max_length=128)),
                ("expires_at", models.DateTimeField()),
                ("consumed_at", models.DateTimeField(default=None, null=True)),
            ],
        ),
        migrations.CreateModel(
            name="MCPAccessToken",
            fields=[
                ("digest", models.CharField(max_length=64, primary_key=True, serialize=False)),
                (
                    "grant",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        to="zerver.mcpaccessgrant",
                    ),
                ),
                (
                    "kind",
                    models.CharField(
                        choices=[("access", "access"), ("refresh", "refresh")], max_length=10
                    ),
                ),
                ("expires_at", models.DateTimeField()),
                ("consumed_at", models.DateTimeField(default=None, null=True)),
            ],
        ),
        migrations.CreateModel(
            name="MCPAccessAudit",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True, primary_key=True, serialize=False, verbose_name="ID"
                    ),
                ),
                (
                    "grant",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        to="zerver.mcpaccessgrant",
                    ),
                ),
                ("tool_name", models.CharField(max_length=100)),
                ("outcome", models.CharField(max_length=20)),
                ("created_at", models.DateTimeField(default=django.utils.timezone.now)),
            ],
        ),
    ]
