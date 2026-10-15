from django.http import HttpRequest, HttpResponse

from zerver.actions.pins import do_pin_message, do_unpin_message
from zerver.lib.pins import access_message_to_pin, access_pin_stream, pinned_message_dicts
from zerver.lib.response import json_success
from zerver.models import UserProfile


def get_channel_pins(
    request: HttpRequest, user_profile: UserProfile, *, stream_id: int
) -> HttpResponse:
    stream = access_pin_stream(user_profile, stream_id)
    return json_success(request, data={"pins": pinned_message_dicts(user_profile, stream)})


def pin_message(
    request: HttpRequest, user_profile: UserProfile, *, message_id: int
) -> HttpResponse:
    stream, message = access_message_to_pin(user_profile, message_id)
    do_pin_message(user_profile=user_profile, stream=stream, message=message)
    return json_success(request)


def unpin_message(
    request: HttpRequest, user_profile: UserProfile, *, message_id: int
) -> HttpResponse:
    stream, message = access_message_to_pin(user_profile, message_id)
    do_unpin_message(user_profile=user_profile, stream=stream, message=message)
    return json_success(request)
