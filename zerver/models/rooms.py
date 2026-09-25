"""Per-room metadata layered on top of a channel, and its daily digest.

RoomMeta never becomes a second source of truth for the channel itself; it
only holds the extra fields a room needs (due date, summary opt-in, quiet
notice). RoomDigest holds one generated digest per room per day.
"""

from django.db import models

from zerver.models.messages import Message
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
