"""Single-use operation authorization and uncertain-outcome reconciliation."""

from datetime import timedelta
from urllib.parse import urlsplit
from uuid import UUID

from django.utils.timezone import now
from pydantic import TypeAdapter

from zerver.actions.agent_jobs import (
    audit,
    check_attempt_access,
    digest,
    locked_attempt,
    request_stop,
    transition,
)
from zerver.lib import agent_protocol as p
from zerver.lib.agent_context import agent_transaction, require_audience, require_job_access
from zerver.lib.agent_policy import AgentAccessDenied
from zerver.models import UserProfile, agents

APPROVAL_ACTIONS = {"git.push", "git.draft_pr", "dependencies.install", "shell.run", "team.manage"}

# Proposals for these arguments carry no repository or tree hash to validate.
NO_WORKSPACE_ARGUMENTS = (p.ContextReadArguments, p.TeamArguments)


class OutcomeUnknownError(ValueError):
    """A team.manage operation was consumed but has no execution receipt yet.

    This means a crash happened between phases 1 and 2 of contract 2.6 item
    3. The runner must not retry; the commander checks the channel instead.
    """


def proposal_data(operation: agents.AgentOperation) -> dict[str, object]:
    approval = (
        agents.AgentApproval.objects.filter(operation=operation).order_by("-created_at").first()
    )
    return {
        "operation_id": str(operation.operation_id),
        "operation_hash": operation.argument_digest,
        "version": operation.version,
        "status": operation.status,
        "arguments": operation.arguments,
        "scope": operation.scope_binding,
        "approval_id": str(approval.id) if approval else None,
        "approval_version": approval.version if approval else None,
        "nonce": str(approval.nonce) if approval else None,
        "expires_at": approval.expires_at.isoformat() if approval else None,
        "server_receipt": operation.server_receipt,
    }


def propose_operation(
    runner: agents.AgentRunner,
    job_id: UUID,
    attempt_id: UUID,
    epoch: int,
    *,
    operation_id: UUID,
    arguments: dict[str, object],
    tree_hash: str | None,
    diff_artifact_id: UUID | None,
) -> agents.AgentOperation:
    parsed: p.OperationArguments = TypeAdapter(p.OperationArguments).validate_python(arguments)
    arguments = p.serialize_payload(parsed)
    with agent_transaction():
        job, attempt = locked_attempt(runner, job_id, attempt_id, epoch)
        action = parsed.action
        descriptor = p.AttemptDescriptor.model_validate(attempt.descriptor)
        if isinstance(parsed, p.TeamArguments):
            if job.job_kind != "manage" or tree_hash is not None:
                raise ValueError("Operation exceeds the tested authority.")
            check_attempt_access(job.requester, job, attempt, "team.manage")
        elif action not in descriptor.tested_configuration.actions or (
            job.job_kind == "answer" and action not in {"context.read", "repository.read"}
        ):
            raise ValueError("Operation exceeds the tested authority.")
        if not isinstance(parsed, NO_WORKSPACE_ARGUMENTS) and (
            parsed.repository_id != job.repository_id
            or attempt.workspace_prepared_at is None
            or tree_hash != attempt.tree_hash
        ):
            raise ValueError("Operation workspace is unavailable.")
        if isinstance(parsed, p.ContextReadArguments) and agents.AgentContextRef.objects.filter(
            job=job, id__in=parsed.context_ids
        ).count() != len(set(parsed.context_ids)):
            raise ValueError("Context references are unavailable.")
        if isinstance(parsed, p.ToolArguments) and (
            parsed.network != descriptor.policy.network
            or parsed.cwd.startswith("/")
            or ".." in parsed.cwd.split("/")
        ):
            raise ValueError("Tool scope is invalid.")
        if isinstance(parsed, p.ChecksArguments):
            assert descriptor.repository is not None
            if (
                not set(parsed.check_ids)
                <= {check.id for check in descriptor.repository.required_checks}
                or parsed.tree_hash != tree_hash
            ):
                raise ValueError("Check scope is invalid.")
        if isinstance(parsed, p.RepositoryReadArguments) and any(
            path.startswith("/") or ".." in path.split("/") for path in parsed.paths
        ):
            raise ValueError("Repository path is invalid.")
        if isinstance(parsed, (p.PushArguments, p.DraftPRArguments)):
            if (
                job.repository is None
                or not job.repository.canonical_origin
                or parsed.remote != job.repository.canonical_origin
            ):
                raise ValueError("Remote differs from the approved repository.")
            if (
                isinstance(parsed, p.DraftPRArguments)
                and parsed.base not in job.repository.allowed_refs
            ):
                raise ValueError("Draft PR base is outside the approved refs.")
            branch = parsed.branch if isinstance(parsed, p.PushArguments) else parsed.head
            if not branch.startswith(f"grow-agent/{job.id}/") or any(
                char in branch for char in " ~^:?*[\\"
            ):
                raise ValueError("Remote branch is outside this job.")
        diff_checksum = None
        if diff_artifact_id is not None:
            artifact = agents.AgentArtifact.objects.get(
                id=diff_artifact_id,
                attempt=attempt,
                realm=job.realm,
                kind="diff",
                unavailable_at__isnull=True,
            )
            diff_checksum = artifact.checksum
        if action in {"git.push", "git.draft_pr"} and diff_checksum is None:
            raise ValueError("Remote proposal requires a reviewable diff.")
        binding = {
            "attempt_id": str(attempt.id),
            "lease_epoch": epoch,
            "policy_version": descriptor.policy.version,
            "tree_hash": tree_hash,
            "diff_artifact_id": str(diff_artifact_id) if diff_artifact_id else None,
            "diff_checksum": diff_checksum,
            "audience": attempt.audience_binding,
        }
        operation_hash = digest({"arguments": arguments, "scope": binding})
        prior = agents.AgentOperation.objects.filter(operation_id=operation_id).first()
        if prior is not None:
            if prior.attempt_id != attempt.id or prior.argument_digest != operation_hash:
                raise ValueError("Operation idempotency conflict.")
            return prior
        if isinstance(parsed, p.TeamArguments):
            from zerver.actions.agent_team_tools import team_tool_needs_confirmation

            requires_approval = team_tool_needs_confirmation(job, parsed.input)
        else:
            requires_approval = (
                action in {"git.push", "git.draft_pr"} or action not in descriptor.policy.actions
            )
        if requires_approval and action not in APPROVAL_ACTIONS:
            raise ValueError("Operation requires a new scoped task.")
        if not requires_approval:
            check_attempt_access(job.requester, job, attempt, action)
        operation = agents.AgentOperation.objects.create(
            realm=job.realm,
            attempt=attempt,
            operation_id=operation_id,
            tool_class=action,
            argument_digest=operation_hash,
            arguments=arguments,
            scope_binding=binding,
            status="proposed" if requires_approval else "authorized",
        )
        if requires_approval:
            approval = agents.AgentApproval.objects.create(
                realm=job.realm,
                job=job,
                attempt=attempt,
                operation=operation,
                operation_hash=operation_hash,
                policy_version=descriptor.policy.version,
                tree_hash=tree_hash or "",
                arguments=arguments,
                expires_at=now() + timedelta(minutes=15),
            )
            transition(job, "waiting_for_approval")
            audit(
                job,
                "approval.requested",
                {
                    "approval_id": str(approval.id),
                    "operation_hash": operation_hash,
                    "decision": "pending",
                    "version": 1,
                },
                attempt=attempt,
            )
        return operation


def decide_approval(
    actor: UserProfile,
    approval_id: UUID,
    *,
    expected_version: int,
    operation_hash: str,
    nonce: UUID,
    decision: str,
) -> agents.AgentApproval:
    if decision not in {"approved", "rejected"}:
        raise ValueError("Invalid approval decision.")
    with agent_transaction():
        lookup = agents.AgentApproval.objects.get(id=approval_id, realm=actor.realm)
        job, attempt = locked_attempt(
            lookup.attempt.runner, lookup.job_id, lookup.attempt_id, lookup.attempt.lease_epoch
        )
        operation = agents.AgentOperation.objects.select_for_update().get(id=lookup.operation_id)
        approval = agents.AgentApproval.objects.select_for_update().get(id=approval_id)
        require_job_access(actor, job)
        if operation.tool_class == "team.manage" and actor.id != job.requester_id:
            raise AgentAccessDenied("Agent access denied.")
        check_attempt_access(actor, job, attempt, operation.tool_class)
        if (
            approval.operation_hash != operation_hash
            or approval.nonce != nonce
            or approval.version != expected_version
            or approval.decision != "pending"
            or approval.expires_at <= now()
            or attempt.tree_hash != approval.tree_hash
            or job.profile.policy_version != approval.policy_version
        ):
            raise ValueError("Approval proposal changed or expired.")
        approval.approver = actor
        approval.decision = decision
        approval.decided_at = now()
        approval.version += 1
        approval.save(update_fields=["approver", "decision", "decided_at", "version"])
        if decision == "rejected":
            request_stop(job, attempt, target="blocked", reason="approval_rejected")
        else:
            transition(job, "running")
        audit(
            job,
            "approval.resolved",
            {
                "approval_id": str(approval.id),
                "operation_hash": operation.argument_digest,
                "decision": decision,
                "version": approval.version,
            },
            attempt=attempt,
            actor=actor,
        )
        return approval


def consume_operation(
    runner: agents.AgentRunner,
    job_id: UUID,
    attempt_id: UUID,
    epoch: int,
    *,
    operation_id: UUID,
    expected_version: int,
    operation_hash: str,
    nonce: UUID | None = None,
) -> dict[str, object]:
    with agent_transaction():
        job, attempt = locked_attempt(runner, job_id, attempt_id, epoch)
        operation = agents.AgentOperation.objects.select_for_update().get(
            operation_id=operation_id, attempt=attempt, realm=job.realm
        )
        descriptor = p.AttemptDescriptor.model_validate(attempt.descriptor)
        if (
            operation.version != expected_version
            or operation.argument_digest != operation_hash
            or operation.status not in {"proposed", "authorized"}
        ):
            raise ValueError("Operation was consumed or changed. Reconcile before retry.")
        if (
            operation.scope_binding["tree_hash"] != (attempt.tree_hash or None)
            and operation.tool_class != "context.read"
        ):
            raise ValueError("Operation tree changed.")
        if operation.scope_binding["policy_version"] != job.profile.policy_version:
            raise ValueError("Operation policy changed.")
        if attempt.tool_rounds >= descriptor.budget.tool_rounds:
            raise ValueError("Tool budget exhausted.")
        approval = (
            agents.AgentApproval.objects.select_for_update()
            .filter(operation=operation, decision="approved")
            .first()
        )
        if operation.status == "proposed":
            if (
                approval is None
                or approval.expires_at <= now()
                or approval.nonce != nonce
                or approval.approver is None
            ):
                raise ValueError("Approval is unavailable.")
            check_attempt_access(approval.approver, job, attempt, operation.tool_class)
            if approval.operation_hash != operation_hash or approval.tree_hash != attempt.tree_hash:
                raise ValueError("Approval no longer matches.")
            approval.decision = "consumed"
            approval.consumed_at = now()
            approval.version += 1
            approval.save(update_fields=["decision", "consumed_at", "version"])
        else:
            check_attempt_access(job.requester, job, attempt, operation.tool_class)
        job.phase = {
            "repository.edit": "edit",
            "shell.run": "edit",
            "dependencies.install": "edit",
            "checks.run": "verify",
            "git.commit": "review",
            "git.push": "deliver",
            "git.draft_pr": "deliver",
        }.get(operation.tool_class, "inspect")
        job.save(update_fields=["phase"])
        operation.status = "started"
        operation.started_at = now()
        operation.version += 1
        operation.save(update_fields=["status", "started_at", "version"])
        attempt.tool_rounds += 1
        attempt.save(update_fields=["tool_rounds"])
        return proposal_data(operation)


def reconcile_operation(
    runner: agents.AgentRunner,
    job_id: UUID,
    attempt_id: UUID,
    epoch: int,
    *,
    operation_id: UUID,
    receipt: p.RemoteReceipt,
) -> agents.AgentOperation:
    with agent_transaction():
        job, attempt = locked_attempt(runner, job_id, attempt_id, epoch, execution=False)
        if runner.revoked_at is not None:
            raise ValueError("Runner is revoked.")
        require_audience(job)
        check_attempt_access(job.requester, job, attempt, "profile.use")
        operation = agents.AgentOperation.objects.select_for_update().get(
            operation_id=operation_id, attempt=attempt, realm=job.realm
        )
        if operation.tool_class not in {"git.push", "git.draft_pr"}:
            raise ValueError("Operation has no remote receipt.")
        arguments = operation.arguments
        branch = arguments.get("branch", arguments.get("head"))
        if (
            receipt.operation_id != operation_id
            or receipt.remote != arguments["remote"]
            or receipt.commit != arguments["commit"]
            or receipt.branch != branch
        ):
            raise ValueError("Remote receipt does not match the operation.")
        if operation.tool_class == "git.draft_pr":
            url = urlsplit(receipt.pull_request_url or "")
            remote_host = urlsplit(receipt.remote).hostname
            if remote_host is None and "@" in receipt.remote:
                remote_host = receipt.remote.split("@", 1)[1].split(":", 1)[0]
            if (
                not receipt.pull_request_id
                or url.scheme != "https"
                or url.username
                or not url.hostname
                or url.hostname != remote_host
            ):
                raise ValueError("Draft PR identity is invalid.")
        data = p.serialize_payload(receipt)
        if operation.status == "succeeded":
            if operation.remote_receipt != data:
                raise ValueError("Remote receipt conflict.")
            return operation
        if (
            operation.status not in {"started", "outcome_unknown"}
            or not agents.AgentApproval.objects.filter(
                operation=operation, decision="consumed"
            ).exists()
        ):
            raise ValueError("Remote operation was not authorized.")
        operation.remote_receipt = data
        operation.status = "succeeded"
        operation.finished_at = now()
        operation.version += 1
        operation.save(update_fields=["remote_receipt", "status", "finished_at", "version"])
        return operation


def reconcile_local_operation(
    runner: agents.AgentRunner,
    job_id: UUID,
    attempt_id: UUID,
    epoch: int,
    receipt: p.LocalOperationReceipt,
) -> agents.AgentOperation:
    with agent_transaction():
        job, attempt = locked_attempt(runner, job_id, attempt_id, epoch, execution=False)
        require_audience(job)
        check_attempt_access(job.requester, job, attempt, "profile.use")
        if (
            attempt.active
            or attempt.process_state != "stopped"
            or agents.AgentAttempt.objects.filter(job=job, number__gt=attempt.number).exists()
        ):
            raise ValueError("Local reconciliation requires the stopped latest attempt.")
        operation = agents.AgentOperation.objects.select_for_update().get(
            attempt=attempt,
            operation_id=receipt.operation_id,
            argument_digest=receipt.argument_digest,
        )
        data = p.serialize_payload(receipt)
        if operation.local_receipt is not None:
            if operation.local_receipt != data:
                raise ValueError("Local reconciliation receipt changed.")
            return operation
        if (
            operation.tool_class in {"git.push", "git.draft_pr"}
            or operation.status != "outcome_unknown"
        ):
            raise ValueError("Operation does not permit local reconciliation.")
        if (
            attempt.stopped_at is None
            or receipt.base_commit != (attempt.base_commit or None)
            or receipt.observed_at < attempt.stopped_at
            or receipt.observed_at > now() + timedelta(seconds=5)
        ):
            raise ValueError("Local observation does not match the stopped workspace.")
        if job.repository_id is not None and receipt.tree_hash is None:
            raise ValueError("Workspace tree observation is required.")
        if receipt.outcome == "no_effect" and receipt.tree_hash != operation.scope_binding.get(
            "tree_hash"
        ):
            raise ValueError("Workspace changed despite the no-effect receipt.")
        operation.local_receipt = data
        operation.status = "cancelled" if receipt.outcome == "no_effect" else receipt.outcome
        operation.finished_at = now()
        operation.version += 1
        operation.save(update_fields=["local_receipt", "status", "finished_at", "version"])
        attempt.tree_hash = receipt.tree_hash or ""
        attempt.save(update_fields=["tree_hash"])
        return operation


def execute_operation(
    runner: agents.AgentRunner,
    job_id: UUID,
    attempt_id: UUID,
    epoch: int,
    *,
    job_version: int,
    operation_id: UUID,
    expected_version: int,
    operation_hash: str,
    nonce: UUID | None = None,
) -> dict[str, object]:
    """Contract 2.6 item 3: three phases for a team.manage operation.

    Phase 1 (consume) and phase 3 (receipt and event) run in agent_transaction.
    Phase 2, the actual Zulip action, runs as the commander outside it, so the
    lock that agent_transaction holds on chat tables stays short.
    """
    from zerver.actions.agent_team_tools import execute_team_tool

    with agent_transaction():
        job, attempt = locked_attempt(
            runner, job_id, attempt_id, epoch, expected_version=job_version
        )
        operation = agents.AgentOperation.objects.select_for_update().get(
            operation_id=operation_id, attempt=attempt, realm=job.realm
        )
        if operation.tool_class != "team.manage":
            raise ValueError("Operation does not support execution.")
        if operation.server_receipt is not None:
            return proposal_data(operation)
        if operation.status == "started":
            raise OutcomeUnknownError
        commander = job.requester
        profile = agents.AgentProfile.objects.get(id=job.profile_id, realm=job.realm)
        tool_input = p.TeamArguments.model_validate(operation.arguments).input
        consume_operation(
            runner,
            job_id,
            attempt_id,
            epoch,
            operation_id=operation_id,
            expected_version=expected_version,
            operation_hash=operation_hash,
            nonce=nonce,
        )

    receipt = execute_team_tool(commander, profile, tool_input)

    with agent_transaction():
        job, attempt = locked_attempt(runner, job_id, attempt_id, epoch, execution=False)
        operation = agents.AgentOperation.objects.select_for_update().get(
            operation_id=operation_id, attempt=attempt, realm=job.realm
        )
        if operation.server_receipt is not None:
            return proposal_data(operation)
        operation.server_receipt = receipt
        operation.status = receipt["outcome"]
        operation.finished_at = now()
        operation.version += 1
        operation.clean()
        operation.save(update_fields=["server_receipt", "status", "finished_at", "version"])
        audit(
            job,
            "team.executed",
            {**receipt, "operation_id": str(operation.operation_id)},
            attempt=attempt,
        )
        return proposal_data(operation)
