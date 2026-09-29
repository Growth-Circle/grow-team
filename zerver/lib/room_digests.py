"""Kaki's opt-in morning summary of a room: the schedule, the digest job,
and the two endpoints that back the Room banner.

RoomDigest and RoomMeta (zerver/models/rooms.py) hold the data. This
module fills and reads them.
"""

import json
import logging
import re
import zoneinfo
from datetime import date, datetime, time, timedelta
from typing import Any
from uuid import uuid4

from django.http import HttpRequest, HttpResponse
from django.utils.timezone import now as timezone_now
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy
from django.utils.translation import override as override_language

from zerver.actions.agent_jobs import TERMINAL, agent_language, create_job
from zerver.actions.message_send import check_message, do_send_messages
from zerver.actions.room_meta import (
    can_toggle_room_summary,
    get_room_meta_or_unsaved,
    get_room_owner,
)
from zerver.actions.streams import bulk_add_subscriptions
from zerver.lib.addressee import Addressee
from zerver.lib.agent_context import AgentBusy, agent_transaction
from zerver.lib.agent_events import send_room_meta_event
from zerver.lib.agent_policy import AgentAccessDenied
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

logger = logging.getLogger(__name__)

# A fixed topic, so the digests of a room read as one running thread.
ROOM_DIGEST_TOPIC_NAME = gettext_lazy("Kaki's summary")

DIGEST_HOUR = 7

# The first summary of a room, and every manual one, covers this much time.
DIGEST_LOOKBACK = timedelta(hours=24)

# The job protocol allows 100 context references in one job, and the
# digest's own source message takes one of them.
CONTEXT_MESSAGE_LIMIT = 99

# A digest is a routine job, so it runs on the cheap model preset. The
# preset of a job comes from its profile, and Kaki's profile serves other
# work too: the code that picks the model asks is_room_digest_job.
ROOM_DIGEST_MODEL_PRESET = "fast"


def _realm_local_now(realm: Realm) -> datetime:
    """The current time in the timezone that the workspace chose."""
    tz_name = (
        AgentRealmSettings.objects.filter(realm=realm).values_list("timezone", flat=True).first()
    ) or "Asia/Jakarta"
    return timezone_now().astimezone(zoneinfo.ZoneInfo(canonicalize_timezone(tz_name)))


def _find_kaki(realm: Realm) -> agents.AgentProfile | None:
    """The built-in planner agent. The role finds it, so a rename does not
    hide it (ensure_builtin_agents.live_builtin does the same)."""
    return (
        agents.AgentProfile.objects.filter(
            realm=realm,
            is_builtin=True,
            agent_role="planner",
            archived_at__isnull=True,
            bot_user__is_active=True,
        )
        .select_related("bot_user")
        .order_by("-created_at")
        .first()
    )


def _recent_message_ids(stream: Stream, since: datetime) -> list[int]:
    """The ids of the newest messages that `stream` got after `since`, in
    every topic, oldest first, at most CONTEXT_MESSAGE_LIMIT of them. The
    messages of earlier digests do not count: Kaki must not summarize its
    own summaries, and a quiet room must stay quiet.

    ponytail: a digest row keeps only its newest job and line. A manual
    summary replaces the row of the day, so the card of the digest that it
    replaced counts as one new message later. Keep every job and line of a
    day in a table of their own, if that ever matters."""
    assert stream.recipient_id is not None
    digests = RoomDigest.objects.filter(stream=stream)
    query = (
        Message.objects.filter(
            realm_id=stream.realm_id, recipient_id=stream.recipient_id, date_sent__gt=since
        )
        .exclude(id__in=digests.filter(source_message__isnull=False).values("source_message_id"))
        .exclude(
            id__in=digests.filter(job__result_message__isnull=False).values(
                "job__result_message_id"
            )
        )
    )
    ids = list(query.order_by("-id").values_list("id", flat=True)[:CONTEXT_MESSAGE_LIMIT])
    ids.reverse()
    return ids


def _post_kaki_message(
    bot: UserProfile, stream: Stream, topic_name: str, content: str, acting_user: UserProfile
) -> Message:
    try:
        access_stream_for_send_message(bot, stream, forwarder_user_profile=None)
    except JsonableError:
        # Kaki is not yet in a private room, or the room limits who can
        # post: add Kaki to the room and try once more.
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


def create_room_digest_job(
    stream: Stream,
    actor: UserProfile,
    kaki: agents.AgentProfile,
    *,
    today: date,
    context_ids: list[int],
) -> agents.AgentJob:
    """Post Kaki's source message, start the answer job for it, and file
    the RoomDigest of `today` as waiting for that job. A row that already
    exists for `today` starts over, so a manual summary redoes the day.

    The first line of the request is the job's title on its card, so it
    must read well without the rest."""
    with override_language(agent_language(stream.realm_id)):
        topic_name = str(ROOM_DIGEST_TOPIC_NAME)
        title = _("Summarize this channel since the last summary.")
        request = f"{title}\n\n" + _(
            "Read the messages of this channel since the last summary. Reply with "
            'JSON only, in this exact shape: {"decided": ["..."], "blocker": '
            '["..."], "waiting": ["..."], "summary": "..."}. Keep each item short.'
        )
    source = _post_kaki_message(kaki.bot_user, stream, topic_name, title, actor)
    job = create_job(
        actor,
        profile=kaki,
        source=source,
        request=request,
        idempotency_key=uuid4(),
        job_kind="answer",
        delivery_target="answer",
        trigger_kind="manual",
        context_message_ids=context_ids,
        # A job for Kaki that needs setup waits as a blocked job. It does
        # not stop the summaries of the other rooms.
        allow_blocked=True,
    )
    RoomDigest.objects.update_or_create(
        stream=stream,
        date=today,
        defaults={
            "message_count": len(context_ids),
            "source_message": source,
            "job": job,
            "decided": [],
            "blocker": [],
            "waiting": [],
            "summary": "",
        },
    )
    return job


def is_room_digest_job(job: agents.AgentJob) -> bool:
    return RoomDigest.objects.filter(job=job).exists()


def _digest_actor(room_meta: RoomMeta) -> UserProfile | None:
    """Who the digest job runs for: the person who turned summaries on,
    or the room owner when that person has left."""
    actor = room_meta.summary_enabled_by
    if actor is not None and actor.is_active and not actor.is_bot:
        return actor
    return get_room_owner(room_meta.stream)


def _send_room_digest(
    room_meta: RoomMeta, kaki: agents.AgentProfile, today: date, context_ids: list[int]
) -> bool:
    """Start the digest job of one room. Call inside agent_transaction().
    The checks that decided to call it run again here, under the lock, so
    two runs at the same time never start two digests of one room."""
    current = RoomMeta.objects.select_for_update().get(id=room_meta.id)
    if (
        not current.summary_enabled
        or RoomDigest.objects.filter(stream_id=current.stream_id, date=today).exists()
    ):
        return False
    actor = _digest_actor(room_meta)
    if actor is None:
        return False
    create_room_digest_job(room_meta.stream, actor, kaki, today=today, context_ids=context_ids)
    current.last_digest_at = timezone_now()
    current.save(update_fields=["last_digest_at"])
    return True


def send_realm_room_digests(realm: Realm) -> int:
    """Start the morning digest of every room in `realm` that turned
    summaries on and has new messages since its last digest, once the
    workspace's own clock reaches 07:00. Returns how many digests started.
    Call once per realm, inside agent_context.agent_realm(realm.id)."""
    if not AgentRealmSettings.objects.filter(realm=realm, enabled=True).exists():
        return 0
    if model_budget_state(realm) == "exceeded":
        return 0
    local_now = _realm_local_now(realm)
    if local_now.time() < time(DIGEST_HOUR, 0):
        return 0
    kaki = _find_kaki(realm)
    if kaki is None:
        return 0
    today = local_now.date()
    done_stream_ids = set(
        RoomDigest.objects.filter(stream__realm=realm, date=today).values_list(
            "stream_id", flat=True
        )
    )
    sent = 0
    # Only a room that turned summaries on is read. The room's history stays
    # unread for a room that did not.
    room_metas = RoomMeta.objects.filter(
        stream__realm=realm, stream__deactivated=False, summary_enabled=True
    ).select_related("stream", "summary_enabled_by")
    for room_meta in room_metas:
        if room_meta.stream_id in done_stream_ids:
            continue
        since = room_meta.last_digest_at or timezone_now() - DIGEST_LOOKBACK
        context_ids = _recent_message_ids(room_meta.stream, since)
        if not context_ids:
            continue
        try:
            with agent_transaction():
                started = _send_room_digest(room_meta, kaki, today, context_ids)
        except AgentBusy:
            # Another agent write holds the lock. The next run tries again.
            continue
        except (ValueError, JsonableError, AgentRealmSettings.DoesNotExist) as error:
            # One room that cannot be summarized must not stop the summaries
            # of the other rooms.
            logger.warning("No digest for channel %s: %s", room_meta.stream_id, error)
            continue
        sent += started
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
    """The owner of a room asks Kaki for a summary of the last 24 hours."""
    (stream, _sub) = access_stream_by_id(user_profile, stream_id, require_content_access=False)
    if not can_toggle_room_summary(user_profile, stream):
        raise JsonableError(_("Only the channel owner can ask for a summary."))
    if not get_room_meta_or_unsaved(stream).summary_enabled:
        raise JsonableError(_("Turn on summaries for this channel first."))
    if model_budget_state(stream.realm) == "exceeded":
        raise JsonableError(
            _("The budget for this month is used up. Kaki cannot write a summary now.")
        )
    kaki = _find_kaki(stream.realm)
    if kaki is None:
        raise JsonableError(
            _("Kaki is not available in this workspace. Ask an admin to turn it on.")
        )
    today = _realm_local_now(stream.realm).date()
    try:
        with agent_transaction():
            waiting = (
                RoomDigest.objects.filter(stream=stream, date=today, job__isnull=False)
                .select_related("job")
                .first()
            )
            if (
                waiting is not None
                and waiting.job is not None
                and waiting.job.status not in TERMINAL
            ):
                # Kaki is already writing this summary.
                return json_success(request, data={"job_id": str(waiting.job.id)})
            context_ids = _recent_message_ids(stream, timezone_now() - DIGEST_LOOKBACK)
            if not context_ids:
                raise JsonableError(_("There are no new messages to summarize."))
            job = create_room_digest_job(
                stream, user_profile, kaki, today=today, context_ids=context_ids
            )
    except (AgentRealmSettings.DoesNotExist, ValueError, AgentAccessDenied):
        raise JsonableError(_("Kaki cannot write a summary for this channel right now."))
    return json_success(request, data={"job_id": str(job.id)})


@typed_endpoint
def get_room_digest_view(
    request: HttpRequest,
    user_profile: UserProfile,
    *,
    stream_id: PathOnly[int],
    date: str | None = None,
) -> HttpResponse:
    """The digest of one day for the Room banner: today in the workspace's
    timezone, when `date` is not given."""
    (stream, _sub) = access_stream_by_id(user_profile, stream_id)
    target_date = (
        _parse_digest_date(date) if date is not None else _realm_local_now(stream.realm).date()
    )
    digest = RoomDigest.objects.filter(stream=stream, date=target_date).first()
    return json_success(request, data={"digest": None if digest is None else _digest_data(digest)})


def _coerce_str_list(value: object) -> list[str]:
    """The model's list as a list of one-line strings. A single string
    counts as a list of one item."""
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return []
    items = (" ".join(str(item).split()) for item in value)
    return [item for item in items if item]


def _parse_digest_answer(answer: str) -> dict[str, Any] | None:
    """The digest object in the model's answer, or None when there is none.
    A model often wraps the JSON in a code fence or a line of prose."""
    text = answer.strip()
    fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", text, re.DOTALL | re.IGNORECASE)
    if fenced is not None:
        text = fenced.group(1)
    candidates = [text]
    start, end = text.find("{"), text.rfind("}")
    if 0 <= start < end:
        candidates.append(text[start : end + 1])
    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except ValueError:
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def _render_digest_text(digest: RoomDigest, language: str) -> str:
    """The digest as a message that people read: the summary, then the
    sections that have items."""
    with override_language(language):
        sections = [digest.summary] if digest.summary else []
        for label, items in (
            (_("Decisions"), digest.decided),
            (_("Blockers"), digest.blocker),
            (_("Waiting on"), digest.waiting),
        ):
            if items:
                sections.append(f"**{label}**\n\n" + "\n".join(f"- {item}" for item in items))
        return "\n\n".join(sections) if sections else _("Nothing to report.")


def fill_room_digest(job: agents.AgentJob, answer: str, *, keep: bool = True) -> str | None:
    """When `job` writes a room digest, read the digest from its `answer`
    and return the message to post instead of the raw answer. Returns None
    for any other job.

    `keep` fills the RoomDigest, so the Room banner shows it. An answer that
    is not a digest object stays as text in `summary`. Pass keep=False when
    the answer goes to one person only: the room must not see it then."""
    digest = RoomDigest.objects.select_related("stream").filter(job=job).first()
    if digest is None:
        return None
    parsed = _parse_digest_answer(answer)
    if parsed is None:
        digest.summary = answer
    else:
        digest.decided = _coerce_str_list(parsed.get("decided"))
        digest.blocker = _coerce_str_list(parsed.get("blocker"))
        digest.waiting = _coerce_str_list(parsed.get("waiting"))
        digest.summary = str(parsed.get("summary") or "").strip()
    text = answer if parsed is None else _render_digest_text(digest, agent_language(job.realm_id))
    if keep:
        digest.save(update_fields=["decided", "blocker", "waiting", "summary"])
        # An open Room shows the banner as soon as the digest is ready.
        send_room_meta_event(get_room_meta_or_unsaved(digest.stream))
    return text
