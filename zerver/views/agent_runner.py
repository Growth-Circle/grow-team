"""Runner bearer boundaries. Device identity cannot impersonate a chat user."""

from collections.abc import Callable
from functools import wraps

from django.core.exceptions import ObjectDoesNotExist
from django.http import HttpRequest, HttpResponse
from django.utils.timezone import now
from django.views.decorators.csrf import csrf_exempt

from zerver.actions import agent_approvals as approvals
from zerver.actions import agent_jobs as jobs
from zerver.actions.agents import (
    RunnerCredentialError,
    _validate_setup,
    authenticate_runner_token,
    update_runner_metadata,
)
from zerver.lib import agent_job_requests as r
from zerver.lib import agent_protocol as p
from zerver.lib.agent_context import agent_transaction, selected_context
from zerver.lib.agent_requests import RunnerMetadataUpdate
from zerver.lib.agent_results import store_artifact
from zerver.lib.agent_secrets import decrypt_agent_secret
from zerver.lib.exceptions import JsonableError
from zerver.lib.response import json_response
from zerver.models import agents
from zerver.views.agent_devices import _device_token
from zerver.views.agents import _success, safe_agent_endpoint


def endpoint(
    *methods: str,
) -> Callable[[Callable[[HttpRequest], HttpResponse]], Callable[[HttpRequest], HttpResponse]]:
    def decorate(
        view: Callable[[HttpRequest], HttpResponse],
    ) -> Callable[[HttpRequest], HttpResponse]:
        @csrf_exempt
        @safe_agent_endpoint
        @wraps(view)
        def wrapped(request: HttpRequest) -> HttpResponse:
            if request.method not in methods:
                return json_response(
                    "error", "Method is not supported.", {"schema_version": 1}, status=405
                )
            if _device_token(request) is None:
                return json_response(
                    "error", "Runner authentication is required.", {"schema_version": 1}, status=401
                )
            return view(request)

        return wrapped

    return decorate


def runner(request: HttpRequest) -> agents.AgentRunner:
    token = _device_token(request)
    if token is None:
        raise ValueError("Runner credential is unavailable.")
    return authenticate_runner_token(token).runner


@endpoint("GET", "POST")
def metadata(request: HttpRequest) -> HttpResponse:
    token = _device_token(request)
    assert token is not None
    credential = authenticate_runner_token(token)
    device = credential.runner
    if not device.owner.is_active:
        raise RunnerCredentialError("credential_invalid")
    if request.method == "POST":
        if len(request.body) > 65536:
            raise ValueError("Device payload exceeds its limit.")
        data = RunnerMetadataUpdate.model_validate_json(request.body)
        device = update_runner_metadata(
            device.owner,
            device,
            expected_metadata_revision=data.expected_metadata_revision,
            name=data.name,
            host_kind=data.host_kind,
        )
    return _success(
        request,
        {
            "metadata": {
                "name": device.name,
                "host_kind": device.host_kind,
                "metadata_revision": device.metadata_revision,
            }
        },
    )


@endpoint("POST")
def claims(request: HttpRequest) -> HttpResponse:
    data = r.Claim.model_validate_json(request.body)
    with agent_transaction():
        attempt = jobs.claim_work(runner(request), claim_key=data.claim_key)
        version = agents.AgentJob.objects.get(id=attempt["job_id"]).version if attempt else None
        return _success(request, {"attempt": attempt, "job_version": version})


@endpoint("GET")
def leases(request: HttpRequest) -> HttpResponse:
    with agent_transaction():
        device = runner(request)
        values = [
            {
                "descriptor": item.descriptor,
                "job_version": item.job.version,
                "lease_expires_at": item.lease_expires_at.isoformat(),
                "process_state": item.process_state,
                "event_cursor": item.event_cursor,
            }
            for item in agents.AgentAttempt.objects.filter(
                runner=device, realm=device.realm, active=True
            ).order_by("number")[:100]
        ]
        return _success(request, {"leases": values})


@endpoint("GET")
def controls(request: HttpRequest) -> HttpResponse:
    with agent_transaction():
        device = runner(request)
        for item in agents.AgentAttempt.objects.filter(
            runner=device, realm=device.realm, active=True
        )[:100]:
            if item.process_state in {"starting", "active"}:
                from zerver.lib.agent_context import AgentBusy

                try:
                    job, current = jobs.locked_attempt(
                        device, item.job_id, item.id, item.lease_epoch
                    )
                    jobs.fence_prepared_result(job, current)
                except AgentBusy:
                    raise
                except (ValueError, JsonableError, ObjectDoesNotExist):
                    jobs.request_stop(item.job, item, target="blocked", reason="authority_changed")
        values = [
            {
                "job_id": str(item.job_id),
                "attempt_id": str(item.id),
                "lease_epoch": item.lease_epoch,
                "job_version": item.job.version,
                "control": (
                    "stop"
                    if item.process_state in {"stopping", "unknown"}
                    or item.lease_expires_at <= now()
                    else "continue"
                ),
                "approvals": [
                    {
                        "id": str(approval.id),
                        "operation_id": str(approval.operation.operation_id),
                        "decision": approval.decision,
                        "version": approval.version,
                        "nonce": str(approval.nonce),
                    }
                    for approval in agents.AgentApproval.objects.filter(attempt=item)
                ],
            }
            for item in agents.AgentAttempt.objects.filter(
                runner=device, realm=device.realm, active=True
            )[:100]
        ]
        return _success(request, {"controls": values})


@endpoint("POST")
def heartbeat(request: HttpRequest) -> HttpResponse:
    data = r.Heartbeat.model_validate_json(request.body)
    with agent_transaction():
        return _success(request, {"leases": jobs.heartbeat(runner(request), data.leases)})


@endpoint("POST")
def events(request: HttpRequest) -> HttpResponse:
    data = r.EventBatch.model_validate_json(request.body)
    first = data.events[0]
    if any(
        (item.job_id, item.attempt_id, item.lease_epoch)
        != (first.job_id, first.attempt_id, first.lease_epoch)
        for item in data.events
    ):
        raise ValueError("Event batch spans execution leases.")
    with agent_transaction():
        device = runner(request)
        receipts = []
        version = data.job_version
        for event in data.events:
            receipt = jobs.record_event(device, event, expected_version=version)
            version = int(str(receipt["job_version"]))
            receipts.append(receipt)
        return _success(request, {"receipts": receipts})


@endpoint("POST")
def stopped(request: HttpRequest) -> HttpResponse:
    data = r.StopEvidence.model_validate_json(request.body)
    token = _device_token(request)
    assert token is not None
    return _success(request, {"receipt": jobs.stop_evidence(token, data.event)})


@endpoint("POST")
def context(request: HttpRequest) -> HttpResponse:
    data = r.Context.model_validate_json(request.body)
    with agent_transaction():
        job, _ = jobs.locked_attempt(
            runner(request),
            data.job_id,
            data.attempt_id,
            data.lease_epoch,
            expected_version=data.job_version,
        )
        return _success(request, {"references": selected_context(job, data.reference_ids)})


@endpoint("POST")
def inputs(request: HttpRequest) -> HttpResponse:
    data = r.LeaseRequest.model_validate_json(request.body)
    with agent_transaction():
        device = runner(request)
        jobs.locked_attempt(
            device,
            data.job_id,
            data.attempt_id,
            data.lease_epoch,
            expected_version=data.job_version,
        )
        return _success(
            request,
            {"inputs": jobs.deliver_inputs(device, data.job_id, data.attempt_id, data.lease_epoch)},
        )


@endpoint("POST")
def artifacts(request: HttpRequest) -> HttpResponse:
    if set(request.POST) != {"payload"} or set(request.FILES) != {"file"}:
        raise ValueError("Artifact upload requires metadata and one file.")
    data = r.Artifact.model_validate_json(request.POST["payload"])
    device = runner(request)
    with agent_transaction():
        jobs.locked_attempt(
            runner(request),
            data.job_id,
            data.attempt_id,
            data.lease_epoch,
            expected_version=data.job_version,
        )
    upload = request.FILES["file"]
    from django.core.files.uploadedfile import UploadedFile

    if not isinstance(upload, UploadedFile):
        raise ValueError("Invalid upload.")
    item = store_artifact(
        device,
        data.job_id,
        data.attempt_id,
        data.lease_epoch,
        chunks=upload.chunks(),
        checksum=data.checksum,
        kind=data.kind,
        filename=data.filename,
        media_type=data.media_type,
        credential_token=_device_token(request),
    )
    return _success(
        request, {"artifact_id": str(item.id), "checksum": item.checksum, "size": item.size}
    )


@endpoint("POST")
def propose(request: HttpRequest) -> HttpResponse:
    data = r.Proposal.model_validate_json(request.body)
    with agent_transaction():
        device = runner(request)
        jobs.locked_attempt(
            device,
            data.job_id,
            data.attempt_id,
            data.lease_epoch,
            expected_version=data.job_version,
        )
        item = approvals.propose_operation(
            device,
            data.job_id,
            data.attempt_id,
            data.lease_epoch,
            operation_id=data.operation_id,
            arguments=p.serialize_payload(data.arguments),
            tree_hash=data.tree_hash,
            diff_artifact_id=data.diff_artifact_id,
        )
        return _success(request, {"operation": approvals.proposal_data(item)})


@endpoint("POST")
def consume(request: HttpRequest) -> HttpResponse:
    data = r.Consume.model_validate_json(request.body)
    with agent_transaction():
        device = runner(request)
        jobs.locked_attempt(
            device,
            data.job_id,
            data.attempt_id,
            data.lease_epoch,
            expected_version=data.job_version,
        )
        result = approvals.consume_operation(
            device,
            data.job_id,
            data.attempt_id,
            data.lease_epoch,
            operation_id=data.operation_id,
            expected_version=data.expected_version,
            operation_hash=data.operation_hash,
            nonce=data.nonce,
        )
        return _success(request, {"operation": result})


@endpoint("POST")
def execute(request: HttpRequest) -> HttpResponse:
    """Run a team.manage operation (contract 2.6 item 3).

    This does not wrap the whole call in one agent_transaction: phase 2 runs
    the Zulip action outside it, so the chat-table lock stays short.
    """
    data = r.Consume.model_validate_json(request.body)
    device = runner(request)
    result = approvals.execute_operation(
        device,
        data.job_id,
        data.attempt_id,
        data.lease_epoch,
        job_version=data.job_version,
        operation_id=data.operation_id,
        expected_version=data.expected_version,
        operation_hash=data.operation_hash,
        nonce=data.nonce,
    )
    return _success(request, {"operation": result})


@endpoint("POST")
def reconcile_operation(request: HttpRequest) -> HttpResponse:
    data = r.Reconcile.model_validate_json(request.body)
    with agent_transaction():
        device = runner(request)
        jobs.locked_attempt(
            device,
            data.job_id,
            data.attempt_id,
            data.lease_epoch,
            # Recovery observes immutable authority without a current job-version gate.
            execution=False,
        )
        item = approvals.reconcile_operation(
            device,
            data.job_id,
            data.attempt_id,
            data.lease_epoch,
            operation_id=data.operation_id,
            receipt=data.receipt,
        )
        return _success(request, {"operation": approvals.proposal_data(item)})


@endpoint("POST")
def checkpoint(request: HttpRequest) -> HttpResponse:
    data = r.Checkpoint.model_validate_json(request.body)
    with agent_transaction():
        device = runner(request)
        jobs.locked_attempt(
            device,
            data.job_id,
            data.attempt_id,
            data.lease_epoch,
            expected_version=data.job_version,
        )
        saved = jobs.save_checkpoint(
            device, data.job_id, data.attempt_id, data.lease_epoch, data.checkpoint
        )
        return _success(request, {"checkpoint_id": str(saved.id)})


def provider_secret(provider: agents.AgentProvider, version: int) -> str:
    secret = provider.secret
    if (
        provider.disabled_at is not None
        or secret is None
        or secret.revoked_at is not None
        or secret.version != version
    ):
        raise ValueError("Provider credential is unavailable.")
    return decrypt_agent_secret(
        bytes(secret.ciphertext),
        bytes(secret.wrapped_key),
        key_id=secret.key_id,
        realm_id=secret.realm_id,
        owner_id=secret.owner_id,
        version=secret.version,
    )


@endpoint("POST")
def authority(request: HttpRequest) -> HttpResponse:
    data = r.LeaseRequest.model_validate_json(request.body)
    with agent_transaction():
        job, attempt = jobs.locked_attempt(
            runner(request),
            data.job_id,
            data.attempt_id,
            data.lease_epoch,
            expected_version=data.job_version,
        )
        return _success(
            request,
            {
                "job_id": str(job.id),
                "attempt_id": str(attempt.id),
                "lease_epoch": attempt.lease_epoch,
                "job_version": job.version,
                "expires_at": attempt.lease_expires_at.isoformat(),
            },
        )


@endpoint("POST")
def credential(request: HttpRequest) -> HttpResponse:
    data = r.Credential.model_validate_json(request.body)
    with agent_transaction():
        job, attempt = jobs.locked_attempt(
            runner(request),
            data.job_id,
            data.attempt_id,
            data.lease_epoch,
            expected_version=data.job_version,
        )
        if job.repository_id is not None and attempt.workspace_prepared_at is None:
            raise ValueError("Workspace preparation is required.")
        descriptor = p.AttemptDescriptor.model_validate(attempt.descriptor)
        if descriptor.provider is None or descriptor.provider.id != data.provider_id:
            raise ValueError("Provider is outside this attempt.")
        provider = agents.AgentProvider.objects.get(
            id=data.provider_id, realm=job.realm, runner=attempt.runner
        )
        response = _success(
            request,
            {
                "secret": provider_secret(provider, data.secret_version),
                "expires_at": attempt.lease_expires_at.isoformat(),
            },
        )
        response["Cache-Control"] = "no-store"
        return response


@endpoint("POST")
def probe_authority(request: HttpRequest) -> HttpResponse:
    """Observe the current grant without a lease change or a new claim."""
    data = r.ProbeAuthority.model_validate_json(request.body)
    with agent_transaction():
        device = runner(request)
        if not agents.AgentRealmSettings.objects.filter(realm=device.realm, enabled=True).exists():
            raise ValueError("Agent connections are disabled.")
        setup = agents.AgentSetupOperation.objects.select_for_update().get(
            id=data.setup_id,
            runner=device,
            realm=device.realm,
            claim_key=data.claim_key,
            lease_epoch=data.lease_epoch,
            descriptor_digest=data.descriptor_digest,
            configuration_digest=data.configuration_digest,
            phase="probing",
            lease_expires_at__gt=now(),
        )
        _validate_setup(setup, device)
        grant = agents.AgentProbeGrant.objects.get(setup_operation=setup)
        response = _success(
            request,
            {
                "setup_id": str(setup.id),
                "claim_key": str(setup.claim_key),
                "lease_epoch": setup.lease_epoch,
                "descriptor_digest": setup.descriptor_digest,
                "configuration_digest": setup.configuration_digest,
                "grant_id": str(grant.id),
                "expires_at": min(setup.lease_expires_at, grant.expires_at).isoformat(),
            },
        )
        response["Cache-Control"] = "no-store"
        return response


@endpoint("POST")
def probe_credential(request: HttpRequest) -> HttpResponse:
    data = r.ProbeCredential.model_validate_json(request.body)
    with agent_transaction():
        device = runner(request)
        setup = agents.AgentSetupOperation.objects.select_for_update().get(
            id=data.setup_id,
            runner=device,
            realm=device.realm,
            claim_key=data.claim_key,
            lease_epoch=data.lease_epoch,
            descriptor_digest=data.descriptor_digest,
            configuration_digest=data.configuration_digest,
            provider_id=data.provider_id,
            phase="probing",
            lease_expires_at__gt=now(),
        )
        _validate_setup(setup, device)
        provider = setup.provider
        if provider is None:
            raise ValueError("Provider is unavailable.")
        response = _success(
            request,
            {
                "secret": provider_secret(provider, data.secret_version),
                "expires_at": (
                    setup.lease_expires_at.isoformat() if setup.lease_expires_at else None
                ),
            },
        )
        response["Cache-Control"] = "no-store"
        return response


@endpoint("POST")
def reconcile_input(request: HttpRequest) -> HttpResponse:
    data = r.InputReconciliation.model_validate_json(request.body)
    with agent_transaction():
        device = runner(request)
        jobs.locked_attempt(
            device,
            data.job_id,
            data.attempt_id,
            data.lease_epoch,
            # Receipt recovery does not grant execution or require a current job version.
            execution=False,
        )
        item = jobs.reconcile_input(
            device,
            data.job_id,
            data.attempt_id,
            data.lease_epoch,
            input_id=data.input_id,
            input_sequence=data.input_sequence,
            outcome=data.outcome,
            receipt_id=data.receipt_id,
        )
        return _success(request, {"input": jobs.input_data(item)})


@endpoint("POST")
def operations(request: HttpRequest) -> HttpResponse:
    data = r.LeaseRequest.model_validate_json(request.body)
    with agent_transaction():
        job, attempt = jobs.locked_attempt(
            runner(request),
            data.job_id,
            data.attempt_id,
            data.lease_epoch,
            # Recovery observes immutable authority without a current job-version gate.
            execution=False,
        )
        from zerver.lib.agent_context import require_audience
        from zerver.lib.agent_policy import check_agent_access

        require_audience(job)
        check_agent_access(
            job.requester, job.profile, job.repository, job.source_message, "profile.use"
        )
        return _success(
            request,
            {
                "operations": [
                    approvals.proposal_data(item) | {"receipt": item.remote_receipt}
                    for item in agents.AgentOperation.objects.filter(
                        attempt=attempt, realm=job.realm
                    ).order_by("created_at")[:100]
                ]
            },
        )


@endpoint("POST")
def context_file(request: HttpRequest) -> HttpResponse:
    data = r.ContextFile.model_validate_json(request.body)
    with agent_transaction():
        device = runner(request)
        job, attempt = jobs.locked_attempt(
            device,
            data.job_id,
            data.attempt_id,
            data.lease_epoch,
            expected_version=data.job_version,
        )
        selected_context(job, [data.reference_id])
        ref = agents.AgentContextRef.objects.get(id=data.reference_id, job=job, kind="attachment")
        attachment = ref.attachment
        if attachment is None:
            raise ValueError("Selected file is unavailable.")
        path = attachment.path_id
        maximum = min(attempt.descriptor["budget"]["tool_output_bytes"], p.MAX_ARTIFACT_BYTES)
        if attachment.size > maximum:
            raise ValueError("Selected file exceeds the tool output budget.")
    from zerver.lib.upload import attachment_source

    # The backend can use remote storage. Read outside every authority transaction.
    stream = attachment_source(path).reader()
    try:
        content = stream.read(maximum + 1)
    finally:
        stream.close()
    if len(content) > maximum:
        raise ValueError("Selected file exceeds the tool output budget.")
    with agent_transaction():
        job, _ = jobs.locked_attempt(
            runner(request),
            data.job_id,
            data.attempt_id,
            data.lease_epoch,
            expected_version=data.job_version,
        )
        selected_context(job, [data.reference_id])
        ref = agents.AgentContextRef.objects.get(id=data.reference_id, job=job)
        if (
            ref.attachment_id != attachment.id
            or ref.attachment is None
            or ref.attachment.path_id != path
        ):
            raise ValueError("Selected file changed.")
    response = HttpResponse(content, content_type="application/octet-stream")
    response["Content-Disposition"] = 'attachment; filename="selected-file"'
    response["X-Content-Type-Options"] = "nosniff"
    response["Cache-Control"] = "no-store"
    return response


@endpoint("POST")
def reconcile_local(request: HttpRequest) -> HttpResponse:
    data = r.ReconcileLocal.model_validate_json(request.body)
    with agent_transaction():
        device = runner(request)
        jobs.locked_attempt(
            device,
            data.job_id,
            data.attempt_id,
            data.lease_epoch,
            # Recovery observes immutable authority without a current job-version gate.
            execution=False,
        )
        operation = approvals.reconcile_local_operation(
            device, data.job_id, data.attempt_id, data.lease_epoch, data.receipt
        )
        return _success(request, {"operation": approvals.proposal_data(operation)})
