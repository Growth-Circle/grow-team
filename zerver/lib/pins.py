"""Read-side helpers for pinned messages."""

from typing import Any

from django.utils.translation import gettext as _

from zerver.lib.exceptions import JsonableError
from zerver.lib.message import access_message, bulk_access_messages
from zerver.lib.streams import access_stream_by_id, access_stream_for_send_message
from zerver.models import ChannelPin, Message, Recipient, Stream, UserProfile
from zerver.models.streams import get_stream_by_id_in_realm


def access_pin_stream(user_profile: UserProfile, stream_id: int) -> Stream:
    # A reader can still look at the pins of an archived channel.
    stream, _sub = access_stream_by_id(user_profile, stream_id, require_active_channel=False)
    return stream


def access_message_to_pin(user_profile: UserProfile, message_id: int) -> tuple[Stream, Message]:
    """The channel message a member may pin or unpin.

    Reading the message is not enough: pinning changes what the whole
    channel sees, so the user must also be allowed to post there.
    """
    message = access_message(user_profile, message_id, is_modifying_message=False)
    if message.recipient.type != Recipient.STREAM:
        raise JsonableError(_("Only channel messages can be pinned."))

    stream = get_stream_by_id_in_realm(message.recipient.type_id, user_profile.realm)
    access_stream_by_id(user_profile, stream.id, require_active_channel=False)
    access_stream_for_send_message(user_profile, stream, None)
    return stream, message


def pinned_message_dicts(user_profile: UserProfile, stream: Stream) -> list[dict[str, Any]]:
    """The channel's pins, newest first, limited to messages the user may read.

    A private channel can hide its older history from someone who joined
    later, and that must hold for a pin as well. A message moved to another
    channel keeps its pin row, so those rows are skipped here.
    """
    assert stream.recipient_id is not None
    pins = list(
        ChannelPin.objects.filter(stream=stream, message__recipient_id=stream.recipient_id)
        .select_related("message", "message__sender")
        .order_by("-date_pinned", "-id")
    )
    readable_ids = {
        message.id
        for message in bulk_access_messages(
            user_profile, [pin.message for pin in pins], stream=stream, is_modifying_message=False
        )
    }
    return [
        {
            **pin.to_api_dict(),
            "sender_id": pin.message.sender_id,
            "sender_full_name": pin.message.sender.full_name,
            "topic": pin.message.topic_name(),
            "date_sent": int(pin.message.date_sent.timestamp()),
            "rendered_content": pin.message.rendered_content or "",
        }
        for pin in pins
        if pin.message_id in readable_ids
    ]
