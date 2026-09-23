"""Human lifecycle routes use Zulip authentication and versioned form payloads."""

from uuid import UUID

from django.core.exceptions import ObjectDoesNotExist
from django.http import HttpRequest, HttpResponse
from django.utils.timezone import now

from zerver.actions import agent_approvals, agent_jobs
from zerver.lib import agent_job_requests as r
from zerver.lib import agent_protocol as p
from zerver.lib.agent_context import (
    agent_transaction,
    ensure_budget,
    require_audience,
    require_job_access,
)
from zerver.lib.agent_policy import AgentAccessDenied
from zerver.lib.agent_results import download_artifact
from zerver.lib.exceptions import JsonableError
from zerver.lib.message import access_message
from zerver.models import Message, UserProfile, agents
from zerver.views.agents import _success, payload, safe_agent_endpoint


def job_data(actor: UserProfile, job: agents.AgentJob) -> dict[str, object]:
    actions = []
    try:
        agent_jobs.require_control(actor, job)
        if job.status not in {"completed", "cancelled"}:
            actions.append("cancel")
        if job.status == "draft":
            actions.append("configure")
        if job.status in {"cancelled", "failed", "interrupted", "blocked"}:
            actions.append("resume")
        if job.status not in agent_jobs.TERMINAL | {"cancel_requested"} and (
            job.status == "queued"
            or agents.AgentAttempt.objects.filter(
                job=job, active=True, process_state__in=["starting", "active"]
            ).exists()
        ):
            actions.append("input")
    except AgentAccessDenied:
        pass
    return {
        "id": str(job.id),
        "profile_id": str(job.profile_id),
        "requester_id": job.requester_id,
        "source_message_id": job.source_message_id,
        "status": job.status,
        "phase": job.phase,
        "version": job.version,
        "request": job.request,
        "job_kind": job.job_kind,
        "delivery_target": job.delivery_target,
        "blocked_reason": job.blocked_reason,
        "requirements": (
            [{"code": "profile_needs_action", "surface": "adapter", "action": "probe_again"}]
            if job.blocked_reason == "profile_needs_action"
            else []
        ),
        "start_deadline": job.start_deadline.isoformat() if job.start_deadline else None,
        "result": job.result_receipt,
        "allowed_actions": actions,
    }


def window(request: HttpRequest) -> tuple[int, int]:
    offset, limit = int(request.GET.get("offset", "0")), int(request.GET.get("limit", "50"))
    if offset < 0 or not 1 <= limit <= 100:
        raise ValueError("Invalid pagination.")
    return offset, limit


def evidence_window(request: HttpRequest, name: str) -> tuple[int, int]:
    offset = int(request.GET.get(f"{name}_offset", "0"))
    limit = int(request.GET.get(f"{name}_limit", "100"))
    if offset < 0 or not 1 <= limit <= 100:
        raise ValueError("Invalid evidence pagination.")
    return offset, limit


def required_check_data(
    job: agents.AgentJob, attempts: list[agents.AgentAttempt]
) -> list[dict[str, object]]:
    current = next((item for item in reversed(attempts) if item.active), None)
    if current is None:
        current = attempts[-1] if attempts else None
    if current is None:
        return []
    descriptor = p.AttemptDescriptor.model_validate(current.descriptor)
    if descriptor.repository is None:
        return []
    result = []
    for check in descriptor.repository.required_checks:
        verification = None
        if current.tree_hash:
            verification = (
                agents.AgentVerification.objects.filter(
                    attempt=current, check_id=check.id, tree_hash=current.tree_hash
                )
                .order_by("-finished_at")
                .first()
            )
        if verification is None:
            outcome = "missing" if not current.tree_hash else "stale"
            result.append({"check_id": check.id, "outcome": outcome})
            continue
        outcome = (
            "passing" if verification.exit_code == 0 and not verification.timed_out else "failed"
        )
        result.append(
            {
                "check_id": check.id,
                "attempt_id": str(current.id),
                "tree_hash": current.tree_hash,
                "command": verification.command,
                "cwd": verification.cwd,
                "outcome": outcome,
                "started_at": verification.started_at.isoformat(),
                "finished_at": verification.finished_at.isoformat(),
                "output_artifact_id": str(verification.output_artifact_id),
            }
        )
    return result


def operation_data(actor: UserProfile, operation: agents.AgentOperation) -> dict[str, object]:
    result = agent_approvals.proposal_data(operation)
    approval = (
        agents.AgentApproval.objects.filter(operation=operation).order_by("-created_at").first()
    )
    result["attempt_id"] = str(operation.attempt_id)
    result["action"] = operation.tool_class
    if operation.tool_class == "team.manage":
        from zerver.actions.agent_team_tools import describe_team_tool_input
        from zerver.lib.agent_protocol import TeamArguments

        result["summary"] = (
            operation.server_receipt["summary"]
            if operation.server_receipt
            else describe_team_tool_input(
                actor, TeamArguments.model_validate(operation.arguments).input
            )
        )
    if approval is None:
        return result
    attempt = operation.attempt
    job = approval.job
    can_decide = (
        approval.decision == "pending"
        and approval.expires_at > now()
        and operation.status == "proposed"
        and attempt.active
        and attempt.process_state in {"starting", "active"}
        and job.status in agent_jobs.EXECUTING
        and attempt.lease_expires_at is not None
        and attempt.lease_expires_at > now()
        and (now() - attempt.created_at).total_seconds()
        <= attempt.descriptor["budget"]["active_seconds"]
        and attempt.audience_binding == job.conversation.audience_binding
        and attempt.tree_hash == approval.tree_hash
        and job.profile.policy_version == approval.policy_version
        and operation.scope_binding.get("attempt_id") == str(attempt.id)
        and operation.scope_binding.get("lease_epoch") == attempt.lease_epoch
        and operation.scope_binding.get("policy_version") == approval.policy_version
        and operation.scope_binding.get("tree_hash") == (attempt.tree_hash or None)
        and operation.argument_digest == approval.operation_hash
    )
    if can_decide and operation.tool_class == "team.manage" and actor.id != job.requester_id:
        can_decide = False
    if can_decide:
        try:
            require_audience(job)
            agent_jobs.check_attempt_access(actor, job, attempt, operation.tool_class)
        except (JsonableError, ObjectDoesNotExist, ValueError):
            can_decide = False
    result["approval_decision"] = approval.decision
    result["can_decide"] = can_decide
    if not can_decide:
        result["nonce"] = None
    return result


@safe_agent_endpoint
def create_job(request: HttpRequest, user_profile: UserProfile) -> HttpResponse:
    data = payload(request, r.CreateJob)
    job = agent_jobs.create_job(
        user_profile,
        profile=agents.AgentProfile.objects.get(id=data.profile_id, realm=user_profile.realm),
        source=Message.objects.get(id=data.source_message_id, realm=user_profile.realm),
        repository=(
            agents.AgentRepository.objects.get(id=data.repository_id, realm=user_profile.realm)
            if data.repository_id
            else None
        ),
        request=data.request,
        idempotency_key=data.idempotency_key,
        job_kind=data.job_kind,
        delivery_target=data.delivery_target,
        base_ref=data.base_ref,
        context_message_ids=data.context_message_ids,
        context_attachment_ids=data.context_attachment_ids,
    )
    return _success(request, {"job": job_data(user_profile, job)})


@safe_agent_endpoint
def message_preflight(request: HttpRequest, user_profile: UserProfile) -> HttpResponse:
    data = payload(request, r.MessagePreflight)
    source = (
        Message.objects.get(id=data.source_message_id, realm=user_profile.realm)
        if data.source_message_id is not None
        else None
    )
    if source is not None:
        access_message(user_profile, source.id, is_modifying_message=False)
    from zerver.actions.agent_dispatch import preflight_profile

    decisions = []
    with agent_transaction():
        for profile_id in dict.fromkeys(data.profile_ids):
            try:
                profile = agents.AgentProfile.objects.get(id=profile_id, realm=user_profile.realm)
                decision = preflight_profile(user_profile, profile, source, data.destination)
            except (JsonableError, ValueError, agents.AgentProfile.DoesNotExist):
                decision = "rejected"
            decisions.append({"profile_id": str(profile_id), "decision": decision})
    return _success(request, {"decisions": decisions})


@safe_agent_endpoint
def message_dispatch(
    request: HttpRequest, user_profile: UserProfile, message_id: int
) -> HttpResponse:
    with agent_transaction():
        access_message(user_profile, message_id, is_modifying_message=False)
        receipts = agents.AgentDispatchReceipt.objects.filter(
            realm=user_profile.realm, source_message_id=message_id, requester=user_profile
        ).select_related("job")
        visible = []
        for item in receipts:
            if item.job is not None:
                try:
                    require_job_access(user_profile, item.job)
                except JsonableError:
                    continue
            visible.append(
                {
                    "profile_id": str(item.profile_id),
                    "decision": item.decision,
                    "reason": item.reason,
                    "job_id": str(item.job_id) if item.job_id else None,
                    "job_status": item.job.status if item.job is not None else None,
                }
            )
        return _success(request, {"source_message_id": message_id, "dispatch_receipts": visible})


@safe_agent_endpoint
def send_intent(request: HttpRequest, user_profile: UserProfile, client_key: UUID) -> HttpResponse:
    intent = agents.AgentSendIntent.objects.get(
        realm=user_profile.realm, sender=user_profile, client_key=client_key
    )
    if intent.sent_message_id is None:
        raise ValueError("Send intent is pending.")
    if intent.source_message_id is not None:
        access_message(user_profile, intent.source_message_id, is_modifying_message=False)
    return _success(
        request,
        {"source_message_id": intent.sent_message_id, "deleted": intent.source_message_id is None},
    )


@safe_agent_endpoint
def list_jobs(request: HttpRequest, user_profile: UserProfile) -> HttpResponse:
    offset, limit = window(request)
    with agent_transaction():
        visible = []
        for job in agents.AgentJob.objects.filter(realm=user_profile.realm).order_by(
            "-created_at", "id"
        ):
            ensure_budget()
            try:
                require_job_access(user_profile, job)
            except JsonableError:
                continue
            visible.append(job)
        return _success(
            request,
            {
                "count": len(visible),
                "jobs": [job_data(user_profile, job) for job in visible[offset : offset + limit]],
            },
        )


@safe_agent_endpoint
def get_job(request: HttpRequest, user_profile: UserProfile, job_id: UUID) -> HttpResponse:
    with agent_transaction():
        job = agents.AgentJob.objects.get(id=job_id, realm=user_profile.realm)
        require_job_access(user_profile, job)
        attempt_models = list(agents.AgentAttempt.objects.filter(job=job).order_by("number"))
        attempts = [
            {
                "id": str(item.id),
                "number": item.number,
                "lease_epoch": item.lease_epoch,
                "process_state": item.process_state,
                "active": item.active,
                "base_commit": item.base_commit,
                "tree_hash": item.tree_hash,
                "input_cursor": item.input_cursor,
                "event_cursor": item.event_cursor,
            }
            for item in attempt_models
        ]
        operation_offset, operation_limit = evidence_window(request, "operation")
        artifact_offset, artifact_limit = evidence_window(request, "artifact")
        operation_rows = agents.AgentOperation.objects.filter(attempt__job=job).order_by(
            "created_at"
        )
        artifact_rows = agents.AgentArtifact.objects.filter(
            attempt__job=job, unavailable_at__isnull=True
        ).order_by("created_at")
        operation_count = operation_rows.count()
        artifact_count = artifact_rows.count()
        operations = [
            operation_data(user_profile, item)
            for item in operation_rows[operation_offset : operation_offset + operation_limit]
        ]
        artifacts = [
            {
                "id": str(item.id),
                "attempt_id": str(item.attempt_id),
                "kind": item.kind,
                "filename": item.filename,
                "size": item.size,
                "checksum": item.checksum,
                "media_type": item.media_type,
            }
            for item in artifact_rows[artifact_offset : artifact_offset + artifact_limit]
        ]
        return _success(
            request,
            {
                "job": job_data(user_profile, job),
                "attempts": attempts,
                "operations": operations,
                "artifacts": artifacts,
                "required_checks": required_check_data(job, attempt_models),
                "operations_cursor": {
                    "offset": operation_offset,
                    "next_offset": operation_offset + len(operations),
                    "truncated": operation_offset + len(operations) < operation_count,
                },
                "artifacts_cursor": {
                    "offset": artifact_offset,
                    "next_offset": artifact_offset + len(artifacts),
                    "truncated": artifact_offset + len(artifacts) < artifact_count,
                },
            },
        )


@safe_agent_endpoint
def get_events(request: HttpRequest, user_profile: UserProfile, job_id: UUID) -> HttpResponse:
    after = int(request.GET.get("after", "0"))
    if after < 0:
        raise ValueError
    with agent_transaction():
        job = agents.AgentJob.objects.get(id=job_id, realm=user_profile.realm)
        require_job_access(user_profile, job)
        events = [
            {
                "id": str(item.event_id),
                "sequence": item.sequence,
                "attempt_id": str(item.attempt_id) if item.attempt_id else None,
                "type": item.type,
                "payload": item.payload,
                "occurred_at": item.occurred_at.isoformat(),
            }
            for item in agents.AgentAuditEvent.objects.filter(job=job, sequence__gt=after).order_by(
                "sequence"
            )[:100]
        ]
        return _success(request, {"events": events, "job_version": job.version})


@safe_agent_endpoint
def inputs(request: HttpRequest, user_profile: UserProfile, job_id: UUID) -> HttpResponse:
    if request.method == "POST":
        data = payload(request, r.Input)
        item = agent_jobs.add_input(
            user_profile,
            job_id,
            expected_version=data.expected_version,
            client_key=data.client_key,
            text=data.text,
            input_type=data.input_type,
            source_message_id=data.source_message_id,
        )
        return _success(request, {"input": agent_jobs.input_data(item)})
    offset, limit = window(request)
    with agent_transaction():
        job = agents.AgentJob.objects.get(id=job_id, realm=user_profile.realm)
        require_job_access(user_profile, job)
        items = agents.AgentInput.objects.filter(job=job).order_by("sequence")
        return _success(
            request,
            {
                "count": items.count(),
                "inputs": [agent_jobs.input_data(item) for item in items[offset : offset + limit]],
            },
        )


@safe_agent_endpoint
def cancel(request: HttpRequest, user_profile: UserProfile, job_id: UUID) -> HttpResponse:
    data = payload(request, r.JobControl)
    job = agent_jobs.cancel_job(user_profile, job_id, data.expected_version)
    return _success(request, {"job": job_data(user_profile, job)})


@safe_agent_endpoint
def resume(request: HttpRequest, user_profile: UserProfile, job_id: UUID) -> HttpResponse:
    data = payload(request, r.Resume)
    job = agent_jobs.resume_job(user_profile, job_id, data.expected_version, data.checkpoint_id)
    return _success(request, {"job": job_data(user_profile, job)})


@safe_agent_endpoint
def decide(request: HttpRequest, user_profile: UserProfile, approval_id: UUID) -> HttpResponse:
    data = payload(request, r.ApprovalDecision)
    approval = agent_approvals.decide_approval(
        user_profile,
        approval_id,
        expected_version=data.expected_version,
        operation_hash=data.operation_hash,
        nonce=data.nonce,
        decision=data.decision,
    )
    return _success(
        request,
        {
            "approval_id": str(approval.id),
            "decision": approval.decision,
            "version": approval.version,
        },
    )


@safe_agent_endpoint
def artifact(request: HttpRequest, user_profile: UserProfile, artifact_id: UUID) -> HttpResponse:
    item, data = download_artifact(user_profile, artifact_id)
    response = HttpResponse(data, content_type=item.media_type)
    from django.utils.http import content_disposition_header

    response["Content-Disposition"] = content_disposition_header(True, item.filename)
    response["X-Content-Type-Options"] = "nosniff"
    response["Cache-Control"] = "private, no-store"
    return response


@safe_agent_endpoint
def complete_draft(request: HttpRequest, user_profile: UserProfile, job_id: UUID) -> HttpResponse:
    data = payload(request, r.CompleteDraft)
    job = agent_jobs.complete_draft(
        user_profile,
        job_id,
        expected_version=data.expected_version,
        repository_id=data.repository_id,
        base_ref=data.base_ref,
    )
    return _success(request, {"job": job_data(user_profile, job)})
