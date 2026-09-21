"""Current audience checks for bounded agent transactions."""

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from time import monotonic
from uuid import UUID

from django.db import OperationalError, connection, transaction

from zerver.lib import agent_protocol as p
from zerver.lib.agent_policy import AgentAccessDenied, _owner_or_grant, check_agent_access
from zerver.lib.exceptions import JsonableError
from zerver.lib.message import access_message
from zerver.lib.streams import user_has_content_access
from zerver.lib.user_groups import UserGroupMembershipDetails
from zerver.models import Message, Recipient, Stream, Subscription, UserProfile, agents
from zerver.models.recipients import get_direct_message_group_user_ids

# SHARE conflicts with SQL writes, including membership insertion phantoms.
# NOWAIT avoids acquiring a partial guard behind a nested writer's locks.
ACL_TABLES = (
    "zerver_realm",
    "zerver_userprofile",
    "zerver_usergroup",
    "zerver_namedusergroup",
    "zerver_usergroupmembership",
    "zerver_groupgroupmembership",
    "zerver_stream",
    "zerver_subscription",
    "zerver_recipient",
    "zerver_message",
    "zerver_usermessage",
    "zerver_attachment",
    "zerver_attachment_messages",
    "zerver_agentgrant",
    "zerver_agentprofile",
    "zerver_agentprovider",
    "zerver_agentsecret",
    "zerver_agentrepository",
    "zerver_agentrunner",
    "zerver_agentrunnercredential",
)


class AgentBusy(ValueError):  # noqa: N818
    pass


_deadline: ContextVar[float | None] = ContextVar("agent_transaction_deadline", default=None)


def ensure_budget() -> None:
    deadline = _deadline.get()
    if deadline is not None and monotonic() >= deadline:
        raise AgentBusy("Agent authority is busy. Retry this request.")


@contextmanager
def agent_transaction(*, retain_nested_limits: bool = False) -> Iterator[None]:
    """Use bounded rollback on contention; this is not a deadlock-free lock order."""
    outermost = not connection.in_atomic_block
    token = _deadline.set(_deadline.get() or monotonic() + 5)
    try:
        with transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_try_advisory_xact_lock(174621, 3)")
                if not cursor.fetchone()[0]:
                    raise AgentBusy("Agent authority is busy. Retry this request.")
                cursor.execute("LOCK TABLE " + ", ".join(ACL_TABLES) + " IN SHARE MODE NOWAIT")
                cursor.execute(
                    "SELECT name, setting FROM pg_settings WHERE name IN ('lock_timeout', 'statement_timeout')"
                )
                previous = dict(cursor.fetchall())
                for name, maximum in [("lock_timeout", 250), ("statement_timeout", 2500)]:
                    old = int(previous[name])
                    duration = min(old, maximum) if old else maximum
                    cursor.execute("SELECT set_config(%s, %s, true)", [name, str(duration)])
            ensure_budget()
            yield
            ensure_budget()
            if not outermost and not retain_nested_limits:
                # Nested admission retains locks. Its caller owns commit-time deadlines.
                with connection.cursor() as cursor:
                    for name, value in previous.items():
                        cursor.execute("SELECT set_config(%s, %s, true)", [name, value])
    except OperationalError:
        raise AgentBusy("Agent authority is busy. Retry this request.") from None
    finally:
        _deadline.reset(token)


def current_audience(
    conversation: agents.AgentConversation,
    requester: UserProfile,
    bot: UserProfile,
) -> p.AudienceBinding:
    if conversation.anchor_message_id is None:
        raise ValueError("Conversation anchor is unavailable.")
    requester = UserProfile.objects.get(
        id=requester.id, realm_id=conversation.realm_id, is_active=True
    )
    bot = UserProfile.objects.get(id=bot.id, realm_id=conversation.realm_id, is_active=True)
    try:
        anchor = access_message(
            requester, conversation.anchor_message_id, is_modifying_message=False
        )
        access_message(bot, anchor.id, is_modifying_message=False)
    except JsonableError:
        raise ValueError("Conversation access changed.") from None
    route: dict[str, object] = {}
    if anchor.recipient.type == Recipient.STREAM:
        stream = Stream.objects.get(
            id=anchor.recipient.type_id, realm_id=conversation.realm_id, deactivated=False
        )
        assert stream.recipient_id is not None
        subscribed = set(
            Subscription.objects.filter(recipient_id=stream.recipient_id, active=True).values_list(
                "user_profile_id", flat=True
            )
        )
        users = list(
            UserProfile.objects.filter(realm_id=conversation.realm_id, is_active=True).order_by(
                "id"
            )[:10001]
        )
        if len(users) > 10000:
            raise ValueError("Pilot audience limit exceeded.")
        members = []
        for user in users:
            ensure_budget()
            if user_has_content_access(
                user,
                stream,
                UserGroupMembershipDetails(user_recursive_group_ids=None),
                is_subscribed=user.id in subscribed,
            ):
                members.append(user.id)
        route = {
            "kind": "stream",
            "stream_id": stream.id,
            "invite_only": stream.invite_only,
            "is_web_public": stream.is_web_public,
            "history_public_to_subscribers": stream.history_public_to_subscribers,
        }
    else:
        members = list(get_direct_message_group_user_ids(anchor.recipient))
        route = {"kind": "direct"}
    return p.AudienceBinding.model_validate(
        {
            "conversation_id": str(conversation.id),
            "epoch": conversation.audience_epoch,
            "realm_id": conversation.realm_id,
            "profile_id": str(conversation.profile_id),
            "requester_user_id": requester.id,
            "bot_user_id": bot.id,
            "anchor_message_id": anchor.id,
            "recipient_id": anchor.recipient_id,
            "audience_user_ids": members,
            **route,
        }
    )


def require_audience(job: agents.AgentJob) -> p.AudienceBinding:
    accepted = p.AudienceBinding.model_validate(job.conversation.audience_binding)
    current = current_audience(job.conversation, job.requester, job.profile.bot_user)
    if current != accepted:
        raise ValueError("Conversation audience changed. Explicit replan is required.")
    return current


def scope_for_message(message: Message) -> p.ConversationScope:
    if message.recipient.type == Recipient.STREAM:
        return p.ConversationScope(
            kind="stream",
            stream_id=message.recipient.type_id,
            topic=message.topic_name(),
            anchor_message_id=message.id,
        )
    return p.ConversationScope(
        kind="direct",
        anchor_message_id=message.id,
        participant_user_ids=list(get_direct_message_group_user_ids(message.recipient)),
    )


def require_job_access(actor: UserProfile, job: agents.AgentJob) -> None:
    actor = UserProfile.objects.get(id=actor.id)
    if not actor.is_active or actor.realm_id != job.realm_id:
        raise AgentAccessDenied("Agent access denied.")
    # Discovery may succeed for another source. History needs this job's exact scope.
    resources: list[
        tuple[
            agents.AgentProfile
            | agents.AgentRunner
            | agents.AgentRepository
            | agents.AgentProvider,
            str,
            str,
        ]
    ] = [(job.profile, "profile", "profile.use"), (job.runner, "runner", "runner.use")]
    if job.repository is not None:
        resources.append((job.repository, "repository", "repository.read"))
    attempt = agents.AgentAttempt.objects.filter(job=job).order_by("-number").first()
    provider_data = attempt.descriptor.get("provider") if attempt is not None else None
    provider = (
        (
            agents.AgentProvider.objects.get(id=provider_data["id"], realm_id=job.realm_id)
            if provider_data
            else None
        )
        if attempt is not None
        else job.profile.provider
    )
    if provider is not None:
        resources.append((provider, "provider", "provider.use"))
    for resource, kind, action in resources:
        if resource.realm_id != actor.realm_id or not _owner_or_grant(
            actor,
            resource,
            target_kind=kind,
            action=action,
            repository=job.repository,
            source_message=job.source_message,
        ):
            raise AgentAccessDenied("Agent access denied.")
    if job.source_message_id is not None:
        access_message(actor, job.source_message_id, is_modifying_message=False)
        # A broader current destination never grants access to older private data.
        binding = p.AudienceBinding.model_validate(job.conversation.audience_binding)
        if actor.id not in binding.audience_user_ids:
            raise AgentAccessDenied("Agent access denied.")
    elif actor.id not in {job.requester_id, job.profile.owner_id}:
        raise AgentAccessDenied("Agent access denied.")


def require_reference_audience(job: agents.AgentJob, message_id: int) -> None:
    accepted = require_audience(job)
    reference = agents.AgentConversation(
        id=job.conversation_id,
        realm_id=job.realm_id,
        profile_id=job.profile_id,
        anchor_message_id=message_id,
        audience_epoch=accepted.epoch,
    )
    source = current_audience(reference, job.requester, job.profile.bot_user)
    if not set(accepted.audience_user_ids) <= set(source.audience_user_ids) or (
        accepted.is_web_public and not source.is_web_public
    ):
        raise ValueError("Reference audience cannot flow to this destination.")


def selected_context(job: agents.AgentJob, ref_ids: list[UUID]) -> list[dict[str, object]]:
    require_audience(job)
    check_agent_access(
        job.requester, job.profile, job.repository, job.source_message, "context.read"
    )
    refs = list(agents.AgentContextRef.objects.filter(job=job, realm=job.realm, id__in=ref_ids))
    if len(refs) != len(set(ref_ids)):
        raise ValueError("Context reference is unavailable.")
    result: list[dict[str, object]] = []
    for ref in refs:
        ensure_budget()
        if ref.kind == "message" and ref.message_id is not None:
            require_reference_audience(job, ref.message_id)
            message = access_message(job.requester, ref.message_id, is_modifying_message=False)
            access_message(job.profile.bot_user, ref.message_id, is_modifying_message=False)
            result.append(
                {
                    "id": str(ref.id),
                    "kind": "message",
                    "message_id": message.id,
                    "text": message.content,
                }
            )
        elif ref.kind == "attachment" and ref.attachment is not None:
            if (
                ref.message_id is None
                or not ref.attachment.messages.filter(id=ref.message_id).exists()
            ):
                raise ValueError("Selected file source is unavailable.")
            require_reference_audience(job, ref.message_id)
            from zerver.lib.attachments import validate_attachment_request

            if not all(
                validate_attachment_request(user, ref.attachment.path_id)[0]
                for user in [job.requester, job.profile.bot_user]
            ):
                raise ValueError("Context attachment is unavailable.")
            result.append(
                {
                    "id": str(ref.id),
                    "kind": "attachment",
                    "attachment_id": ref.attachment_id,
                    "name": ref.attachment.file_name,
                    "size": ref.attachment.size,
                }
            )
        elif ref.kind == "repository" and ref.repository_id == job.repository_id:
            result.append(
                {"id": str(ref.id), "kind": "repository", "repository_id": str(ref.repository_id)}
            )
        else:
            raise ValueError("Context reference is unavailable.")
    if len(p.canonical_json(result)) > min(1024 * 1024, job.budget["input_tokens"] * 4):
        raise ValueError("Selected context exceeds the input budget.")
    return result
