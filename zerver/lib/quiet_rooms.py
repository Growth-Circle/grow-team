"""Detect rooms with no recent activity: the quiet-channels list (spec 01,
04) and the candidates for the daily notify_quiet_rooms digest (PLAN.md
WP14 step 3 and 5; map-backend.md O8)."""

import zoneinfo
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

from django.db.models import Max
from django.utils.timezone import now as timezone_now

from zerver.lib.streams import get_streams_for_user
from zerver.lib.timestamp import datetime_to_timestamp
from zerver.lib.timezone import canonicalize_timezone
from zerver.models import AgentRealmSettings, Message, Realm, RoomMeta, Stream, UserProfile

QUIET_THRESHOLD_DAYS = 30


def compute_quiet_streams(
    streams: Sequence[Stream], *, threshold_days: int = QUIET_THRESHOLD_DAYS
) -> list[tuple[Stream, datetime]]:
    """The streams among `streams` with no message in the last
    `threshold_days` days (or none ever), found with one aggregate query:
    `Max(date_sent)` per room, over the zerver_message_realm_recipient_
    date_sent index. Every stream in `streams` must belong to the same
    realm: filtering on that realm_id (WP14 review defect 7) is what lets
    Postgres use that index's leading column, instead of scanning every
    realm's messages for a matching recipient_id."""
    if not streams:
        return []
    assert all(stream.realm_id == streams[0].realm_id for stream in streams)
    recipient_ids = [stream.recipient_id for stream in streams]
    rows = (
        Message.objects.filter(realm_id=streams[0].realm_id, recipient_id__in=recipient_ids)
        .values("recipient_id")
        .annotate(last_sent=Max("date_sent"))
    )
    last_sent_by_recipient = {row["recipient_id"]: row["last_sent"] for row in rows}
    threshold = timezone_now() - timedelta(days=threshold_days)
    quiet: list[tuple[Stream, datetime]] = []
    for stream in streams:
        last_activity = last_sent_by_recipient.get(stream.recipient_id) or stream.date_created
        if last_activity < threshold:
            quiet.append((stream, last_activity))
    return quiet


def get_quiet_channels(user_profile: UserProfile) -> dict[str, Any]:
    if user_profile.is_guest:
        return {"threshold_days": QUIET_THRESHOLD_DAYS, "quiet_channels": []}
    streams = get_streams_for_user(user_profile)
    quiet_channels = [
        {"stream_id": stream.id, "last_message_at": datetime_to_timestamp(last_activity)}
        for stream, last_activity in compute_quiet_streams(streams)
    ]
    return {"threshold_days": QUIET_THRESHOLD_DAYS, "quiet_channels": quiet_channels}


@dataclass
class QuietRoomNotice:
    stream: Stream
    room_meta: RoomMeta
    due_date_passed: bool


def _realm_today(realm: Realm) -> date:
    """`realm`'s current date in its own configured timezone (WP14 review
    defect 11b). notify_quiet_rooms runs once a day on a fixed schedule;
    comparing against the UTC date made the overdue-project reminder a
    day late for any realm whose local midnight falls before UTC
    midnight."""
    settings_row = AgentRealmSettings.objects.filter(realm=realm).first()
    tz_name = "Asia/Jakarta" if settings_row is None else settings_row.timezone
    time_zone = zoneinfo.ZoneInfo(canonicalize_timezone(tz_name))
    return timezone_now().astimezone(time_zone).date()


def rooms_needing_notice(realm: Realm) -> list[QuietRoomNotice]:
    """Rooms whose owner has not yet been told about the *current* reason:
    quiet 30+ days, or a Proyek-type room past its due date (PLAN.md WP14
    step 5, Q-02). `RoomMeta.quiet_notified_at` still gates both reasons,
    but a room that goes active again and then quiet again, or that was
    quiet-notified before it also became overdue, notifies again (WP14
    review defect 10)."""
    streams = list(Stream.objects.filter(realm=realm, deactivated=False).select_related("folder"))
    quiet_by_stream_id = {
        stream.id: last_activity for stream, last_activity in compute_quiet_streams(streams)
    }

    today = _realm_today(realm)
    overdue_room_metas = {
        room_meta.stream_id: room_meta
        for room_meta in RoomMeta.objects.filter(
            stream__realm=realm,
            stream__deactivated=False,
            # Case-insensitive: a folder created by hand as "proyek" is
            # still the Proyek folder (WP14 review defect 11a).
            stream__folder__name__iexact="Proyek",
            due_date__isnull=False,
            due_date__lt=today,
        )
    }

    candidate_ids = set(quiet_by_stream_id) | set(overdue_room_metas)
    # A room that went active again, or that stopped being an overdue
    # Proyek room, must notify again the next time it re-qualifies
    # (WP14 review defect 6): clear its old notified mark now.
    RoomMeta.objects.filter(stream__realm=realm, quiet_notified_at__isnull=False).exclude(
        stream_id__in=candidate_ids
    ).update(quiet_notified_at=None)
    if not candidate_ids:
        return []

    streams_by_id = {stream.id: stream for stream in streams if stream.id in candidate_ids}
    existing_room_metas = {
        room_meta.stream_id: room_meta
        for room_meta in RoomMeta.objects.filter(stream_id__in=candidate_ids)
    }

    notices = []
    for stream_id in candidate_ids:
        room_meta = existing_room_metas.get(stream_id) or overdue_room_metas.get(stream_id)
        if room_meta is None:
            room_meta, _created = RoomMeta.objects.get_or_create(stream_id=stream_id)

        due_date_passed = stream_id in overdue_room_metas
        if due_date_passed:
            due_date = overdue_room_metas[stream_id].due_date
            assert due_date is not None
            should_notify = (
                room_meta.quiet_notified_at is None
                or room_meta.quiet_notified_at.date() <= due_date
            )
        else:
            last_activity = quiet_by_stream_id[stream_id]
            should_notify = (
                room_meta.quiet_notified_at is None or room_meta.quiet_notified_at < last_activity
            )
        if not should_notify:
            continue
        notices.append(
            QuietRoomNotice(
                stream=streams_by_id[stream_id],
                room_meta=room_meta,
                due_date_passed=due_date_passed,
            )
        )
    return notices
