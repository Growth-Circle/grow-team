"""Tests for the Perlu kamu inbox (spec 03), zerver/lib/needs.py and
zerver/views/needs.py, and for the agent approval rules it relies on in
zerver/actions/agent_approvals.py."""

import hashlib
import tempfile
from datetime import datetime, timedelta, timezone
from io import StringIO
from typing import TYPE_CHECKING
from uuid import uuid4

import orjson
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
from zerver.actions.tasks import do_add_task_note, do_create_task, do_update_task
from zerver.actions.user_groups import check_add_user_group
from zerver.actions.users import do_change_user_role
from zerver.lib import agent_protocol as p
from zerver.lib import needs
from zerver.lib.agent_policy import AgentAccessDenied
from zerver.lib.agent_results import store_artifact
from zerver.lib.mcp_pending import request_approval
from zerver.lib.tasks import get_or_create_default_board
from zerver.lib.test_classes import ZulipTestCase
from zerver.models import (
    Attachment,
    MCPAccessGrant,
    MCPPendingCall,
    Message,
    RolePermission,
    Task,
    TaskNotification,
    UserMessage,
    UserProfile,
    agents,
)
from zerver.models.needs import NeedResolution, NeedSnooze

if TYPE_CHECKING:
    from django.test.client import _MonkeyPatchedWSGIResponse as TestHttpResponse

# The counts of the task card kinds, for a test that creates no card item.
NO_CARDS = {"card_mention": 0, "card_assigned": 0}


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
        self.assertEqual(
            result["counts"], {**NO_CARDS, "approval": 1, "mention": 0, "decision": 0, "all": 1}
        )
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
        self.assertEqual(
            result["counts"], {**NO_CARDS, "approval": 0, "mention": 0, "decision": 0, "all": 0}
        )

    def test_user_without_agent_access_sees_no_approval_or_decision(self) -> None:
        self._pending_approval()
        self._pending_decision()
        self.assertEqual(len(self._open_ids(self.owner)), 2)
        cordelia = self.example_user("cordelia")
        self.subscribe(cordelia, "Denmark")
        result = needs.list_needs(cordelia, kind=None, status="open", since=None)
        self.assertEqual(
            result["counts"], {**NO_CARDS, "approval": 0, "mention": 0, "decision": 0, "all": 0}
        )

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
        self.assertEqual(item["text"], "can you review this?")
        self.assertEqual(item["raw_text"], f"@{cordelia.full_name} can you review this?")
        self.assertIsNone(item["attachment"])
        self.assertEqual(item["actions"], [])
        self.assertIsNone(item["job_id"])

    def _mention_text_of(self, user: UserProfile, content: str) -> tuple[str, str]:
        for name in ["needs-small"]:
            for subscriber in [self.owner, user]:
                self.subscribe(subscriber, name)
        message_id = self.send_stream_message(self.owner, "needs-small", content, topic_name="text")
        [item] = [
            item
            for item in needs.list_needs(user, kind="mention", status="open", since=None)["items"]
            if item["id"] == f"mention:{message_id}"
        ]
        return item["text"], item["raw_text"]

    def test_excerpt_drops_leading_mentions_and_keeps_the_first_sentence(self) -> None:
        cordelia = self.example_user("cordelia")
        othello = self.example_user("othello")
        iago = self.example_user("iago")
        mentions = " ".join(f"@**{user.full_name}**" for user in [cordelia, othello, iago])

        text, raw_text = self._mention_text_of(
            cordelia, f"{mentions}, Rekap harian 17 Okt. Ada tiga hal yang perlu dicek."
        )
        self.assertEqual(text, "Rekap harian 17 Okt.")
        self.assertTrue(raw_text.startswith(f"@{cordelia.full_name} @{othello.full_name}"))
        self.assertIn("Ada tiga hal", raw_text)

        # A mention that is not at the start stays in the sentence.
        text, _raw = self._mention_text_of(
            cordelia, f"@**{cordelia.full_name}** can you ask @**{othello.full_name}**? Thanks"
        )
        self.assertEqual(text, f"can you ask @{othello.full_name}?")

        # Without a sentence end, the text is cut at the line break.
        text, _raw = self._mention_text_of(
            cordelia, f"@**{cordelia.full_name}** first line\n\nsecond line"
        )
        self.assertEqual(text, "first line")

    def test_excerpt_of_a_message_with_only_mentions_keeps_them(self) -> None:
        cordelia = self.example_user("cordelia")
        text, raw_text = self._mention_text_of(cordelia, f"@**{cordelia.full_name}**")
        self.assertEqual(text, f"@{cordelia.full_name}")
        self.assertEqual(raw_text, text)

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

    def _broadcast_of(self, user: UserProfile, message_id: int) -> bool:
        [item] = [
            item
            for item in needs.list_needs(user, kind="mention", status="open", since=None)["items"]
            if item["id"] == f"mention:{message_id}"
        ]
        return item["broadcast"]

    def test_mention_of_a_few_people_is_personal(self) -> None:
        cordelia = self.example_user("cordelia")
        othello = self.example_user("othello")
        iago = self.example_user("iago")
        for user in [self.owner, cordelia, othello, iago]:
            self.subscribe(user, "needs-small")

        def send(*mentioned: UserProfile) -> int:
            mentions = " ".join(f"@**{user.full_name}**" for user in mentioned)
            return self.send_stream_message(
                self.owner, "needs-small", f"{mentions} look", topic_name="mentions"
            )

        self.assertFalse(self._broadcast_of(cordelia, send(cordelia)))
        self.assertFalse(self._broadcast_of(cordelia, send(cordelia, othello)))
        self.assertTrue(self._broadcast_of(cordelia, send(cordelia, othello, iago)))

    def test_bots_do_not_count_towards_a_team_wide_mention(self) -> None:
        cordelia = self.example_user("cordelia")
        othello = self.example_user("othello")
        for user in [self.owner, cordelia, othello, self.example_user("default_bot")]:
            self.subscribe(user, "needs-small")
        message_id = self.send_stream_message(
            self.owner,
            "needs-small",
            f"@**{cordelia.full_name}** @**{othello.full_name}**"
            f" @**{self.example_user('default_bot').full_name}** look",
            topic_name="mentions",
        )
        self.assertFalse(self._broadcast_of(cordelia, message_id))

    def test_wildcard_mention_is_team_wide_unless_it_names_only_the_user(self) -> None:
        cordelia = self.example_user("cordelia")
        othello = self.example_user("othello")
        for user in [self.owner, cordelia, othello]:
            self.subscribe(user, "needs-small")
        team_wide = self.send_stream_message(
            self.owner,
            "needs-small",
            f"@**all** @**{cordelia.full_name}** @**{othello.full_name}** standup",
            topic_name="mentions",
        )
        addressed_to_user = self.send_stream_message(
            self.owner,
            "needs-small",
            f"@**all** @**{cordelia.full_name}** please answer",
            topic_name="mentions",
        )
        self.assertTrue(self._broadcast_of(cordelia, team_wide))
        self.assertFalse(self._broadcast_of(cordelia, addressed_to_user))

    def test_counts_leave_out_team_wide_mentions(self) -> None:
        cordelia = self.example_user("cordelia")
        othello = self.example_user("othello")
        iago = self.example_user("iago")
        for user in [self.owner, cordelia, othello, iago]:
            self.subscribe(user, "needs-small")
        self.send_stream_message(
            self.owner, "needs-small", f"@**{cordelia.full_name}** look", topic_name="personal"
        )
        self.send_stream_message(
            self.owner,
            "needs-small",
            " ".join(f"@**{user.full_name}**" for user in [cordelia, othello, iago]),
            topic_name="team",
        )

        result = needs.list_needs(cordelia, kind=None, status="open", since=None)
        self.assertEqual(len(result["items"]), 2)
        self.assertEqual(
            result["counts"], {**NO_CARDS, "approval": 0, "mention": 1, "decision": 0, "all": 1}
        )
        self.assertEqual(
            result["broadcast_counts"],
            {**NO_CARDS, "approval": 0, "mention": 1, "decision": 0, "all": 1},
        )

    def test_direct_message_is_never_team_wide(self) -> None:
        cordelia = self.example_user("cordelia")
        message_id = self.send_personal_message(self.owner, cordelia, "Are you free?")
        self.assertFalse(self._broadcast_of(cordelia, message_id))

    def test_resolved_mention_keeps_its_team_wide_flag(self) -> None:
        cordelia = self.example_user("cordelia")
        othello = self.example_user("othello")
        iago = self.example_user("iago")
        for user in [self.owner, cordelia, othello, iago]:
            self.subscribe(user, "needs-small")
        message_id = self.send_stream_message(
            self.owner,
            "needs-small",
            " ".join(f"@**{user.full_name}**" for user in [cordelia, othello, iago]),
            topic_name="mentions",
        )
        needs.resolve_mention(cordelia, message_id)

        [item] = needs.list_needs(cordelia, kind="mention", status="resolved", since="today")[
            "items"
        ]
        self.assertTrue(item["broadcast"])

        replied_id = self.send_stream_message(
            self.owner,
            "needs-small",
            " ".join(f"@**{user.full_name}**" for user in [cordelia, othello, iago]),
            topic_name="replied",
        )
        self.send_stream_message(cordelia, "needs-small", "On it", topic_name="replied")
        resolved = needs.list_needs(cordelia, kind="mention", status="resolved", since="today")
        [replied] = [item for item in resolved["items"] if item["id"] == f"mention:{replied_id}"]
        self.assertTrue(replied["broadcast"])
        self.assertEqual(replied["resolved_action"], "replied")

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
        self.assertEqual(
            payload["counts"], {**NO_CARDS, "approval": 0, "mention": 1, "decision": 1, "all": 2}
        )
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


class NeedsBroadcastTests(NeedsTestCase):
    @override
    def setUp(self) -> None:
        super().setUp()
        self.cordelia = self.example_user("cordelia")
        self.othello = self.example_user("othello")
        self.iago = self.example_user("iago")
        for user in [self.owner, self.cordelia, self.othello, self.iago]:
            self.subscribe(user, "needs-small")

    def _team_post(self, topic: str, text: str = "Rekap") -> str:
        """A mention of three humans, which is team-wide for cordelia."""
        mentions = " ".join(
            f"@**{user.full_name}**" for user in [self.cordelia, self.othello, self.iago]
        )
        message_id = self.send_stream_message(
            self.owner, "needs-small", f"{mentions} {text}", topic_name=topic
        )
        return f"mention:{message_id}"

    def _personal_post(self, topic: str) -> str:
        message_id = self.send_stream_message(
            self.owner, "needs-small", f"@**{self.cordelia.full_name}** look", topic_name=topic
        )
        return f"mention:{message_id}"

    def test_only_the_newest_team_wide_mention_per_topic_stays_open(self) -> None:
        oldest = self._team_post("Cek pagi")
        other_topic = self._team_post("Rekap")
        middle = self._team_post("Cek pagi")
        newest = self._team_post("cek PAGI")

        self.assertCountEqual(self._open_ids(self.cordelia), [newest, other_topic])
        self.assertEqual(
            set(
                NeedResolution.objects.filter(user=self.cordelia, action="superseded").values_list(
                    "message_id", flat=True
                )
            ),
            {int(oldest.split(":")[1]), int(middle.split(":")[1])},
        )

    def test_superseded_mentions_stay_resolved_and_do_not_reopen(self) -> None:
        oldest = self._team_post("Cek pagi")
        newest = self._team_post("Cek pagi")
        self.assertEqual(self._open_ids(self.cordelia), [newest])

        needs.resolve_many(self.cordelia, [newest])
        self.assertEqual(self._open_ids(self.cordelia), [])
        needs.unresolve_many(self.cordelia, [newest, oldest])
        self.assertEqual(self._open_ids(self.cordelia), [newest])

        # A newer post in the topic takes the place of the one still open.
        newer = self._team_post("Cek pagi")
        self.assertEqual(self._open_ids(self.cordelia), [newer])

    def test_superseded_mentions_are_not_listed_as_done(self) -> None:
        self._team_post("Cek pagi")
        self._team_post("Cek pagi")
        self._open_ids(self.cordelia)
        resolved = needs.list_needs(self.cordelia, kind=None, status="resolved", since="today")
        self.assertEqual(resolved["items"], [])

    def test_personal_mentions_are_never_superseded(self) -> None:
        first = self._personal_post("Cek pagi")
        second = self._personal_post("Cek pagi")
        self.assertCountEqual(self._open_ids(self.cordelia), [first, second])
        self.assertFalse(NeedResolution.objects.exists())

    def test_team_wide_mention_leaves_the_list_after_48_hours(self) -> None:
        with time_machine.travel(now() - timedelta(hours=49), tick=False):
            stale_team = self._team_post("Lama")
            old_personal = self._personal_post("Lama")
        with time_machine.travel(now() - timedelta(hours=47), tick=False):
            recent_team = self._team_post("Baru")

        open_ids = self._open_ids(self.cordelia)
        self.assertCountEqual(open_ids, [old_personal, recent_team])
        self.assertNotIn(stale_team, open_ids)
        counts = needs.list_needs(self.cordelia, kind=None, status="open", since=None)
        self.assertEqual(counts["counts"]["mention"], 1)
        self.assertEqual(counts["broadcast_counts"]["mention"], 1)

    def test_a_team_wide_mention_the_user_reacted_to_is_left_alone(self) -> None:
        with time_machine.travel(now() - timedelta(hours=49), tick=False):
            stale = self._team_post("Lama")
        older = self._team_post("Cek pagi")
        newest = self._team_post("Cek pagi")
        for need_id in [stale, older]:
            self.api_post(
                self.cordelia,
                f"/api/v1/messages/{need_id.split(':')[1]}/reactions",
                {"emoji_name": "+1"},
            )

        self.assertCountEqual(self._open_ids(self.cordelia), [stale, older, newest])

    def test_a_replied_team_wide_mention_stays_resolved(self) -> None:
        self._team_post("Cek pagi")
        self.send_stream_message(self.cordelia, "needs-small", "On it", topic_name="Cek pagi")
        newest = self._team_post("Cek pagi")
        self.assertEqual(self._open_ids(self.cordelia), [newest])


class NeedsBulkTests(NeedsTestCase):
    def _post_ids(self, path: str, ids: list[str]) -> "TestHttpResponse":
        return self.client_post(path, {"need_ids": orjson.dumps(ids).decode()})

    def test_resolve_many_reports_what_failed_and_keeps_the_rest(self) -> None:
        cordelia = self.example_user("cordelia")
        first_id = self._mention(cordelia, "one")
        second_id = self._mention(cordelia, "two")
        # A message that mentions no one is not a need of cordelia.
        unrelated_id = self.send_stream_message(
            self.owner, "Denmark", "no mention", topic_name="mentions"
        )

        result = needs.resolve_many(
            cordelia,
            [
                f"mention:{first_id}",
                f"mention:{unrelated_id}",
                "approval:x",
                f"mention:{second_id}",
            ],
        )

        self.assertEqual(result["done"], [f"mention:{first_id}", f"mention:{second_id}"])
        self.assertEqual(result["failed"], [f"mention:{unrelated_id}", "approval:x"])
        self.assertEqual(self._open_ids(cordelia, "mention"), [])

    def test_unresolve_many_reopens_mentions(self) -> None:
        cordelia = self.example_user("cordelia")
        ids = [f"mention:{self._mention(cordelia, 'ping')}" for _ in range(2)]
        needs.resolve_many(cordelia, ids)
        self.assertEqual(self._open_ids(cordelia), [])

        result = needs.unresolve_many(cordelia, ids)

        self.assertEqual(result, {"done": ids, "failed": []})
        self.assertCountEqual(self._open_ids(cordelia), ids)

    def test_bulk_calls_reject_a_malformed_or_oversized_list(self) -> None:
        cordelia = self.example_user("cordelia")
        for call in [needs.resolve_many, needs.unresolve_many, needs.snooze_many]:
            with self.assertRaises(ValueError):
                call(cordelia, ["mention:1"] * (needs.BULK_LIMIT + 1))
        for bad_id in ["", "mention", "mention:", "other:1", "x" * 90]:
            with self.assertRaises(ValueError):
                needs.snooze_many(cordelia, [bad_id])

    def test_snooze_hides_an_item_until_seven_the_next_morning(self) -> None:
        cordelia = self.example_user("cordelia")
        # 21:00 in Jakarta on 16 October.
        with time_machine.travel(datetime(2026, 10, 16, 14, 0, tzinfo=timezone.utc), tick=False):
            snoozed_id = f"mention:{self._mention(cordelia, 'later')}"
            other_id = f"mention:{self._mention(cordelia, 'now')}"
            until = needs.snooze_many(cordelia, [snoozed_id])
            self.assertEqual(self._open_ids(cordelia), [other_id])
            counts = needs.list_needs(cordelia, kind=None, status="open", since=None)["counts"]
            self.assertEqual(counts["mention"], 1)
        # 07:00 on 17 October in Jakarta.
        self.assertEqual(until, datetime(2026, 10, 17, 0, 0, tzinfo=timezone.utc))

        with time_machine.travel(until - timedelta(seconds=1), tick=False):
            self.assertEqual(self._open_ids(cordelia), [other_id])
        with time_machine.travel(until, tick=False):
            self.assertCountEqual(self._open_ids(cordelia), [snoozed_id, other_id])

    def test_snooze_before_seven_ends_the_same_morning(self) -> None:
        cordelia = self.example_user("cordelia")
        # 05:30 in Jakarta on 17 October.
        with time_machine.travel(datetime(2026, 10, 16, 22, 30, tzinfo=timezone.utc), tick=False):
            until = needs.snooze_until(cordelia.realm)
        self.assertEqual(until, datetime(2026, 10, 17, 0, 0, tzinfo=timezone.utc))
        # Exactly 07:00 is already the next morning's.
        with time_machine.travel(datetime(2026, 10, 17, 0, 0, tzinfo=timezone.utc), tick=False):
            until = needs.snooze_until(cordelia.realm)
        self.assertEqual(until, datetime(2026, 10, 18, 0, 0, tzinfo=timezone.utc))

    def test_snooze_follows_the_workspace_timezone(self) -> None:
        cordelia = self.example_user("cordelia")
        agents.AgentRealmSettings.objects.filter(realm=cordelia.realm).update(
            timezone="America/New_York"
        )
        # 12:00 UTC on 17 October is 08:00 in New York, so 07:00 is tomorrow's.
        with time_machine.travel(datetime(2026, 10, 17, 12, 0, tzinfo=timezone.utc), tick=False):
            until = needs.snooze_until(cordelia.realm)
        self.assertEqual(until, datetime(2026, 10, 18, 11, 0, tzinfo=timezone.utc))

    def test_snooze_covers_every_kind_and_unsnooze_brings_it_back(self) -> None:
        job, _attempt = self._pending_decision()
        decision_id = f"decision:{job.id}"
        self.assertEqual(self._open_ids(self.owner), [decision_id])

        needs.snooze_many(self.owner, [decision_id])
        self.assertEqual(self._open_ids(self.owner), [])

        needs.unsnooze_many(self.owner, [decision_id])
        self.assertEqual(self._open_ids(self.owner), [decision_id])

    def test_snooze_again_moves_the_end_and_sweeps_old_rows(self) -> None:
        cordelia = self.example_user("cordelia")
        message_id = self._mention(cordelia, "ping")
        stale = NeedSnooze.objects.create(
            realm=cordelia.realm,
            user=cordelia,
            need_id="mention:1",
            until=now() - timedelta(days=1),
        )
        needs.snooze_many(cordelia, [f"mention:{message_id}"])
        needs.snooze_many(cordelia, [f"mention:{message_id}"])

        self.assertFalse(NeedSnooze.objects.filter(id=stale.id).exists())
        self.assertEqual(NeedSnooze.objects.filter(user=cordelia).count(), 1)

    def test_a_snoozed_item_marked_done_stays_done(self) -> None:
        cordelia = self.example_user("cordelia")
        need_id = f"mention:{self._mention(cordelia, 'ping')}"
        needs.snooze_many(cordelia, [need_id])
        needs.resolve_many(cordelia, [need_id])
        needs.unsnooze_many(cordelia, [need_id])
        self.assertEqual(self._open_ids(cordelia), [])

    def test_snooze_is_per_user(self) -> None:
        cordelia = self.example_user("cordelia")
        othello = self.example_user("othello")
        self.subscribe(othello, "Denmark")
        message_id = self.send_stream_message(
            self.owner,
            "Denmark",
            f"@**{cordelia.full_name}** @**{othello.full_name}** look",
            topic_name="mentions",
        )
        need_id = f"mention:{message_id}"
        needs.snooze_many(cordelia, [need_id])
        self.assertEqual(self._open_ids(cordelia), [])
        self.assertEqual(self._open_ids(othello), [need_id])

    def test_bulk_endpoints_round_trip(self) -> None:
        cordelia = self.example_user("cordelia")
        ids = [f"mention:{self._mention(cordelia, 'ping')}" for _ in range(2)]
        self.login_user(cordelia)

        result = self.assert_json_success(
            self._post_ids("/json/needs/resolve", [*ids, "mention:0"])
        )
        self.assertEqual(result["done"], ids)
        self.assertEqual(result["failed"], ["mention:0"])
        self.assertEqual(self._open_ids(cordelia), [])

        result = self.assert_json_success(self._post_ids("/json/needs/unresolve", ids))
        self.assertEqual(result["done"], ids)
        self.assertCountEqual(self._open_ids(cordelia), ids)

        result = self.assert_json_success(self._post_ids("/json/needs/snooze", ids))
        self.assertTrue(result["until"].startswith("20"))
        self.assertEqual(self._open_ids(cordelia), [])

        self.assert_json_success(self._post_ids("/json/needs/unsnooze", ids))
        self.assertCountEqual(self._open_ids(cordelia), ids)

    def test_bulk_endpoints_reject_a_malformed_id(self) -> None:
        self.login_user(self.example_user("cordelia"))
        result = self._post_ids("/json/needs/snooze", ["nonsense"])
        self.assert_json_error(result, "Agent request rejected.")


class CardNeedsTests(ZulipTestCase):
    @override
    def setUp(self) -> None:
        super().setUp()
        self.hamlet = self.example_user("hamlet")
        self.cordelia = self.example_user("cordelia")
        agents.AgentRealmSettings.objects.update_or_create(
            realm=self.hamlet.realm, defaults={"enabled": True}
        )
        board = get_or_create_default_board(self.hamlet.realm)
        self.card = do_create_task(
            user_profile=self.hamlet,
            board=board,
            column=board.columns.all()[0],
            title="Lock the October feature list",
        )

    def note_mentioning_cordelia(self, card: Task | None = None) -> None:
        do_add_task_note(
            user_profile=self.hamlet,
            task=card or self.card,
            content=f"@**{self.cordelia.full_name}|{self.cordelia.id}** please **check** this",
        )

    def test_mention_item_carries_what_the_ui_needs(self) -> None:
        self.note_mentioning_cordelia()
        notification = TaskNotification.objects.get()

        result = needs.list_needs(self.cordelia, kind=None, status="open", since=None)

        self.assertEqual(result["counts"]["card_mention"], 1)
        self.assertEqual(result["counts"]["all"], 1)
        [item] = result["items"]
        self.assertEqual(item["id"], f"card_mention:{notification.id}")
        self.assertEqual(item["kind"], "card_mention")
        self.assertEqual(item["actor"]["id"], self.hamlet.id)
        self.assertEqual(item["actor"]["type"], "human")
        self.assertEqual(item["text"], "please check this")
        self.assertEqual(item["raw_text"], f"@{self.cordelia.full_name} please check this")
        self.assertEqual(item["time"], notification.date_created.isoformat())
        self.assertEqual(
            item["task"],
            {"id": self.card.id, "display_id": "GT-1", "title": "Lock the October feature list"},
        )
        self.assertIsNone(item["resolved_at"])

        # The author is not notified of their own note.
        author_result = needs.list_needs(self.hamlet, kind=None, status="open", since=None)
        self.assertEqual(author_result["items"], [])

    def test_a_note_that_mentions_three_people_is_team_wide(self) -> None:
        self.note_mentioning_cordelia()
        othello = self.example_user("othello")
        iago = self.example_user("iago")
        do_add_task_note(
            user_profile=self.hamlet,
            task=self.card,
            content=" ".join(
                f"@**{user.full_name}|{user.id}**" for user in [self.cordelia, othello, iago]
            ),
        )

        items = needs.list_needs(self.cordelia, kind=None, status="open", since=None)["items"]

        self.assertEqual(
            sorted(item["broadcast"] for item in items if item["kind"] == "card_mention"),
            [False, True],
        )

    def test_assigned_item_shows_the_card_title_and_filters_by_kind(self) -> None:
        self.note_mentioning_cordelia()
        do_update_task(
            user_profile=self.hamlet, task=self.card, changes={"assignee": self.cordelia}
        )
        notification = TaskNotification.objects.get(kind=TaskNotification.ASSIGNED)

        result = needs.list_needs(self.cordelia, kind="card_assigned", status="open", since=None)

        self.assertEqual(result["counts"]["card_assigned"], 1)
        self.assertEqual(result["counts"]["card_mention"], 1)
        self.assertEqual(result["counts"]["all"], 2)
        [item] = result["items"]
        self.assertEqual(item["id"], f"card_assigned:{notification.id}")
        self.assertEqual(item["kind"], "card_assigned")
        self.assertEqual(item["text"], "Lock the October feature list")
        self.assertEqual(item["actor"]["id"], self.hamlet.id)
        self.assertIsNotNone(item["task"])
        assert item["task"] is not None
        self.assertEqual(item["task"]["display_id"], "GT-1")

    def test_item_leaves_the_list_after_the_seven_day_window(self) -> None:
        self.note_mentioning_cordelia()
        with time_machine.travel(now() + timedelta(days=8), tick=False):
            result = needs.list_needs(self.cordelia, kind=None, status="open", since=None)
        self.assertEqual(result["items"], [])

    def test_item_is_hidden_when_the_user_cannot_read_the_card(self) -> None:
        self.note_mentioning_cordelia()
        private_stream = self.make_stream("secret-plan", invite_only=True)
        self.subscribe(self.hamlet, "secret-plan")
        Task.objects.filter(id=self.card.id).update(stream=private_stream)

        result = needs.list_needs(self.cordelia, kind=None, status="open", since=None)
        self.assertEqual(result["items"], [])
        self.assertEqual(result["counts"]["all"], 0)

    def test_opening_the_card_moves_the_item_to_resolved(self) -> None:
        self.note_mentioning_cordelia()
        self.login_user(self.cordelia)
        self.assert_json_success(
            self.client_post(f"/json/tasks/{self.card.id}/notifications/resolve")
        )

        open_items = self.assert_json_success(self.client_get("/json/needs"))
        self.assertEqual(open_items["items"], [])
        resolved = self.assert_json_success(
            self.client_get("/json/needs", {"status": "resolved", "since": "today"})
        )
        [item] = resolved["items"]
        self.assertEqual(item["kind"], "card_mention")
        self.assertEqual(item["resolved_action"], "done")
        self.assertIsNotNone(item["resolved_at"])

    def test_resolving_one_item_closes_every_item_on_the_card(self) -> None:
        self.note_mentioning_cordelia()
        self.note_mentioning_cordelia()
        first = TaskNotification.objects.order_by("id").first()
        assert first is not None
        self.login_user(self.cordelia)

        result = self.client_post(f"/json/needs/card_notifications/{first.id}/resolve")
        self.assert_json_success(result)

        self.assertEqual(TaskNotification.objects.filter(resolved_at__isnull=True).count(), 0)
        self.assertEqual(
            needs.list_needs(self.cordelia, kind=None, status="open", since=None)["items"], []
        )

    def test_team_wide_card_note_leaves_the_list_after_48_hours(self) -> None:
        othello = self.example_user("othello")
        iago = self.example_user("iago")
        mentions = " ".join(
            f"@**{user.full_name}|{user.id}**" for user in [self.cordelia, othello, iago]
        )
        do_add_task_note(user_profile=self.hamlet, task=self.card, content=f"{mentions} rekap")
        self.note_mentioning_cordelia()
        stale, fresh = TaskNotification.objects.filter(user=self.cordelia).order_by("id")
        TaskNotification.objects.filter(id=stale.id).update(
            date_created=now() - timedelta(hours=49)
        )
        TaskNotification.objects.filter(id=fresh.id).update(
            date_created=now() - timedelta(hours=49)
        )

        listed = needs.list_needs(self.cordelia, kind=None, status="open", since=None)["items"]

        # The note that names three people is team-wide and stale. The
        # personal one is as old and stays.
        self.assertEqual([item["id"] for item in listed], [f"card_mention:{fresh.id}"])

    def test_bulk_resolve_and_undo_of_card_items(self) -> None:
        self.note_mentioning_cordelia()
        self.note_mentioning_cordelia()
        ids = [
            f"card_mention:{notification.id}"
            for notification in TaskNotification.objects.order_by("id")
        ]

        result = needs.resolve_many(self.cordelia, ids[:1])
        self.assertEqual(result, {"done": ids[:1], "failed": []})
        # Closing one closed both, as opening the card does.
        self.assertEqual(
            needs.list_needs(self.cordelia, kind=None, status="open", since=None)["items"], []
        )

        needs.unresolve_many(self.cordelia, ids[:1])
        listed = needs.list_needs(self.cordelia, kind=None, status="open", since=None)["items"]
        self.assertCountEqual([item["id"] for item in listed], ids)

    def test_undo_leaves_an_older_closing_of_the_card_alone(self) -> None:
        self.note_mentioning_cordelia()
        old = TaskNotification.objects.get()
        with time_machine.travel(now() - timedelta(hours=2), tick=False):
            needs.resolve_many(self.cordelia, [f"card_mention:{old.id}"])
        self.note_mentioning_cordelia()
        new = TaskNotification.objects.exclude(id=old.id).get()
        needs.resolve_many(self.cordelia, [f"card_mention:{new.id}"])

        needs.unresolve_many(self.cordelia, [f"card_mention:{new.id}"])

        old.refresh_from_db()
        new.refresh_from_db()
        self.assertIsNotNone(old.resolved_at)
        self.assertIsNone(new.resolved_at)

    def test_resolving_someone_elses_item_is_rejected(self) -> None:
        self.note_mentioning_cordelia()
        notification = TaskNotification.objects.get()
        self.login_user(self.hamlet)

        result = self.client_post(f"/json/needs/card_notifications/{notification.id}/resolve")

        self.assert_json_error(result, "Agent request rejected.")
        self.assertIsNone(TaskNotification.objects.get().resolved_at)


class NeedsMcpCallTests(NeedsTestCase):
    """An Ask first call from an external MCP client is an approval item
    for the owner of the grant, with the exact action to approve."""

    @override
    def setUp(self) -> None:
        super().setUp()
        self.grant = MCPAccessGrant.objects.create(
            realm=self.owner.realm,
            user=self.owner,
            name="Claude Cowork",
            scopes=["team:read", "team:write"],
            resource="http://zulip.testserver/mcp",
            expires_at=now() + timedelta(days=7),
        )
        self.subscribe(self.owner, "Denmark")

    def _ask(self, content: str = "Ship it tonight.") -> MCPPendingCall:
        return request_approval(
            self.grant,
            "send_message",
            {
                "channel_id": self.get_stream_id("Denmark"),
                "topic": "Release",
                "content": content,
                "mention_user_ids": [self.example_user("othello").id],
            },
        )

    def _decide_call(self, call: MCPPendingCall, decision: str) -> "TestHttpResponse":
        self.login_user(self.owner)
        return self.client_post(
            f"/json/mcp/calls/{call.id}/decision",
            {
                "decision": decision,
                "argument_digest": call.argument_digest,
                "nonce": str(call.nonce),
            },
        )

    def test_item_shows_the_exact_action_for_the_grant_owner(self) -> None:
        # AC-V1-012
        call = self._ask()
        result = needs.list_needs(self.owner, kind=None, status="open", since=None)
        self.assertEqual(
            result["counts"], {**NO_CARDS, "approval": 1, "mention": 0, "decision": 0, "all": 1}
        )
        [item] = result["items"]
        self.assertEqual(item["id"], f"mcp_call:{call.id}")
        self.assertEqual(item["kind"], "approval")
        self.assertEqual(item["actor"]["name"], "Claude Cowork")
        self.assertEqual(item["actor"]["type"], "agent")
        self.assertEqual(item["stream_id"], self.get_stream_id("Denmark"))
        self.assertEqual(item["topic"], "Release")
        self.assertEqual(item["text"], "Ship it tonight.")
        self.assertEqual(item["actions"], ["approve", "reject"])
        self.assertEqual(item["expires_at"], call.expires_at.isoformat())
        self.assertFalse(item["expired"])
        self.assertEqual(item["nonce"], str(call.nonce))
        self.assertEqual(item["operation_hash"], call.argument_digest)
        self.assertIsNone(item["job_id"])
        self.assertEqual(
            item["mcp_call"],
            {
                "call_id": str(call.id),
                "grant_name": "Claude Cowork",
                "tool": "send_message",
                "target": {
                    "type": "channel",
                    "name": "Denmark",
                    "topic": "Release",
                    "message_id": None,
                },
                "content": "Ship it tonight.",
                "mentions": [self.example_user("othello").full_name],
                "digest": call.argument_digest[:12],
            },
        )

    def test_only_the_owner_sees_it_not_even_an_administrator(self) -> None:
        self._ask()
        for name in ("iago", "othello", "cordelia"):
            self.assertEqual(self._open_ids(self.example_user(name)), [])

    def test_the_text_is_cut_but_the_full_content_comes_with_it(self) -> None:
        content = "word " * 400
        self._ask(content)
        [item] = needs.list_needs(self.owner, kind=None, status="open", since=None)["items"]
        self.assertEqual(item["text"], content[:600])
        self.assertEqual(item["mcp_call"]["content"], content)

    def test_expired_call_stays_listed_for_a_day_then_leaves(self) -> None:
        call = self._ask()
        with time_machine.travel(call.expires_at + timedelta(minutes=1), tick=False):
            [item] = needs.list_needs(self.owner, kind=None, status="open", since=None)["items"]
            self.assertTrue(item["expired"])
        with time_machine.travel(call.expires_at + timedelta(hours=25), tick=False):
            self.assertEqual(self._open_ids(self.owner), [])

    def test_revoked_grant_takes_its_calls_away(self) -> None:
        self._ask()
        MCPAccessGrant.objects.filter(id=self.grant.id).update(revoked_at=now())
        self.assertEqual(self._open_ids(self.owner), [])

    def test_approving_and_rejecting_move_the_item_to_done(self) -> None:
        approved = self._ask("Approve me.")
        rejected = self._ask("Reject me.")
        self.assert_json_success(self._decide_call(approved, "approve"))
        self.assert_json_success(self._decide_call(rejected, "reject"))
        self.assertEqual(self._open_ids(self.owner), [])
        done = needs.list_needs(self.owner, kind=None, status="resolved", since="today")["items"]
        self.assertEqual(
            {item["id"]: item["resolved_action"] for item in done},
            {f"mcp_call:{approved.id}": "approved", f"mcp_call:{rejected.id}": "rejected"},
        )
        self.assertEqual(
            Message.objects.filter(
                realm_id=self.owner.realm_id, sender=self.owner, content__contains="Reject me."
            ).count(),
            0,
        )
        self.assertEqual(
            Message.objects.filter(
                realm_id=self.owner.realm_id, sender=self.owner, content__contains="Approve me."
            ).count(),
            1,
        )

    def test_the_item_can_be_snoozed_but_not_marked_done_in_bulk(self) -> None:
        call = self._ask()
        need_id = f"mcp_call:{call.id}"
        needs.snooze_many(self.owner, [need_id])
        self.assertEqual(self._open_ids(self.owner), [])
        needs.unsnooze_many(self.owner, [need_id])
        self.assertEqual(self._open_ids(self.owner), [need_id])
        self.assertEqual(needs.resolve_many(self.owner, [need_id])["failed"], [need_id])
        call.refresh_from_db()
        self.assertEqual(call.status, "pending")

    def test_kind_filter_returns_it_with_the_approvals(self) -> None:
        call = self._ask()
        self.assertEqual(self._open_ids(self.owner, "approval"), [f"mcp_call:{call.id}"])
        self.assertEqual(self._open_ids(self.owner, "mention"), [])
