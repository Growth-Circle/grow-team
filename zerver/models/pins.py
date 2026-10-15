"""Messages a channel keeps at hand. A pin is a pointer to a message, so
deleting the message or the channel removes the pin with it."""

from typing import Any

from django.db import models
from django.utils.timezone import now as timezone_now

from zerver.models.messages import Message
from zerver.models.realms import Realm
from zerver.models.streams import Stream
from zerver.models.users import UserProfile


class ChannelPin(models.Model):
    realm = models.ForeignKey(Realm, on_delete=models.CASCADE)
    stream = models.ForeignKey(Stream, on_delete=models.CASCADE)
    message = models.ForeignKey(Message, on_delete=models.CASCADE)
    # Kept when the person who pinned leaves; the pin belongs to the channel.
    pinned_by = models.ForeignKey(
        UserProfile, null=True, on_delete=models.SET_NULL, related_name="+"
    )
    date_pinned = models.DateTimeField(default=timezone_now)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["stream", "message"], name="channelpin_unique_stream_message"
            ),
        ]

    def to_api_dict(self) -> dict[str, Any]:
        return {
            "message_id": self.message_id,
            "stream_id": self.stream_id,
            "pinned_by_id": self.pinned_by_id,
            "date_pinned": int(self.date_pinned.timestamp()),
        }
