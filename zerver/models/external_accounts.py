"""External OAuth account links (Drive, Calendar, GitHub) and the room
folders built on top of a linked Drive account."""

from django.db import models
from django.utils.timezone import now as timezone_now

from zerver.models.agents import AgentSecret
from zerver.models.realms import Realm
from zerver.models.streams import Stream
from zerver.models.users import UserProfile


class ExternalAccount(models.Model):
    realm = models.ForeignKey(Realm, on_delete=models.CASCADE)
    # Null means the account belongs to the workspace, not one person.
    # manage.py delete_realm removes UserProfile rows outright, and an old
    # image does not know this table exists, so this reference carries no
    # database-level constraint.
    user = models.ForeignKey(UserProfile, on_delete=models.CASCADE, null=True, db_constraint=False)
    provider = models.CharField(max_length=20, choices=[("google", "google"), ("github", "github")])
    purpose = models.CharField(
        max_length=20,
        choices=[("drive", "drive"), ("calendar", "calendar"), ("github", "github")],
    )
    account_label = models.CharField(max_length=255, default="")
    scopes = models.JSONField(default=list)
    secret = models.ForeignKey(AgentSecret, on_delete=models.PROTECT, null=True)
    external_id = models.CharField(max_length=255, default="")
    status = models.CharField(
        max_length=20,
        choices=[
            ("active", "active"),
            ("needs_reconnect", "needs_reconnect"),
            ("revoked", "revoked"),
        ],
        default="active",
    )
    created_at = models.DateTimeField(default=timezone_now)
    revoked_at = models.DateTimeField(null=True, default=None)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(provider__in=["google", "github"]),
                name="external_account_provider_valid",
            ),
            models.CheckConstraint(
                condition=models.Q(purpose__in=["drive", "calendar", "github"]),
                name="external_account_purpose_valid",
            ),
            models.CheckConstraint(
                condition=models.Q(status__in=["active", "needs_reconnect", "revoked"]),
                name="external_account_status_valid",
            ),
        ]


class DriveFolderLink(models.Model):
    realm = models.ForeignKey(Realm, on_delete=models.CASCADE)
    stream = models.ForeignKey(Stream, on_delete=models.CASCADE)
    account = models.ForeignKey(ExternalAccount, on_delete=models.CASCADE)
    folder_id = models.CharField(max_length=255)
    folder_name = models.CharField(max_length=255, default="")
    mode = models.CharField(
        max_length=20, choices=[("read", "read"), ("read_write", "read_write")], default="read"
    )
    # manage.py delete_realm removes UserProfile rows outright, and an old
    # image does not know this table exists, so this reference carries no
    # database-level constraint.
    linked_by = models.ForeignKey(UserProfile, on_delete=models.PROTECT, db_constraint=False)
    created_at = models.DateTimeField(default=timezone_now)
    removed_at = models.DateTimeField(null=True, default=None)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(mode__in=["read", "read_write"]),
                name="drive_folder_link_mode_valid",
            ),
        ]
