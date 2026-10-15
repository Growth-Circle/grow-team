"""Write-side operations for pinned messages."""

from django.db import transaction
from django.utils.translation import gettext as _

from zerver.lib.exceptions import JsonableError
from zerver.lib.tasks import channel_reader_user_ids
from zerver.models import ChannelPin, Message, Stream, UserProfile
from zerver.tornado.django_api import send_event_on_commit


@transaction.atomic(durable=True)
def do_pin_message(*, user_profile: UserProfile, stream: Stream, message: Message) -> ChannelPin:
    pin, created = ChannelPin.objects.get_or_create(
        stream=stream,
        message=message,
        defaults={"realm": user_profile.realm, "pinned_by": user_profile},
    )
    if not created:
        raise JsonableError(_("Message is already pinned."))

    event = {"type": "channel_pin", "op": "add", **pin.to_api_dict()}
    send_event_on_commit(
        user_profile.realm, event, channel_reader_user_ids(user_profile.realm, stream)
    )
    return pin


@transaction.atomic(durable=True)
def do_unpin_message(*, user_profile: UserProfile, stream: Stream, message: Message) -> None:
    deleted, _rows = ChannelPin.objects.filter(stream=stream, message=message).delete()
    if not deleted:
        raise JsonableError(_("Message is not pinned."))

    event = {
        "type": "channel_pin",
        "op": "remove",
        "stream_id": stream.id,
        "message_id": message.id,
    }
    send_event_on_commit(
        user_profile.realm, event, channel_reader_user_ids(user_profile.realm, stream)
    )
