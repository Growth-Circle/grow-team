"""Views for room meta and room topics (spec 01, 04; PLAN.md WP14)."""

from datetime import date
from typing import Any

from django.db.models import Count, Q
from django.http import HttpRequest, HttpResponse
from django.utils.translation import gettext as _
from pydantic import Json
from pydantic_partials.sentinels import Missing, MissingType

from zerver.actions.room_meta import (
    DUE_DATE_UNSET, DueDateUnset, can_edit_room_meta, can_toggle_room_summary,
    do_update_room_meta, get_room_meta_or_unsaved, get_room_owner,
)
from zerver.lib.exceptions import JsonableError
from zerver.lib.response import json_success
from zerver.lib.room_topics import get_room_topics
from zerver.lib.streams import access_stream_by_id
from zerver.lib.typed_endpoint import PathOnly, typed_endpoint
from zerver.models import DriveFolderLink, RoomChannelLink, RoomMeta, Stream, Subscription, UserProfile


def _parse_due_date(raw: str) -> date:
    try:
        return date.fromisoformat(raw)
    except ValueError:
        raise JsonableError(_("Enter the date as YYYY-MM-DD."))


def _room_meta_data(
    user_profile: UserProfile, stream: Stream, room_meta: RoomMeta
) -> dict[str, Any]:
    owner = get_room_owner(stream)
    assert stream.recipient_id is not None
    counts = Subscription.objects.filter(
        recipient_id=stream.recipient_id, active=True, user_profile__is_active=True
    ).aggregate(
        people_count=Count("id", filter=Q(user_profile__is_bot=False)),
        agent_count=Count("id", filter=Q(user_profile__agent_profile__isnull=False)),
    )
    whatsapp = RoomChannelLink.objects.filter(
        stream=stream, provider="whatsapp", removed_at__isnull=True
    ).exists()
    drive_folder_link = (
        DriveFolderLink.objects.filter(stream=stream, removed_at__isnull=True)
        .order_by("id").first()
    )
    can_edit_meta = not stream.deactivated and can_edit_room_meta(user_profile, stream)
    can_toggle_summary = not stream.deactivated and can_toggle_room_summary(user_profile, stream)
    return {
        "stream_id": stream.id,
        "folder_name": None if stream.folder is None else stream.folder.name,
        "owner": None if owner is None else {"id": owner.id, "full_name": owner.full_name},
        "due_date": None if room_meta.due_date is None else room_meta.due_date.isoformat(),
        "summary_enabled": room_meta.summary_enabled,
        "people_count": counts["people_count"],
        "agent_count": counts["agent_count"],
        "whatsapp": whatsapp,
        "drive_folder_link_id": None if drive_folder_link is None else drive_folder_link.id,
        "can_edit_meta": can_edit_meta,
        "can_toggle_summary": can_toggle_summary,
    }


@typed_endpoint
def get_room_meta(
    request: HttpRequest, user_profile: UserProfile, *, stream_id: PathOnly[int],
) -> HttpResponse:
    (stream, _sub) = access_stream_by_id(
        user_profile, stream_id, require_active_channel=False, require_content_access=False
    )
    room_meta = get_room_meta_or_unsaved(stream)
    return json_success(request, data=_room_meta_data(user_profile, stream, room_meta))


@typed_endpoint
def update_room_meta(
    request: HttpRequest, user_profile: UserProfile, *, stream_id: PathOnly[int],
    announce: Json[bool] = False, due_date: Json[str | None] | MissingType = Missing,
    summary_enabled: Json[bool] | None = None,
) -> HttpResponse:
    (stream, _sub) = access_stream_by_id(user_profile, stream_id, require_content_access=False)
    if (not isinstance(due_date, MissingType) or announce) and not can_edit_room_meta(
        user_profile, stream
    ):
        raise JsonableError(_("You do not have permission to change this channel."))
    if summary_enabled is not None and not can_toggle_room_summary(user_profile, stream):
        raise JsonableError(_("You do not have permission to change this channel."))
    parsed_due_date: date | None | DueDateUnset = DUE_DATE_UNSET
    if not isinstance(due_date, MissingType):
        parsed_due_date = None if due_date is None else _parse_due_date(due_date)
    room_meta = do_update_room_meta(
        stream, due_date=parsed_due_date, summary_enabled=summary_enabled,
        announce=announce, acting_user=user_profile,
    )
    return json_success(request, data=_room_meta_data(user_profile, stream, room_meta))


@typed_endpoint
def get_room_topics_view(
    request: HttpRequest, user_profile: UserProfile, *, stream_id: PathOnly[int],
) -> HttpResponse:
    (stream, _sub) = access_stream_by_id(user_profile, stream_id, require_active_channel=False)
    return json_success(request, data={"topics": get_room_topics(user_profile, stream)})
