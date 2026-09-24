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
from zerver.lib.agent_context import (
    AudienceChanged,
    agent_transaction,
    require_audience,
    require_job_access,
)
from zerver.lib.agent_job_requests import Draft
from zerver.lib.exceptions import JsonableError
from zerver.models import Message, UserProfile, agents


class RequiredChecksFailed(ValueError):  # noqa: N818
    """A code job's required checks do not prove the result's final tree."""


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
    job: agents.AgentJob,
    attempt: agents.AgentAttempt,
    artifacts: list[agents.AgentArtifact],
    *,
    check_audience: bool = True,
) -> None:
    if (
        job.status != "verifying"
        or attempt.active
        or attempt.process_state != "stopped"
        or attempt.stopped_at is None
    ):
        raise ValueError("Empty containment is required before completion.")
    if check_audience and attempt.audience_binding != job.conversation.audience_binding:
        raise AudienceChanged("Result audience changed.")
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
                raise RequiredChecksFailed("Required checks do not prove the final tree.")
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


def _block_publication(job_id: UUID, *, target_status: str, reason: str, park_outbox: bool) -> None:
    with agent_transaction():
        job = agents.AgentJob.objects.select_for_update().get(id=job_id)
        if job.status == "verifying" and job.blocked_reason != reason:
            transition(job, target_status, reason=reason)
            audit(
                job,
                "publication.blocked",
                {"result_message_id": None, "reason": reason},
                authority="publisher",
            )
            if park_outbox:
                agents.AgentOutbox.objects.filter(job=job, event_type="result.publish").exclude(
                    status="delivered"
                ).update(status="blocked")


def publish_result(job_id: UUID) -> dict[str, object]:
    try:
        return _publish_result(job_id)
    except AudienceChanged:
        # The result stays available for the requester to send privately
        # (deliver_result_privately); reconcile never retries a blocked row.
        _block_publication(
            job_id, target_status="verifying", reason="audience_changed", park_outbox=True
        )
        raise
    except RequiredChecksFailed:
        # A required check failed against the final tree: end the job instead
        # of leaving it stuck in "verifying" forever (contract 9.5).
        _block_publication(
            job_id, target_status="failed", reason="verification_failed", park_outbox=True
        )
        raise
    except (ValueError, ObjectDoesNotExist, JsonableError):
        # Any other error is presumed transient: leave the outbox row pending
        # so reconcile_agents retries it with its own backoff.
        _block_publication(
            job_id, target_status="verifying", reason="publication_blocked", park_outbox=False
        )
        raise


def _result_message_content(job: agents.AgentJob, summary: str) -> str:
    """The result body (before the mention prefix and task link): the summary,
    plus the manage job's executed steps or the code job's done line."""
    content = summary
    if job.job_kind == "manage":
        # Contract 2.6 item 7: end the reply with the server-written list of
        # executed steps, so it never claims a step that did not happen.
        from zerver.actions.agent_team_tools import team_manage_result_lines

        steps = team_manage_result_lines(job)
        if steps:
            with override_language(job.realm.default_language):
                header = _("Steps taken:")
            content = f"{content}\n\n{header}\n" + "\n".join(steps)
    elif job.job_kind == "code":
        # Contract 9.8: the done line names the delivery target explicitly.
        with override_language(job.realm.default_language):
            done_line = (
                _("Done. The diff is ready for review.")
                if job.delivery_target == "patch"
                else _("Done. The draft pull request is ready for review.")
            )
        content = f"{content}\n\n{done_line}"
    return content


def _edit_draft_after_commit(
    bot: UserProfile, message_id: int, content: str, *, draft_of: UUID | None = None
) -> None:
    """Replace the text of a streamed draft once the agent transaction has
    committed. check_update_message is a durable transaction, so it cannot
    run inside agent_transaction. A draft edit (`draft_of` set) is skipped
    when the final result was published first, so it never covers it."""
    from django.db import transaction

    from zerver.actions.message_edit import check_update_message

    def edit() -> None:
        if (
            draft_of is not None
            and agents.AgentJob.objects.filter(id=draft_of, result_receipt__isnull=False).exists()
        ):
            return
        with contextlib.suppress(JsonableError):
            check_update_message(bot, message_id, content=content)

    transaction.on_commit(edit, robust=True)


# Shown at the end of a streamed draft while the model is still writing.
DRAFT_WRITING_MARK = " \u258d"


def publish_draft(runner: agents.AgentRunner, data: Draft) -> None:
    """Show the answer as it is written: the first draft sends one bot
    message in the conversation, and later drafts edit that message. The
    final result replaces it (_publish_result), so a job still ends with one
    message. Drafts are best effort; the caller drops a rejected draft."""
    from zerver.actions.message_send import check_message, do_send_messages
    from zerver.lib.addressee import Addressee
    from zerver.lib.mention import silent_mention_syntax_for_user
    from zerver.models.clients import get_client

    with agent_transaction():
        job, attempt = locked_attempt(runner, data.job_id, data.attempt_id, data.lease_epoch)
        if job.job_kind != "answer" or job.result_receipt is not None:
            return
        audience = require_audience(job)
        check_attempt_access(job.requester, job, attempt, "profile.use")
        reject_secrets(job, data.text.encode())
        content = f"{silent_mention_syntax_for_user(job.requester)} {data.text}{DRAFT_WRITING_MARK}"
        if job.result_message_id is not None:
            _edit_draft_after_commit(
                job.profile.bot_user, job.result_message_id, content, draft_of=job.id
            )
            return
        anchor = Message.objects.get(id=audience.anchor_message_id)
        addressee = (
            Addressee.for_stream_id(audience.stream_id, anchor.topic_name())
            if audience.stream_id is not None
            else Addressee.for_user_ids(audience.audience_user_ids, job.realm)
        )
        message = check_message(
            job.profile.bot_user,
            get_client("Grow Agent"),
            addressee,
            content,
            realm=job.realm,
            no_previews=True,
        )
        job.result_message_id = do_send_messages([message])[0].message_id
        job.save(update_fields=["result_message"])


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
        content = _result_message_content(job, proposal.summary)
        anchor = Message.objects.get(id=audience.anchor_message_id)
        addressee = (
            Addressee.for_stream_id(audience.stream_id, anchor.topic_name())
            if audience.stream_id is not None
            else Addressee.for_user_ids(audience.audience_user_ids, job.realm)
        )
        content = f"{silent_mention_syntax_for_user(job.requester)} {content} {job_task_link(job)}"
        if job.result_message_id is not None:
            # The streamed draft becomes the result message.
            message_id = job.result_message_id
            _edit_draft_after_commit(job.profile.bot_user, message_id, content)
        else:
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


def deliver_result_privately(
    actor: UserProfile, job_id: UUID, expected_version: int
) -> agents.AgentJob:
    """AT-23: send a held result straight to the requester when the
    conversation changed under it, instead of leaving it unreadable."""
    from zerver.actions.message_send import check_message, do_send_messages
    from zerver.lib.addressee import Addressee
    from zerver.lib.mention import silent_mention_syntax_for_user
    from zerver.lib.message import access_message
    from zerver.models.clients import get_client

    with agent_transaction():
        job = agents.AgentJob.objects.select_for_update().get(id=job_id, realm=actor.realm)
        if actor.id != job.requester_id:
            raise ValueError("Job cannot deliver privately.")
        if job.result_receipt is not None:
            # Idempotent replay: the job has already moved on (typically to
            # "completed"), so the state checks below no longer apply to it.
            return job
        if (
            job.status != "verifying"
            or job.blocked_reason != "audience_changed"
            or job.version != expected_version
        ):
            raise ValueError("Job cannot deliver privately.")
        assert job.source_message_id is not None
        access_message(actor, job.source_message_id, is_modifying_message=False)
        proposal = p.ResultPayload.model_validate(job.result_proposal)
        artifacts = list(
            agents.AgentArtifact.objects.filter(id__in=proposal.artifact_ids, attempt__job=job)
        )
        if len(artifacts) != len(set(proposal.artifact_ids)):
            raise ValueError("Result artifacts are unavailable.")
        attempt = (
            agents.AgentAttempt.objects.select_for_update()
            .filter(job=job)
            .order_by("-number")
            .first()
        )
        if attempt is None:
            raise ValueError("Attempt is unavailable.")
        # The requester could have lost access to the profile itself (not
        # merely to the source message, checked above) while the result sat
        # blocked; recheck it here, the same way _publish_result does.
        check_attempt_access(actor, job, attempt, "profile.use")
        # Only this action skips the audience check: that check is exactly
        # why the job is here, and re-running it would always fail again.
        verify_result(job, attempt, artifacts, check_audience=False)
        reject_secrets(job, proposal.summary.encode())
        with override_language(job.realm.default_language):
            first_line = _("This result was sent here because the conversation changed.")
        content = _result_message_content(job, proposal.summary)
        content = (
            f"{first_line}\n{silent_mention_syntax_for_user(job.requester)}"
            f" {content} {job_task_link(job)}"
        )
        message = check_message(
            job.profile.bot_user,
            get_client("Grow Agent"),
            Addressee.for_user_ids([job.requester_id], job.realm),
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
            "destination": "direct",
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
            {"result_message_id": message_id, "reason": "delivered_privately"},
            attempt=attempt,
            authority="publisher",
        )
        return job


def job_task_link(job: agents.AgentJob) -> str:
    return f"{job.realm.url}/#agent-jobs/{job.id}"


def post_job_notice(
    job: agents.AgentJob | UUID, marker_key: str, sentence: str, *, mention_requester: bool = True
) -> bool:
    """Post one bot message in the job's conversation, at most once per marker_key.

    marker_key identifies the occurrence (an approval, an input request, or a
    job ending); a repeat call with the same key is a silent no-op. The
    marker row and the message share one transaction (contract 9.3): a
    failed send leaves no marker, so a caller may retry with the same key.
    """
    from zerver.actions.message_send import check_message, do_send_messages
    from zerver.lib.addressee import Addressee
    from zerver.models.clients import get_client

    job_id = job.id if isinstance(job, agents.AgentJob) else job
    with agent_transaction():
        if agents.AgentOutbox.objects.filter(delivery_key=marker_key).exists():
            return False
        job = agents.AgentJob.objects.get(id=job_id)
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
        agents.AgentOutbox.objects.create(
            realm=job.realm,
            job=job,
            delivery_key=marker_key,
            event_type="status.notice",
            status="delivered",
            delivered_at=now(),
        )
    return True
