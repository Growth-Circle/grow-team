"""Tests for the Perlu kamu inbox (spec 03), zerver/lib/needs.py and
zerver/views/needs.py, and for the agent approval rules it relies on in
zerver/actions/agent_approvals.py."""

import hashlib
import tempfile
from datetime import datetime, timedelta, timezone
from io import StringIO
from uuid import uuid4

import time_machine
from django.db.models import F
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
from zerver.actions.user_groups import check_add_user_group
from zerver.actions.users import do_change_user_role
from zerver.lib import agent_protocol as p
from zerver.lib import needs
from zerver.lib.agent_policy import AgentAccessDenied
from zerver.lib.agent_results import store_artifact
from zerver.lib.test_classes import ZulipTestCase
from zerver.models import Attachment, Message, RolePermission, UserMessage, UserProfile, agents
from zerver.models.needs import NeedResolution


class NeedsTestCase(ZulipTestCase):
    """Shared fixtures: an enabled chat profile (self.profile, "answer"
    mode) and a code-capable profile (self.code_profile, "code" mode with a
    repository) on the same runner, both owned by self.owner."""

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
            # One test holds an approval job and a decision job at once.
            capacity=2,
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
        self.profile = self._ready_profile(name="NeedsAnswer", default_mode="answer")
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

    def _pending_decision(
        self, question: str = "Continue without the check?", options: list[str] | None = None
    ) -> tuple[agents.AgentJob, agents.AgentAttempt]:
        message_id = self.send_stream_message(
            self.owner, "Denmark", "Please answer this", topic_name="needs-test"
        )
        message = Message.objects.get(id=message_id)
        job = actions.create_job(
            self.owner,
            profile=self.profile,
            source=message,
            request="Please answer",
            idempotency_key=uuid4(),
            job_kind="answer",
            delivery_target="answer",
        )
        actions.claim_work(self.runner, claim_key=uuid4())
        attempt = agents.AgentAttempt.objects.get(job=job)
        self._record_event(
            job,
            attempt,
            "input.requested",
            {"question": question, "options": options or ["Ya", "Tidak"]},
        )
        job.refresh_from_db()
        return job, attempt

    def _answer(self, job: agents.AgentJob, text: str) -> None:
        job.refresh_from_db()
        actions.add_input(
            self.owner,
            job.id,
            expected_version=job.version,
            client_key=uuid4(),
            text=text,
            input_type="answer",
        )

    def _mention(self, user: UserProfile, text: str, *, stream: str = "Denmark") -> int:
        self.subscribe(user, stream)
        return self.send_stream_message(
            self.owner, stream, f"@**{user.full_name}** {text}", topic_name="mentions"
        )

    def _mark_read(self, user: UserProfile, message_id: int) -> None:
        UserMessage.objects.filter(user_profile=user, message_id=message_id).update(
            flags=F("flags").bitor(UserMessage.flags.read)
        )

    def _open_ids(self, user: UserProfile, kind: str | None = None) -> list[str]:
        result = needs.list_needs(user, kind=kind, status="open", since=None)
        return [item["id"] for item in result["items"]]


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


class NeedsApprovalTests(NeedsTestCase):
    def test_approval_item_lists_for_the_requester(self) -> None:
        job, attempt, approval = self._pending_approval()
        result = needs.list_needs(self.owner, kind=None, status="open", since=None)
        self.assertEqual(result["counts"], {"approval": 1, "mention": 0, "decision": 0, "all": 1})
        [item] = result["items"]
        self.assertEqual(item["id"], f"approval:{approval.id}")
        self.assertEqual(item["kind"], "approval")
        self.assertEqual(item["actor"]["type"], "agent")
        self.assertEqual(item["actor"]["name"], "NeedsCode")
        self.assertEqual(item["actor"]["initials"], "NE")
        self.assertEqual(item["stream_id"], self.get_stream_id("Denmark"))
        self.assertEqual(item["topic"], "needs-test")
        self.assertEqual(item["text"], "Push the release branch")
        artifact = agents.AgentArtifact.objects.get(attempt=attempt, kind="diff")
        self.assertEqual(
            item["attachment"], {"kind": "diff", "id": str(artifact.id), "filename": "diff.patch"}
        )
        self.assertEqual(item["actions"], ["approve"])
        self.assertEqual(item["expires_at"], approval.expires_at.isoformat())
        self.assertFalse(item["expired"])
        self.assertIsNone(item["resolved_at"])
        self.assertIsNone(item["resolved_action"])
        self.assertEqual(item["job_id"], str(job.id))
        self.assertEqual(item["approval_version"], approval.version)
        self.assertEqual(item["operation_hash"], approval.operation_hash)
        self.assertEqual(item["nonce"], str(approval.nonce))
        self.assertIsNone(item["job_version"])

    def test_expired_approval_stays_listed_as_expired(self) -> None:
        _job, _attempt, approval = self._pending_approval()
        with time_machine.travel(approval.expires_at + timedelta(minutes=1), tick=False):
            result = needs.list_needs(self.owner, kind="approval", status="open", since=None)
        [item] = result["items"]
        self.assertTrue(item["expired"])

    def test_user_without_the_approve_permission_sees_no_approval(self) -> None:
        self._pending_approval()
        self._revoke_member_approve_permission()
        self.assertEqual(self._open_ids(self.owner), [])

        RolePermission.objects.filter(realm=self.owner.realm).delete()
        self.owner.role = UserProfile.ROLE_GUEST
        self.owner.save(update_fields=["role"])
        result = needs.list_needs(self.owner, kind=None, status="open", since=None)
        self.assertEqual(result["counts"], {"approval": 0, "mention": 0, "decision": 0, "all": 0})

    def test_user_without_agent_access_sees_no_approval_or_decision(self) -> None:
        self._pending_approval()
        self._pending_decision()
        self.assertEqual(len(self._open_ids(self.owner)), 2)
        cordelia = self.example_user("cordelia")
        self.subscribe(cordelia, "Denmark")
        result = needs.list_needs(cordelia, kind=None, status="open", since=None)
        self.assertEqual(result["counts"], {"approval": 0, "mention": 0, "decision": 0, "all": 0})

    def test_only_the_requester_sees_a_team_change_approval(self) -> None:
        do_change_user_role(
            self.owner, UserProfile.ROLE_REALM_ADMINISTRATOR, acting_user=None, notify=True
        )
        team_profile = self._ready_profile(
            name="NeedsTeam",
            default_mode="manage",
            capabilities={"tool_calling": "passed", "team_tools": "passed"},
        )
        iago = self.example_user("iago")
        agents.AgentGrant.objects.create(
            realm=self.owner.realm,
            owner=self.owner,
            principal_user=iago,
            target_kind="profile",
            actions=["profile.use", "context.read", "team.manage"],
            profile=team_profile,
        )
        agents.AgentGrant.objects.create(
            realm=self.owner.realm,
            owner=self.owner,
            principal_user=iago,
            target_kind="runner",
            actions=["runner.use"],
            runner=self.runner,
        )
        for user in [self.owner, iago, team_profile.bot_user]:
            self.subscribe(user, "agent-tasks")
        mention = f"@**{team_profile.bot_user.full_name}|{team_profile.bot_user_id}**"
        message_id = self.send_stream_message(self.owner, "agent-tasks", f"{mention} Please help")
        job = agents.AgentJob.objects.get(source_message_id=message_id)
        actions.claim_work(self.runner, claim_key=uuid4())
        attempt = agents.AgentAttempt.objects.get(job=job)
        channel = self.subscribe(self.owner, "team-updates")
        othello = self.example_user("othello")
        self.subscribe(othello, "team-updates")
        operation = approvals.propose_operation(
            self.runner,
            job.id,
            attempt.id,
            attempt.lease_epoch,
            operation_id=uuid4(),
            arguments={
                "action": "team.manage",
                "input": {
                    "tool": "channel.unsubscribe",
                    "channel_id": channel.id,
                    "user_ids": [othello.id],
                },
            },
            tree_hash=None,
            diff_artifact_id=None,
        )
        approval = agents.AgentApproval.objects.get(operation=operation)
        self.assertEqual(self._open_ids(iago, "approval"), [])
        self.assertEqual(self._open_ids(self.owner, "approval"), [f"approval:{approval.id}"])
        [item] = needs.list_needs(self.owner, kind="approval", status="open", since=None)["items"]
        self.assertIsNone(item["attachment"])

    def test_approved_approval_stays_resolved_after_the_runner_uses_it(self) -> None:
        job, attempt, approval = self._pending_approval()
        self._decide(approval)
        self._consume(job, attempt, approval)
        approval.refresh_from_db()
        self.assertEqual(approval.decision, "consumed")
        assert approval.decided_at is not None

        self.assertEqual(self._open_ids(self.owner, "approval"), [])
        result = needs.list_needs(self.owner, kind="approval", status="resolved", since="today")
        self.assertEqual(result["counts"]["approval"], 1)
        [item] = result["items"]
        self.assertEqual(item["id"], f"approval:{approval.id}")
        self.assertEqual(item["resolved_at"], approval.decided_at.isoformat())
        self.assertEqual(item["resolved_action"], "approved")
        self.assertIsNone(item["nonce"])

    def test_rejected_approval_lists_as_rejected(self) -> None:
        _job, _attempt, approval = self._pending_approval()
        self._decide(approval, decision="rejected")
        [item] = needs.list_needs(self.owner, kind="approval", status="resolved", since="today")[
            "items"
        ]
        self.assertEqual(item["resolved_action"], "rejected")


class NeedsDecisionTests(NeedsTestCase):
    def test_decision_item_lists_with_question_and_options(self) -> None:
        job, _attempt = self._pending_decision("Deploy to production?", ["Ya", "Tidak"])
        result = needs.list_needs(self.owner, kind="decision", status="open", since=None)
        self.assertEqual(result["counts"]["decision"], 1)
        [item] = result["items"]
        self.assertEqual(item["id"], f"decision:{job.id}")
        self.assertEqual(item["text"], "Deploy to production?")
        self.assertEqual(item["actions"], ["Ya", "Tidak"])
        self.assertFalse(item["expired"])
        self.assertEqual(item["job_id"], str(job.id))
        self.assertEqual(item["job_version"], job.version)
        self.assertIsNone(item["nonce"])
        event = agents.AgentAuditEvent.objects.get(job=job, type="input.requested")
        self.assertAlmostEqual(
            event.occurred_at + timedelta(hours=24),
            datetime.fromisoformat(item["expires_at"]),
            delta=timedelta(seconds=5),
        )

    def test_resolved_decision_shows_the_newest_answer_once(self) -> None:
        job, attempt = self._pending_decision("First question?", ["A", "B"])
        self._answer(job, "A")
        self._record_event(
            job, attempt, "input.requested", {"question": "Second question?", "options": ["C", "D"]}
        )
        self._answer(job, "D")

        self.assertEqual(self._open_ids(self.owner, "decision"), [])
        result = needs.list_needs(self.owner, kind="decision", status="resolved", since="today")
        [item] = result["items"]
        self.assertEqual(item["id"], f"decision:{job.id}")
        self.assertEqual(item["text"], "Second question?")
        self.assertEqual(item["resolved_action"], "D")
        answer = agents.AgentInput.objects.get(job=job, text="D")
        self.assertEqual(item["resolved_at"], answer.created_at.isoformat())


class NeedsMentionTests(NeedsTestCase):
    def test_unread_personal_mention_is_a_need(self) -> None:
        cordelia = self.example_user("cordelia")
        message_id = self._mention(cordelia, "can you review this?")
        result = needs.list_needs(cordelia, kind="mention", status="open", since=None)
        self.assertEqual(result["counts"]["mention"], 1)
        [item] = result["items"]
        self.assertEqual(item["id"], f"mention:{message_id}")
        self.assertEqual(item["actor"]["type"], "human")
        self.assertEqual(item["actor"]["id"], self.owner.id)
        self.assertEqual(item["actor"]["initials"], "KH")
        self.assertEqual(item["stream_id"], self.get_stream_id("Denmark"))
        self.assertEqual(item["topic"], "mentions")
        self.assertEqual(item["text"], f"@{cordelia.full_name} can you review this?")
        self.assertIsNone(item["attachment"])
        self.assertEqual(item["actions"], [])
        self.assertIsNone(item["job_id"])

    def test_group_wildcard_and_silent_mentions_are_not_needs(self) -> None:
        cordelia = self.example_user("cordelia")
        for user in [self.owner, cordelia]:
            self.subscribe(user, "needs-small")
        group = check_add_user_group(
            self.owner.realm, "needs-test-group", [cordelia], acting_user=self.owner
        )
        message_id = self.send_stream_message(
            self.owner,
            "needs-small",
            f"@*{group.name}* and @_**{cordelia.full_name}** please look at this",
            topic_name="mentions",
        )
        user_message = UserMessage.objects.get(user_profile=cordelia, message_id=message_id)
        self.assertTrue(user_message.flags.mentioned)
        for wildcard in ["@**all**", "@**topic**"]:
            self.send_stream_message(
                self.owner, "needs-small", f"{wildcard} heads up", topic_name="mentions"
            )

        result = needs.list_needs(cordelia, kind="mention", status="open", since=None)
        self.assertEqual(result["items"], [])
        self.assertEqual(result["counts"]["mention"], 0)
        resolved = needs.list_needs(cordelia, kind="mention", status="resolved", since="today")
        self.assertEqual(resolved["items"], [])

    def test_bot_message_is_not_a_need(self) -> None:
        cordelia = self.example_user("cordelia")
        bot = self.example_user("default_bot")
        for user in [bot, cordelia]:
            self.subscribe(user, "needs-small")
        self.send_stream_message(bot, "needs-small", f"@**{cordelia.full_name}** status update")
        self.assertEqual(self._open_ids(cordelia), [])

    def test_mention_older_than_seven_days_is_not_a_need(self) -> None:
        cordelia = self.example_user("cordelia")
        with time_machine.travel(now() - timedelta(days=8), tick=False):
            self._mention(cordelia, "an old question")
        self.assertEqual(self._open_ids(cordelia), [])

    def test_read_mention_becomes_a_need_again_after_two_hours_without_reply(self) -> None:
        cordelia = self.example_user("cordelia")
        start = now()
        with time_machine.travel(start, tick=False):
            message_id = self._mention(cordelia, "any update?")
            self._mark_read(cordelia, message_id)
        with time_machine.travel(start + timedelta(minutes=30), tick=False):
            self.assertEqual(self._open_ids(cordelia), [])
        with time_machine.travel(start + timedelta(hours=3), tick=False):
            self.assertEqual(self._open_ids(cordelia), [f"mention:{message_id}"])

    def test_reply_closes_a_mention_even_while_unread(self) -> None:
        cordelia = self.example_user("cordelia")
        # 10:00 in the workspace's Asia/Jakarta timezone, far from midnight.
        start = datetime(2026, 9, 25, 3, 0, tzinfo=timezone.utc)
        with time_machine.travel(start, tick=False):
            message_id = self._mention(cordelia, "any update?")
        reply_time = start + timedelta(minutes=10)
        with time_machine.travel(reply_time, tick=False):
            self.send_stream_message(cordelia, "Denmark", "On it", topic_name="mentions")
        user_message = UserMessage.objects.get(user_profile=cordelia, message_id=message_id)
        self.assertFalse(user_message.flags.read)

        with time_machine.travel(start + timedelta(minutes=20), tick=False):
            self.assertEqual(self._open_ids(cordelia), [])
            result = needs.list_needs(cordelia, kind="mention", status="resolved", since="today")
        [item] = result["items"]
        self.assertEqual(item["id"], f"mention:{message_id}")
        self.assertEqual(item["resolved_action"], "replied")
        self.assertEqual(item["resolved_at"], reply_time.isoformat())

    def test_one_on_one_direct_message_is_a_need_until_answered(self) -> None:
        cordelia = self.example_user("cordelia")
        message_id = self.send_personal_message(self.owner, cordelia, "Are you free at three?")
        [item] = needs.list_needs(cordelia, kind="mention", status="open", since=None)["items"]
        self.assertEqual(item["id"], f"mention:{message_id}")
        self.assertIsNone(item["stream_id"])
        self.assertIsNone(item["topic"])

        self.send_personal_message(cordelia, self.owner, "Yes")
        self.assertEqual(self._open_ids(cordelia), [])

    def test_mention_lists_its_file(self) -> None:
        cordelia = self.example_user("cordelia")
        self.login_user(self.owner)
        upload = StringIO("zulip!")
        upload.name = "notes.txt"
        url = self.assert_json_success(self.client_post("/json/user_uploads", {"file": upload}))[
            "url"
        ]
        message_id = self._mention(cordelia, f"see [notes.txt]({url})")
        attachment = Attachment.objects.get(messages__id=message_id)
        [item] = needs.list_needs(cordelia, kind="mention", status="open", since=None)["items"]
        self.assertEqual(
            item["attachment"], {"kind": "file", "id": str(attachment.id), "filename": "notes.txt"}
        )

    def test_resolve_and_unresolve_mention(self) -> None:
        cordelia = self.example_user("cordelia")
        message_id = self._mention(cordelia, "ping")
        needs.resolve_mention(cordelia, message_id)
        self.assertEqual(self._open_ids(cordelia), [])
        resolution = NeedResolution.objects.get(user=cordelia, message_id=message_id)
        [item] = needs.list_needs(cordelia, kind="mention", status="resolved", since="today")[
            "items"
        ]
        self.assertEqual(item["id"], f"mention:{message_id}")
        self.assertEqual(item["resolved_action"], "done")
        self.assertEqual(item["resolved_at"], resolution.resolved_at.isoformat())

        needs.unresolve_mention(cordelia, message_id)
        self.assertEqual(self._open_ids(cordelia), [f"mention:{message_id}"])

    def test_resolve_rejects_a_message_that_is_not_a_need(self) -> None:
        cordelia = self.example_user("cordelia")
        self.subscribe(cordelia, "Denmark")
        message_id = self.send_stream_message(
            self.owner, "Denmark", "No mention here", topic_name="mentions"
        )
        with self.assertRaises(ValueError):
            needs.resolve_mention(cordelia, message_id)
        self.assertFalse(NeedResolution.objects.filter(user=cordelia).exists())

    def test_resolved_mention_drops_out_once_the_user_loses_the_message(self) -> None:
        cordelia = self.example_user("cordelia")
        message_id = self._mention(cordelia, "ping")
        needs.resolve_mention(cordelia, message_id)
        # A move to a channel cordelia cannot see removes her UserMessage row.
        UserMessage.objects.filter(user_profile=cordelia, message_id=message_id).delete()
        resolved = needs.list_needs(cordelia, kind="mention", status="resolved", since="today")
        self.assertEqual(resolved["items"], [])

    def test_today_starts_at_midnight_in_the_workspace_timezone(self) -> None:
        cordelia = self.example_user("cordelia")
        settings = agents.AgentRealmSettings.objects.get(realm=self.owner.realm)
        self.assertEqual(settings.timezone, "Asia/Jakarta")
        with time_machine.travel(datetime(2026, 9, 25, 10, 0, tzinfo=timezone.utc), tick=False):
            yesterday_id = self._mention(cordelia, "first")
            today_id = self._mention(cordelia, "second")
        # 23:30 on 25 September in Jakarta, then 00:30 on 26 September.
        with time_machine.travel(datetime(2026, 9, 25, 16, 30, tzinfo=timezone.utc), tick=False):
            needs.resolve_mention(cordelia, yesterday_id)
        with time_machine.travel(datetime(2026, 9, 25, 17, 30, tzinfo=timezone.utc), tick=False):
            needs.resolve_mention(cordelia, today_id)
        with time_machine.travel(datetime(2026, 9, 25, 18, 0, tzinfo=timezone.utc), tick=False):
            result = needs.list_needs(cordelia, kind="mention", status="resolved", since="today")
        self.assertEqual([item["id"] for item in result["items"]], [f"mention:{today_id}"])

    def test_list_needs_rejects_invalid_arguments(self) -> None:
        for kind, status, since in [
            ("task", "open", None),
            (None, "done", None),
            (None, "resolved", "yesterday"),
        ]:
            with self.assertRaises(ValueError):
                needs.list_needs(self.owner, kind=kind, status=status, since=since)


class NeedsViewTests(NeedsTestCase):
    def test_list_needs_endpoint_returns_an_approval(self) -> None:
        self._pending_approval()
        self.login_user(self.owner)
        payload = self.assert_json_success(self.client_get("/json/needs"))
        self.assertEqual(payload["counts"]["approval"], 1)
        self.assertEqual(len(payload["items"]), 1)

    def test_list_needs_endpoint_returns_a_decision_and_a_mention(self) -> None:
        self._pending_decision()
        cordelia = self.example_user("cordelia")
        self.subscribe(cordelia, "Denmark")
        message_id = self.send_stream_message(
            cordelia, "Denmark", f"@**{self.owner.full_name}** a question", topic_name="mentions"
        )
        self.login_user(self.owner)
        payload = self.assert_json_success(self.client_get("/json/needs"))
        self.assertEqual(payload["counts"], {"approval": 0, "mention": 1, "decision": 1, "all": 2})
        self.assertEqual({item["kind"] for item in payload["items"]}, {"decision", "mention"})

        self.assert_json_success(self.client_post(f"/json/needs/mentions/{message_id}/resolve"))
        payload = self.assert_json_success(
            self.client_get("/json/needs", {"status": "resolved", "since": "today"})
        )
        self.assertEqual([item["id"] for item in payload["items"]], [f"mention:{message_id}"])

    def test_list_needs_endpoint_rejects_invalid_parameters(self) -> None:
        self.login_user(self.owner)
        for params in [{"kind": "task"}, {"status": "done"}, {"since": "yesterday"}]:
            result = self.client_get("/json/needs", params)
            self.assert_json_error(result, "Agent request rejected.")

    def test_resolve_mention_endpoint_round_trip(self) -> None:
        cordelia = self.example_user("cordelia")
        message_id = self._mention(cordelia, "ping")
        self.login_user(cordelia)
        self.assert_json_success(self.client_post(f"/json/needs/mentions/{message_id}/resolve"))
        self.assertEqual(self._open_ids(cordelia), [])

        self.assert_json_success(self.client_delete(f"/json/needs/mentions/{message_id}/resolve"))
        self.assertEqual(self._open_ids(cordelia), [f"mention:{message_id}"])

    def test_resolve_mention_endpoint_rejects_a_message_the_user_cannot_see(self) -> None:
        cordelia = self.example_user("cordelia")
        self.make_stream("needs-private", invite_only=True)
        self.subscribe(self.owner, "needs-private")
        message_id = self.send_stream_message(
            self.owner, "needs-private", f"@**{cordelia.full_name}** secret"
        )
        self.login_user(cordelia)
        result = self.client_post(f"/json/needs/mentions/{message_id}/resolve")
        self.assert_json_error(result, "Agent request rejected.")
        self.assertFalse(NeedResolution.objects.filter(user=cordelia).exists())
