"""Kaki's opt-in morning room digest: schedule gating, digest generation,
and the two endpoints that back it (spec 02, 04; PLAN.md WP24).

RoomDigest and RoomMeta (zerver/models/rooms.py) already exist; this module
is what fills and reads them.
"""

import json
import zoneinfo
from datetime import date, datetime, time
from typing import Any
from uuid import uuid4

from django.http import HttpRequest, HttpResponse
from django.utils.timezone import now as timezone_now
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy
from django.utils.translation import override as override_language

from zerver.actions.agent_jobs import create_job
from zerver.actions.message_send import check_message, do_send_messages
from zerver.actions.room_meta import (
    can_toggle_room_summary,
    get_room_meta_or_unsaved,
    get_room_owner,
)
from zerver.actions.streams import bulk_add_subscriptions
from zerver.lib.addressee import Addressee
from zerver.lib.agent_context import agent_transaction
from zerver.lib.exceptions import JsonableError
from zerver.lib.model_budget import model_budget_state
from zerver.lib.response import json_success
from zerver.lib.streams import access_stream_by_id, access_stream_for_send_message
from zerver.lib.timezone import canonicalize_timezone
from zerver.lib.typed_endpoint import PathOnly, typed_endpoint
from zerver.models import (
    AgentRealmSettings,
    Message,
    Realm,
    RoomDigest,
    RoomMeta,
    Stream,
    UserProfile,
    agents,
)
from zerver.models.clients import get_client

# A fixed topic so a room's digests read as one running thread, translated
# the same way as Realm.STREAM_EVENTS_NOTIFICATION_TOPIC_NAME.
ROOM_DIGEST_TOPIC_NAME = gettext_lazy("Kaki's summary")

DIGEST_HOUR = 7

# create_job caps a job's total context references (the source message
# plus selected context) at 100. Leave one slot for the source message.
# ponytail: the brief asks for up to 200 context messages; 99 is the most
# that still fits create_job's shared cap without raising it for every
# caller of that function, not only digests.
CONTEXT_MESSAGE_LIMIT = 99


def _realm_local_now(realm: Realm) -> datetime:
    """`realm`'s current time in its own configured timezone (WP14's
    quiet_rooms._realm_today follows the same pattern for its own file;
    that helper is private to its module, so this repeats the ~5 lines
    instead of importing it)."""
    settings_row = AgentRealmSettings.objects.filter(realm=realm).first()
    tz_name = "Asia/Jakarta" if settings_row is None else settings_row.timezone
    zone = zoneinfo.ZoneInfo(canonicalize_timezone(tz_name))
    return timezone_now().astimezone(zone)


def _find_builtin_agent_profile(realm: Realm, name: str) -> agents.AgentProfile | None:
    """The named built-in agent's own profile. room_meta._find_builtin_agent_bot
    runs the same lookup for its bot user only, and is private to its module."""
    return (
        agents.AgentProfile.objects.filter(
            realm=realm,
            is_builtin=True,
            name=name,
            archived_at__isnull=True,
            bot_user__is_active=True,
        )
        .select_related("bot_user")
        .order_by("id")
        .first()
    )


def _recent_message_ids(stream: Stream, since: datetime | None) -> tuple[list[int], int]:
    """Up to CONTEXT_MESSAGE_LIMIT message ids from `stream`, all topics,
    sent after `since` (or ever, when `since` is None), oldest first. Also
    returns the true total, which can be higher than the capped list."""
    assert stream.recipient_id is not None
    query = Message.objects.filter(realm=stream.realm, recipient_id=stream.recipient_id)
    if since is not None:
        query = query.filter(date_sent__gt=since)
    total = query.count()
    ids = list(query.order_by("-id").values_list("id", flat=True)[:CONTEXT_MESSAGE_LIMIT])
    ids.reverse()
    return ids, total


def _post_kaki_message(
    bot: UserProfile, stream: Stream, topic_name: str, content: str, acting_user: UserProfile
) -> Message:
    try:
        access_stream_for_send_message(bot, stream, forwarder_user_profile=None)
    except JsonableError:
        # Not yet a subscriber of a private room, or one with a restricted
        # send policy: subscribe it and retry once (room_meta._send_room_announce
        # follows the same pattern for the room-created announcement).
        bulk_add_subscriptions(stream.realm, [stream], [bot], acting_user=acting_user)
        access_stream_for_send_message(bot, stream, forwarder_user_profile=None)
    message = check_message(
        bot,
        get_client("Grow Agent"),
        Addressee.for_stream(stream, topic_name),
        content,
        realm=stream.realm,
        no_previews=True,
    )
    return Message.objects.get(id=do_send_messages([message])[0].message_id)


def _digest_request_text() -> str:
    return _(
        "Read this channel's messages since the last summary. Reply with "
        'JSON only, in this exact shape: {"decided": ["..."], "blocker": '
        '["..."], "waiting": ["..."], "summary": "..."}. Keep each item short.'
    )


def create_room_digest_job(
    stream: Stream,
    room_meta: RoomMeta,
    actor: UserProfile,
    kaki: agents.AgentProfile,
    *,
    today: date,
    context_ids: list[int],
    message_count: int,
) -> agents.AgentJob:
    """Post Kaki's digest source message, start its answer job, and file
    the room's RoomDigest row for `today`. Replaces a row already made for
    that date, so a manual re-summarize redoes the day's digest (spec 04)."""
    with override_language(stream.realm.default_language):
        topic_name = str(ROOM_DIGEST_TOPIC_NAME)
        text = _digest_request_text()
    source = _post_kaki_message(kaki.bot_user, stream, topic_name, text, actor)
    job = create_job(
        actor,
        profile=kaki,
        source=source,
        request=text,
        idempotency_key=uuid4(),
        job_kind="answer",
        delivery_target="answer",
        trigger_kind="manual",
        context_message_ids=context_ids,
        # A digest must not fail outright just because Kaki needs setup
        # attention; it waits as a blocked job instead (T-21).
        allow_blocked=True,
    )
    RoomDigest.objects.update_or_create(
        stream=stream,
        date=today,
        defaults={
            "message_count": message_count,
            "source_message": source,
            "job": job,
            "decided": [],
            "blocker": [],
            "waiting": [],
            "summary": "",
        },
    )
    room_meta.last_digest_at = timezone_now()
    room_meta.save(update_fields=["last_digest_at"])
    return job


def send_realm_room_digests(realm: Realm) -> int:
    """Post the morning digest for every opted-in room in `realm` that has
    new messages since its last one, once the realm's local time reaches
    07:00 (PLAN.md WP24 step 1). Call once per realm, inside
    agent_context.agent_realm(realm.id) (send_room_digests does this)."""
    if model_budget_state(realm) == "exceeded":
        return 0
    local_now = _realm_local_now(realm)
    if local_now.time() < time(DIGEST_HOUR, 0):
        return 0
    today = local_now.date()
    kaki = _find_builtin_agent_profile(realm, "Kaki")
    if kaki is None:
        # Built-in agents are seeded per realm by a separate command; skip
        # quietly rather than fail the whole run until that has happened
        # (room_meta._send_room_announce follows the same precedent).
        return 0
    sent = 0
    room_metas = RoomMeta.objects.filter(
        stream__realm=realm, stream__deactivated=False, summary_enabled=True
    ).select_related("stream", "summary_enabled_by")
    for room_meta in room_metas:
        if RoomDigest.objects.filter(stream=room_meta.stream, date=today).exists():
            continue
        ids, total = _recent_message_ids(room_meta.stream, room_meta.last_digest_at)
        if not ids:
            continue
        actor = room_meta.summary_enabled_by
        if actor is None or not actor.is_active:
            actor = get_room_owner(room_meta.stream)
        if actor is None:
            continue
        try:
            with agent_transaction():
                create_room_digest_job(
                    room_meta.stream,
                    room_meta,
                    actor,
                    kaki,
                    today=today,
                    context_ids=ids,
                    message_count=total,
                )
        except (ValueError, JsonableError, AgentRealmSettings.DoesNotExist):
            # One room's broken configuration, or the realm's agents not
            # being enabled at all, must not stop every other room's
            # digest in this realm, nor any other realm's (T-21).
            continue
        sent += 1
    return sent


def _digest_data(digest: RoomDigest) -> dict[str, Any]:
    return {
        "date": digest.date.isoformat(),
        "decided": digest.decided,
        "blocker": digest.blocker,
        "waiting": digest.waiting,
        "summary": digest.summary,
        "message_count": digest.message_count,
    }


def _parse_digest_date(raw: str) -> date:
    try:
        return date.fromisoformat(raw)
    except ValueError:
        raise JsonableError(_("Enter the date as YYYY-MM-DD."))


@typed_endpoint
def summarize_room(
    request: HttpRequest, user_profile: UserProfile, *, stream_id: PathOnly[int]
) -> HttpResponse:
    """POST .../summarize (spec 04): a one-off re-run of the room's
    digest, room owner only (04-A3)."""
    (stream, _sub) = access_stream_by_id(user_profile, stream_id, require_content_access=False)
    room_meta = get_room_meta_or_unsaved(stream)
    if not room_meta.summary_enabled:
        raise JsonableError(_("Turn on summaries for this channel first."))
    if not can_toggle_room_summary(user_profile, stream):
        raise JsonableError(_("You do not have permission to change this channel."))
    kaki = _find_builtin_agent_profile(stream.realm, "Kaki")
    if kaki is None:
        raise JsonableError(_("Summaries are not available for this organization."))
    today = _realm_local_now(stream.realm).date()
    ids, total = _recent_message_ids(stream, room_meta.last_digest_at)
    try:
        with agent_transaction():
            job = create_room_digest_job(
                stream,
                room_meta,
                user_profile,
                kaki,
                today=today,
                context_ids=ids,
                message_count=total,
            )
    except AgentRealmSettings.DoesNotExist:
        raise JsonableError(_("Summaries are not available for this organization."))
    except ValueError as e:
        raise JsonableError(str(e))
    return json_success(request, data={"job_id": str(job.id)})


@typed_endpoint
def get_room_digest_view(
    request: HttpRequest,
    user_profile: UserProfile,
    *,
    stream_id: PathOnly[int],
    date: str | None = None,
) -> HttpResponse:
    """GET .../digest?date= (spec 02): the room's digest for one day, for
    the Room banner. `date` defaults to the realm's local today."""
    (stream, _sub) = access_stream_by_id(user_profile, stream_id)
    target_date = (
        _parse_digest_date(date) if date is not None else _realm_local_now(stream.realm).date()
    )
    digest = RoomDigest.objects.filter(stream=stream, date=target_date).first()
    return json_success(request, data={"digest": None if digest is None else _digest_data(digest)})


def _coerce_str_list(value: object) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [value]
    return []


def _render_digest_text(job: agents.AgentJob, digest: RoomDigest, fallback: str) -> str:
    with override_language(job.realm.default_language):
        lines = [digest.summary] if digest.summary else []
        for label, items in (
            (_("Decisions:"), digest.decided),
            (_("Blocker:"), digest.blocker),
            (_("Waiting on:"), digest.waiting),
        ):
            if items:
                lines.append(label)
                lines.extend(f"- {item}" for item in items)
    return "\n".join(lines) if lines else fallback


def fill_room_digest(job: agents.AgentJob, answer: str) -> str | None:
    """When `job` backs a RoomDigest, parse its answer as the digest JSON,
    fill the row, and return the human-readable text to post in the room
    instead of raw JSON (PLAN.md WP24 step 2). Broken JSON still fills the
    row, with `answer` kept as-is in `summary`. Returns None for a job
    that is not a digest job, so the caller keeps `answer` unchanged."""
    digest = RoomDigest.objects.filter(job=job).first()
    if digest is None:
        return None
    try:
        parsed = json.loads(answer)
        if not isinstance(parsed, dict):
            raise ValueError("Digest reply is not a JSON object.")
    except ValueError:
        digest.summary = answer
        digest.save(update_fields=["summary"])
        return answer
    digest.decided = _coerce_str_list(parsed.get("decided"))
    digest.blocker = _coerce_str_list(parsed.get("blocker"))
    digest.waiting = _coerce_str_list(parsed.get("waiting"))
    digest.summary = str(parsed.get("summary") or "")
    digest.save(update_fields=["decided", "blocker", "waiting", "summary"])
    return _render_digest_text(job, digest, answer)
