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
from datetime import datetime, timedelta, timezone
from typing import TypedDict
from uuid import UUID
from zoneinfo import ZoneInfo

from django.core.exceptions import ObjectDoesNotExist
from django.db import transaction
from django.db.models import Q, QuerySet
from django.utils.timezone import now as timezone_now

from zerver.actions.agent_jobs import check_attempt_access
from zerver.lib.agent_context import agent_transaction, require_job_access
from zerver.lib.exceptions import JsonableError
from zerver.lib.message import access_message
from zerver.lib.push_notifications import get_mobile_push_content
from zerver.lib.role_permissions import has_role_permission
from zerver.lib.topic import messages_for_topic
from zerver.models import (
    Attachment,
    Message,
    MutedUser,
    Realm,
    Subscription,
    UserMessage,
    UserProfile,
    agents,
)
from zerver.models.needs import NeedResolution
from zerver.views.agent_jobs import needs_my_action

# Q-06 / Q-32: decisions and mentions keep a fixed display TTL. Approvals use
# the workspace's approval_ttl_minutes (WP03 AgentRealmSettings) instead.
DECISION_TTL = timedelta(hours=24)
MENTION_TTL = timedelta(days=7)
# Q-05: a read mention only becomes a need again once this much time has
# passed with no reply from the mentioned user in that conversation.
MENTION_REPLY_GRACE = timedelta(hours=2)

_ROW_LIMIT = 200

KINDS = ("approval", "mention", "decision")


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


class NeedItem(TypedDict):
    id: str
    kind: str
    actor: NeedActor
    stream_id: int | None
    topic: str | None
    time: str
    text: str
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


class NeedCounts(TypedDict):
    approval: int
    mention: int
    decision: int
    all: int


class NeedsResult(TypedDict):
    items: list[NeedItem]
    counts: NeedCounts


def _initials(full_name: str) -> str:
    """Mirrors web/src/people.ts's initials_for_full_name for parity."""
    words = full_name.split()
    if len(words) > 1:
        return (words[0][0] + words[-1][0]).upper()
    return "".join(words)[:2].upper()


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
    }


def _mention_item(
    message: Message, *, resolved_at: datetime | None = None, resolved_action: str | None = None
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
        # Plain text, the same way a push notification shows the message.
        "text": get_mobile_push_content(message.rendered_content or "")[:400],
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


def _open_approvals(actor: UserProfile) -> list[NeedItem]:
    # 10-M13: a Guest never has the approve permission, and an administrator
    # can turn it off for a Member. decide_approval() rejects both.
    if not has_role_permission(actor, "approve"):
        return []
    items: list[NeedItem] = []
    approvals = (
        agents.AgentApproval.objects.filter(
            job__realm=actor.realm, job__status="waiting_for_approval", decision="pending"
        )
        .select_related("job", "job__profile", "job__conversation", "operation", "attempt")
        .order_by("-created_at")[:_ROW_LIMIT]
    )
    for approval in approvals:
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


def _latest_questions(job_ids: list[UUID]) -> dict[UUID, agents.AgentAuditEvent]:
    questions: dict[UUID, agents.AgentAuditEvent] = {}
    for event in agents.AgentAuditEvent.objects.filter(
        job_id__in=job_ids, type="input.requested"
    ).order_by("job_id", "-sequence"):
        questions.setdefault(event.job_id, event)
    return questions


def _open_decisions(actor: UserProfile) -> list[NeedItem]:
    jobs = [
        job
        for job in agents.AgentJob.objects.filter(realm=actor.realm, status="waiting_for_input")
        .select_related("profile", "conversation")
        .order_by("-created_at")[:_ROW_LIMIT]
        if needs_my_action(actor, job)
    ]
    questions = _latest_questions([job.id for job in jobs])
    return [_decision_item(job, questions[job.id]) for job in jobs if job.id in questions]


def _open_mentions(actor: UserProfile) -> list[NeedItem]:
    one_on_one_ids = _one_on_one_recipient_ids(actor)
    resolved_ids = NeedResolution.objects.filter(realm=actor.realm, user=actor).values("message_id")
    candidates = _mention_candidates(actor, one_on_one_ids).exclude(message_id__in=resolved_ids)
    return [
        _mention_item(user_message.message)
        for user_message in candidates[:_ROW_LIMIT]
        if _is_need_message(user_message.message, actor.id, one_on_one_ids)
        and _mention_is_open(user_message, actor)
    ]


def _resolved_approvals(actor: UserProfile, since: datetime) -> list[NeedItem]:
    # Only decide_approval() sets approver and decided_at. Only an approved
    # approval moves on afterwards (to consumed, expired, or cancelled), so
    # every decision except "rejected" was an approval.
    approvals = (
        agents.AgentApproval.objects.filter(
            job__realm=actor.realm, approver=actor, decided_at__gte=since
        )
        .select_related("job", "job__profile", "job__conversation", "operation")
        .order_by("-decided_at")[:_ROW_LIMIT]
    )
    return [
        _approval_item(
            approval.job,
            approval,
            resolved_at=approval.decided_at,
            resolved_action="rejected" if approval.decision == "rejected" else "approved",
        )
        for approval in approvals
    ]


def _resolved_decisions(actor: UserProfile, since: datetime) -> list[NeedItem]:
    items: list[NeedItem] = []
    seen_job_ids: set[UUID] = set()
    answers = (
        agents.AgentInput.objects.filter(
            job__realm=actor.realm, author=actor, input_type="answer", created_at__gte=since
        )
        .select_related("job", "job__profile", "job__conversation")
        .order_by("-created_at")[:_ROW_LIMIT]
    )
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
        NeedResolution.objects.filter(
            realm=actor.realm, user=actor, resolved_at__gte=since
        ).order_by("-resolved_at")[:_ROW_LIMIT]
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
    items = [
        _mention_item(
            user_messages[resolution.message_id].message,
            resolved_at=resolution.resolved_at,
            resolved_action=resolution.action,
        )
        for resolution in resolutions
        if resolution.message_id in user_messages
    ]
    # 03-R4: a reply resolves a mention with no resolution of its own.
    one_on_one_ids = _one_on_one_recipient_ids(actor)
    candidates = _mention_candidates(actor, one_on_one_ids).exclude(message_id__in=resolved_ids)
    for user_message in candidates[:_ROW_LIMIT]:
        message = user_message.message
        if not _is_need_message(message, actor.id, one_on_one_ids):
            continue
        replied_at = _replied_at(message, actor)
        if replied_at is not None and replied_at >= since:
            items.append(_mention_item(message, resolved_at=replied_at, resolved_action="replied"))
    return items


def _start_of_today(realm: Realm) -> datetime:
    """Midnight in the workspace's own timezone, for "Selesai hari ini"."""
    settings = agents.AgentRealmSettings.objects.filter(realm=realm).first()
    local_now = timezone_now().astimezone(
        ZoneInfo(settings.timezone if settings is not None else "UTC")
    )
    start_of_day = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
    return start_of_day.astimezone(timezone.utc)


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
        if status == "open":
            buckets = {
                "approval": _open_approvals(actor),
                "decision": _open_decisions(actor),
                "mention": _open_mentions(actor),
            }
        else:
            start = _start_of_today(actor.realm)
            buckets = {
                "approval": _resolved_approvals(actor, start),
                "decision": _resolved_decisions(actor, start),
                "mention": _resolved_mentions(actor, start),
            }
    counts: NeedCounts = {
        "approval": len(buckets["approval"]),
        "mention": len(buckets["mention"]),
        "decision": len(buckets["decision"]),
        "all": sum(len(values) for values in buckets.values()),
    }
    items = (
        buckets[kind]
        if kind is not None
        else [item for values in buckets.values() for item in values]
    )
    if status == "open":
        items.sort(key=lambda item: item["time"], reverse=True)
    else:
        items.sort(key=lambda item: item["resolved_at"] or "", reverse=True)
    return {"items": items, "counts": counts}


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


def unresolve_mention(actor: UserProfile, message_id: int) -> None:
    # Deleting the user's own resolution needs no other check.
    with transaction.atomic(durable=True):
        NeedResolution.objects.filter(realm=actor.realm, user=actor, message_id=message_id).delete()
