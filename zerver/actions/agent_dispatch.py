"""Admit renderer-proven agent mentions after their source message is stored."""

from uuid import NAMESPACE_URL, uuid5

from zerver.actions import agent_jobs
from zerver.lib.agent_context import AgentBusy, agent_transaction
from zerver.lib.agent_policy import AgentAccessDenied
from zerver.models import Message, Recipient, UserProfile, agents
from zerver.models.recipients import get_direct_message_group_user_ids


def _targets(
    message: Message, personal_mention_user_ids: set[int]
) -> dict[agents.AgentProfile, str]:
    """Return each target once, with explicit mention taking precedence."""
    profiles = {
        profile.bot_user_id: profile
        for profile in agents.AgentProfile.objects.filter(
            realm=message.realm, bot_user_id__in=personal_mention_user_ids
        ).select_related("default_repository", "bot_user")
    }
    result: dict[agents.AgentProfile, str] = dict.fromkeys(profiles.values(), "mention")

    if message.recipient.type != Recipient.DIRECT_MESSAGE_GROUP:
        return result
    members = set(get_direct_message_group_user_ids(message.recipient))
    if len(members) != 2 or message.sender_id not in members:
        return result
    for profile in agents.AgentProfile.objects.filter(
        realm=message.realm, bot_user_id__in=members
    ).select_related("default_repository", "bot_user"):
        if profile.bot_user_id != message.sender_id:
            result.setdefault(profile, "direct_message")
    return result


def _receipt(
    message: Message,
    profile: agents.AgentProfile,
    requester: UserProfile,
    trigger_kind: str,
    decision: str,
    reason: str = "",
    job: agents.AgentJob | None = None,
) -> agents.AgentDispatchReceipt:
    receipt, _created = agents.AgentDispatchReceipt.objects.get_or_create(
        realm=message.realm,
        source_message=message,
        profile=profile,
        trigger_kind=trigger_kind,
        defaults={
            "requester": requester,
            "decision": decision,
            "reason": reason,
            "job": job,
        },
    )
    return receipt


def admit_message(
    message: Message, *, personal_mention_user_ids: set[int]
) -> list[agents.AgentDispatchReceipt]:
    """Create only durable jobs and receipts. This function does no network work."""
    if message.sender.is_bot:
        return []
    targets = _targets(message, personal_mention_user_ids)
    if not targets:
        return []
    receipts = []
    # Keep the bounded database limits until do_send_messages commits. PostgreSQL
    # keeps the associated locks to that outer commit in either case.
    with agent_transaction(retain_nested_limits=True):
        for profile, trigger_kind in targets.items():
            key = uuid5(NAMESPACE_URL, f"agent-message:{message.id}:{profile.id}:{trigger_kind}")
            try:
                if profile.default_mode == "code":
                    repository = profile.default_repository
                    ready_to_queue = repository is not None and len(repository.allowed_refs) == 1
                    job = agent_jobs.create_job(
                        message.sender,
                        profile=profile,
                        source=message,
                        request=message.content,
                        idempotency_key=key,
                        job_kind="code",
                        delivery_target="patch",
                        repository=repository,
                        base_ref=repository.allowed_refs[0] if ready_to_queue else "",
                        trigger_kind=trigger_kind,
                        draft=not ready_to_queue,
                    )
                    decision, reason = (
                        ("accepted", "")
                        if ready_to_queue
                        else ("needs_input", "Coding choices are required.")
                    )
                else:
                    job = agent_jobs.create_job(
                        message.sender,
                        profile=profile,
                        source=message,
                        request=message.content,
                        idempotency_key=key,
                        job_kind="answer",
                        delivery_target="answer",
                        trigger_kind=trigger_kind,
                    )
                    decision, reason = "accepted", ""
            except AgentBusy:
                raise
            except (AgentAccessDenied, ValueError):
                receipts.append(
                    _receipt(message, profile, message.sender, trigger_kind, "rejected", "Admission denied.")
                )
                continue
            receipts.append(_receipt(message, profile, message.sender, trigger_kind, decision, reason, job))
    return receipts
