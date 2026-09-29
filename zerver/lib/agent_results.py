"""Private artifact storage and atomic, current-audience result publication."""

import contextlib
import hashlib
import json
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

from zerver.actions.agent_jobs import (
    TERMINAL,
    agent_language,
    audit,
    check_attempt_access,
    locked_attempt,
    react_after_commit,
    request_stop,
    throttle,
    transition,
    typing_after_commit,
    widget_card_data,
)
from zerver.actions.submessage import do_add_submessage
from zerver.lib import agent_protocol as p
from zerver.lib.agent_context import (
    AudienceChanged,
    agent_transaction,
    require_audience,
    require_job_access,
)
from zerver.lib.agent_job_requests import Draft
from zerver.lib.exceptions import JsonableError
from zerver.lib.message import truncate_content
from zerver.models import Message, Stream, UserProfile, agents


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
            attempt = (
                agents.AgentAttempt.objects.select_for_update().filter(job=job, active=True).first()
                if target_status != "verifying"
                else None
            )
            if attempt is not None:
                # The job ends while its attempt still runs (FL-30): ask the
                # runner to stop first, the same as an expired approval.
                request_stop(job, attempt, target=target_status, reason=reason)
            else:
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
            if reason == "audience_changed":
                _show_neutral_line(job)


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


# Shown at the end of a streamed draft while the model is still writing.
DRAFT_WRITING_MARK = " ▍"


def publish_draft(runner: agents.AgentRunner, data: Draft) -> None:
    """Show the answer as it is written: each draft edits the job's card
    message (SD-05). The final result replaces it (_publish_result), so a
    job still ends with one message. Drafts are best effort: the caller
    drops a rejected draft, and the card takes at most one draft per
    second (SD-04)."""
    from zerver.actions.message_send import check_message, do_send_messages
    from zerver.lib.addressee import Addressee
    from zerver.lib.mention import silent_mention_syntax_for_user
    from zerver.models.clients import get_client

    try:
        with agent_transaction():
            job, attempt = locked_attempt(runner, data.job_id, data.attempt_id, data.lease_epoch)
            if job.job_kind != "answer" or job.result_receipt is not None:
                return
            audience = require_audience(job)
            check_attempt_access(job.requester, job, attempt, "profile.use")
            reject_secrets(job, data.text.encode())
            prefix = f"{silent_mention_syntax_for_user(job.requester)} "
            room = settings.MAX_MESSAGE_LENGTH - len(prefix) - len(DRAFT_WRITING_MARK)
            content = f"{prefix}{data.text[:room]}{DRAFT_WRITING_MARK}"
            if job.result_message_id is not None:
                if not throttle(f"agent-draft:{job.result_message_id}", 1):
                    return
                message = _lock_card_message(job.result_message_id)
                first_draft = _is_fallback_line(job, message.content)
                _update_card_message(job, message, content)
                if first_draft:
                    typing_after_commit(job.id, "stop")
                return
            # Defensive fallback only: admission (create_job) already posts the
            # job's one card message on_commit, so result_message_id is normally
            # set well before any draft can arrive. This covers that post
            # having failed or still being retried.
            anchor = Message.objects.get(id=audience.anchor_message_id)
            addressee = (
                Addressee.for_stream_id(audience.stream_id, anchor.topic_name())
                if audience.stream_id is not None
                else Addressee.for_user_ids(audience.audience_user_ids, job.realm)
            )
            sent = check_message(
                job.profile.bot_user,
                get_client("Grow Agent"),
                addressee,
                content,
                realm=job.realm,
                no_previews=True,
            )
            job.result_message_id = do_send_messages([sent])[0].message_id
            job.save(update_fields=["result_message"])
    except AudienceChanged:
        # FL-22: a draft already shown must not stay visible to the new
        # audience either, not only the final answer.
        with agent_transaction():
            job = agents.AgentJob.objects.select_for_update().get(id=data.job_id)
            if job.result_receipt is None:
                _show_neutral_line(job)
        raise


def _answer_text(summary: str, summary_data: bytes) -> str:
    """Q-19: ResultPayload.summary holds at most 4096 characters. A longer
    answer arrives whole in the summary artifact, and the summary is its
    start; use that full text, else the summary as it is."""
    text = summary_data.decode(errors="replace")
    return text if len(text) > len(summary) and text.startswith(summary) else summary


def _fit_message_limit(job: agents.AgentJob, content: str) -> tuple[str, bool]:
    """Q-19/13-Q2: a result that does not fit in one message is cut at the
    limit, with one closing sentence instead of Zulip's generic "[message
    truncated]" marker. Returns whether it cut: the caller then shows the
    summary artifact, which holds the full text, as a file chip."""
    if len(content) <= settings.MAX_MESSAGE_LENGTH:
        return content, False
    with override_language(agent_language(job.realm_id)):
        closing = _("The full answer is attached as a file.")
    return truncate_content(content, settings.MAX_MESSAGE_LENGTH, f"\n\n{closing}"), True


def _summary_artifact(artifacts: list[agents.AgentArtifact]) -> agents.AgentArtifact | None:
    return next((item for item in artifacts if item.kind == "summary"), None)


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
    summary_artifact = _summary_artifact(artifacts)
    summary_data = None
    # File checks run before database locks. Stored artifact files are immutable.
    for artifact in [*artifacts, *verification_artifacts]:
        data = read_artifact(artifact)
        if artifact == summary_artifact:
            summary_data = data
    from zerver.actions.message_send import check_message, do_send_messages
    from zerver.lib.addressee import Addressee
    from zerver.lib.mention import silent_mention_syntax_for_user
    from zerver.lib.room_digests import fill_room_digest
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
        # verify_result requires a summary artifact, read before the locks.
        assert summary_data is not None
        answer = _answer_text(proposal.summary, summary_data)
        digest_text = fill_room_digest(job, answer)
        if digest_text is not None:
            answer = digest_text
        reject_secrets(job, answer.encode())
        # 13-R2: no raw job URL in the message; the card's own Detail action
        # opens the job instead.
        content = _result_message_content(job, answer)
        content = f"{silent_mention_syntax_for_user(job.requester)} {content}"
        content, cut = _fit_message_limit(job, content)
        if job.result_message_id is not None:
            # The card message becomes the result message.
            message = _lock_card_message(job.result_message_id)
            message_id = message.id
            _notify_requester(job, message_id)
            _update_card_message(job, message, content)
        else:
            anchor = Message.objects.get(id=audience.anchor_message_id)
            addressee = (
                Addressee.for_stream_id(audience.stream_id, anchor.topic_name())
                if audience.stream_id is not None
                else Addressee.for_user_ids(audience.audience_user_ids, job.realm)
            )
            sent = check_message(
                job.profile.bot_user,
                client,
                addressee,
                content,
                realm=job.realm,
                no_previews=True,
            )
            message_id = do_send_messages([sent])[0].message_id
            _notify_requester(job, message_id)
        receipt: dict[str, object] = {
            "delivery_key": f"result:{job.id}",
            "message_id": message_id,
            "attempt_id": str(attempt.id),
            "published_at": now().isoformat(),
        }
        if cut:
            assert summary_artifact is not None
            receipt["full_text_artifact_id"] = str(summary_artifact.id)
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
    conversation changed under it, instead of leaving it unreadable. The
    job keeps its card message (result_message_id), which shows a neutral
    line (FL-22); only the receipt names the direct message."""
    from zerver.actions.message_send import check_message, do_send_messages
    from zerver.lib.addressee import Addressee
    from zerver.lib.mention import silent_mention_syntax_for_user
    from zerver.lib.message import access_message
    from zerver.lib.room_digests import fill_room_digest
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
        summary_artifact = _summary_artifact(artifacts)
        assert summary_artifact is not None
        answer = _answer_text(proposal.summary, read_artifact(summary_artifact))
        # This answer goes to one person because the room changed under it,
        # so the room's banner must not show it.
        digest_text = fill_room_digest(job, answer, keep=False)
        if digest_text is not None:
            answer = digest_text
        reject_secrets(job, answer.encode())
        with override_language(job.realm.default_language):
            first_line = _("This result was sent here because the conversation changed.")
        content = _result_message_content(job, answer)
        content = f"{first_line}\n{silent_mention_syntax_for_user(job.requester)} {content}"
        content, cut = _fit_message_limit(job, content)
        message = check_message(
            job.profile.bot_user,
            get_client("Grow Agent"),
            Addressee.for_user_ids([job.requester_id], job.realm),
            content,
            realm=job.realm,
            no_previews=True,
        )
        message_id = do_send_messages([message])[0].message_id
        receipt: dict[str, object] = {
            "delivery_key": f"result:{job.id}",
            "message_id": message_id,
            "attempt_id": str(attempt.id),
            "published_at": now().isoformat(),
            "destination": "direct",
        }
        if cut:
            receipt["full_text_artifact_id"] = str(summary_artifact.id)
        job.result_receipt = receipt
        job.completed_at = now()
        job.phase = "deliver"
        job.save(update_fields=["result_receipt", "completed_at", "phase"])
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


def _lock_card_message(message_id: int) -> Message:
    return (
        Message.objects.select_for_update(of=("self",))
        .select_related("recipient", "sender")
        .get(id=message_id)
    )


def _update_card_message(job: agents.AgentJob, message: Message, content: str) -> None:
    """Replace the card message's content in place (SD-05, FL-19). This is
    the server updating its own status line, not a person editing a
    message: it writes no edit history, shows no "edited" label, and
    ignores the realm's edit time limit. The caller holds the message's
    row lock (_lock_card_message).

    Mirrors do_update_embedded_data: the same message cache update, and a
    rendering_only event for everyone who can see the message."""
    from zerver.actions.message_send import render_incoming_message
    from zerver.lib.markdown import version as markdown_version
    from zerver.lib.mention import MentionBackend, MentionData
    from zerver.lib.message import event_recipient_ids_for_action_on_messages, normalize_body
    from zerver.lib.message_cache import update_message_cache
    from zerver.lib.timestamp import datetime_to_timestamp
    from zerver.models import UserMessage
    from zerver.tornado.django_api import send_event_on_commit

    content = normalize_body(content)
    mention_data = MentionData(
        mention_backend=MentionBackend(job.realm_id), content=content, message_sender=message.sender
    )
    rendering_result = render_incoming_message(
        message, content, job.realm, mention_data=mention_data
    )
    message.content = content
    message.rendered_content = rendering_result.rendered_content
    message.rendered_content_version = markdown_version
    # render_incoming_message also set has_image/has_link on this object.
    message.save(
        update_fields=[
            "content",
            "rendered_content",
            "rendered_content_version",
            "has_image",
            "has_link",
        ]
    )
    update_message_cache([message])
    event = {
        "type": "update_message",
        "user_id": None,
        "edit_timestamp": datetime_to_timestamp(now()),
        "message_id": message.id,
        "message_ids": [message.id],
        "content": content,
        "rendered_content": rendering_result.rendered_content,
        "rendering_only": True,
    }
    flags = {
        um.user_profile_id: um.flags_list() for um in UserMessage.objects.filter(message=message.id)
    }
    # A subscriber with no UserMessage row (a channel with shared history)
    # still gets the edit, the same way do_update_message tells them.
    user_ids = event_recipient_ids_for_action_on_messages([message.id], message.is_channel_message)
    users = [{"id": user_id, "flags": flags.get(user_id, ["read"])} for user_id in sorted(user_ids)]
    send_event_on_commit(job.realm, event, users)


def _notify_requester(job: agents.AgentJob, message_id: int) -> None:
    """SD-06: drafts and card updates notify nobody; the finished result
    notifies its requester once, as a mention would. A message edit never
    sends push or email notifications, so queue them here."""
    from zerver.actions.message_send import filter_presence_idle_user_ids, get_recipient_info
    from zerver.lib.notification_data import UserMessageNotificationsData
    from zerver.lib.queue import mobile_notifications_queue_name, queue_event_on_commit
    from zerver.lib.stream_topic import StreamTopicTarget
    from zerver.models import UserMessage

    # The audience check before publication means the requester received
    # this message; publication runs once per job, so this runs once too.
    user_message = UserMessage.objects.select_for_update().get(
        message_id=message_id, user_profile_id=job.requester_id
    )
    user_message.flags |= UserMessage.flags.mentioned
    user_message.save(update_fields=["flags"])
    message = Message.objects.select_related("recipient").get(id=message_id)
    info = get_recipient_info(
        realm_id=job.realm_id,
        recipient=message.recipient,
        sender_id=message.sender_id,
        stream_topic=(
            StreamTopicTarget(stream_id=message.recipient.type_id, topic_name=message.topic_name())
            if message.is_channel_message
            else None
        ),
        possibly_mentioned_user_ids={job.requester_id},
        possible_topic_wildcard_mention=False,
        possible_stream_wildcard_mention=False,
    )
    data = UserMessageNotificationsData.from_user_id_sets(
        user_id=job.requester_id,
        flags=user_message.flags_list(),
        private_message=not message.is_channel_message,
        disable_external_notifications=False,
        online_push_user_ids=info.online_push_user_ids,
        dm_mention_push_disabled_user_ids=info.dm_mention_push_disabled_user_ids,
        dm_mention_email_disabled_user_ids=info.dm_mention_email_disabled_user_ids,
        stream_push_user_ids=info.stream_push_user_ids,
        stream_email_user_ids=info.stream_email_user_ids,
        topic_wildcard_mention_user_ids=info.topic_wildcard_mention_user_ids,
        stream_wildcard_mention_user_ids=info.stream_wildcard_mention_user_ids,
        followed_topic_push_user_ids=info.followed_topic_push_user_ids,
        followed_topic_email_user_ids=info.followed_topic_email_user_ids,
        topic_wildcard_mention_in_followed_topic_user_ids=info.topic_wildcard_mention_in_followed_topic_user_ids,
        stream_wildcard_mention_in_followed_topic_user_ids=info.stream_wildcard_mention_in_followed_topic_user_ids,
        muted_sender_user_ids=info.muted_sender_user_ids,
        all_bot_user_ids=info.all_bot_user_ids,
        push_device_registered_user_ids=info.push_device_registered_user_ids,
    )
    idle = job.requester_id in filter_presence_idle_user_ids({job.requester_id})
    sender_id = message.sender_id
    if data.is_push_notifiable(sender_id, idle):
        queue_event_on_commit(
            mobile_notifications_queue_name(job.requester_id),
            {
                "user_profile_id": job.requester_id,
                "message_id": message_id,
                "trigger": data.get_push_notification_trigger(sender_id, idle),
                "type": "add",
                "mentioned_user_group_id": None,
            },
        )
    if data.is_email_notifiable(sender_id, idle):
        queue_event_on_commit(
            "missedmessage_emails",
            {
                "user_profile_id": job.requester_id,
                "message_id": message_id,
                "trigger": data.get_email_notification_trigger(sender_id, idle),
                "mentioned_user_group_id": None,
            },
        )


def _plain_name(name: str) -> str:
    # A name like "[x](https://...)" must not become a link named "x" in
    # the fallback line. Zulip Markdown shows a backslash escape as typed,
    # so swap the square brackets instead.
    return name.replace("[", "(").replace("]", ")")


def _fallback_lines(job: agents.AgentJob) -> dict[str, str]:
    """The card message's text before any draft, one per card state, for
    clients that show only the message text (13-R7), in the agent language."""
    name = _plain_name(job.profile.name)
    with override_language(agent_language(job.realm_id)):
        return {
            "queued": _("{name} · Queued").format(name=name),
            "working": _("{name} · Working").format(name=name),
            "approval": _("{name} · Waiting for approval").format(name=name),
            "decision": _("{name} · Waiting for a decision").format(name=name),
            "done": _("{name} · Done").format(name=name),
            "cancelled": _("{name} · Cancelled").format(name=name),
            "stopped": _("{name} · Stopped").format(name=name),
        }


def _card_state(job: agents.AgentJob) -> str:
    if job.status == "queued":
        return "queued"
    if job.status == "waiting_for_approval":
        return "approval"
    if job.status == "waiting_for_input":
        return "decision"
    if job.status == "completed":
        return "done"
    if job.status == "cancelled" or (
        job.status == "cancel_requested" and job.stop_target == "cancelled"
    ):
        return "cancelled"
    if job.status in TERMINAL or job.status == "cancel_requested":
        return "stopped"
    return "working"


def _card_fallback(job: agents.AgentJob) -> str:
    return _fallback_lines(job)[_card_state(job)]


def _is_fallback_line(job: agents.AgentJob, content: str) -> bool:
    return content in _fallback_lines(job).values()


def _show_neutral_line(job: agents.AgentJob) -> None:
    """FL-22: the card's draft can hold text that the conversation's new
    audience never should see; replace it with one neutral line."""
    if job.result_message_id is None:
        return
    message = _lock_card_message(job.result_message_id)
    with override_language(agent_language(job.realm_id)):
        neutral = _("This task's answer is no longer shown here.")
    if message.content != neutral:
        _update_card_message(job, message, neutral)


def _card_content(job: agents.AgentJob, content: str) -> str:
    """The card message's text for the job's current state: the fallback
    line until the first draft, and no writing mark once the job stops
    (FL-21 adds a short note to a draft that a cancel stopped)."""
    if _is_fallback_line(job, content):
        return _card_fallback(job)
    if not content.endswith(DRAFT_WRITING_MARK) or (
        job.status not in TERMINAL and job.status != "cancel_requested"
    ):
        return content
    content = content.removesuffix(DRAFT_WRITING_MARK).rstrip()
    if _card_state(job) == "cancelled":
        with override_language(agent_language(job.realm_id)):
            note = _("Cancelled.")
        content = f"{content} {note}"
    return content


def send_card_typing(job_id: UUID, operator: str) -> None:
    """SD-01/SD-02: the bot's typing indicator in the card's conversation.
    A start is sent only while the card still shows its fallback line: the
    first draft ends the indicator."""
    from zerver.actions.typing import (
        do_send_stream_typing_notification,
        do_send_typing_notification,
    )
    from zerver.models import UserMessage

    job = agents.AgentJob.objects.select_related("profile__bot_user", "realm").get(id=job_id)
    if job.result_message_id is None:
        return
    message = Message.objects.select_related("recipient").get(id=job.result_message_id)
    if operator == "start" and not _is_fallback_line(job, message.content):
        return
    bot = job.profile.bot_user
    if message.is_channel_message:
        stream = Stream.objects.get(recipient_id=message.recipient_id)
        do_send_stream_typing_notification(bot, operator, stream, message.topic_name())
    else:
        user_ids = UserMessage.objects.filter(message=message).values_list(
            "user_profile_id", flat=True
        )
        recipients = list(UserProfile.objects.filter(id__in=user_ids))
        do_send_typing_notification(job.realm, bot, recipients, operator)


def post_admission_card(job_id: UUID, *, acknowledge: bool) -> None:
    """13-R3: the job's one card message (fallback text + the `agent_job`
    widget), created once at admission. For a mention or a direct message,
    13-R4 and SD-01 add the source message's acknowledgement reaction and,
    for a queued answer, the bot's typing indicator."""
    from zerver.actions.message_send import check_message, do_send_messages
    from zerver.lib.addressee import Addressee
    from zerver.models.clients import get_client

    with agent_transaction():
        # Lock the job so claim_work (skip_locked) sees the card once it
        # commits; a claim that commits first shows here as "running".
        job = (
            agents.AgentJob.objects.select_for_update(of=("self",))
            .select_related("profile__bot_user", "realm")
            .get(id=job_id)
        )
        if job.result_message_id is not None:
            return  # Already posted: a retried on_commit hook, or a replay.
        audience = require_audience(job)
        anchor = Message.objects.get(id=audience.anchor_message_id)
        addressee = (
            Addressee.for_stream_id(audience.stream_id, anchor.topic_name())
            if audience.stream_id is not None
            else Addressee.for_user_ids(audience.audience_user_ids, job.realm)
        )
        widget = {"widget_type": "agent_job", "extra_data": widget_card_data(job)}
        message = check_message(
            job.profile.bot_user,
            get_client("Grow Agent"),
            addressee,
            _card_fallback(job),
            realm=job.realm,
            no_previews=True,
            widget_content=json.dumps(widget),
        )
        job.result_message_id = do_send_messages([message])[0].message_id
        job.save(update_fields=["result_message"])
        if acknowledge and job.source_message_id is not None:
            react_after_commit(job.profile.bot_user, job.source_message_id, "eyes")
            if job.job_kind == "answer" and job.status == "queued":
                throttle(f"agent-typing:{job.id}", 10)
                typing_after_commit(job.id, "start")


def update_job_card(job_id: UUID) -> None:
    """Refresh the job's card from its current state (13-R6): always a fresh
    widget snapshot, never merged with the previous one, plus the fallback
    text and the writing mark (_card_content). The job is read only after
    the card's row lock, so the last writer always writes the newest state."""
    with agent_transaction():
        result_message_id = (
            agents.AgentJob.objects.only("result_message").get(id=job_id).result_message_id
        )
        if result_message_id is None:
            return
        # do_add_submessage asks for this lock too, so two refreshes that
        # commit close together cannot write their snapshots out of order.
        message = _lock_card_message(result_message_id)
        job = agents.AgentJob.objects.select_related("profile__bot_user", "realm").get(id=job_id)
        do_add_submessage(
            realm=job.realm,
            sender_id=job.profile.bot_user_id,
            message_id=message.id,
            msg_type="widget",
            content=json.dumps(widget_card_data(job)),
        )
        content = _card_content(job, message.content)
        if content != message.content:
            _update_card_message(job, message, content)
        if job.job_kind == "answer" and job.status not in {"queued", "running"}:
            typing_after_commit(job.id, "stop")
