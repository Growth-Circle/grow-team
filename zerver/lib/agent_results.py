"""Private artifact storage and atomic, current-audience result publication."""

import contextlib
import hashlib
import os
import re
import stat
from collections.abc import Iterable
from datetime import timedelta
from pathlib import Path
from uuid import UUID, uuid4

from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist
from django.db.models import Sum
from django.utils.timezone import now
from django.utils.translation import gettext as _
from django.utils.translation import override as override_language

from zerver.actions.agent_jobs import audit, check_attempt_access, locked_attempt, transition
from zerver.lib import agent_protocol as p
from zerver.lib.agent_context import agent_transaction, require_audience, require_job_access
from zerver.lib.exceptions import JsonableError
from zerver.models import Message, UserProfile, agents


def artifact_root() -> Path:
    configured = getattr(settings, "AGENT_ARTIFACT_ROOT", None)
    if not configured:
        raise ValueError("Private artifact storage is not configured.")
    root = Path(configured).absolute()
    if root.resolve() != root or not root.is_dir():
        raise ValueError("Private artifact root is unavailable.")
    for setting in ("LOCAL_UPLOADS_DIR", "STATIC_ROOT"):
        public = getattr(settings, setting, None)
        if public and root.is_relative_to(Path(public).resolve()):
            raise ValueError("Artifact storage cannot use a public directory.")
    return root


def reject_secrets(job: agents.AgentJob, value: bytes) -> None:
    # Reject known server values instead of changing checksum-bound evidence.
    secret_ids = {job.profile.provider.secret_id} if job.profile.provider is not None else set()
    for descriptor in agents.AgentAttempt.objects.filter(job=job).values_list(
        "descriptor", flat=True
    ):
        provider = descriptor.get("provider")
        reference = provider.get("credential_ref") if provider else None
        if reference and reference.get("kind") == "server":
            secret_ids.add(reference["id"])
    for secret in agents.AgentSecret.objects.filter(
        id__in=[value for value in secret_ids if value is not None], realm=job.realm
    ):
        from zerver.lib.agent_secrets import decrypt_agent_secret

        plaintext = decrypt_agent_secret(
            bytes(secret.ciphertext),
            bytes(secret.wrapped_key),
            key_id=secret.key_id,
            realm_id=secret.realm_id,
            owner_id=secret.owner_id,
            version=secret.version,
        )
        if plaintext and plaintext.encode() in value:
            raise ValueError("Sensitive artifact content was rejected.")
    if re.search(
        rb"(?i)(?:authorization\s*:\s*bearer\s+\S+|-----BEGIN [A-Z ]*PRIVATE KEY-----)", value
    ):
        raise ValueError("Sensitive artifact content was rejected.")


def store_artifact(
    runner: agents.AgentRunner,
    job_id: UUID,
    attempt_id: UUID,
    epoch: int,
    *,
    chunks: Iterable[bytes],
    checksum: str,
    kind: str,
    filename: str,
    media_type: str,
    credential_token: str | None = None,
) -> agents.AgentArtifact:
    artifact_id = uuid4()
    metadata = p.ArtifactRecord.model_validate(
        {
            "id": str(artifact_id),
            "attempt_id": str(attempt_id),
            "kind": kind,
            "checksum": checksum,
            "size": 0,
            "media_type": media_type,
            "filename": filename,
            "expires_at": (now() + timedelta(days=7)).isoformat(),
        }
    )
    if Path(filename).name != filename or any(char in filename for char in "\x00\r\n\\/"):
        raise ValueError("Invalid artifact filename.")
    with agent_transaction():
        job, attempt = locked_attempt(runner, job_id, attempt_id, epoch)
        budget = p.Budget.model_validate(attempt.descriptor["budget"])
    root = artifact_root()
    storage_ref = f"{artifact_id.hex}.data"
    staging = f".{artifact_id.hex}.partial"
    directory = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    data = bytearray()
    written = False
    registered = False
    try:
        descriptor = os.open(
            staging, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory
        )
        with os.fdopen(descriptor, "wb") as stream:
            for chunk in chunks:
                if len(data) + len(chunk) > budget.artifact_bytes:
                    raise ValueError("Artifact size limit exceeded.")
                data.extend(chunk)
                stream.write(chunk)
            stream.flush()
            os.fsync(stream.fileno())
        if hashlib.sha256(data).hexdigest() != checksum:
            raise ValueError("Artifact checksum mismatch.")
        if media_type != "application/octet-stream":
            data.decode("utf-8")
        reject_secrets(job, bytes(data))
        os.replace(staging, storage_ref, src_dir_fd=directory, dst_dir_fd=directory)
        os.fsync(directory)
        written = True
        with agent_transaction():
            if credential_token is not None:
                from zerver.actions.agents import authenticate_runner_token

                if authenticate_runner_token(credential_token).runner_id != runner.id:
                    raise ValueError("Artifact credential changed.")
            job, attempt = locked_attempt(runner, job_id, attempt_id, epoch)
            size = (
                agents.AgentArtifact.objects.filter(attempt__job=job).aggregate(total=Sum("size"))[
                    "total"
                ]
                or 0
            )
            if size + len(data) > budget.job_artifact_bytes:
                raise ValueError("Job artifact limit exceeded.")
            artifact = agents.AgentArtifact.objects.create(
                id=artifact_id,
                realm=job.realm,
                attempt=attempt,
                kind=kind,
                checksum=checksum,
                size=len(data),
                storage_ref=storage_ref,
                filename=filename,
                media_type=media_type,
                expires_at=metadata.expires_at,
                acl_scope=job.conversation.scope,
                audience_binding=attempt.audience_binding,
            )
        registered = True
        return artifact
    finally:
        if not registered:
            with contextlib.suppress(FileNotFoundError):
                os.unlink(storage_ref if written else staging, dir_fd=directory)
        os.close(directory)


def read_artifact(artifact: agents.AgentArtifact) -> bytes:
    if (
        not re.fullmatch(r"[0-9a-f]{32}\.data", artifact.storage_ref)
        or artifact.unavailable_at is not None
    ):
        raise ValueError("Artifact is unavailable.")
    directory = os.open(artifact_root(), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        descriptor = os.open(artifact.storage_ref, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory)
        with os.fdopen(descriptor, "rb") as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise ValueError("Artifact is unavailable.")
            data = stream.read(p.MAX_ARTIFACT_BYTES + 1)
        if len(data) != artifact.size or hashlib.sha256(data).hexdigest() != artifact.checksum:
            raise ValueError("Artifact integrity check failed.")
        return data
    except OSError:
        raise ValueError("Artifact is unavailable.") from None
    finally:
        os.close(directory)


def download_artifact(actor: UserProfile, artifact_id: UUID) -> tuple[agents.AgentArtifact, bytes]:
    artifact = agents.AgentArtifact.objects.get(id=artifact_id, realm=actor.realm)
    data = read_artifact(artifact)
    with agent_transaction():
        artifact = agents.AgentArtifact.objects.get(id=artifact_id, realm=actor.realm)
        require_job_access(actor, artifact.attempt.job)
        if artifact.audience_binding != artifact.attempt.audience_binding:
            raise ValueError("Artifact scope is invalid.")
    return artifact, data


def verify_result(
    job: agents.AgentJob, attempt: agents.AgentAttempt, artifacts: list[agents.AgentArtifact]
) -> None:
    if (
        job.status != "verifying"
        or attempt.active
        or attempt.process_state != "stopped"
        or attempt.stopped_at is None
    ):
        raise ValueError("Empty containment is required before completion.")
    if attempt.audience_binding != job.conversation.audience_binding:
        raise ValueError("Result audience changed.")
    result = p.ResultPayload.model_validate(job.result_proposal)
    if not result.summary.strip() or not any(item.kind == "summary" for item in artifacts):
        raise ValueError("Result summary is required.")
    if any(
        item.attempt_id != attempt.id or item.audience_binding != attempt.audience_binding
        for item in artifacts
    ):
        raise ValueError("Result artifact scope is invalid.")
    if agents.AgentInput.objects.filter(
        job=job, delivery_state__in=["pending", "delivered", "delivery_uncertain"]
    ).exists():
        raise ValueError("Inputs remain unapplied.")
    if (
        agents.AgentOperation.objects.filter(
            attempt__job=job, status__in=["started", "outcome_unknown"]
        )
        .exclude(tool_class="team.manage")
        .exists()
    ):
        raise ValueError("Operation outcomes remain unresolved.")
    if job.job_kind == "code":
        if (
            not attempt.base_commit
            or not attempt.tree_hash
            or result.tree_hash != attempt.tree_hash
        ):
            raise ValueError("Final tree is unavailable.")
        if not any(item.kind == "diff" for item in artifacts):
            raise ValueError("Final diff is required.")
        repository = p.RepositoryConfig.model_validate(attempt.descriptor["repository"])
        if not repository.required_checks:
            raise ValueError("Required checks are unavailable.")
        last_mutation = (
            agents.AgentOperation.objects.filter(
                attempt=attempt,
                tool_class__in=["repository.edit", "shell.run", "dependencies.install"],
            )
            .order_by("-finished_at")
            .first()
        )
        for check in repository.required_checks:
            verification = (
                agents.AgentVerification.objects.filter(
                    attempt=attempt,
                    check_id=check.id,
                    tree_hash=attempt.tree_hash,
                )
                .order_by("-finished_at")
                .first()
            )
            if (
                verification is None
                or verification.exit_code != 0
                or verification.timed_out
                or verification.output_artifact.unavailable_at is not None
                or verification.operation is None
                or verification.operation.status != "succeeded"
                or verification.command != check.argv
                or verification.cwd != check.cwd
                or (
                    last_mutation is not None
                    and last_mutation.finished_at is not None
                    and verification.started_at < last_mutation.finished_at
                )
            ):
                raise ValueError("Required checks do not prove the final tree.")
        if job.delivery_target == "draft_pr":
            for action in ["git.push", "git.draft_pr"]:
                operations = agents.AgentOperation.objects.filter(
                    attempt=attempt, tool_class=action, status="succeeded"
                )
                matched = False
                for operation in operations:
                    if operation.remote_receipt is None:
                        continue
                    receipt = p.RemoteReceipt.model_validate(operation.remote_receipt)
                    if (
                        receipt.commit == operation.arguments["commit"]
                        and operation.scope_binding.get("tree_hash") == attempt.tree_hash
                        and agents.AgentApproval.objects.filter(
                            operation=operation, decision="consumed"
                        ).exists()
                    ) and (
                        action != "git.draft_pr"
                        or (receipt.pull_request_id and receipt.pull_request_url)
                    ):
                        matched = True
                if not matched:
                    raise ValueError("Draft PR delivery is unverified.")


def publish_result(job_id: UUID) -> dict[str, object]:
    try:
        return _publish_result(job_id)
    except (ValueError, ObjectDoesNotExist, JsonableError):
        with agent_transaction():
            job = agents.AgentJob.objects.select_for_update().get(id=job_id)
            if job.status == "verifying" and job.blocked_reason != "publication_blocked":
                transition(job, "verifying", reason="publication_blocked")
                audit(
                    job,
                    "publication.blocked",
                    {"result_message_id": None, "reason": "publication_blocked"},
                    authority="publisher",
                )
                agents.AgentOutbox.objects.filter(job=job, event_type="result.publish").exclude(
                    status="delivered"
                ).update(status="blocked")
        raise


def _publish_result(job_id: UUID) -> dict[str, object]:
    job = agents.AgentJob.objects.get(id=job_id)
    if job.result_receipt is not None:
        return job.result_receipt
    proposal = p.ResultPayload.model_validate(job.result_proposal)
    artifacts = list(
        agents.AgentArtifact.objects.filter(id__in=proposal.artifact_ids, attempt__job=job)
    )
    if len(artifacts) != len(set(proposal.artifact_ids)):
        raise ValueError("Result artifacts are unavailable.")
    verification_artifacts = list(
        agents.AgentArtifact.objects.filter(agentverification__attempt__job=job).distinct()
    )
    # File checks run before database locks. Stored artifact files are immutable.
    for artifact in [*artifacts, *verification_artifacts]:
        read_artifact(artifact)
    from zerver.actions.message_send import check_message, do_send_messages
    from zerver.lib.addressee import Addressee
    from zerver.lib.mention import silent_mention_syntax_for_user
    from zerver.models.clients import get_client

    client = get_client("Grow Agent")
    with agent_transaction():
        job = agents.AgentJob.objects.select_for_update().get(id=job_id)
        if job.result_receipt is not None:
            return job.result_receipt
        if p.ResultPayload.model_validate(job.result_proposal) != proposal:
            raise ValueError("Result proposal changed.")
        attempt = (
            agents.AgentAttempt.objects.select_for_update()
            .filter(job=job)
            .order_by("-number")
            .first()
        )
        if attempt is None:
            raise ValueError("Attempt is unavailable.")
        audience = require_audience(job)
        check_attempt_access(job.requester, job, attempt, "profile.use")
        verify_result(job, attempt, artifacts)
        reject_secrets(job, proposal.summary.encode())
        content = proposal.summary
        if job.job_kind == "manage":
            # Contract 2.6 item 7: end the reply with the server-written list of
            # executed steps, so it never claims a step that did not happen.
            from zerver.actions.agent_team_tools import team_manage_result_lines

            steps = team_manage_result_lines(job)
            if steps:
                with override_language(job.realm.default_language):
                    header = _("Steps taken:")
                content = f"{content}\n\n{header}\n" + "\n".join(steps)
        anchor = Message.objects.get(id=audience.anchor_message_id)
        addressee = (
            Addressee.for_stream_id(audience.stream_id, anchor.topic_name())
            if audience.stream_id is not None
            else Addressee.for_user_ids(audience.audience_user_ids, job.realm)
        )
        content = f"{silent_mention_syntax_for_user(job.requester)} {content} {job_task_link(job)}"
        message = check_message(
            job.profile.bot_user,
            client,
            addressee,
            content,
            realm=job.realm,
            no_previews=True,
        )
        message_id = do_send_messages([message])[0].message_id
        receipt = {
            "delivery_key": f"result:{job.id}",
            "message_id": message_id,
            "attempt_id": str(attempt.id),
            "published_at": now().isoformat(),
        }
        job.result_message_id = message_id
        job.result_receipt = receipt
        job.completed_at = now()
        job.phase = "deliver"
        job.save(update_fields=["result_message", "result_receipt", "completed_at", "phase"])
        transition(job, "completed")
        agents.AgentOutbox.objects.update_or_create(
            delivery_key=f"result:{job.id}",
            defaults={
                "realm": job.realm,
                "job": job,
                "event_type": "result.publish",
                "status": "delivered",
                "delivered_at": now(),
            },
        )
        audit(
            job,
            "result.published",
            {"result_message_id": message_id, "reason": ""},
            attempt=attempt,
            authority="publisher",
        )
        return receipt


def job_task_link(job: agents.AgentJob) -> str:
    return f"{job.realm.url}/#agent-jobs/{job.id}"


def post_job_notice(
    job: agents.AgentJob, marker_key: str, sentence: str, *, mention_requester: bool = True
) -> bool:
    """Post one bot message in the job's conversation, at most once per marker_key.

    marker_key identifies the occurrence (an approval, an input request, or a
    job ending); a repeat call with the same key is a silent no-op.
    """
    _outbox, created = agents.AgentOutbox.objects.get_or_create(
        delivery_key=marker_key,
        defaults={
            "realm": job.realm,
            "job": job,
            "event_type": "status.notice",
            "status": "delivered",
            "delivered_at": now(),
        },
    )
    if not created:
        return False
    from zerver.actions.message_send import check_message, do_send_messages
    from zerver.lib.addressee import Addressee
    from zerver.models.clients import get_client

    with agent_transaction():
        audience = require_audience(job)
        anchor = Message.objects.get(id=audience.anchor_message_id)
        addressee = (
            Addressee.for_stream_id(audience.stream_id, anchor.topic_name())
            if audience.stream_id is not None
            else Addressee.for_user_ids(audience.audience_user_ids, job.realm)
        )
        mention = (
            f"@**{job.requester.full_name}|{job.requester.id}**" if mention_requester else None
        )
        content = " ".join(part for part in [mention, sentence, job_task_link(job)] if part)
        message = check_message(
            job.profile.bot_user,
            get_client("Grow Agent"),
            addressee,
            content,
            realm=job.realm,
            no_previews=True,
        )
        do_send_messages([message])
    return True
