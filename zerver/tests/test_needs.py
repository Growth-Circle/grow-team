"""Tests for the agent approval rules that the Perlu kamu inbox (spec 03)
relies on, in zerver/actions/agent_approvals.py."""

import hashlib
import tempfile
from datetime import datetime, timedelta
from uuid import uuid4

import time_machine
from django.test import override_settings
from django.utils.timezone import now
from typing_extensions import override

from zerver.actions import agent_approvals as approvals
from zerver.actions import agent_jobs as actions
from zerver.actions.agents import (
    _default_policy,
    create_profile,
    enable_profile,
    record_readiness,
    register_repository,
)
from zerver.lib import agent_protocol as p
from zerver.lib.agent_policy import AgentAccessDenied
from zerver.lib.agent_results import store_artifact
from zerver.lib.test_classes import ZulipTestCase
from zerver.models import Message, RolePermission, UserProfile, agents


class NeedsTestCase(ZulipTestCase):
    """Shared fixtures: a code-capable profile (self.code_profile, "code"
    mode with a repository) on a runner, both owned by self.owner."""

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
            name="NeedsRunner",
            fingerprint="f" * 64,
            catalog_report={
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
            },
        )
        self.repository = register_repository(
            self.owner,
            self.runner,
            workspace_alias="needscode",
            canonical_origin="https://example.com/team/needsrepo",
            allowed_refs=["main"],
            required_checks=[{"id": "required", "argv": ["true"]}],
        )
        self.code_profile = self._ready_profile(
            name="NeedsCode",
            default_mode="code",
            repository=self.repository,
            policy={
                **_default_policy(self.owner, self.runner),
                "actions": [
                    "context.read",
                    "repository.read",
                    "repository.edit",
                    "checks.run",
                    "git.push",
                ],
            },
            capabilities={"code_ready": True, "tool_calling": "passed", "sandbox": "passed"},
        )
        # Sequential tests own an outer transaction; acquire its guard before
        # assertions (same hack as AgentLifecycleTests.setUp).
        from time import sleep

        from zerver.lib.agent_context import AgentBusy, agent_transaction

        for retry in range(20):
            try:
                with agent_transaction():
                    pass
                break
            except AgentBusy:
                if retry == 19:
                    raise
                sleep(0.1)

    def _ready_profile(
        self,
        *,
        name: str,
        default_mode: str,
        repository: agents.AgentRepository | None = None,
        policy: dict[str, object] | None = None,
        capabilities: dict[str, object] | None = None,
    ) -> agents.AgentProfile:
        profile = create_profile(
            self.owner,
            name=name,
            runner=self.runner,
            adapter_id="acp",
            adapter_version="1",
            repository=repository,
            policy=policy,
            default_mode=default_mode,
            idempotency_key=uuid4(),
        )
        setup = agents.AgentSetupOperation.objects.get(profile=profile)
        record_readiness(
            self.runner,
            setup,
            {
                "schema_version": 1,
                "profile_id": str(profile.id),
                "profile_revision": 1,
                "runner_id": str(self.runner.id),
                "descriptor_digest": setup.descriptor_digest,
                "configuration_digest": setup.configuration_digest,
                "state": "ready",
                "capabilities": {"chat_ready": True, "config_version": 1, **(capabilities or {})},
            },
        )
        profile.refresh_from_db()
        enable_profile(self.owner, profile, expected_revision=profile.revision)
        return profile

    def _record_event(
        self,
        job: agents.AgentJob,
        attempt: agents.AgentAttempt,
        kind: str,
        payload: dict[str, object],
    ) -> None:
        # event.sequence must be exactly attempt.event_cursor + 1 (agent_jobs.py
        # record_event "Event sequence gap"), and the cursor only moves via
        # record_event itself, so re-read it fresh before every call.
        attempt.refresh_from_db()
        actions.record_event(
            self.runner,
            p.RunnerEvent.model_validate(
                {
                    "schema_version": 1,
                    "job_id": str(job.id),
                    "attempt_id": str(attempt.id),
                    "lease_epoch": attempt.lease_epoch,
                    "sequence": attempt.event_cursor + 1,
                    "event_id": str(uuid4()),
                    "occurred_at": now().isoformat(),
                    "type": kind,
                    "payload": payload,
                }
            ),
        )

    def _pending_approval(
        self,
    ) -> tuple[agents.AgentJob, agents.AgentAttempt, agents.AgentApproval]:
        """A waiting_for_approval job/attempt/approval via a real git.push
        proposal, the same path propose_operation's other callers use."""
        message_id = self.send_stream_message(
            self.owner, "Denmark", "Please push the release branch", topic_name="needs-test"
        )
        message = Message.objects.get(id=message_id)
        job = actions.create_job(
            self.owner,
            profile=self.code_profile,
            source=message,
            request="Push the release branch",
            idempotency_key=uuid4(),
            job_kind="code",
            delivery_target="patch",
            repository=self.repository,
            base_ref="main",
        )
        actions.claim_work(self.runner, claim_key=uuid4())
        attempt = agents.AgentAttempt.objects.get(job=job)
        with (
            tempfile.TemporaryDirectory() as directory,
            override_settings(AGENT_ARTIFACT_ROOT=directory),
        ):
            self._record_event(
                job,
                attempt,
                "workspace.prepared",
                {
                    "repository_id": str(self.repository.id),
                    "workspace_reference": "fixture",
                    "base_ref": "main",
                    "base_commit": "a" * 40,
                    "tree_hash": "b" * 40,
                    "user_worktree_dirty": False,
                },
            )
            self._record_event(job, attempt, "attempt.started", {"process_state": "active"})
            content = b"diff"
            artifact = store_artifact(
                self.runner,
                job.id,
                attempt.id,
                1,
                chunks=[content],
                checksum=hashlib.sha256(content).hexdigest(),
                kind="diff",
                filename="diff.patch",
                media_type="text/x-diff",
            )
            with self.captureOnCommitCallbacks(execute=True):
                operation = approvals.propose_operation(
                    self.runner,
                    job.id,
                    attempt.id,
                    1,
                    operation_id=uuid4(),
                    arguments={
                        "action": "git.push",
                        "repository_id": str(self.repository.id),
                        "remote": self.repository.canonical_origin,
                        "branch": f"grow-agent/{job.id}/result",
                        "commit": "c" * 40,
                        "expected_remote_head": None,
                    },
                    tree_hash="b" * 40,
                    diff_artifact_id=artifact.id,
                )
        job.refresh_from_db()
        attempt.refresh_from_db()
        approval = agents.AgentApproval.objects.get(operation=operation)
        return job, attempt, approval

    def _decide(
        self,
        approval: agents.AgentApproval,
        *,
        actor: UserProfile | None = None,
        decision: str = "approved",
    ) -> None:
        approvals.decide_approval(
            actor or self.owner,
            approval.id,
            expected_version=approval.version,
            operation_hash=approval.operation_hash,
            nonce=approval.nonce,
            decision=decision,
        )

    def _consume(
        self, job: agents.AgentJob, attempt: agents.AgentAttempt, approval: agents.AgentApproval
    ) -> None:
        operation = agents.AgentOperation.objects.get(id=approval.operation_id)
        approvals.consume_operation(
            self.runner,
            job.id,
            attempt.id,
            attempt.lease_epoch,
            operation_id=operation.operation_id,
            expected_version=operation.version,
            operation_hash=operation.argument_digest,
            nonce=approval.nonce,
        )

    def _budget_deadline(self, attempt: agents.AgentAttempt) -> datetime:
        return attempt.created_at + timedelta(
            seconds=attempt.descriptor["budget"]["active_seconds"]
        )

    def _revoke_member_approve_permission(self) -> None:
        RolePermission.objects.create(
            realm=self.owner.realm,
            permission_key="approve",
            role=UserProfile.ROLE_MEMBER,
            allowed=False,
        )


class ApprovalRuleTests(NeedsTestCase):
    def test_short_approval_ttl_sets_the_expiry(self) -> None:
        agents.AgentRealmSettings.objects.filter(realm=self.owner.realm).update(
            approval_ttl_minutes=30
        )
        _job, attempt, approval = self._pending_approval()
        self.assertAlmostEqual(
            approval.expires_at,
            approval.created_at + timedelta(minutes=30),
            delta=timedelta(seconds=5),
        )
        self.assertLess(approval.expires_at, self._budget_deadline(attempt))

    def test_approval_never_outlives_its_attempt(self) -> None:
        """The attempt stops at its active_seconds budget however fresh its
        lease is. With the default 120-minute TTL, that budget (60 minutes)
        sets the expiry, and the approval can be decided until then."""
        job, attempt, approval = self._pending_approval()
        settings = agents.AgentRealmSettings.objects.get(realm=self.owner.realm)
        self.assertEqual(settings.approval_ttl_minutes, 120)
        deadline = self._budget_deadline(attempt)
        self.assertEqual(approval.expires_at, deadline)

        # Give the attempt a lease past the deadline, as if the runner kept
        # sending heartbeats, so only the expiry can stop the decision.
        agents.AgentAttempt.objects.filter(id=attempt.id).update(
            lease_expires_at=deadline + timedelta(minutes=5)
        )
        with (
            time_machine.travel(deadline + timedelta(seconds=1), tick=False),
            self.assertRaises(ValueError),
        ):
            self._decide(approval)
        with time_machine.travel(deadline - timedelta(minutes=1), tick=False):
            self._decide(approval)
        job.refresh_from_db()
        self.assertEqual(job.status, "running")

    def test_decide_approval_rejects_guest(self) -> None:
        _job, _attempt, approval = self._pending_approval()
        self.owner.role = UserProfile.ROLE_GUEST
        self.owner.save(update_fields=["role"])
        with self.assertRaises(AgentAccessDenied):
            self._decide(approval)
        approval.refresh_from_db()
        self.assertEqual(approval.decision, "pending")

    def test_consume_rejects_an_approver_without_the_approve_permission(self) -> None:
        job, attempt, approval = self._pending_approval()
        self._decide(approval)
        self._revoke_member_approve_permission()
        with self.assertRaisesRegex(ValueError, "Approval is unavailable."):
            self._consume(job, attempt, approval)
        approval.refresh_from_db()
        self.assertEqual(approval.decision, "approved")
