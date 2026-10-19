"""Perlu kamu: one read-only list of approvals, decisions, and mentions a
user must act on (spec 03), plus the small write path that lets a user mark
a mention done or undo that.

Approval and decision items only ever reflect state owned elsewhere
(AgentApproval, AgentJob); the buttons a client renders for them call the
existing approval and input endpoints, never a path in this module (spec 03
"Tombol di UI hanya memanggil endpoint approval yang sama"). Only a mention
has a resolution this module writes itself, because Zulip has no "answer
this mention" endpoint to defer to.

One case never shows up here: a job "verifying" with blocked_reason
"audience_changed" also waits on its requester (job_data()'s
"deliver_privately" action, zerver/views/agent_jobs.py). It has no approval
row and no input.requested event to build an item from, and it is rare
enough (the audience changing mid-job) that this list leaves it out rather
than add a fourth kind.
"""

import re
from datetime import datetime, time, timedelta, timezone
from typing import NotRequired, TypedDict
from uuid import UUID
from zoneinfo import ZoneInfo

from django.core.exceptions import ObjectDoesNotExist
from django.db import transaction
from django.db.models import Q, QuerySet
from django.utils.timezone import now as timezone_now

from zerver.actions.agent_jobs import check_attempt_access
from zerver.actions.tasks import do_resolve_task_notifications
from zerver.lib.agent_context import agent_transaction, require_job_access
from zerver.lib.exceptions import JsonableError
from zerver.lib.mcp_preview import PreviewTarget
from zerver.lib.message import access_message
from zerver.lib.push_notifications import get_mobile_push_content
from zerver.lib.realm_clock import realm_local_now
from zerver.lib.role_permissions import has_role_permission
from zerver.lib.task_reminders import REMINDER_HOUR
from zerver.lib.tasks import realm_task_id_prefix, user_can_access_task
from zerver.lib.topic import messages_for_topic
from zerver.models import (
    Attachment,
    MCPPendingCall,
    Message,
    MutedUser,
    Reaction,
    Realm,
    Subscription,
    TaskNotification,
    UserMessage,
    UserProfile,
    agents,
)
from zerver.models.needs import NeedResolution, NeedSnooze
from zerver.views.agent_jobs import needs_my_action

# Q-06 / Q-32: decisions and mentions keep a fixed display TTL. Approvals use
# the workspace's approval_ttl_minutes (WP03 AgentRealmSettings) instead.
DECISION_TTL = timedelta(hours=24)
MENTION_TTL = timedelta(days=7)
# A team-wide mention (a daily summary, say) is stale sooner than a mention
# of one person: it leaves the list after this, though it stays in its channel.
BROADCAST_TTL = timedelta(hours=48)
# Q-05: a read mention only becomes a need again once this much time has
# passed with no reply from the mentioned user in that conversation.
MENTION_REPLY_GRACE = timedelta(hours=2)

_ROW_LIMIT = 200

# One call that handles many items stays as small as a list.
BULK_LIMIT = _ROW_LIMIT

# "Undo" after a bulk mark-done is a matter of seconds. A card item is
# reopened only if it was closed this recently, so an old, separate closing
# of the same card is never undone with it.
CARD_UNDO_WINDOW = timedelta(minutes=5)

# A message that names this many people, or a wildcard, is team-wide: it
# reaches the user as one of many, so the list hides it unless asked.
TEAM_WIDE_MENTION_MIN_PEOPLE = 3

_USER_MENTION_RE = re.compile(r'<span class="user-mention(?: silent)?" data-user-id="(\d+)"')
_WILDCARD_MENTION_RE = re.compile(
    r'<span class="(?:user-mention channel-wildcard-mention|topic-mention)"'
)

# The run of @-mentions a message opens with, and what separates them. They
# fill the excerpt with names while the person is shown who asked anyway.
_LEADING_MENTIONS_RE = re.compile(
    r'^<p>(?:[\s,:;]|&nbsp;|<span class="(?:user-mention|user-group-mention|topic-mention)[^"]*"[^>]*>[^<]*</span>)+'
)
_SENTENCE_END_RE = re.compile(r"([.!?])(?=\s|$)|\n")
_EXCERPT_LIMIT = 400

KINDS = ("approval", "mention", "decision", "card_mention", "card_assigned")

# An approval for a call that an external client made to an Ask first tool has
# this prefix in its id instead of "approval" (spec 03: no new kind).
MCP_CALL_PREFIX = "mcp_call"
# A call that waited past its expiry stays listed, marked expired, this long.
MCP_CALL_EXPIRED_GRACE = timedelta(hours=24)
# The card shows this much of the content, and the person can open the rest.
MCP_CALL_TEXT_LIMIT = 600


class NeedActor(TypedDict):
    type: str
    id: int
    name: str
    agent_role: str | None
    shape: str | None
    color: str | None
    initials: str


class NeedAttachment(TypedDict):
    kind: str
    id: str
    filename: str


class NeedTask(TypedDict):
    id: int
    display_id: str
    title: str


class NeedMcpCall(TypedDict):
    call_id: str
    grant_name: str
    tool: str
    target: PreviewTarget
    # The whole content; the item's text keeps only the start of it.
    content: str
    mentions: list[str]
    digest: str


class NeedItem(TypedDict):
    id: str
    kind: str
    actor: NeedActor
    stream_id: int | None
    topic: str | None
    time: str
    # For a mention, the first sentence after its leading @-mentions.
    text: str
    # `text` as it is, without that trimming. Same as `text` for other kinds.
    raw_text: str
    attachment: NeedAttachment | None
    actions: list[str]
    expires_at: str
    expired: bool
    # 03-D6: when and how the user handled the item; null while it is open.
    resolved_at: str | None
    resolved_action: str | None
    # The arguments the approval and input endpoints need, so a client can
    # act on an item without first loading its job. A mention has none.
    job_id: str | None
    approval_version: int | None
    operation_hash: str | None
    nonce: str | None
    job_version: int | None
    # The card a card_mention or card_assigned item is about; null otherwise.
    task: NeedTask | None
    # A mention that names 3 or more people, or a wildcard, in a channel,
    # and is not addressed to this user alone. Always false for other kinds.
    broadcast: bool
    # Only on an approval for an external client's call.
    mcp_call: NotRequired[NeedMcpCall]


class NeedCounts(TypedDict):
    approval: int
    mention: int
    decision: int
    card_mention: int
    card_assigned: int
    all: int


class BulkNeedsResult(TypedDict):
    # The ids that were handled and the ones that were not, in the order
    # given. A failure leaves the other items done.
    done: list[str]
    failed: list[str]


class NeedsResult(TypedDict):
    items: list[NeedItem]
    # What a person sees by default: team-wide mentions are not in here.
    counts: NeedCounts
    broadcast_counts: NeedCounts


def _initials(full_name: str) -> str:
    """Mirrors web/src/people.ts's initials_for_full_name for parity."""
    words = full_name.split()
    if len(words) > 1:
        return (words[0][0] + words[-1][0]).upper()
    return "".join(words)[:2].upper()


def _plain_excerpt(rendered_content: str) -> str:
    return get_mobile_push_content(rendered_content)[:_EXCERPT_LIMIT]


def _mention_excerpt(rendered_content: str) -> str:
    """The first sentence of a message once its leading @-mentions are gone,
    so the excerpt shows what is asked instead of who is addressed. A message
    that is nothing but mentions keeps its whole text."""
    stripped = _LEADING_MENTIONS_RE.sub("<p>", rendered_content, count=1)
    text = get_mobile_push_content(stripped).strip()
    if text == "":
        return _plain_excerpt(rendered_content)
    sentence_end = _SENTENCE_END_RE.search(text)
    if sentence_end is not None:
        # The punctuation stays with its sentence; a line break does not.
        text = text[: sentence_end.end() if sentence_end.group(1) else sentence_end.start()]
    return text[:_EXCERPT_LIMIT]


def _agent_actor(profile: agents.AgentProfile) -> NeedActor:
    return {
        "type": "agent",
        "id": profile.bot_user_id,
        "name": profile.name,
        "agent_role": profile.agent_role,
        "shape": profile.avatar_shape,
        "color": profile.avatar_color,
        "initials": _initials(profile.name),
    }


def _human_actor(user: UserProfile) -> NeedActor:
    return {
        "type": "human",
        "id": user.id,
        "name": user.full_name,
        "agent_role": None,
        "shape": None,
        "color": None,
        "initials": _initials(user.full_name),
    }


def _job_scope(job: agents.AgentJob) -> tuple[int | None, str | None]:
    scope = job.conversation.scope
    return scope.get("stream_id"), scope.get("topic")


def _approval_attachment(operation: agents.AgentOperation) -> NeedAttachment | None:
    artifact_id = operation.scope_binding.get("diff_artifact_id")
    artifact = (
        agents.AgentArtifact.objects.filter(id=artifact_id, unavailable_at__isnull=True).first()
        if artifact_id
        else None
    )
    if artifact is None:
        return None
    return {"kind": "diff", "id": str(artifact.id), "filename": artifact.filename}


def _message_attachment(message: Message) -> NeedAttachment | None:
    attachment = (
        Attachment.objects.filter(messages=message).order_by("id").first()
        if message.has_attachment
        else None
    )
    if attachment is None:
        return None
    return {"kind": "file", "id": str(attachment.id), "filename": attachment.file_name}


def _approval_item(
    job: agents.AgentJob,
    approval: agents.AgentApproval,
    *,
    resolved_at: datetime | None = None,
    resolved_action: str | None = None,
) -> NeedItem:
    stream_id, topic = _job_scope(job)
    pending = approval.decision == "pending"
    return {
        "id": f"approval:{approval.id}",
        "kind": "approval",
        "actor": _agent_actor(job.profile),
        "stream_id": stream_id,
        "topic": topic,
        "time": approval.created_at.isoformat(),
        "text": job.request[:80],
        "raw_text": job.request[:80],
        "attachment": _approval_attachment(approval.operation),
        # Every approval gets the same action today, whatever its tool.
        "actions": ["approve"],
        "expires_at": approval.expires_at.isoformat(),
        "expired": pending and approval.expires_at <= timezone_now(),
        "resolved_at": resolved_at.isoformat() if resolved_at else None,
        "resolved_action": resolved_action,
        "job_id": str(job.id),
        "approval_version": approval.version,
        "operation_hash": approval.operation_hash,
        # Like operation_data(), give out the nonce only while it can be used.
        "nonce": str(approval.nonce) if pending else None,
        "job_version": None,
        "task": None,
        "broadcast": False,
    }


def _mcp_call_actor(call: MCPPendingCall) -> NeedActor:
    return {
        "type": "agent",
        "id": call.grant.user_id,
        "name": call.grant.name,
        "agent_role": None,
        "shape": "box",
        "color": None,
        "initials": _initials(call.grant.name),
    }


def _mcp_call_item(
    call: MCPPendingCall,
    *,
    resolved_at: datetime | None = None,
    resolved_action: str | None = None,
) -> NeedItem:
    preview = call.preview
    pending = call.status == "pending"
    arguments = call.arguments
    in_channel = call.tool_name == "send_message"
    text = (preview["content"] or preview["target"]["name"])[:MCP_CALL_TEXT_LIMIT]
    return {
        "id": f"{MCP_CALL_PREFIX}:{call.id}",
        "kind": "approval",
        "actor": _mcp_call_actor(call),
        "stream_id": arguments["channel_id"] if in_channel else None,
        "topic": arguments["topic"] if in_channel else None,
        "time": call.created_at.isoformat(),
        "text": text,
        "raw_text": text,
        "attachment": None,
        "actions": ["approve", "reject"],
        "expires_at": call.expires_at.isoformat(),
        "expired": pending and call.expires_at <= timezone_now(),
        "resolved_at": resolved_at.isoformat() if resolved_at else None,
        "resolved_action": resolved_action,
        "job_id": None,
        "approval_version": None,
        "operation_hash": call.argument_digest,
        # Give out the nonce only while it can be used.
        "nonce": str(call.nonce) if pending else None,
        "job_version": None,
        "task": None,
        "broadcast": False,
        "mcp_call": {
            "call_id": str(call.id),
            "grant_name": call.grant.name,
            "tool": call.tool_name,
            "target": preview["target"],
            "content": preview["content"],
            "mentions": preview["mentions"],
            "digest": call.argument_digest[:12],
        },
    }


def _decision_item(
    job: agents.AgentJob,
    question: agents.AgentAuditEvent,
    *,
    resolved_at: datetime | None = None,
    resolved_action: str | None = None,
) -> NeedItem:
    stream_id, topic = _job_scope(job)
    expires_at = question.occurred_at + DECISION_TTL
    return {
        "id": f"decision:{job.id}",
        "kind": "decision",
        "actor": _agent_actor(job.profile),
        "stream_id": stream_id,
        "topic": topic,
        "time": question.occurred_at.isoformat(),
        "text": question.payload.get("question") or job.request[:80],
        "raw_text": question.payload.get("question") or job.request[:80],
        "attachment": None,
        "actions": list(question.payload.get("options") or []),
        "expires_at": expires_at.isoformat(),
        "expired": job.status == "waiting_for_input" and expires_at <= timezone_now(),
        "resolved_at": resolved_at.isoformat() if resolved_at else None,
        "resolved_action": resolved_action,
        "job_id": str(job.id),
        "approval_version": None,
        "operation_hash": None,
        "nonce": None,
        "job_version": job.version,
        "task": None,
        "broadcast": False,
    }


def _team_wide_keys(rendered_by_key: dict[int, str], actor: UserProfile) -> set[int]:
    """Which of the given channel messages (or notes), by key, are team-wide
    for `actor`: they carry a wildcard or name 3 or more people, bots left out
    and silent mentions counted. One that names no human but `actor` is
    addressed to them, wildcard or not, so it stays personal."""
    mentioned_by_key = {
        key: {int(user_id) for user_id in _USER_MENTION_RE.findall(rendered)}
        for key, rendered in rendered_by_key.items()
    }
    all_ids = set().union(*mentioned_by_key.values())
    human_ids = (
        set(
            UserProfile.objects.filter(realm=actor.realm, id__in=all_ids, is_bot=False).values_list(
                "id", flat=True
            )
        )
        if all_ids
        else set()
    )
    team_wide = set()
    for key, rendered in rendered_by_key.items():
        humans = mentioned_by_key[key] & human_ids
        if humans == {actor.id}:
            continue
        if len(humans) >= TEAM_WIDE_MENTION_MIN_PEOPLE or _WILDCARD_MENTION_RE.search(rendered):
            team_wide.add(key)
    return team_wide


def _channel_message_team_wide_ids(messages: list[Message], actor: UserProfile) -> set[int]:
    return _team_wide_keys(
        {
            message.id: message.rendered_content or ""
            for message in messages
            if message.is_channel_message
        },
        actor,
    )


def _mention_item(
    message: Message,
    *,
    broadcast: bool,
    resolved_at: datetime | None = None,
    resolved_action: str | None = None,
) -> NeedItem:
    stream_id = message.recipient.type_id if message.is_channel_message else None
    topic = message.topic_name() if message.is_channel_message else None
    expires_at = message.date_sent + MENTION_TTL
    return {
        "id": f"mention:{message.id}",
        "kind": "mention",
        "actor": _human_actor(message.sender),
        "stream_id": stream_id,
        "topic": topic,
        "time": message.date_sent.isoformat(),
        "text": _mention_excerpt(message.rendered_content or ""),
        # Plain text, the same way a push notification shows the message.
        "raw_text": _plain_excerpt(message.rendered_content or ""),
        "attachment": _message_attachment(message),
        "actions": [],
        "expires_at": expires_at.isoformat(),
        "expired": expires_at <= timezone_now(),
        "resolved_at": resolved_at.isoformat() if resolved_at else None,
        "resolved_action": resolved_action,
        "job_id": None,
        "approval_version": None,
        "operation_hash": None,
        "nonce": None,
        "job_version": None,
        "task": None,
        "broadcast": broadcast,
    }


def _card_item(notification: TaskNotification, *, task_id_prefix: str, broadcast: bool) -> NeedItem:
    task = notification.task
    expires_at = notification.date_created + MENTION_TTL
    resolved_at = notification.resolved_at
    if notification.note is not None:
        text = _mention_excerpt(notification.note.rendered_content)
        raw_text = _plain_excerpt(notification.note.rendered_content)
    else:
        text = raw_text = task.title[:400]
    return {
        "id": f"card_{notification.kind}:{notification.id}",
        "kind": f"card_{notification.kind}",
        "actor": _human_actor(notification.actor),
        "stream_id": task.stream_id,
        "topic": task.topic or None,
        "time": notification.date_created.isoformat(),
        "text": text,
        "raw_text": raw_text,
        "attachment": None,
        "actions": [],
        "expires_at": expires_at.isoformat(),
        "expired": resolved_at is None and expires_at <= timezone_now(),
        "resolved_at": resolved_at.isoformat() if resolved_at else None,
        "resolved_action": "done" if resolved_at else None,
        "job_id": None,
        "approval_version": None,
        "operation_hash": None,
        "nonce": None,
        "job_version": None,
        "task": {
            "id": task.id,
            "display_id": f"{task_id_prefix}-{task.counter}",
            "title": task.title,
        },
        "broadcast": broadcast,
    }


def _one_on_one_recipient_ids(actor: UserProfile) -> set[int]:
    """The actor's own 1:1 direct message conversations. Q-05: every
    message there is a need, with no mention required."""
    return set(
        Subscription.objects.filter(
            user_profile=actor, recipient__directmessagegroup__group_size=2
        ).values_list("recipient_id", flat=True)
    )


def _mention_candidates(actor: UserProfile, one_on_one_ids: set[int]) -> QuerySet[UserMessage]:
    """Mentions of the actor and messages in their 1:1 direct messages,
    newest first, inside the mention TTL. A message from the actor, a bot,
    or a muted user never counts: an agent's own notice (for example "This
    task needs your approval") is already an approval or decision item."""
    muted_sender_ids = MutedUser.objects.filter(user_profile=actor).values("muted_user_id")
    return (
        UserMessage.objects.filter(
            Q(flags__andnz=UserMessage.flags.mentioned.mask)
            | Q(message__recipient_id__in=one_on_one_ids),
            user_profile=actor,
            message__date_sent__gt=timezone_now() - MENTION_TTL,
            message__sender__is_bot=False,
        )
        .exclude(message__sender=actor)
        .exclude(message__sender_id__in=muted_sender_ids)
        .select_related("message", "message__sender", "message__recipient")
        .order_by("-message_id")
    )


def _is_need_message(message: Message, actor_id: int, one_on_one_ids: set[int]) -> bool:
    """R11: the mentioned flag also fires for a group mention, and a silent
    mention of this user can come with one. Only a genuine, non-silent
    @-mention renders exactly class="user-mention" with this user's
    data-user-id (a silent one adds " silent", a wildcard uses "*")."""
    if message.recipient_id in one_on_one_ids:
        return True
    rendered = message.rendered_content or ""
    return re.search(rf'class="user-mention" data-user-id="{actor_id}"', rendered) is not None


def _replied_at(message: Message, user: UserProfile) -> datetime | None:
    """When `user` first wrote in the conversation of `message` (its
    topic, or its direct message conversation) after it, or None."""
    if message.is_channel_message:
        conversation = messages_for_topic(
            message.realm_id, message.recipient_id, message.topic_name()
        )
    else:
        conversation = Message.objects.filter(
            realm_id=message.realm_id, recipient_id=message.recipient_id
        )
    return (
        conversation.filter(sender=user, id__gt=message.id)
        .order_by("id")
        .values_list("date_sent", flat=True)
        .first()
    )


def _mention_is_open(user_message: UserMessage, actor: UserProfile) -> bool:
    """Q-05: open while unread, and again once two hours pass with no reply.
    A reply closes it either way (03-R4), including a reply sent from a
    notification, which leaves the message unread."""
    message = user_message.message
    if _replied_at(message, actor) is not None:
        return False
    if not user_message.flags.read:
        return True
    return timezone_now() - message.date_sent >= MENTION_REPLY_GRACE


def _open_approvals(
    actor: UserProfile,
    *,
    profile: agents.AgentProfile | None = None,
    scan_limit: int = _ROW_LIMIT,
    stop_after: int | None = None,
) -> list[NeedItem]:
    # 10-M13: a Guest never has the approve permission, and an administrator
    # can turn it off for a Member. decide_approval() rejects both.
    if not has_role_permission(actor, "approve"):
        return []
    items: list[NeedItem] = []
    pending = agents.AgentApproval.objects.filter(
        job__realm=actor.realm, job__status="waiting_for_approval", decision="pending"
    )
    if profile is not None:
        pending = pending.filter(job__profile=profile)
    approvals = pending.select_related(
        "job", "job__profile", "job__conversation", "operation", "attempt"
    ).order_by("-created_at")[:scan_limit]
    for approval in approvals:
        if stop_after is not None and len(items) >= stop_after:
            break
        job = approval.job
        # The same rule as decide_approval(): only the requester decides a
        # team change.
        if approval.operation.tool_class == "team.manage" and actor.id != job.requester_id:
            continue
        # The checks of needs_my_action(), without its filter on expired
        # approvals: this list keeps an expired one, marked expired, so the
        # client can say why its button is off (09-F1).
        try:
            require_job_access(actor, job)
            check_attempt_access(actor, job, approval.attempt, approval.operation.tool_class)
        except (JsonableError, ObjectDoesNotExist, ValueError):
            continue
        items.append(_approval_item(job, approval))
    return items


def _open_mcp_calls(actor: UserProfile) -> list[NeedItem]:
    """The calls waiting on this person as the owner of a grant. Only the
    owner decides, so nobody else sees them."""
    calls = (
        MCPPendingCall.objects.filter(
            grant__user=actor,
            grant__realm=actor.realm,
            grant__revoked_at__isnull=True,
            grant__expires_at__gt=timezone_now(),
            status="pending",
            expires_at__gt=timezone_now() - MCP_CALL_EXPIRED_GRACE,
        )
        .select_related("grant")
        .order_by("-created_at")[:_ROW_LIMIT]
    )
    return [_mcp_call_item(call) for call in calls]


def _latest_questions(job_ids: list[UUID]) -> dict[UUID, agents.AgentAuditEvent]:
    questions: dict[UUID, agents.AgentAuditEvent] = {}
    for event in agents.AgentAuditEvent.objects.filter(
        job_id__in=job_ids, type="input.requested"
    ).order_by("job_id", "-sequence"):
        questions.setdefault(event.job_id, event)
    return questions


def _open_decisions(
    actor: UserProfile,
    *,
    profile: agents.AgentProfile | None = None,
    scan_limit: int = _ROW_LIMIT,
    stop_after: int | None = None,
) -> list[NeedItem]:
    waiting = agents.AgentJob.objects.filter(realm=actor.realm, status="waiting_for_input")
    if profile is not None:
        waiting = waiting.filter(profile=profile)
    jobs: list[agents.AgentJob] = []
    for job in waiting.select_related("profile", "conversation").order_by("-created_at")[
        :scan_limit
    ]:
        if stop_after is not None and len(jobs) >= stop_after:
            break
        if needs_my_action(actor, job):
            jobs.append(job)
    questions = _latest_questions([job.id for job in jobs])
    return [_decision_item(job, questions[job.id]) for job in jobs if job.id in questions]


def _superseded_message_ids(
    open_messages: list[Message], team_wide_ids: set[int], actor: UserProfile
) -> set[int]:
    """Of the open team-wide mentions, the ones a newer one in the same
    topic replaces. A message the actor reacted to is one they are acting
    on, so it neither replaces nor is replaced."""
    broadcasts = [
        message
        for message in open_messages
        if message.id in team_wide_ids and message.is_channel_message
    ]
    reacted_ids = set(
        Reaction.objects.filter(
            user_profile=actor, message_id__in=[message.id for message in broadcasts]
        ).values_list("message_id", flat=True)
    )
    newest_by_topic: dict[tuple[int, str], int] = {}
    # open_messages is newest first, so the first one seen per topic wins.
    for message in broadcasts:
        if message.id not in reacted_ids:
            newest_by_topic.setdefault(
                (message.recipient_id, message.topic_name().lower()), message.id
            )
    return {
        message.id
        for message in broadcasts
        if message.id not in reacted_ids
        and newest_by_topic[(message.recipient_id, message.topic_name().lower())] != message.id
    }


def _record_superseded(actor: UserProfile, message_ids: set[int]) -> None:
    """Make the superseding stick: once an item is replaced it stays
    resolved, even after the item that replaced it is handled."""
    if not message_ids:
        return
    # A plain transaction, for the reason resolve_mention() gives.
    with transaction.atomic(durable=True):
        NeedResolution.objects.bulk_create(
            [
                NeedResolution(
                    realm=actor.realm, user=actor, message_id=message_id, action="superseded"
                )
                for message_id in message_ids
            ],
            ignore_conflicts=True,
        )


def _open_mentions(actor: UserProfile) -> tuple[list[NeedItem], set[int]]:
    """The open mention items, and the message ids of the team-wide ones
    that a newer team-wide mention in the same topic replaces: those are
    left out and the caller records them."""
    one_on_one_ids = _one_on_one_recipient_ids(actor)
    resolved_ids = NeedResolution.objects.filter(realm=actor.realm, user=actor).values("message_id")
    candidates = _mention_candidates(actor, one_on_one_ids).exclude(message_id__in=resolved_ids)
    open_messages = [
        user_message.message
        for user_message in candidates[:_ROW_LIMIT]
        if _is_need_message(user_message.message, actor.id, one_on_one_ids)
        and _mention_is_open(user_message, actor)
    ]
    team_wide_ids = _channel_message_team_wide_ids(open_messages, actor)
    superseded_ids = _superseded_message_ids(open_messages, team_wide_ids, actor)
    reacted_ids = set(
        Reaction.objects.filter(user_profile=actor, message_id__in=team_wide_ids).values_list(
            "message_id", flat=True
        )
    )
    stale_before = timezone_now() - BROADCAST_TTL
    items = [
        _mention_item(message, broadcast=message.id in team_wide_ids)
        for message in open_messages
        if message.id not in superseded_ids
        and not (
            message.id in team_wide_ids
            and message.id not in reacted_ids
            and message.date_sent <= stale_before
        )
    ]
    return items, superseded_ids


def _card_items(
    actor: UserProfile, *, resolved_since: datetime | None
) -> dict[str, list[NeedItem]]:
    """Notes that mention the actor and cards handed to them, one bucket per
    kind. A card the actor can no longer read drops out, so a title never
    outlives the access to it."""
    notifications = TaskNotification.objects.filter(
        user=actor, task__realm=actor.realm
    ).select_related("task", "note", "actor")
    if resolved_since is None:
        notifications = notifications.filter(
            resolved_at__isnull=True, date_created__gt=timezone_now() - MENTION_TTL
        )
    else:
        notifications = notifications.filter(resolved_at__gte=resolved_since)
    task_id_prefix = realm_task_id_prefix(actor.realm)
    readable = [
        notification
        for notification in notifications[:_ROW_LIMIT]
        if user_can_access_task(actor, notification.task)
    ]
    team_wide_ids = _team_wide_keys(
        {
            notification.id: notification.note.rendered_content
            for notification in readable
            if notification.note is not None
        },
        actor,
    )
    buckets: dict[str, list[NeedItem]] = {"card_mention": [], "card_assigned": []}
    for notification in readable:
        item = _card_item(
            notification,
            task_id_prefix=task_id_prefix,
            broadcast=notification.id in team_wide_ids,
        )
        buckets[item["kind"]].append(item)
    return buckets


def _resolved_approvals(
    actor: UserProfile,
    since: datetime,
    *,
    profile: agents.AgentProfile | None = None,
    limit: int = _ROW_LIMIT,
) -> list[NeedItem]:
    # Only decide_approval() sets approver and decided_at. Only an approved
    # approval moves on afterwards (to consumed, expired, or cancelled), so
    # every decision except "rejected" was an approval.
    decided = agents.AgentApproval.objects.filter(
        job__realm=actor.realm, approver=actor, decided_at__gte=since
    )
    if profile is not None:
        decided = decided.filter(job__profile=profile)
    approvals = decided.select_related(
        "job", "job__profile", "job__conversation", "operation"
    ).order_by("-decided_at")[:limit]
    return [
        _approval_item(
            approval.job,
            approval,
            resolved_at=approval.decided_at,
            resolved_action="rejected" if approval.decision == "rejected" else "approved",
        )
        for approval in approvals
    ]


def _resolved_mcp_calls(actor: UserProfile, since: datetime) -> list[NeedItem]:
    calls = (
        MCPPendingCall.objects.filter(
            grant__user=actor,
            grant__realm=actor.realm,
            decided_at__gte=since,
            status__in=["approved", "executed", "failed", "rejected"],
        )
        .select_related("grant")
        .order_by("-decided_at")[:_ROW_LIMIT]
    )
    return [
        _mcp_call_item(
            call,
            resolved_at=call.decided_at,
            resolved_action="rejected" if call.status == "rejected" else "approved",
        )
        for call in calls
    ]


def _resolved_decisions(
    actor: UserProfile,
    since: datetime,
    *,
    profile: agents.AgentProfile | None = None,
    limit: int = _ROW_LIMIT,
) -> list[NeedItem]:
    items: list[NeedItem] = []
    seen_job_ids: set[UUID] = set()
    given = agents.AgentInput.objects.filter(
        job__realm=actor.realm, author=actor, input_type="answer", created_at__gte=since
    )
    if profile is not None:
        given = given.filter(job__profile=profile)
    answers = given.select_related("job", "job__profile", "job__conversation").order_by(
        "-created_at"
    )[:limit]
    for answer in answers:
        # A decision item's id names its job, so a job that asked more than
        # once shows only its newest answer.
        if answer.job_id in seen_job_ids:
            continue
        seen_job_ids.add(answer.job_id)
        # The question this answer replied to: the newest one the server
        # recorded before the answer.
        question = (
            agents.AgentAuditEvent.objects.filter(
                job=answer.job, type="input.requested", created_at__lte=answer.created_at
            )
            .order_by("-sequence")
            .first()
        )
        if question is not None:
            items.append(
                _decision_item(
                    answer.job,
                    question,
                    resolved_at=answer.created_at,
                    resolved_action=answer.text[:400],
                )
            )
    return items


def _resolved_mentions(actor: UserProfile, since: datetime) -> list[NeedItem]:
    resolutions = list(
        NeedResolution.objects.filter(realm=actor.realm, user=actor, resolved_at__gte=since)
        .exclude(action="superseded")
        .order_by("-resolved_at")[:_ROW_LIMIT]
    )
    resolved_ids = [resolution.message_id for resolution in resolutions]
    # Load the messages through the user's own UserMessage rows: a message
    # moved to a channel the user cannot see must drop out, not leak.
    user_messages = {
        user_message.message_id: user_message
        for user_message in UserMessage.objects.filter(
            user_profile=actor, message_id__in=resolved_ids
        ).select_related("message", "message__sender", "message__recipient")
    }
    # 03-R4: a reply resolves a mention with no resolution of its own.
    one_on_one_ids = _one_on_one_recipient_ids(actor)
    superseded_ids = NeedResolution.objects.filter(
        realm=actor.realm, user=actor, action="superseded"
    ).values("message_id")
    candidates = (
        _mention_candidates(actor, one_on_one_ids)
        .exclude(message_id__in=resolved_ids)
        .exclude(message_id__in=superseded_ids)
    )
    replied: list[tuple[Message, datetime]] = []
    for user_message in candidates[:_ROW_LIMIT]:
        message = user_message.message
        if not _is_need_message(message, actor.id, one_on_one_ids):
            continue
        replied_at = _replied_at(message, actor)
        if replied_at is not None and replied_at >= since:
            replied.append((message, replied_at))

    team_wide_ids = _channel_message_team_wide_ids(
        [user_messages[message_id].message for message_id in user_messages]
        + [message for message, _replied_at_time in replied],
        actor,
    )
    items = [
        _mention_item(
            user_messages[resolution.message_id].message,
            broadcast=resolution.message_id in team_wide_ids,
            resolved_at=resolution.resolved_at,
            resolved_action=resolution.action,
        )
        for resolution in resolutions
        if resolution.message_id in user_messages
    ]
    items += [
        _mention_item(
            message,
            broadcast=message.id in team_wide_ids,
            resolved_at=replied_at,
            resolved_action="replied",
        )
        for message, replied_at in replied
    ]
    return items


def _start_of_today(realm: Realm) -> datetime:
    """Midnight in the workspace's own timezone, for "Selesai hari ini"."""
    settings = agents.AgentRealmSettings.objects.filter(realm=realm).first()
    local_now = timezone_now().astimezone(
        ZoneInfo(settings.timezone if settings is not None else "UTC")
    )
    start_of_day = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
    return start_of_day.astimezone(timezone.utc)


def _drop_stale_card_broadcasts(buckets: dict[str, list[NeedItem]]) -> None:
    stale_before = (timezone_now() - BROADCAST_TTL).isoformat()
    buckets["card_mention"] = [
        item
        for item in buckets["card_mention"]
        if not item["broadcast"] or item["time"] > stale_before
    ]


def _drop_snoozed(actor: UserProfile, buckets: dict[str, list[NeedItem]]) -> None:
    snoozed_ids = set(
        NeedSnooze.objects.filter(user=actor, until__gt=timezone_now()).values_list(
            "need_id", flat=True
        )
    )
    if not snoozed_ids:
        return
    for name, items in buckets.items():
        buckets[name] = [item for item in items if item["id"] not in snoozed_ids]


def _count_items(buckets: dict[str, list[NeedItem]]) -> NeedCounts:
    return {
        "approval": len(buckets["approval"]),
        "mention": len(buckets["mention"]),
        "decision": len(buckets["decision"]),
        "card_mention": len(buckets["card_mention"]),
        "card_assigned": len(buckets["card_assigned"]),
        "all": sum(len(values) for values in buckets.values()),
    }


def list_needs(
    actor: UserProfile, *, kind: str | None, status: str, since: str | None
) -> NeedsResult:
    """The Perlu kamu list for one user: read-only, no write lock (spec 03
    step 7), so WP23's Hari ini aggregate can call this directly too."""
    if kind is not None and kind not in KINDS:
        raise ValueError("Invalid need kind.")
    if status not in ("open", "resolved"):
        raise ValueError("Invalid need status.")
    if since not in (None, "", "today"):
        raise ValueError("Invalid since parameter.")
    with agent_transaction(read_only=True):
        superseded_ids: set[int] = set()
        if status == "open":
            mentions, superseded_ids = _open_mentions(actor)
            buckets = {
                "approval": _open_approvals(actor) + _open_mcp_calls(actor),
                "decision": _open_decisions(actor),
                "mention": mentions,
                **_card_items(actor, resolved_since=None),
            }
        else:
            start = _start_of_today(actor.realm)
            buckets = {
                "approval": _resolved_approvals(actor, start) + _resolved_mcp_calls(actor, start),
                "decision": _resolved_decisions(actor, start),
                "mention": _resolved_mentions(actor, start),
                **_card_items(actor, resolved_since=start),
            }
    if status == "open":
        _record_superseded(actor, superseded_ids)
        _drop_stale_card_broadcasts(buckets)
        _drop_snoozed(actor, buckets)
    personal_counts = _count_items(
        {
            name: [item for item in values if not item["broadcast"]]
            for name, values in buckets.items()
        }
    )
    broadcast_counts = _count_items(
        {name: [item for item in values if item["broadcast"]] for name, values in buckets.items()}
    )
    items = (
        buckets[kind]
        if kind is not None
        else [item for values in buckets.values() for item in values]
    )
    if status == "open":
        items.sort(key=lambda item: item["time"], reverse=True)
    else:
        items.sort(key=lambda item: item["resolved_at"] or "", reverse=True)
    return {"items": items, "counts": personal_counts, "broadcast_counts": broadcast_counts}


def list_agent_needs(
    actor: UserProfile,
    profile: agents.AgentProfile,
    *,
    decided_since: datetime,
    scan_limit: int,
    item_limit: int,
) -> tuple[list[NeedItem], list[NeedItem]]:
    """The approvals and decisions of one agent: the ones waiting on this
    user, and the ones this user decided since `decided_since`, both
    newest first. Read-only, like list_needs, and it reads at most
    `scan_limit` rows of each kind. The open items, which list_needs
    shows with the same checks, stop at `item_limit`. The decided ones
    are not checked against job access here: the caller does that."""
    with agent_transaction(read_only=True):
        waiting = _open_approvals(
            actor, profile=profile, scan_limit=scan_limit, stop_after=item_limit
        ) + _open_decisions(actor, profile=profile, scan_limit=scan_limit, stop_after=item_limit)
        decided = _resolved_approvals(
            actor, decided_since, profile=profile, limit=scan_limit
        ) + _resolved_decisions(actor, decided_since, profile=profile, limit=scan_limit)
    waiting.sort(key=lambda item: item["time"], reverse=True)
    decided.sort(key=lambda item: item["resolved_at"] or "", reverse=True)
    return waiting[:item_limit], decided


def resolve_mention(actor: UserProfile, message_id: int) -> None:
    # A plain transaction, not agent_transaction(): NeedResolution is not an
    # agent table, so this write must not wait on, or block, agent writes.
    with transaction.atomic(durable=True):
        access_message(actor, message_id, is_modifying_message=False)
        # Only a message this list can show may be marked done.
        one_on_one_ids = _one_on_one_recipient_ids(actor)
        user_message = (
            _mention_candidates(actor, one_on_one_ids).filter(message_id=message_id).first()
        )
        if user_message is None or not _is_need_message(
            user_message.message, actor.id, one_on_one_ids
        ):
            raise ValueError("This message is not a need of this user.")
        NeedResolution.objects.update_or_create(
            realm=actor.realm,
            user=actor,
            message_id=message_id,
            defaults={"action": "done", "resolved_at": timezone_now()},
        )


def resolve_card_notification(actor: UserProfile, notification_id: int) -> None:
    """Marking one card item done closes every open item on that card: the
    person has seen the card, whichever item brought them to it."""
    notification = TaskNotification.objects.select_related("task__realm").get(
        id=notification_id, user=actor
    )
    do_resolve_task_notifications(user_profile=actor, task=notification.task)


def unresolve_mention(actor: UserProfile, message_id: int) -> None:
    # Deleting the user's own resolution needs no other check.
    with transaction.atomic(durable=True):
        # A superseded item is not the user's to reopen.
        NeedResolution.objects.filter(realm=actor.realm, user=actor, message_id=message_id).exclude(
            action="superseded"
        ).delete()


def _split_need_id(need_id: str) -> tuple[str, str]:
    kind, _separator, raw_id = need_id.partition(":")
    if (kind not in KINDS and kind != MCP_CALL_PREFIX) or raw_id == "" or len(need_id) > 80:
        raise ValueError("Invalid need id.")
    return kind, raw_id


def _check_bulk_size(need_ids: list[str]) -> None:
    if len(need_ids) > BULK_LIMIT:
        raise ValueError("Too many items.")


def resolve_many(actor: UserProfile, need_ids: list[str]) -> BulkNeedsResult:
    """Mark each given mention and card item done. Each one is its own
    transaction, so an item that cannot be resolved (it is gone, or no longer
    this user's) is reported and the others stay done. Approvals and
    decisions are answered one at a time and are not accepted."""
    _check_bulk_size(need_ids)
    result: BulkNeedsResult = {"done": [], "failed": []}
    for need_id in need_ids:
        try:
            kind, raw_id = _split_need_id(need_id)
            if kind == "mention":
                resolve_mention(actor, int(raw_id))
            elif kind in ("card_mention", "card_assigned"):
                resolve_card_notification(actor, int(raw_id))
            else:
                raise ValueError("Only mentions and card items resolve in bulk.")
        except (ValueError, ObjectDoesNotExist, JsonableError):
            result["failed"].append(need_id)
        else:
            result["done"].append(need_id)
    return result


def unresolve_card_notification(actor: UserProfile, notification_id: int) -> None:
    """Reopen the items that closing this one closed with it: the user's
    items on the same card that were closed just now."""
    notification = TaskNotification.objects.get(id=notification_id, user=actor)
    TaskNotification.objects.filter(
        user=actor,
        task_id=notification.task_id,
        resolved_at__gte=timezone_now() - CARD_UNDO_WINDOW,
    ).update(resolved_at=None)


def unresolve_many(actor: UserProfile, need_ids: list[str]) -> BulkNeedsResult:
    """Undo resolve_many(). Same rules: each item on its own, and the
    ones that fail are reported."""
    _check_bulk_size(need_ids)
    result: BulkNeedsResult = {"done": [], "failed": []}
    for need_id in need_ids:
        try:
            kind, raw_id = _split_need_id(need_id)
            if kind == "mention":
                unresolve_mention(actor, int(raw_id))
            elif kind in ("card_mention", "card_assigned"):
                unresolve_card_notification(actor, int(raw_id))
            else:
                raise ValueError("Only mentions and card items resolve in bulk.")
        except (ValueError, ObjectDoesNotExist, JsonableError):
            result["failed"].append(need_id)
        else:
            result["done"].append(need_id)
    return result


def snooze_until(realm: Realm) -> datetime:
    """The next 07:00 on the workspace's clock: the hour the morning
    reminder goes out, so a snoozed item is back when the day starts."""
    local_now = realm_local_now(realm)
    wake_up = datetime.combine(local_now.date(), time(REMINDER_HOUR, 0), tzinfo=local_now.tzinfo)
    if wake_up <= local_now:
        wake_up = datetime.combine(
            local_now.date() + timedelta(days=1), time(REMINDER_HOUR, 0), tzinfo=local_now.tzinfo
        )
    return wake_up.astimezone(timezone.utc)


def snooze_many(actor: UserProfile, need_ids: list[str]) -> datetime:
    """Hide the given items of any kind until snooze_until(). An id that
    names no current item hides nothing, so the ids are only checked for
    shape. Returns when the items come back."""
    _check_bulk_size(need_ids)
    for need_id in need_ids:
        _split_need_id(need_id)
    until = snooze_until(actor.realm)
    with transaction.atomic(durable=True):
        NeedSnooze.objects.filter(user=actor, until__lte=timezone_now()).delete()
        for need_id in need_ids:
            NeedSnooze.objects.update_or_create(
                realm=actor.realm, user=actor, need_id=need_id, defaults={"until": until}
            )
    return until


def unsnooze_many(actor: UserProfile, need_ids: list[str]) -> None:
    _check_bulk_size(need_ids)
    with transaction.atomic(durable=True):
        NeedSnooze.objects.filter(user=actor, need_id__in=need_ids).delete()
