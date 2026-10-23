from django.http import HttpRequest, HttpResponse

from zerver.lib.response import json_success
from zerver.lib.typed_endpoint import typed_endpoint_without_parameters
from zerver.lib.unread_backlog import (
    STALE_UNREAD_DAYS,
    count_stale_unread,
    mark_stale_unread_as_read,
)
from zerver.models import UserProfile


@typed_endpoint_without_parameters
def get_stale_unread(request: HttpRequest, user_profile: UserProfile) -> HttpResponse:
    return json_success(
        request, data={"count": count_stale_unread(user_profile), "days": STALE_UNREAD_DAYS}
    )


@typed_endpoint_without_parameters
def mark_stale_unread(request: HttpRequest, user_profile: UserProfile) -> HttpResponse:
    return json_success(request, data={"count": mark_stale_unread_as_read(user_profile)})
