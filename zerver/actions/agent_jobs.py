"""Durable job transitions. The caller never receives model authority."""

import hashlib
from collections.abc import Sequence
from datetime import timedelta
from typing import Any
from uuid import UUID, uuid4

from django.db.models import Max
from django.utils.timezone import now

from zerver.actions.agents import current_execution_configuration, provider_config, validate_runtime
from zerver.lib import agent_protocol as p
from zerver.lib.agent_context import (
    agent_transaction,
    current_audience,
    require_audience,
    require_job_access,
    scope_for_message,
)
from zerver.lib.agent_policy import AgentAccessDenied, _owner_or_grant, check_agent_access
from zerver.models import Message, UserProfile, agents

TERMINAL = {"completed", "cancelled", "failed", "interrupted", "blocked"}
EXECUTING = {"running", "waiting_for_input", "waiting_for_approval", "verifying"}


def digest(value: object) -> str:
    return hashlib.sha256(p.canonical_json(value)).hexdigest()


def audit(
    job: agents.AgentJob,
    event_type: str,
    payload: dict[str, object],
    *,
    attempt: agents.AgentAttempt | None = None,
    authority: str = "server",
    event: p.RunnerEvent | None = None,
    actor: UserProfile | None = None,
) -> agents.AgentAuditEvent:
    job.event_sequence += 1
    job.save(update_fields=["event_sequence"])
    from zerver.lib.agent_results import reject_secrets

    if event_type not in {
        "attempt.stopped",
        "attempt.interrupted",
        "attempt.stop_requested",
        "publication.blocked",
    }:
        reject_secrets(job, p.canonical_json(payload))
    record = agents.AgentAuditEvent(
        realm=job.realm,
        job=job,
        attempt=attempt,
        sequence=job.event_sequence,
        attempt_sequence=event.sequence if event else None,
        event_id=event.event_id if event else uuid4(),
        actor=actor,
        authority=authority,
        type=event_type,
        payload=payload,
        occurred_at=event.occurred_at if event else now(),
    )
    record.clean()
    record.save()
    return record


def transition(job: agents.AgentJob, status: str, *, reason: str = "") -> None:
    job.status = status
    job.blocked_reason = reason
    job.version += 1
    job.save(update_fields=["status", "blocked_reason", "version"])


def require_control(actor: UserProfile, job: agents.AgentJob) -> None:
    require_job_access(actor, job)
    if actor.id != job.requester_id and not _owner_or_grant(
        actor,
        job.profile,
        target_kind="profile",
        action="job.control",
        repository=job.repository,
        source_message=job.source_message,
    ):
        raise AgentAccessDenied("Agent access denied.")


def current_actions(job: agents.AgentJob, ceiling: Sequence[str]) -> list[str]:
    result = []
    for action in ceiling:
        try:
            check_agent_access(
                job.requester, job.profile, job.repository, job.source_message, action
            )
        except AgentAccessDenied:
            continue
        result.append(action)
    return sorted(set(result))


def require_ready(profile: agents.AgentProfile) -> p.ExecutionConfiguration:
    if (
        profile.desired_state != "enabled"
        or profile.enabled_revision != profile.revision
        or profile.readiness_revision != profile.revision
        or profile.readiness_state != "ready"
    ):
        raise ValueError("Profile readiness is stale.")
    validate_runtime(profile)
    tested = p.ExecutionConfiguration.model_validate(profile.readiness_configuration)
    if (
        digest(p.serialize_payload(tested)) != profile.readiness_configuration_digest
        or current_execution_configuration(profile) != tested
    ):
        raise ValueError("Profile readiness is stale.")
    return tested


def create_job(
    actor: UserProfile,
    *,
    profile: agents.AgentProfile,
    source: Message,
    request: str,
    idempotency_key: UUID,
    job_kind: str,
    delivery_target: str,
    repository: agents.AgentRepository | None = None,
    base_ref: str = "",
    context_message_ids: list[int] | None = None,
    context_attachment_ids: list[int] | None = None,
    trigger_kind: str = "manual",
    draft: bool = False,
    completing: agents.AgentJob | None = None,
    allow_blocked: bool = False,
) -> agents.AgentJob:
    if not request or len(request) > 20000:
        raise ValueError("Invalid request.")
    if (job_kind == "answer") != (delivery_target == "answer") or job_kind not in {
        "answer",
        "code",
    }:
        raise ValueError("Invalid delivery target.")
    if delivery_target not in {"answer", "patch", "draft_pr"}:
        raise ValueError("Invalid delivery target.")
    ids = sorted({source.id, *(context_message_ids or [])})
    attachment_ids = sorted(set(context_attachment_ids or []))
    if len(attachment_ids) > 20:
        raise ValueError("Too many selected files.")
    if len(ids) > 100:
        raise ValueError("Too many context references.")
    payload_hash = digest(
        {
            "profile": str(profile.id),
            "source": source.id,
            "request": request,
            "kind": job_kind,
            "delivery": delivery_target,
            "repository": str(repository.id) if repository else None,
            "base_ref": base_ref,
            "context": ids,
            "attachments": attachment_ids,
            "trigger": trigger_kind,
        }
    )
    with agent_transaction():
        actor = UserProfile.objects.get(id=actor.id, is_active=True)
        prior = agents.AgentJob.objects.filter(
            realm=actor.realm, requester=actor, idempotency_key=idempotency_key
        ).first()
        if prior is not None and completing is None:
            require_job_access(actor, prior)
            if prior.payload_digest != payload_hash:
                raise ValueError("Idempotency conflict.")
            return prior
        existing_trigger = agents.AgentJob.objects.filter(
            realm=actor.realm, source_message=source, profile=profile, trigger_kind=trigger_kind
        ).first()
        if existing_trigger is not None and completing is None:
            require_job_access(actor, existing_trigger)
            if (
                existing_trigger.requester_id != actor.id
                or existing_trigger.payload_digest != payload_hash
            ):
                raise ValueError("Message trigger already has a different task.")
            return existing_trigger
        settings = agents.AgentRealmSettings.objects.select_for_update().get(
            realm=actor.realm, enabled=True
        )
        profile = agents.AgentProfile.objects.get(id=profile.id, realm=actor.realm)
        blocked_reason = ""
        try:
            require_ready(profile)
        except ValueError:
            if not allow_blocked or profile.desired_state != "enabled":
                raise
            blocked_reason = "profile_needs_action"
        source = Message.objects.get(id=source.id, realm=actor.realm)
        if repository is not None and repository.id != profile.default_repository_id:
            raise ValueError("Repository requires a new configuration.")
        if job_kind == "code" and repository is None and not draft:
            raise ValueError("Coding requires a repository.")
        if repository is not None and not draft and base_ref not in repository.allowed_refs:
            raise ValueError("Base ref is not approved.")
        if (
            job_kind == "code"
            and not draft
            and not blocked_reason
            and (
                not profile.capability_report.get("code_ready", False)
                or repository is None
                or not repository.required_checks
            )
        ):
            raise ValueError("Coding readiness and required checks are required.")
        check_agent_access(actor, profile, repository, source, "profile.use")
        if (
            not draft
            and not blocked_reason
            and (
                agents.AgentJob.objects.filter(realm=actor.realm, status="queued").count()
                >= settings.queued_job_limit
                or agents.AgentJob.objects.filter(profile=profile, status="queued").count()
                >= settings.profile_queue_limit
            )
        ):
            raise ValueError("Agent queue is full.")
        scope = p.serialize_payload(scope_for_message(source))
        if completing is None:
            conversation = agents.AgentConversation.objects.create(
                realm=actor.realm,
                profile=profile,
                repository=repository,
                anchor_message=source,
                scope=scope,
            )
            conversation.audience_binding = p.serialize_payload(
                current_audience(conversation, actor, profile.bot_user)
            )
            conversation.save(update_fields=["audience_binding"])
        else:
            require_audience(completing)
            conversation = completing.conversation
            conversation.repository = repository
            conversation.save(update_fields=["repository"])
        policy = dict(profile.policy)
        policy["scope"] = scope
        job = agents.AgentJob(
            realm=actor.realm,
            requester=actor,
            profile=profile,
            runner=profile.runner,
            repository=repository,
            source_message=source,
            conversation=conversation,
            request=request,
            job_kind=job_kind,
            delivery_target=delivery_target,
            base_ref=base_ref,
            trigger_kind=trigger_kind,
            idempotency_key=idempotency_key,
            payload_digest=payload_hash,
            admission_revision=profile.revision,
            status="draft" if draft else "blocked" if blocked_reason else "queued",
            blocked_reason=blocked_reason,
            start_deadline=None if draft or blocked_reason else now() + timedelta(hours=24),
            policy=policy,
            budget=profile.budget,
        )
        ceiling = policy["actions"]
        if job_kind == "answer":
            ceiling = [
                action
                for action in ceiling
                if action == "context.read"
                or (repository is not None and action == "repository.read")
            ]
        policy["actions"] = current_actions(job, ceiling)
        if "context.read" not in policy["actions"] or (
            job_kind == "code"
            and not draft
            and not {"repository.read", "repository.edit", "checks.run"} <= set(policy["actions"])
        ):
            raise ValueError("Required job authority is unavailable.")
        if completing is not None:
            job.id = completing.id
            job.created_at = completing.created_at
            job.version = completing.version + 1
            job.event_sequence = completing.event_sequence
            job.draft_completion = {
                "expected_version": completing.version,
                "repository_id": str(repository.id) if repository else None,
                "base_ref": base_ref,
            }
            agents.AgentContextRef.objects.filter(job=completing).delete()
        job.clean()
        job.save(force_update=completing is not None)
        from zerver.lib.message import access_message

        for message_id in ids:
            from zerver.lib.agent_context import require_reference_audience

            require_reference_audience(job, message_id)
            access_message(actor, message_id, is_modifying_message=False)
            access_message(profile.bot_user, message_id, is_modifying_message=False)
            agents.AgentContextRef.objects.create(
                realm=actor.realm, job=job, kind="message", message_id=message_id, scope=scope
            )
        from zerver.lib.agent_context import selected_context
        from zerver.models import Attachment

        for attachment_id in attachment_ids:
            attachment = Attachment.objects.get(id=attachment_id, realm=actor.realm)
            linked = attachment.messages.filter(id__in=ids).first()
            if linked is None:
                raise ValueError("Selected file requires a selected source message.")
            ref = agents.AgentContextRef.objects.create(
                realm=actor.realm,
                job=job,
                kind="attachment",
                attachment=attachment,
                message=linked,
                scope=scope,
            )
            selected_context(job, [ref.id])
        if not draft and not blocked_reason:
            agents.AgentOutbox.objects.create(
                realm=actor.realm, job=job, delivery_key=f"wake:{job.id}:1", event_type="job.wake"
            )
            audit(job, "job.queued", {"status": "queued", "reason": ""}, actor=actor)
        return job


def complete_draft(
    actor: UserProfile,
    job_id: UUID,
    *,
    expected_version: int,
    repository_id: UUID,
    base_ref: str,
) -> agents.AgentJob:
    with agent_transaction():
        job = agents.AgentJob.objects.select_for_update().get(id=job_id, realm=actor.realm)
        require_control(actor, job)
        receipt = {
            "expected_version": expected_version,
            "repository_id": str(repository_id),
            "base_ref": base_ref,
        }
        if job.draft_completion is not None:
            if job.draft_completion != receipt:
                raise ValueError("Draft completion conflicts with the previous request.")
            return job
        if job.status != "draft" or job.version != expected_version or job.source_message is None:
            raise ValueError("Draft is no longer configurable.")
        repository = agents.AgentRepository.objects.get(id=repository_id, realm=actor.realm)
        result = create_job(
            actor,
            profile=job.profile,
            source=job.source_message,
            request=job.request,
            idempotency_key=job.idempotency_key,
            job_kind=job.job_kind,
            delivery_target=job.delivery_target,
            repository=repository,
            base_ref=base_ref,
            trigger_kind=job.trigger_kind,
            completing=job,
            context_message_ids=list(
                agents.AgentContextRef.objects.filter(
                    job=job, kind="message", message__isnull=False
                ).values_list("message_id", flat=True)
            ),
            context_attachment_ids=list(
                agents.AgentContextRef.objects.filter(
                    job=job, kind="attachment", attachment__isnull=False
                ).values_list("attachment_id", flat=True)
            ),
        )
        agents.AgentDispatchReceipt.objects.filter(job=result).update(
            decision="accepted", reason=""
        )
        return result


def build_descriptor(job: agents.AgentJob, attempt: agents.AgentAttempt) -> dict[str, Any]:
    profile = job.profile
    tested = require_ready(profile)
    if job.job_kind == "code" and (
        not profile.capability_report.get("code_ready", False)
        or job.repository is None
        or not job.repository.required_checks
    ):
        raise ValueError("Coding readiness and required checks are required.")
    policy = p.Policy.model_validate(job.policy)
    policy.actions = current_actions(job, policy.actions)  # type: ignore[assignment]
    repository = None
    if job.repository is not None:
        repo = job.repository
        repository = {
            "id": str(repo.id),
            "owner_user_id": repo.owner_id,
            "runner_id": str(repo.runner_id),
            "workspace_alias": repo.workspace_alias,
            "canonical_origin": repo.canonical_origin or None,
            "allowed_refs": repo.allowed_refs,
            "base_ref": job.base_ref,
            "base_commit": None,
            "policy_version": repo.policy_version,
            "required_checks": repo.required_checks,
        }
    data = {
        "schema_version": 1,
        "job_id": str(job.id),
        "attempt_id": str(attempt.id),
        "lease_epoch": attempt.lease_epoch,
        "lease_expires_at": attempt.lease_expires_at.isoformat(),
        "audience": job.conversation.audience_binding,
        "tested_configuration": p.serialize_payload(tested),
        "profile_id": str(profile.id),
        "profile_revision": profile.revision,
        "descriptor_digest": "0" * 64,
        "configuration_digest": profile.readiness_configuration_digest,
        "job_kind": job.job_kind,
        "delivery_target": job.delivery_target,
        "request": job.request,
        "runner_id": str(job.runner_id),
        "adapter": {
            "id": profile.adapter_id,
            "version": profile.adapter_version,
            "mode": profile.mode,
        },
        "provider": provider_config(profile.provider) if profile.provider else None,
        "repository": repository,
        "policy": p.serialize_payload(policy),
        "budget": job.budget,
        "context_refs": [
            {
                "id": str(ref.id),
                "kind": ref.kind,
                "message_id": ref.message_id if ref.kind == "message" else None,
                "attachment_id": ref.attachment_id,
                "repository_id": str(ref.repository_id) if ref.repository_id else None,
                "scope": ref.scope,
                "validated_at": ref.validated_at.isoformat(),
            }
            for ref in agents.AgentContextRef.objects.filter(job=job)
        ],
        "inputs": [
            input_data(item)
            for item in agents.AgentInput.objects.filter(job=job).order_by("sequence")[:100]
        ],
        "checkpoint": (
            checkpoint_data(attempt.source_checkpoint) if attempt.source_checkpoint else None
        ),
    }
    descriptor = p.AttemptDescriptor.model_validate(data)
    result = p.serialize_payload(descriptor)
    if p.configuration_digest(descriptor) != profile.readiness_configuration_digest:
        raise ValueError("Configuration changed.")
    result["descriptor_digest"] = p.descriptor_digest(result)
    return result


def claim_work(runner: agents.AgentRunner, *, claim_key: UUID) -> dict[str, Any] | None:
    with agent_transaction():
        settings = agents.AgentRealmSettings.objects.select_for_update().get(
            realm_id=runner.realm_id
        )
        runner = agents.AgentRunner.objects.select_for_update().get(
            id=runner.id, revoked_at__isnull=True
        )
        prior = agents.AgentAttempt.objects.filter(runner=runner, claim_key=claim_key).first()
        if prior is not None:
            return prior.descriptor
        if not settings.enabled:
            raise ValueError("Agent feature is disabled.")
        if (
            agents.AgentAttempt.objects.filter(runner=runner, active=True).count()
            >= runner.capacity
            or agents.AgentAttempt.objects.filter(realm=runner.realm, active=True).count()
            >= settings.active_job_limit
        ):
            return None
        candidates = (
            agents.AgentJob.objects.select_for_update(skip_locked=True)
            .filter(runner=runner, status="queued", next_attempt_at__lte=now())
            .order_by("created_at", "id")[:100]
        )
        for job in candidates:
            if job.start_deadline is None or job.start_deadline <= now():
                transition(job, "blocked", reason="start_deadline_expired")
                continue
            if agents.AgentAttempt.objects.filter(job=job, active=True).exists():
                continue
            try:
                require_audience(job)
                check_agent_access(
                    job.requester, job.profile, job.repository, job.source_message, "profile.use"
                )
                require_ready(job.profile)
            except (ValueError, AgentAccessDenied):
                continue
            number = (
                agents.AgentAttempt.objects.filter(job=job).aggregate(value=Max("number"))["value"]
                or 0
            ) + 1
            previous = agents.AgentAttempt.objects.filter(job=job).order_by("-number").first()
            checkpoint = (
                job.resume_checkpoint
                if job.resume_checkpoint_id
                else (previous.checkpoints.order_by("-created_at").first() if previous else None)
            )
            attempt = agents.AgentAttempt(
                realm=job.realm,
                job=job,
                runner=runner,
                number=number,
                lease_epoch=number,
                lease_expires_at=now() + timedelta(seconds=90),
                claim_key=claim_key,
                audience_binding=job.conversation.audience_binding,
                source_checkpoint=checkpoint,
            )
            descriptor = build_descriptor(job, attempt)
            attempt.descriptor = descriptor
            attempt.configuration_digest = descriptor["configuration_digest"]
            attempt.descriptor_digest = descriptor["descriptor_digest"]
            attempt.adapter_version = job.profile.adapter_version
            attempt.provider_config_version = (
                job.profile.provider.config_version if job.profile.provider else None
            )
            attempt.clean()
            attempt.save()
            transition(job, "running")
            audit(job, "attempt.starting", {"status": "running", "reason": ""}, attempt=attempt)
            return descriptor
        return None


def locked_attempt(
    runner: agents.AgentRunner,
    job_id: UUID,
    attempt_id: UUID,
    epoch: int,
    *,
    expected_version: int | None = None,
    execution: bool = True,
) -> tuple[agents.AgentJob, agents.AgentAttempt]:
    runner = agents.AgentRunner.objects.select_for_update().get(
        id=runner.id, realm_id=runner.realm_id
    )
    job = agents.AgentJob.objects.select_for_update().get(
        id=job_id, runner=runner, realm=runner.realm
    )
    attempt = agents.AgentAttempt.objects.select_for_update().get(
        id=attempt_id, job=job, runner=runner, realm=runner.realm, lease_epoch=epoch
    )
    if expected_version is not None and expected_version != job.version:
        raise ValueError("Job version changed.")
    if execution:
        if (
            runner.revoked_at is not None
            or not attempt.active
            or attempt.lease_expires_at <= now()
            or attempt.process_state not in {"starting", "active"}
            or job.status not in EXECUTING
        ):
            raise ValueError("Execution lease is unavailable.")
        if (now() - attempt.created_at).total_seconds() > attempt.descriptor["budget"][
            "active_seconds"
        ]:
            raise ValueError("Job time budget exhausted.")
        require_audience(job)
        if attempt.audience_binding != job.conversation.audience_binding:
            raise ValueError("Attempt audience changed.")
        check_attempt_access(job.requester, job, attempt, "profile.use")
    return job, attempt


def check_attempt_access(
    actor: UserProfile, job: agents.AgentJob, attempt: agents.AgentAttempt, action: str
) -> None:
    actor = UserProfile.objects.get(id=actor.id, realm_id=job.realm_id, is_active=True)
    profile = agents.AgentProfile.objects.get(id=job.profile_id, realm_id=job.realm_id)
    descriptor = p.AttemptDescriptor.model_validate(attempt.descriptor)
    if profile.desired_state == "archived" or profile.policy_version != descriptor.policy.version:
        raise AgentAccessDenied("Agent access denied.")
    if action != "profile.use" and action not in descriptor.tested_configuration.actions:
        raise AgentAccessDenied("Agent access denied.")
    from zerver.lib.message import access_message

    if job.source_message_id is None:
        raise AgentAccessDenied("Agent access denied.")
    access_message(actor, job.source_message_id, is_modifying_message=False)
    access_message(profile.bot_user, job.source_message_id, is_modifying_message=False)
    if not _owner_or_grant(
        actor,
        profile,
        target_kind="profile",
        action=action,
        repository=job.repository,
        source_message=job.source_message,
    ):
        raise AgentAccessDenied("Agent access denied.")
    if job.repository_id is not None:
        repository = agents.AgentRepository.objects.get(id=job.repository_id, realm_id=job.realm_id)
        repo_action = (
            action
            if action.startswith(("repository.", "checks.", "shell.", "dependencies.", "git."))
            else "repository.read"
        )
        if repository.disabled_at is not None or not _owner_or_grant(
            actor,
            repository,
            target_kind="repository",
            action=repo_action,
            source_message=job.source_message,
        ):
            raise AgentAccessDenied("Agent access denied.")
    require_snapshot_resources(job, attempt, actor=actor)


def require_snapshot_resources(
    job: agents.AgentJob, attempt: agents.AgentAttempt, *, actor: UserProfile | None = None
) -> None:
    actor = actor or job.requester
    runner = agents.AgentRunner.objects.get(id=attempt.runner_id, realm_id=job.realm_id)
    if runner.revoked_at is not None or not _owner_or_grant(
        actor,
        runner,
        target_kind="runner",
        action="runner.use",
        repository=job.repository,
        source_message=job.source_message,
    ):
        raise AgentAccessDenied("Agent access denied.")
    descriptor = p.AttemptDescriptor.model_validate(attempt.descriptor)
    if descriptor.provider is not None:
        current = agents.AgentProvider.objects.get(id=descriptor.provider.id, realm_id=job.realm_id)
        if current.disabled_at is not None or not _owner_or_grant(
            actor,
            current,
            target_kind="provider",
            action="provider.use",
            repository=job.repository,
            source_message=job.source_message,
        ):
            raise AgentAccessDenied("Agent access denied.")
        if current.config_version != descriptor.provider.config_version or provider_config(current)[
            "credential_ref"
        ] != (
            p.serialize_payload(descriptor.provider.credential_ref)
            if descriptor.provider.credential_ref is not None
            else None
        ):
            raise ValueError("Provider authority changed.")


def invalidate_approvals(attempt: agents.AgentAttempt) -> None:
    agents.AgentApproval.objects.filter(
        attempt=attempt, decision__in=["pending", "approved"]
    ).update(decision="cancelled")


def request_stop(
    job: agents.AgentJob,
    attempt: agents.AgentAttempt | None,
    *,
    target: str = "cancelled",
    reason: str = "",
) -> None:
    job.stop_target = target
    job.save(update_fields=["stop_target"])
    if attempt is None:
        transition(job, target, reason=reason)
        return
    attempt.process_state = "stopping"
    attempt.save(update_fields=["process_state"])
    invalidate_approvals(attempt)
    transition(job, "cancel_requested", reason=reason)
    agents.AgentOutbox.objects.get_or_create(
        delivery_key=f"stop:{attempt.id}",
        defaults={
            "realm": job.realm,
            "job": job,
            "payload_ref": attempt.id,
            "event_type": "attempt.stop",
        },
    )
    audit(
        job,
        "attempt.stop_requested",
        {"status": "cancel_requested", "reason": reason},
        attempt=attempt,
    )


def cancel_job(actor: UserProfile, job_id: UUID, expected_version: int) -> agents.AgentJob:
    with agent_transaction():
        job = agents.AgentJob.objects.select_for_update().get(id=job_id, realm=actor.realm)
        require_control(actor, job)
        if job.status in {"cancel_requested", "cancelled"}:
            return job
        if job.version != expected_version or job.status == "completed":
            raise ValueError("Job version changed.")
        attempt = (
            agents.AgentAttempt.objects.select_for_update().filter(job=job, active=True).first()
        )
        request_stop(job, attempt)
        return job


def input_data(item: agents.AgentInput) -> dict[str, object]:
    return {
        "id": str(item.id),
        "author_user_id": item.author_id,
        "source_message_id": item.source_message_id,
        "client_key": str(item.client_key),
        "sequence": item.sequence,
        "input_type": item.input_type,
        "text": item.text,
        "delivery_state": item.delivery_state,
    }


def checkpoint_data(item: agents.AgentCheckpoint) -> dict[str, object]:
    return {
        "id": str(item.id),
        "source_attempt_id": str(item.attempt_id),
        "base_commit": item.base_commit or None,
        "tree_hash": item.tree_hash or None,
        "summary": item.summary,
        "context_ref_ids": item.context_refs,
        "artifact_ids": item.artifact_ids,
        "remaining_work": item.remaining_work,
        "next_step": item.next_step,
        "adapter_session_ref": None,
        "input_cursor": item.input_cursor,
    }


def add_input(
    actor: UserProfile,
    job_id: UUID,
    *,
    expected_version: int,
    client_key: UUID,
    text: str,
    input_type: str = "steering",
    source_message_id: int | None = None,
) -> agents.AgentInput:
    if not text or len(text) > 20000 or input_type not in {"steering", "answer", "replan"}:
        raise ValueError("Invalid input.")
    payload_hash = digest(
        {"text": text, "type": input_type, "source": source_message_id, "actor": actor.id}
    )
    with agent_transaction():
        job = agents.AgentJob.objects.select_for_update().get(id=job_id, realm=actor.realm)
        require_control(actor, job)
        prior = agents.AgentInput.objects.filter(job=job, client_key=client_key).first()
        if prior:
            if prior.payload_digest != payload_hash:
                raise ValueError("Input idempotency conflict.")
            return prior
        if source_message_id is not None:
            previous_source = agents.AgentInput.objects.filter(
                job=job, source_message_id=source_message_id
            ).first()
            if previous_source is not None:
                if previous_source.payload_digest != payload_hash:
                    raise ValueError("Input source idempotency conflict.")
                return previous_source
        if job.version != expected_version or job.status in TERMINAL | {"cancel_requested"}:
            raise ValueError("Job cannot accept input.")
        if job.input_sequence >= 100:
            raise ValueError("Job input limit exceeded.")
        if (
            job.status != "queued"
            and not agents.AgentAttempt.objects.filter(job=job, active=True).exists()
        ):
            raise ValueError("Stopped execution cannot accept new input.")
        require_audience(job)
        check_agent_access(actor, job.profile, job.repository, job.source_message, "profile.use")
        if source_message_id is not None:
            from zerver.lib.agent_context import require_reference_audience
            from zerver.lib.message import access_message

            require_reference_audience(job, source_message_id)
            access_message(actor, source_message_id, is_modifying_message=False)
            access_message(job.profile.bot_user, source_message_id, is_modifying_message=False)
        job.input_sequence += 1
        job.version += 1
        if job.status in {"verifying", "waiting_for_input", "waiting_for_approval"}:
            job.status = "running"
            job.result_proposal = None
            for attempt in agents.AgentAttempt.objects.filter(job=job, active=True):
                invalidate_approvals(attempt)
        job.save(update_fields=["input_sequence", "version", "status", "result_proposal"])
        item = agents.AgentInput.objects.create(
            realm=job.realm,
            job=job,
            author=actor,
            source_message_id=source_message_id,
            client_key=client_key,
            payload_digest=payload_hash,
            sequence=job.input_sequence,
            text=text,
            input_type=input_type,
        )
        audit(
            job,
            "input.received",
            {
                "input_id": str(item.id),
                "input_sequence": item.sequence,
                "delivery_state": "pending",
            },
            actor=actor,
        )
        return item


def resume_job(
    actor: UserProfile, job_id: UUID, expected_version: int, checkpoint_id: UUID | None = None
) -> agents.AgentJob:
    with agent_transaction():
        job = agents.AgentJob.objects.select_for_update().get(id=job_id, realm=actor.realm)
        require_control(actor, job)
        if job.version != expected_version or job.status not in {
            "cancelled",
            "failed",
            "interrupted",
            "blocked",
        }:
            raise ValueError("Job cannot resume.")
        if agents.AgentAttempt.objects.filter(job=job, active=True).exists():
            raise ValueError("Previous containment has not stopped.")
        if agents.AgentOperation.objects.filter(
            attempt__job=job, status__in=["started", "outcome_unknown"]
        ).exists():
            raise ValueError("Operation reconciliation is required.")
        if agents.AgentInput.objects.filter(job=job, delivery_state="delivery_uncertain").exists():
            raise ValueError("Input reconciliation is required.")
        require_ready(job.profile)
        require_audience(job)
        check_agent_access(actor, job.profile, job.repository, job.source_message, "profile.use")
        if checkpoint_id is not None:
            checkpoint = agents.AgentCheckpoint.objects.get(
                id=checkpoint_id, attempt__job=job, realm=job.realm
            )
            if checkpoint.audience_binding != job.conversation.audience_binding:
                raise ValueError("Checkpoint audience changed.")
            if agents.AgentArtifact.objects.filter(
                id__in=checkpoint.artifact_ids,
                attempt=checkpoint.attempt,
                unavailable_at__isnull=True,
            ).count() != len(set(checkpoint.artifact_ids)):
                raise ValueError("Checkpoint artifacts are unavailable.")
            if agents.AgentContextRef.objects.filter(
                id__in=checkpoint.context_refs, job=job
            ).count() != len(set(checkpoint.context_refs)):
                raise ValueError("Checkpoint context is unavailable.")
        if not agents.AgentRealmSettings.objects.get(realm=job.realm).enabled:
            raise ValueError("Agents are disabled.")
        job.resume_checkpoint_id = checkpoint_id
        job.start_deadline = now() + timedelta(hours=24)
        job.result_proposal = None
        job.save(update_fields=["resume_checkpoint", "start_deadline", "result_proposal"])
        transition(job, "queued")
        agents.AgentOutbox.objects.create(
            realm=job.realm,
            job=job,
            delivery_key=f"wake:{job.id}:{job.version}",
            event_type="job.wake",
        )
        return job


def record_event(
    runner: agents.AgentRunner,
    event: p.RunnerEvent,
    *,
    expected_version: int | None = None,
    stop_only: bool = False,
) -> dict[str, object]:
    event = p.parse_runner_event(p.serialize_payload(event))
    with agent_transaction():
        job, attempt = locked_attempt(
            runner, event.job_id, event.attempt_id, event.lease_epoch, execution=False
        )
        if agents.AgentAttempt.objects.filter(job=job, number__gt=attempt.number).exists():
            raise ValueError("Attempt was superseded.")
        prior = agents.AgentAuditEvent.objects.filter(
            attempt=attempt, event_id=event.event_id
        ).first()
        payload = p.serialize_payload(event.payload)
        if prior is not None:
            if (
                prior.type != event.type
                or prior.attempt_sequence != event.sequence
                or prior.payload != payload
                or prior.occurred_at != event.occurred_at
            ):
                raise ValueError("Event idempotency conflict.")
            return {
                "event_sequence": prior.sequence,
                "job_version": job.version,
                "status": job.status,
            }
        cleanup = event.type in {"attempt.stopped", "attempt.interrupted"}
        if (
            cleanup
            and isinstance(event.payload, p.ProcessPayload)
            and (event.payload.summary or event.payload.adapter_session_ref is not None)
        ):
            raise ValueError("Cleanup evidence cannot contain runtime text.")
        if stop_only and event.type != "attempt.stopped":
            raise ValueError("Stop-only authority cannot report execution.")
        if not cleanup:
            locked_attempt(
                runner, job.id, attempt.id, attempt.lease_epoch, expected_version=expected_version
            )
        elif not attempt.active:
            raise ValueError("Attempt has ended.")
        if event.sequence != attempt.event_cursor + 1:
            raise ValueError("Event sequence gap.")
        if event.occurred_at > now() + timedelta(
            minutes=5
        ) or event.occurred_at < attempt.created_at - timedelta(minutes=5):
            raise ValueError("Event time is invalid.")
        if isinstance(event.payload, p.WorkspacePreparedPayload):
            prepared = event.payload
            if (
                attempt.workspace_prepared_at is not None
                or attempt.process_state != "starting"
                or job.repository_id != prepared.repository_id
                or prepared.base_ref != job.base_ref
            ):
                raise ValueError("Workspace preparation is invalid.")
            checkpoint = attempt.source_checkpoint
            if checkpoint is not None and (
                prepared.base_commit != checkpoint.base_commit
                or prepared.tree_hash != checkpoint.tree_hash
            ):
                raise ValueError("Prepared workspace does not match the resume checkpoint.")
            attempt.base_commit = prepared.base_commit
            attempt.tree_hash = prepared.tree_hash
            attempt.workspace_reference = prepared.workspace_reference
            attempt.workspace_prepared_at = now()
        elif isinstance(event.payload, p.ProcessPayload):
            process = event.payload
            if event.type == "attempt.starting":
                if attempt.process_state != "starting":
                    raise ValueError("Attempt already started.")
            elif event.type == "attempt.started":
                if attempt.process_state != "starting" or (
                    job.repository_id is not None and attempt.workspace_prepared_at is None
                ):
                    raise ValueError("Workspace must be prepared before runtime start.")
                attempt.process_state = "active"
                attempt.started_at = now()
                attempt.runtime_session_reference = process.adapter_session_ref or ""
            elif event.type == "attempt.interrupted":
                attempt.process_state = "unknown"
                invalidate_approvals(attempt)
                agents.AgentOperation.objects.filter(attempt=attempt, status="started").update(
                    status="outcome_unknown"
                )
                agents.AgentInput.objects.filter(
                    delivered_attempt=attempt, delivery_state="delivered"
                ).update(delivery_state="delivery_uncertain")
                transition(job, "interrupted", reason="stop_unconfirmed")
            elif event.type == "attempt.stopped":
                # stop_confirmed means the trusted supervisor observed an empty containment.
                # An ACP end_turn or a model sentence cannot construct this control event.
                if not process.stop_confirmed:
                    raise ValueError("Empty containment evidence is required.")
                attempt.process_state = "stopped"
                attempt.stopped_at = now()
                attempt.ended_at = now()
                attempt.active = False
                attempt.stop_receipt = p.serialize_payload(event)
                agents.AgentInput.objects.filter(
                    delivered_attempt=attempt, delivery_state="delivered"
                ).update(delivery_state="delivery_uncertain")
                invalidate_approvals(attempt)
                agents.AgentOperation.objects.filter(attempt=attempt, status="started").update(
                    status="outcome_unknown"
                )
                if (
                    job.status == "cancel_requested"
                    or runner.revoked_at is not None
                    or agents.AgentOutbox.objects.filter(delivery_key=f"stop:{attempt.id}").exists()
                ):
                    transition(job, job.stop_target)
                elif job.status != "verifying":
                    transition(job, "interrupted", reason="runtime_stopped")
        elif isinstance(event.payload, p.InputPayload):
            item = agents.AgentInput.objects.select_for_update().get(
                id=event.payload.input_id,
                job=job,
                realm=job.realm,
                sequence=event.payload.input_sequence,
            )
            if item.delivered_attempt_id != attempt.id or item.delivery_state not in {
                "delivered",
                "delivery_uncertain",
            }:
                raise ValueError("Input was not delivered to this attempt.")
            if event.payload.delivery_state == "applied":
                if agents.AgentInput.objects.filter(
                    job=job,
                    sequence__lt=item.sequence,
                    delivery_state__in=["pending", "delivered", "delivery_uncertain"],
                ).exists():
                    raise ValueError("Input acknowledgement is out of order.")
                item.applied_at = now()
                attempt.input_cursor = item.sequence
            item.delivery_state = event.payload.delivery_state
            item.save(update_fields=["delivery_state", "applied_at"])
        elif isinstance(event.payload, p.ToolPayload):
            operation = agents.AgentOperation.objects.select_for_update().get(
                attempt=attempt,
                operation_id=event.payload.operation_id,
                argument_digest=event.payload.argument_digest,
                tool_class=event.payload.tool_class,
            )
            if operation.status != "started":
                raise ValueError("Tool requires durable execution authorization.")
            if event.type == "tool.finished":
                if event.payload.status == "succeeded" and operation.tool_class in {
                    "git.push",
                    "git.draft_pr",
                }:
                    raise ValueError("Remote success requires a matching receipt.")
                operation.status = event.payload.status
                operation.finished_at = now()
                operation.save(update_fields=["status", "finished_at"])
                if operation.tool_class in {"repository.edit", "shell.run", "dependencies.install"}:
                    attempt.tree_hash = ""
                    job.result_proposal = None
                    job.save(update_fields=["result_proposal"])
        elif isinstance(event.payload, p.VerificationRecord):
            verification = event.payload
            operation = agents.AgentOperation.objects.select_for_update().get(
                attempt=attempt,
                operation_id=verification.operation_id,
                tool_class="checks.run",
                status__in=["started", "succeeded", "failed"],
            )
            arguments = p.ChecksArguments.model_validate(operation.arguments)
            if (
                verification.check_id not in arguments.check_ids
                or verification.tree_hash != arguments.tree_hash
                or operation.started_at is None
                or verification.started_at < operation.started_at
                or verification.tree_hash != attempt.tree_hash
            ):
                raise ValueError("Check lacks matching durable execution authority.")
            if attempt.workspace_prepared_at is None or attempt.process_state != "active":
                raise ValueError("Workspace is unavailable.")
            repository = p.RepositoryConfig.model_validate(attempt.descriptor["repository"])
            check = next(
                (
                    check
                    for check in repository.required_checks
                    if check.id == verification.check_id
                ),
                None,
            )
            if check is None or check.argv != verification.command or check.cwd != verification.cwd:
                raise ValueError("Check differs from the approved check.")
            if (
                verification.started_at < attempt.created_at
                or verification.finished_at > now() + timedelta(seconds=5)
            ):
                raise ValueError("Check timing is invalid.")
            artifact = agents.AgentArtifact.objects.get(
                id=verification.artifact_id,
                attempt=attempt,
                realm=job.realm,
                kind="verification",
                unavailable_at__isnull=True,
            )
            agents.AgentVerification.objects.create(
                realm=job.realm,
                attempt=attempt,
                operation=operation,
                check_id=verification.check_id,
                command=verification.command,
                cwd=verification.cwd,
                exit_code=verification.exit_code,
                started_at=verification.started_at,
                finished_at=verification.finished_at,
                tree_hash=verification.tree_hash,
                output_artifact=artifact,
                timed_out=verification.timed_out,
            )
        elif isinstance(event.payload, p.InputRequestPayload):
            transition(job, "waiting_for_input")
        elif isinstance(event.payload, p.ResultPayload):
            result = event.payload
            if attempt.process_state != "active" or not result.summary.strip():
                raise ValueError("Result is incomplete.")
            artifacts = agents.AgentArtifact.objects.filter(
                id__in=result.artifact_ids,
                attempt=attempt,
                realm=job.realm,
                unavailable_at__isnull=True,
            )
            if artifacts.count() != len(set(result.artifact_ids)):
                raise ValueError("Result artifacts are unavailable.")
            if agents.AgentOperation.objects.filter(
                attempt=attempt, status__in=["started", "outcome_unknown"]
            ).exists():
                raise ValueError("Operation outcomes are unresolved.")
            if job.job_kind == "code" and (
                not result.tree_hash or attempt.workspace_prepared_at is None
            ):
                raise ValueError("Final workspace evidence is required.")
            attempt.tree_hash = result.tree_hash or ""
            job.result_proposal = payload
            job.save(update_fields=["result_proposal"])
            job.phase = "review"
            job.save(update_fields=["phase"])
            transition(job, "verifying")
            agents.AgentOutbox.objects.get_or_create(
                delivery_key=f"result:{job.id}",
                defaults={
                    "realm": job.realm,
                    "job": job,
                    "event_type": "result.publish",
                    "payload_ref": attempt.id,
                },
            )
        attempt.event_cursor = event.sequence
        attempt.save()
        receipt = audit(job, event.type, payload, attempt=attempt, authority="runner", event=event)
        return {
            "event_sequence": receipt.sequence,
            "job_version": job.version,
            "status": job.status,
        }


def deliver_inputs(
    runner: agents.AgentRunner, job_id: UUID, attempt_id: UUID, epoch: int
) -> list[dict[str, object]]:
    with agent_transaction():
        job, attempt = locked_attempt(runner, job_id, attempt_id, epoch)
        items = list(
            agents.AgentInput.objects.select_for_update()
            .filter(job=job, delivery_state__in=["pending", "delivered", "delivery_uncertain"])
            .order_by("sequence")[:100]
        )
        for item in items:
            if item.delivered_attempt_id not in {None, attempt.id}:
                raise ValueError("Earlier input delivery needs reconciliation.")
            if item.delivery_state == "pending":
                item.delivery_state = "delivered"
                item.delivered_attempt = attempt
                item.clean()
                item.save(update_fields=["delivery_state", "delivered_attempt"])
        return [input_data(item) for item in items]


def heartbeat(
    runner: agents.AgentRunner, identities: list[p.LeaseIdentity]
) -> list[dict[str, object]]:
    with agent_transaction():
        runner = agents.AgentRunner.objects.select_for_update().get(
            id=runner.id, revoked_at__isnull=True
        )
        runner.last_heartbeat_at = now()
        runner.status = "online"
        runner.save(update_fields=["last_heartbeat_at", "status"])
        result: list[dict[str, object]] = []
        for identity in identities:
            job, attempt = locked_attempt(
                runner, identity.job_id, identity.attempt_id, identity.lease_epoch, execution=False
            )
            if not attempt.active:
                continue
            if attempt.process_state in {"starting", "active"} and job.status in EXECUTING:
                # An expired lease never comes back to life after a late heartbeat.
                locked_attempt(runner, job.id, attempt.id, attempt.lease_epoch)
                limit = attempt.created_at + timedelta(
                    seconds=attempt.descriptor["budget"]["active_seconds"]
                )
                attempt.lease_expires_at = min(now() + timedelta(seconds=90), limit)
                attempt.save(update_fields=["lease_expires_at"])
            result.append(
                {
                    "job_id": str(job.id),
                    "attempt_id": str(attempt.id),
                    "lease_epoch": attempt.lease_epoch,
                    "job_version": job.version,
                    "lease_expires_at": attempt.lease_expires_at.isoformat(),
                    "control": (
                        "stop" if attempt.process_state in {"stopping", "unknown"} else "continue"
                    ),
                }
            )
        return result


def save_checkpoint(
    runner: agents.AgentRunner, job_id: UUID, attempt_id: UUID, epoch: int, checkpoint: p.Checkpoint
) -> agents.AgentCheckpoint:
    with agent_transaction():
        job, attempt = locked_attempt(runner, job_id, attempt_id, epoch)
        if (
            checkpoint.source_attempt_id != attempt.id
            or checkpoint.input_cursor != attempt.input_cursor
        ):
            raise ValueError("Checkpoint attempt or cursor mismatch.")
        if job.repository_id is not None and (
            checkpoint.base_commit != attempt.base_commit or not checkpoint.tree_hash
        ):
            raise ValueError("Checkpoint workspace mismatch.")
        if agents.AgentContextRef.objects.filter(
            job=job, id__in=checkpoint.context_ref_ids
        ).count() != len(set(checkpoint.context_ref_ids)):
            raise ValueError("Checkpoint context is unavailable.")
        if agents.AgentArtifact.objects.filter(
            attempt=attempt, id__in=checkpoint.artifact_ids, unavailable_at__isnull=True
        ).count() != len(set(checkpoint.artifact_ids)):
            raise ValueError("Checkpoint artifact is unavailable.")
        values = {
            "base_commit": checkpoint.base_commit or "",
            "tree_hash": checkpoint.tree_hash or "",
            "summary": checkpoint.summary,
            "context_refs": [str(value) for value in checkpoint.context_ref_ids],
            "artifact_ids": [str(value) for value in checkpoint.artifact_ids],
            "remaining_work": checkpoint.remaining_work,
            "next_step": checkpoint.next_step,
            "adapter_session_ref": checkpoint.adapter_session_ref or "",
            "input_cursor": checkpoint.input_cursor,
            "audience_binding": attempt.audience_binding,
        }
        prior = agents.AgentCheckpoint.objects.filter(id=checkpoint.id).first()
        if prior is not None:
            if prior.attempt_id != attempt.id or any(
                getattr(prior, key) != value for key, value in values.items()
            ):
                raise ValueError("Checkpoint idempotency conflict.")
            return prior
        from zerver.lib.agent_results import reject_secrets

        reject_secrets(job, p.canonical_json(p.serialize_payload(checkpoint)))
        saved = agents.AgentCheckpoint.objects.create(
            id=checkpoint.id, realm=job.realm, attempt=attempt, **values
        )
        attempt.tree_hash = checkpoint.tree_hash or ""
        attempt.save(update_fields=["tree_hash"])
        return saved


def stop_evidence(token: str, event: p.RunnerEvent) -> dict[str, object]:
    from zerver.actions.agents import authenticate_runner_stop_token, authenticate_runner_token
    from zerver.lib.agent_secrets import credential_matches, hash_agent_credential

    if event.type != "attempt.stopped":
        raise ValueError("Only stopped evidence is accepted.")
    with agent_transaction():
        try:
            credential = authenticate_runner_token(token)
            runner = credential.runner
        except ValueError:
            credential = agents.AgentRunnerCredential.objects.select_related("runner").get(
                token_hash=hash_agent_credential(token)
            )
            runner = credential.runner
            if (
                runner.revoked_at is None
                or credential.revoked_at != runner.revoked_at
                or credential.rotated_at is not None
                or credential.expires_at <= now()
                or not credential_matches(token, credential.token_hash)
            ):
                raise ValueError("Stop credential is unavailable.") from None
            job, attempt = locked_attempt(
                runner, event.job_id, event.attempt_id, event.lease_epoch, execution=False
            )
            if attempt.active and attempt.process_state == "stopping":
                authenticate_runner_stop_token(
                    token,
                    job_id=job.id,
                    attempt_id=attempt.id,
                    lease_epoch=attempt.lease_epoch,
                    job_version=job.version,
                )
            elif attempt.active:
                if attempt.process_state != "unknown" or job.status != "interrupted":
                    raise ValueError("Stop state is unavailable.")
            elif attempt.stop_receipt != p.serialize_payload(event):
                raise ValueError("Stop receipt does not match.")
        job, attempt = locked_attempt(
            runner, event.job_id, event.attempt_id, event.lease_epoch, execution=False
        )
        supplied = p.serialize_payload(event)
        if attempt.stop_receipt is not None:
            if attempt.stop_receipt != supplied:
                raise ValueError("Stop receipt does not match.")
            prior = agents.AgentAuditEvent.objects.get(attempt=attempt, event_id=event.event_id)
            return {
                "event_sequence": prior.sequence,
                "job_version": job.version,
                "status": job.status,
            }
        # Dedicated stop evidence has no execution authority. The server assigns its
        # journal position so a lost execution ACK cannot prevent containment release.
        normalized = event.model_copy(update={"sequence": attempt.event_cursor + 1})
        receipt = record_event(runner, normalized, stop_only=True)
        agents.AgentAttempt.objects.filter(id=attempt.id).update(stop_receipt=supplied)
        return receipt


def reconcile_input(
    runner: agents.AgentRunner,
    job_id: UUID,
    attempt_id: UUID,
    epoch: int,
    *,
    input_id: UUID,
    input_sequence: int,
    outcome: str,
    receipt_id: UUID,
) -> agents.AgentInput:
    """Accept a trusted supervisor receipt; never replay an uncertain input implicitly."""
    if outcome not in {"applied", "not_applied"}:
        raise ValueError("Invalid input reconciliation.")
    with agent_transaction():
        job, attempt = locked_attempt(runner, job_id, attempt_id, epoch, execution=False)
        if (
            runner.revoked_at is not None
            or agents.AgentAttempt.objects.filter(job=job, number__gt=attempt.number).exists()
        ):
            raise ValueError("Input attempt is unavailable.")
        require_audience(job)
        check_agent_access(
            job.requester, job.profile, job.repository, job.source_message, "profile.use"
        )
        item = agents.AgentInput.objects.select_for_update().get(
            id=input_id, job=job, realm=job.realm, sequence=input_sequence
        )
        receipt = {
            "input_id": str(item.id),
            "input_sequence": item.sequence,
            "outcome": outcome,
            "receipt_id": str(receipt_id),
            "attempt_id": str(attempt.id),
            "lease_epoch": epoch,
        }
        if item.reconciliation_receipt is not None:
            if item.reconciliation_receipt != receipt:
                raise ValueError("Input reconciliation conflict.")
            return item
        if item.delivered_attempt_id != attempt.id or item.delivery_state != "delivery_uncertain":
            raise ValueError("Input is not uncertain for this attempt.")
        if outcome == "applied":
            if agents.AgentInput.objects.filter(
                job=job,
                sequence__lt=item.sequence,
                delivery_state__in=["pending", "delivered", "delivery_uncertain"],
            ).exists():
                raise ValueError("Input reconciliation is out of order.")
            item.delivery_state = "applied"
            item.applied_at = now()
        else:
            item.delivery_state = "pending"
            item.delivered_attempt = None
        item.reconciliation_receipt = receipt
        item.save(
            update_fields=[
                "delivery_state",
                "applied_at",
                "delivered_attempt",
                "reconciliation_receipt",
            ]
        )
        return item
