"""Evidence tests for release 23 EX acceptance criteria.

Each test proves one criterion of
internals/docs/spec/2026-09-22-agent-execution-context-skills-and-mcp.md
that the release audit marked "code_done_needs_evidence": the behavior
already exists, but no automated test asserted it before this file. Only
criteria a Django backend test can prove without a real model provider, a
browser, a second machine, or a container are covered here.
"""

import hashlib
import tempfile
from datetime import timedelta
from uuid import uuid4

from django.test import override_settings
from django.utils.timezone import now
from typing_extensions import override

from zerver.actions.agent_jobs import cancel_job, claim_work, create_job, record_event, resume_job
from zerver.actions.agents import (
    _default_policy,
    create_profile,
    enable_profile,
    record_readiness,
    register_repository,
)
from zerver.lib import agent_protocol as p
from zerver.lib import agent_results as results
from zerver.lib.agent_context import selected_context
from zerver.lib.exceptions import JsonableError
from zerver.lib.test_classes import ZulipTestCase
from zerver.models import Message, agents


def _catalog_report() -> dict[str, object]:
    return {
        "revision": 1,
        "adapters": [
            {
                "id": "acp",
                "version": "1",
                "auth_state": "ready",
                "capabilities": {"config_version": 1},
            }
        ],
        "sandboxes": [
            {
                "alias": "default",
                "image_digest": "sha256:" + "a" * 64,
                "toolchain_digest": "b" * 64,
                "catalog_revision": 1,
                "cpu_millicores": 100,
                "memory_bytes": 67108864,
                "pids_limit": 16,
                "temporary_bytes": 1048576,
            }
        ],
    }


class AgentEvidenceEXTests(ZulipTestCase):
    @override
    def setUp(self) -> None:
        super().setUp()
        self.owner = self.example_user("hamlet")
        agents.AgentRealmSettings.objects.update_or_create(
            realm=self.owner.realm, defaults={"enabled": True}
        )
        self.runner = agents.AgentRunner.objects.create(
            realm=self.owner.realm,
            owner=self.owner,
            name="Evidence runner",
            fingerprint="f" * 64,
            catalog_report=_catalog_report(),
        )

    def ready_profile(
        self,
        *,
        name: str = "Evidence agent",
        code: bool = False,
        repository: agents.AgentRepository | None = None,
    ) -> agents.AgentProfile:
        policy = None
        if code:
            policy = _default_policy(self.owner, self.runner)
            policy["actions"] = [
                "context.read",
                "repository.read",
                "repository.edit",
                "checks.run",
            ]
        profile = create_profile(
            self.owner,
            name=name,
            runner=self.runner,
            adapter_id="acp",
            adapter_version="1",
            default_mode="code" if code else "answer",
            repository=repository,
            policy=policy,
            idempotency_key=uuid4(),
        )
        setup = agents.AgentSetupOperation.objects.get(profile=profile)
        record_readiness(
            self.runner,
            setup,
            {
                "schema_version": 1,
                "profile_id": str(profile.id),
                "profile_revision": profile.revision,
                "runner_id": str(self.runner.id),
                "descriptor_digest": setup.descriptor_digest,
                "configuration_digest": setup.configuration_digest,
                "state": "ready",
                "capabilities": {
                    "chat_ready": True,
                    "code_ready": code,
                    "tool_calling": "passed" if code else "unknown",
                    "sandbox": "passed" if code else "unknown",
                    "config_version": 1,
                },
            },
        )
        profile.refresh_from_db()
        return enable_profile(self.owner, profile, expected_revision=profile.revision)

    def test_ex_04_oldest_ineligible_job_does_not_block_a_younger_eligible_one(self) -> None:
        """EX-04: claim_work skips a stale job instead of stalling the runner.

        proof_needed: three queued jobs, oldest to newest. The oldest job's
        profile turns not-ready after admission. The runner must claim the
        oldest *eligible* job (the middle one), and the ineligible and
        newer jobs must stay queued with no attempt.
        """
        blocked_profile = self.ready_profile(name="EX-04 stale")
        eligible_profile = self.ready_profile(name="EX-04 eligible")
        newest_profile = self.ready_profile(name="EX-04 newest")
        for profile in (blocked_profile, eligible_profile, newest_profile):
            self.subscribe(profile.bot_user, "Denmark")

        def job_for(profile: agents.AgentProfile, text: str) -> agents.AgentJob:
            message_id = self.send_stream_message(self.owner, "Denmark", text)
            return create_job(
                self.owner,
                profile=profile,
                source=Message.objects.get(id=message_id),
                request=text,
                idempotency_key=uuid4(),
                job_kind="answer",
                delivery_target="answer",
            )

        oldest = job_for(blocked_profile, "Oldest, but about to go stale")
        middle = job_for(eligible_profile, "Middle, eligible")
        newest = job_for(newest_profile, "Newest, eligible")
        base = now()
        agents.AgentJob.objects.filter(id=oldest.id).update(created_at=base)
        agents.AgentJob.objects.filter(id=middle.id).update(created_at=base + timedelta(seconds=1))
        agents.AgentJob.objects.filter(id=newest.id).update(created_at=base + timedelta(seconds=2))
        # The oldest job's profile becomes stale only after admission, the
        # same way an edited credential resets readiness mid-queue.
        agents.AgentProfile.objects.filter(id=blocked_profile.id).update(readiness_revision=None)

        claimed = claim_work(self.runner, claim_key=uuid4())
        self.assertIsNotNone(claimed)
        assert claimed is not None
        self.assertEqual(claimed["profile_id"], str(eligible_profile.id))
        oldest.refresh_from_db()
        newest.refresh_from_db()
        self.assertEqual(oldest.status, "queued")
        self.assertEqual(newest.status, "queued")
        self.assertFalse(agents.AgentAttempt.objects.filter(job=oldest).exists())
        self.assertFalse(agents.AgentAttempt.objects.filter(job=newest).exists())
        self.assertTrue(agents.AgentAttempt.objects.filter(job=middle, active=True).exists())

    def test_ex_23_a_required_check_with_no_verification_still_blocks_publication(self) -> None:
        """EX-23: a required check that never ran keeps blocking the result.

        proof_needed: if an agent deleted a required check's test, the check
        never produces an AgentVerification row for the final tree. The
        check stays required regardless: publish_result must still refuse
        to complete the job.
        """
        repository = register_repository(
            self.owner,
            self.runner,
            workspace_alias="ex23",
            canonical_origin="https://example.com/team/repo",
            allowed_refs=["main"],
            required_checks=[{"id": "required", "argv": ["true"]}],
        )
        profile = self.ready_profile(name="EX-23 code agent", code=True, repository=repository)
        self.subscribe(profile.bot_user, "Denmark")
        message_id = self.send_stream_message(self.owner, "Denmark", "Fix it")
        job = create_job(
            self.owner,
            profile=profile,
            source=Message.objects.get(id=message_id),
            request="Fix it",
            idempotency_key=uuid4(),
            job_kind="code",
            delivery_target="patch",
            repository=repository,
            base_ref="main",
        )
        claim_work(self.runner, claim_key=uuid4())
        attempt = agents.AgentAttempt.objects.get(job=job)
        sequence = 0

        def event(kind: str, payload: dict[str, object]) -> None:
            nonlocal sequence
            sequence += 1
            record_event(
                self.runner,
                p.RunnerEvent.model_validate(
                    {
                        "schema_version": 1,
                        "job_id": str(job.id),
                        "attempt_id": str(attempt.id),
                        "lease_epoch": 1,
                        "sequence": sequence,
                        "event_id": str(uuid4()),
                        "occurred_at": now().isoformat(),
                        "type": kind,
                        "payload": payload,
                    }
                ),
            )

        event(
            "workspace.prepared",
            {
                "repository_id": str(repository.id),
                "workspace_reference": "fixture",
                "base_ref": "main",
                "base_commit": "a" * 40,
                "tree_hash": "b" * 40,
                "user_worktree_dirty": False,
            },
        )
        event("attempt.started", {"process_state": "active"})
        with (
            tempfile.TemporaryDirectory() as directory,
            override_settings(AGENT_ARTIFACT_ROOT=directory),
        ):
            artifacts = [
                results.store_artifact(
                    self.runner,
                    job.id,
                    attempt.id,
                    1,
                    chunks=[kind.encode()],
                    checksum=hashlib.sha256(kind.encode()).hexdigest(),
                    kind=kind,
                    filename=kind + ".txt",
                    media_type="text/plain",
                )
                for kind in ["summary", "diff"]
            ]
            event(
                "result.prepared",
                {
                    "artifact_ids": [str(item.id) for item in artifacts],
                    "tree_hash": "b" * 40,
                    "summary": "Done. The check's test file no longer exists.",
                },
            )
            event("attempt.stopped", {"process_state": "stopped", "stop_confirmed": True})
            self.assertFalse(agents.AgentVerification.objects.filter(attempt=attempt).exists())
            with self.assertRaisesRegex(ValueError, "Required checks"):
                results.publish_result(job.id)
        self.assertIsNone(agents.AgentJob.objects.get(id=job.id).completed_at)

    def test_ex_37_revoked_context_source_access_blocks_reading_it_after_resume(self) -> None:
        """EX-37: resuming a job never restores stale access to its context.

        proof_needed: revoke the bot's access to a private context source,
        resume the job (which does not recheck each context reference), and
        confirm reading that context is denied by a fresh, uncached check.
        """
        profile = self.ready_profile(name="EX-37 agent")
        third = self.example_user("cordelia")
        # The anchor is a group direct message (three people, no mention),
        # which does not auto-admit on its own. Its audience matches the
        # private context channel's three subscribers exactly, so
        # attaching that channel's message as context is allowed at
        # creation time. Revoking only the bot's channel subscription
        # later narrows the context source's access without touching the
        # group direct message.
        self.make_stream("ex-37-secret", realm=self.owner.realm, invite_only=True)
        for user in (self.owner, profile.bot_user, third):
            self.subscribe(user, "ex-37-secret")
        secret_message_id = self.send_stream_message(self.owner, "ex-37-secret", "Private context")
        anchor_message_id = self.send_group_direct_message(
            self.owner, [profile.bot_user, third], "Please look at that"
        )
        job = create_job(
            self.owner,
            profile=profile,
            source=Message.objects.get(id=anchor_message_id),
            request="Please look at that",
            idempotency_key=uuid4(),
            job_kind="answer",
            delivery_target="answer",
            context_message_ids=[secret_message_id],
        )
        claimed = claim_work(self.runner, claim_key=uuid4())
        self.assertIsNotNone(claimed)
        attempt = agents.AgentAttempt.objects.get(job=job)
        job.refresh_from_db()
        cancel_job(self.owner, job.id, job.version)
        record_event(
            self.runner,
            p.RunnerEvent.model_validate(
                {
                    "schema_version": 1,
                    "job_id": str(job.id),
                    "attempt_id": str(attempt.id),
                    "lease_epoch": 1,
                    "event_id": str(uuid4()),
                    "sequence": 1,
                    "occurred_at": now().isoformat(),
                    "type": "attempt.stopped",
                    "payload": {"process_state": "stopped", "stop_confirmed": True},
                }
            ),
        )
        job.refresh_from_db()
        self.assertEqual(job.status, "cancelled")

        # The profile owner keeps the profile grant, but the source channel
        # that supplied this one context reference revokes the bot.
        self.unsubscribe(profile.bot_user, "ex-37-secret")

        resumed = resume_job(self.owner, job.id, job.version)
        self.assertEqual(resumed.status, "queued")

        ref = agents.AgentContextRef.objects.get(
            job=job, kind="message", message_id=secret_message_id
        )
        with self.assertRaises((JsonableError, ValueError)):
            selected_context(job, [ref.id])
