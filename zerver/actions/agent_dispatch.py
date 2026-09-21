"""Admit renderer-proven agent mentions after their source message is stored."""

from uuid import NAMESPACE_URL, uuid5

from zerver.actions import agent_jobs
from zerver.lib import agent_protocol as p
from zerver.lib.agent_context import AgentBusy, agent_transaction
from zerver.lib.agent_policy import AgentAccessDenied, check_agent_access
from zerver.lib.exceptions import JsonableError
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


def coding_choices(profile: agents.AgentProfile) -> tuple[agents.AgentRepository | None, str, bool]:
    repository = profile.default_repository
    complete = (
        repository is not None
        and len(repository.allowed_refs) == 1
        and bool(repository.required_checks)
    )
    return repository, repository.allowed_refs[0] if complete and repository else "", complete


def preflight_profile(
    actor: UserProfile,
    profile: agents.AgentProfile,
    source: Message | None,
    destination: p.ConversationScope | None,
) -> str:
    actor = UserProfile.objects.get(id=actor.id, realm=actor.realm, is_active=True)
    if not UserProfile.objects.filter(
        id=profile.bot_user_id, realm=actor.realm, is_active=True
    ).exists():
        raise AgentAccessDenied("Agent access denied.")
    settings = agents.AgentRealmSettings.objects.filter(realm=actor.realm, enabled=True).first()
    if settings is None or actor.is_bot:
        raise AgentAccessDenied("Agent access denied.")
    try:
        agent_jobs.require_ready(profile)
    except ValueError:
        if profile.desired_state != "enabled":
            raise
    repository, _base_ref, complete = coding_choices(profile)
    if profile.default_mode != "code":
        repository = None
    required = ["profile.use", "context.read"]
    if profile.default_mode == "code" and complete:
        required += ["repository.read", "repository.edit", "checks.run"]
    for action in required:
        check_agent_access(actor, profile, repository, source, action, destination=destination)
    if (profile.default_mode != "code" or complete) and (
        agents.AgentJob.objects.filter(realm=actor.realm, status="queued").count()
        >= settings.queued_job_limit
        or agents.AgentJob.objects.filter(profile=profile, status="queued").count()
        >= settings.profile_queue_limit
    ):
        raise AgentAccessDenied("Agent access denied.")
    return "needs_input" if profile.default_mode == "code" and not complete else "accepted"


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
                    repository, base_ref, ready_to_queue = coding_choices(profile)
                    job = agent_jobs.create_job(
                        message.sender,
                        profile=profile,
                        source=message,
                        request=message.content,
                        idempotency_key=key,
                        job_kind="code",
                        delivery_target="patch",
                        repository=repository,
                        base_ref=base_ref,
                        trigger_kind=trigger_kind,
                        allow_blocked=True,
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
                        allow_blocked=True,
                    )
                    decision, reason = "accepted", ""
            except AgentBusy:
                raise
            except (JsonableError, ValueError, agents.AgentRealmSettings.DoesNotExist):
                receipts.append(
                    _receipt(
                        message,
                        profile,
                        message.sender,
                        trigger_kind,
                        "rejected",
                        "Admission denied.",
                    )
                )
                continue
            if decision == "accepted":
                if job.blocked_reason:
                    reason = job.blocked_reason
                elif profile.runner.status in {"offline", "unknown"}:
                    reason = f"runner_{profile.runner.status}"
                elif (
                    agents.AgentAttempt.objects.filter(runner=profile.runner, active=True).count()
                    >= profile.runner.capacity
                ):
                    reason = "runner_busy"
            receipts.append(
                _receipt(message, profile, message.sender, trigger_kind, decision, reason, job)
            )
    return receipts
