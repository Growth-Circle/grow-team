"""Per-room metadata layered on top of a channel, and its daily digest.

RoomMeta never becomes a second source of truth for the channel itself; it
only holds the extra fields a room needs (due date, summary opt-in, quiet
notice). RoomDigest holds one generated digest per room per day.
"""

from django.db import models
from django.utils.timezone import now as timezone_now

from zerver.models.messages import Message
from zerver.models.realms import Realm
from zerver.models.streams import Stream
from zerver.models.users import UserProfile


class RoomMeta(models.Model):
    stream = models.OneToOneField(Stream, on_delete=models.CASCADE, related_name="room_meta")
    # A date only (T-37's <input type="date">); DateTimeField would let a
    # timezone conversion shift the day the person picked.
    due_date = models.DateField(null=True, default=None)
    summary_enabled = models.BooleanField(default=False)
    # manage.py delete_realm removes UserProfile rows outright, and an old
    # image does not know this table exists, so this reference carries no
    # database-level constraint.
    summary_enabled_by = models.ForeignKey(
        UserProfile,
        on_delete=models.SET_NULL,
        null=True,
        db_constraint=False,
        related_name="+",
    )
    quiet_notified_at = models.DateTimeField(null=True, default=None)
    last_digest_at = models.DateTimeField(null=True, default=None)


class RoomDigest(models.Model):
    stream = models.ForeignKey(Stream, on_delete=models.CASCADE, related_name="digests")
    date = models.DateField()
    decided = models.JSONField(default=list)
    blocker = models.JSONField(default=list)
    waiting = models.JSONField(default=list)
    summary = models.TextField(default="")
    message_count = models.PositiveIntegerField(default=0)
    # Retention can delete the source message without knowing this table
    # exists, so this reference carries no database-level constraint.
    # Read code must handle a source_message_id whose row is gone.
    source_message = models.ForeignKey(
        Message,
        on_delete=models.SET_NULL,
        null=True,
        db_constraint=False,
        related_name="+",
    )
    job = models.ForeignKey(
        "zerver.AgentJob", on_delete=models.SET_NULL, null=True, related_name="+"
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["stream", "date"], name="room_digest_stream_date_unique"
            ),
        ]


class RoomChannelLink(models.Model):
    """Links a room to an external messaging channel (WhatsApp today)."""

    realm = models.ForeignKey(Realm, on_delete=models.CASCADE)
    stream = models.ForeignKey(Stream, on_delete=models.CASCADE)
    provider = models.CharField(
        max_length=20, choices=[("whatsapp", "whatsapp")], default="whatsapp"
    )
    external_id = models.CharField(max_length=255)
    direction = models.CharField(
        max_length=20,
        choices=[("inbound", "inbound"), ("outbound", "outbound"), ("both", "both")],
        default="both",
    )
    # manage.py delete_realm removes UserProfile rows outright, and an old
    # image does not know this table exists, so this reference carries no
    # database-level constraint.
    created_by = models.ForeignKey(UserProfile, on_delete=models.PROTECT, db_constraint=False)
    created_at = models.DateTimeField(default=timezone_now)
    removed_at = models.DateTimeField(null=True, default=None)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["provider", "external_id"],
                condition=models.Q(removed_at__isnull=True),
                name="room_channel_link_external_unique",
            ),
            models.CheckConstraint(
                condition=models.Q(provider__in=["whatsapp"]),
                name="room_channel_link_provider_valid",
            ),
            models.CheckConstraint(
                condition=models.Q(direction__in=["inbound", "outbound", "both"]),
                name="room_channel_link_direction_valid",
            ),
        ]
