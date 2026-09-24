"""Administrator agent team tools (contract 2.2-2.6): executor, gate, and receipts."""

import hashlib
import tempfile
from uuid import uuid4

from django.test import override_settings
from django.utils.timezone import now
from typing_extensions import override

from zerver.actions import agent_approvals as approvals
from zerver.actions import agent_jobs
from zerver.actions.agent_team_tools import execute_team_tool, team_manage_result_lines
from zerver.actions.agents import create_profile, enable_profile, record_readiness
from zerver.actions.realm_settings import do_change_realm_permission_group_setting
from zerver.actions.user_groups import check_add_user_group
from zerver.actions.users import do_change_user_role
from zerver.lib.agent_policy import AgentAccessDenied
from zerver.lib.agent_protocol import (
    ChannelCreateInput,
    ChannelSubscribeInput,
    GroupAddMembersInput,
    GroupCreateInput,
    GroupRemoveMembersInput,
    ResultPayload,
    TopicPostInput,
    serialize_payload,
)
from zerver.lib.agent_results import job_task_link, publish_result, store_artifact
from zerver.lib.mention import silent_mention_syntax_for_user
from zerver.lib.streams import access_stream_for_send_message
from zerver.lib.test_classes import ZulipTestCase, ZulipTransactionTestCase
from zerver.lib.user_groups import get_role_based_system_groups_dict
from zerver.lib.user_topics import get_topic_visibility_policy
from zerver.models import (
    DirectMessageGroup,
    Message,
    NamedUserGroup,
    RealmAuditLog,
    Recipient,
    Stream,
    Subscription,
    UserGroup,
    UserProfile,
    agents,
)
from zerver.models.groups import SystemGroups
from zerver.models.realm_audit_logs import AuditLogEventType
from zerver.models.streams import get_stream
from zerver.models.user_topics import UserTopic


class AgentTeamToolsTests(ZulipTestCase):
    @override
    def setUp(self) -> None:
        super().setUp()
        self.owner = self.example_user("hamlet")
        # The realm-setting group check reads real group membership, which
        # only a raw role field never updates; go through the action that
        # keeps role:administrators membership in sync.
        do_change_user_role(
            self.owner, UserProfile.ROLE_REALM_ADMINISTRATOR, acting_user=None, notify=True
        )
        self.admin2 = self.example_user("iago")
        self.member = self.example_user("othello")
        self.realm = self.owner.realm
        agents.AgentRealmSettings.objects.update_or_create(
            realm=self.realm, defaults={"enabled": True}
        )
        self.runner = agents.AgentRunner.objects.create(
            realm=self.realm,
            owner=self.owner,
            name="Team runner",
            fingerprint="a" * 64,
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
        self.profile = self._ready_manage_profile(self.owner, name="Admin agent")

    # ---- setup helpers ----

    def _ready_manage_profile(self, owner: UserProfile, *, name: str) -> agents.AgentProfile:
        profile = create_profile(
            owner,
            name=name,
            runner=self.runner,
            adapter_id="acp",
            adapter_version="1",
            default_mode="manage",
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
                    "tool_calling": "passed",
                    "team_tools": "passed",
                    "config_version": 1,
                },
            },
        )
        profile.refresh_from_db()
        return enable_profile(owner, profile, expected_revision=profile.revision)

    def _grant(
        self, principal: UserProfile, *, target_kind: str, actions: list[str], resource: object
    ) -> None:
        agents.AgentGrant.objects.create(
            realm=self.realm,
            owner=self.owner,
            principal_user=principal,
            target_kind=target_kind,
            actions=actions,
            **{target_kind: resource},
        )

    def _share_profile(self, principal: UserProfile) -> None:
        """Everything contract 2.2 rule 3 requires for a non-owner commander."""
        self._grant(
            principal,
            target_kind="profile",
            actions=["profile.use", "context.read", "team.manage"],
            resource=self.profile,
        )
        self._grant(principal, target_kind="runner", actions=["runner.use"], resource=self.runner)

    def _dispatch(
        self, commander: UserProfile, profile: agents.AgentProfile, content: str = "Please help"
    ) -> agents.AgentJob:
        # A stream message with an explicit mention (rather than a 1:1 DM) so a
        # third party such as self.admin2 can also access the source message.
        self.subscribe(commander, "agent-tasks")
        self.subscribe(profile.bot_user, "agent-tasks")
        mention = f"@**{profile.bot_user.full_name}|{profile.bot_user_id}**"
        message_id = self.send_stream_message(commander, "agent-tasks", f"{mention} {content}")
        return agents.AgentJob.objects.get(source_message_id=message_id)

    def _claim(self, job: agents.AgentJob) -> agents.AgentAttempt:
        agent_jobs.claim_work(job.runner, claim_key=uuid4())
        return agents.AgentAttempt.objects.get(job=job)

    def _propose(
        self, job: agents.AgentJob, attempt: agents.AgentAttempt, tool_input: dict[str, object]
    ) -> agents.AgentOperation:
        return approvals.propose_operation(
            job.runner,
            job.id,
            attempt.id,
            attempt.lease_epoch,
            operation_id=uuid4(),
            arguments={"action": "team.manage", "input": tool_input},
            tree_hash=None,
            diff_artifact_id=None,
        )

    def _execute(
        self,
        job: agents.AgentJob,
        attempt: agents.AgentAttempt,
        operation: agents.AgentOperation,
        *,
        operation_hash: str | None = None,
    ) -> dict[str, object]:
        job.refresh_from_db()
        operation.refresh_from_db()
        approval = agents.AgentApproval.objects.filter(
            operation=operation, decision="approved"
        ).first()
        return approvals.execute_operation(
            job.runner,
            job.id,
            attempt.id,
            attempt.lease_epoch,
            job_version=job.version,
            operation_id=operation.operation_id,
            expected_version=operation.version,
            operation_hash=operation_hash or operation.argument_digest,
            nonce=approval.nonce if approval else None,
        )

    def _approve(self, commander: UserProfile, operation: agents.AgentOperation) -> None:
        approval = agents.AgentApproval.objects.get(operation=operation)
        approvals.decide_approval(
            commander,
            approval.id,
            expected_version=approval.version,
            operation_hash=operation.argument_digest,
            nonce=approval.nonce,
            decision="approved",
        )

    def _run_tool(
        self, commander: UserProfile, profile: agents.AgentProfile, tool_input: dict[str, object]
    ) -> dict[str, object]:
        """Full contract 2.6 pipeline: dispatch, claim, propose, confirm, execute."""
        job = self._dispatch(commander, profile)
        attempt = self._claim(job)
        operation = self._propose(job, attempt, tool_input)
        if agents.AgentApproval.objects.filter(operation=operation).exists():
            self._approve(commander, operation)
        result = self._execute(job, attempt, operation)
        assert result["server_receipt"] is not None
        return result["server_receipt"]

    def _subscribed(self, user: UserProfile, channel: Stream) -> bool:
        return Subscription.objects.filter(
            user_profile=user, recipient_id=channel.recipient_id, active=True
        ).exists()

    # ---- each tool succeeds, with the matching Zulip objects and audit trail ----

    def test_find_tool_reports_matches_without_side_effects(self) -> None:
        receipt = self._run_tool(
            self.owner, self.profile, {"tool": "team.find", "query": "othello", "kinds": ["person"]}
        )
        self.assertEqual(receipt["outcome"], "succeeded")
        self.assertIn("othello", receipt["summary"].lower())
        self.assertEqual(receipt["objects"], {})

    def test_channel_create_tool_creates_channel_with_audit_log(self) -> None:
        receipt = self._run_tool(
            self.owner,
            self.profile,
            {
                "tool": "channel.create",
                "name": "launch-q1",
                "description": "Launch planning",
                "is_private": False,
                "subscriber_user_ids": [self.member.id],
            },
        )
        self.assertEqual(receipt["outcome"], "succeeded")
        channel = get_stream("launch-q1", self.realm)
        self.assertEqual(receipt["objects"]["channel_id"], channel.id)
        self.assertTrue(
            RealmAuditLog.objects.filter(
                realm=self.realm,
                event_type=AuditLogEventType.CHANNEL_CREATED,
                modified_stream=channel,
                acting_user=self.owner,
            ).exists()
        )
        self.assertTrue(self._subscribed(self.member, channel))

    def test_channel_subscribe_tool_adds_member_with_audit_log(self) -> None:
        channel = self.subscribe(self.owner, "team-updates")
        receipt = self._run_tool(
            self.owner,
            self.profile,
            {"tool": "channel.subscribe", "channel_id": channel.id, "user_ids": [self.member.id]},
        )
        self.assertEqual(receipt["outcome"], "succeeded")
        self.assertTrue(self._subscribed(self.member, channel))
        self.assertTrue(
            RealmAuditLog.objects.filter(
                realm=self.realm,
                event_type=AuditLogEventType.SUBSCRIPTION_CREATED,
                modified_stream=channel,
                modified_user=self.member,
                acting_user=self.owner,
            ).exists()
        )

    def test_channel_unsubscribe_tool_removes_member_with_audit_log(self) -> None:
        channel = self.subscribe(self.owner, "team-updates")
        self.subscribe(self.member, "team-updates")
        receipt = self._run_tool(
            self.owner,
            self.profile,
            {"tool": "channel.unsubscribe", "channel_id": channel.id, "user_ids": [self.member.id]},
        )
        self.assertEqual(receipt["outcome"], "succeeded")
        self.assertFalse(self._subscribed(self.member, channel))
        self.assertTrue(
            RealmAuditLog.objects.filter(
                realm=self.realm,
                event_type=AuditLogEventType.SUBSCRIPTION_DEACTIVATED,
                modified_stream=channel,
                modified_user=self.member,
                acting_user=self.owner,
            ).exists()
        )

    def test_group_create_tool_creates_group_with_audit_log(self) -> None:
        receipt = self._run_tool(
            self.owner,
            self.profile,
            {
                "tool": "group.create",
                "name": "launch-team",
                "description": "",
                "member_user_ids": [self.member.id],
            },
        )
        self.assertEqual(receipt["outcome"], "succeeded")
        group = NamedUserGroup.objects.get(realm=self.realm, name="launch-team")
        self.assertEqual(receipt["objects"]["group_id"], group.id)
        self.assertTrue(
            RealmAuditLog.objects.filter(
                realm=self.realm,
                event_type=AuditLogEventType.USER_GROUP_CREATED,
                modified_user_group=group,
                acting_user=self.owner,
            ).exists()
        )

    def _create_group_via_tool(self, name: str) -> NamedUserGroup:
        receipt = execute_team_tool(
            self.owner,
            self.profile,
            GroupCreateInput(tool="group.create", name=name, description="", member_user_ids=[]),
        )
        assert receipt["outcome"] == "succeeded"
        return NamedUserGroup.objects.get(id=receipt["objects"]["group_id"])

    def test_group_add_members_tool_adds_member_with_audit_log(self) -> None:
        group = self._create_group_via_tool("launch-team")
        receipt = self._run_tool(
            self.owner,
            self.profile,
            {"tool": "group.add_members", "group_id": group.id, "user_ids": [self.member.id]},
        )
        self.assertEqual(receipt["outcome"], "succeeded")
        self.assertIn(self.member.id, receipt["objects"]["user_ids"])
        self.assertTrue(
            RealmAuditLog.objects.filter(
                realm=self.realm,
                event_type=AuditLogEventType.USER_GROUP_DIRECT_USER_MEMBERSHIP_ADDED,
                modified_user_group=group,
                modified_user=self.member,
                acting_user=self.owner,
            ).exists()
        )

    def test_group_remove_members_tool_removes_member_with_audit_log(self) -> None:
        group = self._create_group_via_tool("launch-team")
        execute_team_tool(
            self.owner,
            self.profile,
            GroupAddMembersInput(
                tool="group.add_members", group_id=group.id, user_ids=[self.member.id]
            ),
        )
        receipt = self._run_tool(
            self.owner,
            self.profile,
            {"tool": "group.remove_members", "group_id": group.id, "user_ids": [self.member.id]},
        )
        self.assertEqual(receipt["outcome"], "succeeded")
        self.assertTrue(
            RealmAuditLog.objects.filter(
                realm=self.realm,
                event_type=AuditLogEventType.USER_GROUP_DIRECT_USER_MEMBERSHIP_REMOVED,
                modified_user_group=group,
                modified_user=self.member,
                acting_user=self.owner,
            ).exists()
        )

    def test_topic_post_tool_sends_message_as_bot(self) -> None:
        self.subscribe(self.owner, "plan")
        self.subscribe(self.profile.bot_user, "plan")
        channel = get_stream("plan", self.realm)
        receipt = self._run_tool(
            self.owner,
            self.profile,
            {
                "tool": "topic.post",
                "channel_id": channel.id,
                "topic": "Kickoff",
                "content": "We start Monday.",
            },
        )
        self.assertEqual(receipt["outcome"], "succeeded")
        message = Message.objects.get(id=receipt["objects"]["message_id"])
        self.assertEqual(message.sender_id, self.profile.bot_user_id)
        self.assertEqual(message.content, "We start Monday.")

    def test_topic_add_person_tool_subscribes_and_mentions(self) -> None:
        self.subscribe(self.owner, "plan")
        self.subscribe(self.profile.bot_user, "plan")
        channel = get_stream("plan", self.realm)
        receipt = self._run_tool(
            self.owner,
            self.profile,
            {
                "tool": "topic.add_person",
                "channel_id": channel.id,
                "topic": "Kickoff",
                "user_ids": [self.member.id],
            },
        )
        self.assertEqual(receipt["outcome"], "succeeded")
        self.assertTrue(self._subscribed(self.member, channel))
        message = Message.objects.get(id=receipt["objects"]["message_id"])
        self.assertIn(str(self.member.id), message.content)

    def test_topic_add_person_follows_topic_only_when_setting_enabled(self) -> None:
        self.subscribe(self.owner, "plan")
        self.subscribe(self.profile.bot_user, "plan")
        channel = get_stream("plan", self.realm)
        follower = self.member
        follower.automatically_follow_topics_where_mentioned = True
        follower.save(update_fields=["automatically_follow_topics_where_mentioned"])
        non_follower = self.example_user("cordelia")
        non_follower.automatically_follow_topics_where_mentioned = False
        non_follower.save(update_fields=["automatically_follow_topics_where_mentioned"])

        self._run_tool(
            self.owner,
            self.profile,
            {
                "tool": "topic.add_person",
                "channel_id": channel.id,
                "topic": "Kickoff",
                "user_ids": [follower.id, non_follower.id],
            },
        )
        self.assertEqual(
            get_topic_visibility_policy(follower, channel.id, "Kickoff"),
            UserTopic.VisibilityPolicy.FOLLOWED,
        )
        self.assertEqual(
            get_topic_visibility_policy(non_follower, channel.id, "Kickoff"),
            UserTopic.VisibilityPolicy.INHERIT,
        )

    def test_topic_resolve_tool_marks_topic_resolved(self) -> None:
        self.subscribe(self.owner, "plan")
        self.subscribe(self.profile.bot_user, "plan")
        channel = get_stream("plan", self.realm)
        self.send_stream_message(self.owner, "plan", "Kickoff plan", topic_name="Kickoff")
        receipt = self._run_tool(
            self.owner,
            self.profile,
            {
                "tool": "topic.resolve",
                "channel_id": channel.id,
                "topic": "Kickoff",
                "resolved": True,
            },
        )
        self.assertEqual(receipt["outcome"], "succeeded")
        self.assertTrue(
            Message.objects.filter(
                recipient_id=channel.recipient_id, subject__startswith="✔"
            ).exists()
        )

    def test_topic_move_tool_renames_topic(self) -> None:
        self.subscribe(self.owner, "plan")
        self.subscribe(self.profile.bot_user, "plan")
        channel = get_stream("plan", self.realm)
        self.send_stream_message(self.owner, "plan", "Kickoff plan", topic_name="Kickoff")
        receipt = self._run_tool(
            self.owner,
            self.profile,
            {
                "tool": "topic.move",
                "channel_id": channel.id,
                "topic": "Kickoff",
                "new_topic": "Kickoff plan",
                "new_channel_id": None,
            },
        )
        self.assertEqual(receipt["outcome"], "succeeded")
        self.assertTrue(
            Message.objects.filter(
                recipient_id=channel.recipient_id, subject="Kickoff plan"
            ).exists()
        )

    # ---- permission denial writes nothing ----

    def test_tool_fails_without_commander_zulip_permission_and_writes_nothing(self) -> None:
        admins = get_role_based_system_groups_dict(self.realm)[SystemGroups.ADMINISTRATORS]
        do_change_realm_permission_group_setting(
            self.realm, "can_create_private_channel_group", admins, acting_user=self.owner
        )
        before = RealmAuditLog.objects.count()
        receipt = execute_team_tool(
            self.member,
            self.profile,
            ChannelCreateInput(
                tool="channel.create",
                name="secret-plans",
                description="",
                is_private=True,
                subscriber_user_ids=[],
            ),
        )
        self.assertEqual(receipt["outcome"], "failed")
        self.assertIn("Insufficient permission", receipt["error"])
        self.assertEqual(RealmAuditLog.objects.count(), before)
        with self.assertRaises(Stream.DoesNotExist):
            get_stream("secret-plans", self.realm)

    def test_topic_post_denies_commander_without_channel_access_even_though_bot_could_post(
        self,
    ) -> None:
        self.subscribe(self.owner, "leadership", invite_only=True)
        self.subscribe(self.profile.bot_user, "leadership", invite_only=True)
        channel = get_stream("leadership", self.realm)
        # The bot can post through its owner's access; confirm that directly.
        access_stream_for_send_message(self.profile.bot_user, channel, forwarder_user_profile=None)
        before = Message.objects.count()
        receipt = execute_team_tool(
            self.member,
            self.profile,
            TopicPostInput(tool="topic.post", channel_id=channel.id, topic="Roadmap", content="Hi"),
        )
        self.assertEqual(receipt["outcome"], "failed")
        self.assertEqual(Message.objects.count(), before)

    # ---- confirmation rule (contract 2.4) ----

    def test_team_find_never_needs_confirmation(self) -> None:
        job = self._dispatch(self.owner, self.profile)
        attempt = self._claim(job)
        operation = self._propose(
            job, attempt, {"tool": "team.find", "query": "a", "kinds": ["person"]}
        )
        self.assertEqual(operation.status, "authorized")
        self.assertFalse(agents.AgentApproval.objects.filter(operation=operation).exists())

    def test_channel_unsubscribe_always_needs_confirmation(self) -> None:
        channel = self.subscribe(self.owner, "team-updates")
        self.subscribe(self.member, "team-updates")
        job = self._dispatch(self.owner, self.profile)
        attempt = self._claim(job)
        operation = self._propose(
            job,
            attempt,
            {"tool": "channel.unsubscribe", "channel_id": channel.id, "user_ids": [self.member.id]},
        )
        self.assertEqual(operation.status, "proposed")
        job.refresh_from_db()
        self.assertEqual(job.status, "waiting_for_approval")

    def test_write_tool_needs_confirmation_for_non_owner_commander(self) -> None:
        self._share_profile(self.admin2)
        job = self._dispatch(self.admin2, self.profile)
        attempt = self._claim(job)
        operation = self._propose(
            job,
            attempt,
            {
                "tool": "channel.create",
                "name": "shared-agent-channel",
                "description": "",
                "is_private": False,
                "subscriber_user_ids": [],
            },
        )
        self.assertEqual(operation.status, "proposed")

    # ---- receipts, idempotency, and uncertain outcomes (contract 2.6 item 3-4) ----

    def test_execute_stores_receipt_and_emits_team_executed_event(self) -> None:
        job = self._dispatch(self.owner, self.profile)
        attempt = self._claim(job)
        operation = self._propose(
            job, attempt, {"tool": "team.find", "query": "a", "kinds": ["person"]}
        )
        self._execute(job, attempt, operation)
        operation.refresh_from_db()
        self.assertEqual(operation.status, "succeeded")
        self.assertIsNotNone(operation.server_receipt)
        self.assertTrue(
            agents.AgentAuditEvent.objects.filter(job=job, type="team.executed").exists()
        )

    def test_repeated_execute_returns_same_receipt_and_creates_one_channel(self) -> None:
        job = self._dispatch(self.owner, self.profile)
        attempt = self._claim(job)
        operation = self._propose(
            job,
            attempt,
            {
                "tool": "channel.create",
                "name": "once-only",
                "description": "",
                "is_private": False,
                "subscriber_user_ids": [],
            },
        )
        first = self._execute(job, attempt, operation)
        second = self._execute(job, attempt, operation)
        self.assertEqual(first["server_receipt"], second["server_receipt"])
        self.assertEqual(
            get_stream("once-only", self.realm).id, first["server_receipt"]["objects"]["channel_id"]
        )

    def test_phase_three_retries_busy_and_stores_the_receipt(self) -> None:
        """RL-1: phase 3 retries on lock contention instead of repeating phase 2."""
        from unittest.mock import patch

        from zerver.actions import agent_approvals
        from zerver.lib.agent_context import AgentBusy

        job = self._dispatch(self.owner, self.profile)
        attempt = self._claim(job)
        operation = self._propose(
            job,
            attempt,
            {
                "tool": "channel.create",
                "name": "retry-once",
                "description": "",
                "is_private": False,
                "subscriber_user_ids": [],
            },
        )
        original = agent_approvals._store_team_receipt
        attempts: list[object] = []

        def flaky(*args: object, **kwargs: object) -> dict[str, object]:
            attempts.append(None)
            if len(attempts) <= 2:
                raise AgentBusy("Agent authority is busy. Retry this request.")
            return original(*args, **kwargs)

        with (
            patch.object(agent_approvals, "_store_team_receipt", side_effect=flaky),
            patch("zerver.actions.agent_approvals.time.sleep") as sleep_mock,
        ):
            result = self._execute(job, attempt, operation)
        self.assertEqual(len(attempts), 3)
        self.assertEqual([call.args[0] for call in sleep_mock.call_args_list], [0.05, 0.1])
        assert result["server_receipt"] is not None
        self.assertEqual(result["server_receipt"]["outcome"], "succeeded")
        # Phase 2 (the actual channel creation) never repeated across retries.
        self.assertEqual(Stream.objects.filter(realm=self.realm, name="retry-once").count(), 1)

    def test_execute_with_started_operation_and_no_receipt_raises_outcome_unknown(self) -> None:
        job = self._dispatch(self.owner, self.profile)
        attempt = self._claim(job)
        operation = self._propose(
            job, attempt, {"tool": "team.find", "query": "a", "kinds": ["person"]}
        )
        # Simulate a crash between phase 1 (consume) and phase 3 (receipt).
        approvals.consume_operation(
            job.runner,
            job.id,
            attempt.id,
            attempt.lease_epoch,
            operation_id=operation.operation_id,
            expected_version=operation.version,
            operation_hash=operation.argument_digest,
        )
        with self.assertRaises(approvals.OutcomeUnknownError):
            self._execute(job, attempt, operation)

    def test_manage_reply_lists_an_uncertain_step_and_publishes(self) -> None:
        """AD-24: an uncertain team.manage step never blocks publication; the
        reply lists it instead of silently dropping or falsely confirming it."""
        job = self._dispatch(self.owner, self.profile)
        attempt = self._claim(job)
        operation = self._propose(
            job,
            attempt,
            {
                "tool": "channel.create",
                "name": "uncertain-step",
                "description": "",
                "is_private": False,
                "subscriber_user_ids": [],
            },
        )
        # Simulate a crash between phase 1 (consume) and phase 3 (receipt): the
        # operation is "started" with no server_receipt, so its outcome is unknown.
        approvals.consume_operation(
            job.runner,
            job.id,
            attempt.id,
            attempt.lease_epoch,
            operation_id=operation.operation_id,
            expected_version=operation.version,
            operation_hash=operation.argument_digest,
        )
        lines = team_manage_result_lines(job)
        self.assertEqual(len(lines), 1)
        self.assertIn("uncertain-step", lines[0])
        self.assertIn(
            "This step may have finished. Check the channel before you try again.", lines[0]
        )

        summary_bytes = b"Done."
        with (
            tempfile.TemporaryDirectory() as directory,
            override_settings(AGENT_ARTIFACT_ROOT=directory),
        ):
            artifact = store_artifact(
                job.runner,
                job.id,
                attempt.id,
                attempt.lease_epoch,
                chunks=[summary_bytes],
                checksum=hashlib.sha256(summary_bytes).hexdigest(),
                kind="summary",
                filename="summary.txt",
                media_type="text/plain",
            )
            job.refresh_from_db()
            job.result_proposal = serialize_payload(
                ResultPayload(summary="Done.", artifact_ids=[artifact.id])
            )
            job.status = "verifying"
            job.save(update_fields=["result_proposal", "status"])
            attempt.refresh_from_db()
            attempt.active = False
            attempt.process_state = "stopped"
            attempt.stopped_at = now()
            attempt.ended_at = now()
            attempt.save(update_fields=["active", "process_state", "stopped_at", "ended_at"])
            # publish_result no longer blocks on this operation's unresolved outcome.
            receipt = publish_result(job.id)
        message = Message.objects.get(id=receipt["message_id"])
        self.assertIn("uncertain-step", message.content)
        self.assertIn(
            "This step may have finished. Check the channel before you try again.",
            message.content,
        )

    def test_uncertain_team_step_does_not_block_resume_of_a_manage_job(self) -> None:
        job = self._dispatch(self.owner, self.profile)
        attempt = self._claim(job)
        operation = self._propose(
            job,
            attempt,
            {
                "tool": "channel.create",
                "name": "resume-uncertain",
                "description": "",
                "is_private": False,
                "subscriber_user_ids": [],
            },
        )
        approvals.consume_operation(
            job.runner,
            job.id,
            attempt.id,
            attempt.lease_epoch,
            operation_id=operation.operation_id,
            expected_version=operation.version,
            operation_hash=operation.argument_digest,
        )
        operation.refresh_from_db()
        self.assertEqual(operation.status, "started")
        agents.AgentJob.objects.filter(id=job.id).update(
            status="interrupted", blocked_reason="stop_unconfirmed"
        )
        agents.AgentAttempt.objects.filter(id=attempt.id).update(
            active=False, process_state="stopped", stopped_at=now(), ended_at=now()
        )
        job.refresh_from_db()
        resumed = agent_jobs.resume_job(self.owner, job.id, job.version)
        self.assertEqual(resumed.status, "queued")

    def test_execute_rejects_changed_operation_hash_after_approval(self) -> None:
        channel = self.subscribe(self.owner, "team-updates")
        self.subscribe(self.member, "team-updates")
        job = self._dispatch(self.owner, self.profile)
        attempt = self._claim(job)
        operation = self._propose(
            job,
            attempt,
            {"tool": "channel.unsubscribe", "channel_id": channel.id, "user_ids": [self.member.id]},
        )
        self._approve(self.owner, operation)
        with self.assertRaises(ValueError):
            self._execute(job, attempt, operation, operation_hash="f" * 64)
        self.assertTrue(self._subscribed(self.member, channel))

    def test_only_requester_can_decide_team_manage_approval(self) -> None:
        self._share_profile(self.admin2)
        self.subscribe(self.admin2, "agent-tasks")
        channel = self.subscribe(self.owner, "team-updates")
        self.subscribe(self.member, "team-updates")
        job = self._dispatch(self.owner, self.profile)
        attempt = self._claim(job)
        operation = self._propose(
            job,
            attempt,
            {"tool": "channel.unsubscribe", "channel_id": channel.id, "user_ids": [self.member.id]},
        )
        approval = agents.AgentApproval.objects.get(operation=operation)
        with self.assertRaises(AgentAccessDenied):
            approvals.decide_approval(
                self.admin2,
                approval.id,
                expected_version=approval.version,
                operation_hash=operation.argument_digest,
                nonce=approval.nonce,
                decision="approved",
            )

    # ---- job kind and mode gates ----

    def test_answer_job_cannot_propose_team_manage(self) -> None:
        answer_profile = create_profile(
            self.owner,
            name="Answer agent",
            runner=self.runner,
            adapter_id="acp",
            adapter_version="1",
            idempotency_key=uuid4(),
        )
        setup = agents.AgentSetupOperation.objects.get(profile=answer_profile)
        record_readiness(
            self.runner,
            setup,
            {
                "schema_version": 1,
                "profile_id": str(answer_profile.id),
                "profile_revision": answer_profile.revision,
                "runner_id": str(self.runner.id),
                "descriptor_digest": setup.descriptor_digest,
                "configuration_digest": setup.configuration_digest,
                "state": "ready",
                "capabilities": {"chat_ready": True, "config_version": 1},
            },
        )
        answer_profile.refresh_from_db()
        answer_profile = enable_profile(
            self.owner, answer_profile, expected_revision=answer_profile.revision
        )
        job = self._dispatch(self.owner, answer_profile)
        self.assertEqual(job.job_kind, "answer")
        attempt = self._claim(job)
        with self.assertRaises(ValueError):
            self._propose(job, attempt, {"tool": "team.find", "query": "a", "kinds": ["person"]})

    def test_non_administrator_cannot_save_manage_mode(self) -> None:
        with self.assertRaises(ValueError):
            create_profile(
                self.member,
                name="Rogue admin agent",
                runner=self.runner,
                adapter_id="acp",
                adapter_version="1",
                default_mode="manage",
                idempotency_key=uuid4(),
            )

    def test_sender_outside_setting_gets_command_not_allowed(self) -> None:
        self.send_personal_message(self.member, self.profile.bot_user, "Please do something")
        receipt = agents.AgentDispatchReceipt.objects.get()
        self.assertEqual(receipt.decision, "rejected")
        self.assertEqual(receipt.reason, "command_not_allowed")
        self.assertEqual(agents.AgentJob.objects.count(), 0)

    def test_revoking_owner_admin_role_stops_commands_at_next_request(self) -> None:
        job = self._dispatch(self.owner, self.profile)
        attempt = self._claim(job)
        do_change_user_role(self.owner, UserProfile.ROLE_MEMBER, acting_user=None, notify=True)
        with self.assertRaises(AgentAccessDenied):
            self._propose(job, attempt, {"tool": "team.find", "query": "a", "kinds": ["person"]})

    # ---- final reply (contract 2.6 item 7) ----

    def test_final_reply_ends_with_executed_steps(self) -> None:
        job = self._dispatch(self.owner, self.profile)
        attempt = self._claim(job)
        operation = self._propose(
            job,
            attempt,
            {
                "tool": "channel.create",
                "name": "reply-check",
                "description": "",
                "is_private": False,
                "subscriber_user_ids": [],
            },
        )
        self._execute(job, attempt, operation)
        lines = team_manage_result_lines(job)
        self.assertEqual(len(lines), 1)
        self.assertIn("reply-check", lines[0])

        # Drive the attempt to the minimal "verifying, stopped" state that
        # publish_result requires; the runner-event pipeline that normally does
        # this is unrelated to the change under test.
        summary_bytes = b"Done."
        with (
            tempfile.TemporaryDirectory() as directory,
            override_settings(AGENT_ARTIFACT_ROOT=directory),
        ):
            artifact = store_artifact(
                job.runner,
                job.id,
                attempt.id,
                attempt.lease_epoch,
                chunks=[summary_bytes],
                checksum=hashlib.sha256(summary_bytes).hexdigest(),
                kind="summary",
                filename="summary.txt",
                media_type="text/plain",
            )
            job.refresh_from_db()
            job.result_proposal = serialize_payload(
                ResultPayload(summary="Done.", artifact_ids=[artifact.id])
            )
            job.status = "verifying"
            job.save(update_fields=["result_proposal", "status"])
            attempt.refresh_from_db()
            attempt.active = False
            attempt.process_state = "stopped"
            attempt.stopped_at = now()
            attempt.ended_at = now()
            attempt.save(update_fields=["active", "process_state", "stopped_at", "ended_at"])
            receipt = publish_result(job.id)
        message = Message.objects.get(id=receipt["message_id"])
        # Contract 3.3: the reply starts with a silent mention of the
        # requester and ends with the task link, the same envelope every
        # job result uses (see test_agents_lifecycle.py).
        self.assertTrue(message.content.startswith(silent_mention_syntax_for_user(job.requester)))
        self.assertIn("Done.", message.content)
        self.assertIn("reply-check", message.content)
        self.assertTrue(message.content.endswith(job_task_link(job)))

    # ---- review-finding regressions: authorization-bypass ----

    def test_owner_of_answer_profile_cannot_create_manage_job(self) -> None:
        """Authorization-bypass: create_job must gate job_kind "manage" on
        the profile's own mode, not only on being its owner. Before this
        fix, any owner of an answer-mode profile could request job_kind
        "manage" and get the team-tool catalog with no administrator gate."""
        self._grant(self.member, target_kind="runner", actions=["runner.use"], resource=self.runner)
        answer_profile = create_profile(
            self.member,
            name="Member's helper",
            runner=self.runner,
            adapter_id="acp",
            adapter_version="1",
            idempotency_key=uuid4(),
        )
        setup = agents.AgentSetupOperation.objects.get(profile=answer_profile)
        record_readiness(
            self.runner,
            setup,
            {
                "schema_version": 1,
                "profile_id": str(answer_profile.id),
                "profile_revision": answer_profile.revision,
                "runner_id": str(self.runner.id),
                "descriptor_digest": setup.descriptor_digest,
                "configuration_digest": setup.configuration_digest,
                "state": "ready",
                "capabilities": {"chat_ready": True, "config_version": 1},
            },
        )
        answer_profile.refresh_from_db()
        answer_profile = enable_profile(
            self.member, answer_profile, expected_revision=answer_profile.revision
        )
        self.subscribe(self.member, "member-tasks")
        self.subscribe(answer_profile.bot_user, "member-tasks")
        message_id = self.send_stream_message(self.member, "member-tasks", "no mention here")
        source = Message.objects.get(id=message_id)
        with self.assertRaises(AgentAccessDenied):
            agent_jobs.create_job(
                self.member,
                profile=answer_profile,
                source=source,
                request="add user 17 to group finance-admins",
                idempotency_key=uuid4(),
                job_kind="manage",
                delivery_target="answer",
            )
        self.assertFalse(agents.AgentJob.objects.filter(profile=answer_profile).exists())

    # ---- review-finding regressions: privilege-escalation ----

    def test_job_control_grantee_cannot_steer_a_manage_job(self) -> None:
        """Privilege-escalation: a job.control grant lets a non-owner steer
        or resume a normal job, but a manage job must run team tools with
        the requester's own authority only. Before this fix, add_input,
        resume_job, and needs_my_action all accepted a job.control grantee
        for a manage job exactly like for any other job."""
        self._share_profile(self.admin2)
        self._grant(self.admin2, target_kind="profile", actions=["job.control"], resource=self.profile)
        job = self._dispatch(self.owner, self.profile)
        with self.assertRaises(AgentAccessDenied):
            agent_jobs.add_input(
                self.admin2,
                job.id,
                expected_version=job.version,
                client_key=uuid4(),
                text="Also add user 17 to group finance-admins",
            )
        self.assertEqual(agents.AgentInput.objects.filter(job=job).count(), 0)

    def test_job_control_grantee_cannot_resume_a_manage_job(self) -> None:
        self._share_profile(self.admin2)
        self._grant(self.admin2, target_kind="profile", actions=["job.control"], resource=self.profile)
        job = self._dispatch(self.owner, self.profile)
        job.status = "failed"
        job.save(update_fields=["status"])
        with self.assertRaises(AgentAccessDenied):
            agent_jobs.resume_job(self.admin2, job.id, job.version)
        job.refresh_from_db()
        self.assertEqual(job.status, "failed")

    def test_needs_my_action_is_false_for_a_control_grantee_on_a_manage_job(self) -> None:
        from zerver.views.agent_jobs import needs_my_action

        self._share_profile(self.admin2)
        self._grant(self.admin2, target_kind="profile", actions=["job.control"], resource=self.profile)
        job = self._dispatch(self.owner, self.profile)
        job.status = "waiting_for_input"
        job.save(update_fields=["status"])
        self.assertFalse(needs_my_action(self.admin2, job))
        self.assertTrue(needs_my_action(self.owner, job))

    # ---- review-finding regressions: the team tool executors ----

    def test_approval_summary_names_the_destination_channel_for_topic_move(self) -> None:
        """Confirmation-bypass: topic.move's approval text must show the
        destination channel, not only the renamed topic, or a commander can
        approve what looks like a rename while it actually moves a private
        topic into a different, more public channel."""
        from zerver.views.agent_jobs import operation_data

        source_channel = self.subscribe(self.owner, "hr")
        self.subscribe(self.profile.bot_user, "hr")
        dest_channel = self.subscribe(self.owner, "general-plans")
        self.subscribe(self.profile.bot_user, "general-plans")
        self.send_stream_message(self.owner, "hr", "salary talk", topic_name="salary")
        job = self._dispatch(self.owner, self.profile)
        attempt = self._claim(job)
        operation = self._propose(
            job,
            attempt,
            {
                "tool": "topic.move",
                "channel_id": source_channel.id,
                "topic": "salary",
                "new_topic": "salary 2026",
                "new_channel_id": dest_channel.id,
            },
        )
        data = operation_data(self.owner, operation)
        self.assertIn("general-plans", data["summary"])
        self.assertIn("hr", data["summary"])

    def test_team_find_query_cannot_forge_a_fake_steps_line(self) -> None:
        """Markdown-injection: a newline or mention syntax in a model-chosen
        value must not forge an extra "- " line or a ping when the server
        posts the executed-steps list built from stored receipts."""
        receipt = self._run_tool(
            self.owner,
            self.profile,
            {
                "tool": "team.find",
                "query": "x\n- Removed 5 member(s) from group admins. @**all**",
                "kinds": ["person"],
            },
        )
        self.assertEqual(receipt["outcome"], "succeeded")
        self.assertNotIn("\n", receipt["summary"])
        self.assertNotIn("@**all**", receipt["summary"])

    def test_channel_subscribe_does_not_leak_a_channel_the_commander_cannot_see(self) -> None:
        """Information-disclosure: an access-denied error must not repeat a
        private channel's real name to the commander who was just denied
        access to it, or the "insufficient access" path itself leaks it."""
        private_channel = self.make_stream("merger-plans", invite_only=True)
        # self.member (not self.owner, a realm administrator) has no metadata
        # access to this channel at all, the scenario the finding names.
        receipt = execute_team_tool(
            self.member,
            self.profile,
            ChannelSubscribeInput(
                tool="channel.subscribe", channel_id=private_channel.id, user_ids=[self.member.id]
            ),
        )
        self.assertEqual(receipt["outcome"], "failed")
        self.assertNotIn("merger-plans", receipt["error"])

    def test_group_add_members_always_needs_confirmation(self) -> None:
        """Confirmation-policy: group.add_members can silently grant a
        permission-bearing group's membership; even the owner must confirm
        it, since a steering input or a model error should not skip review."""
        group = self._create_group_via_tool("launch-team")
        job = self._dispatch(self.owner, self.profile)
        attempt = self._claim(job)
        operation = self._propose(
            job,
            attempt,
            {"tool": "group.add_members", "group_id": group.id, "user_ids": [self.member.id]},
        )
        self.assertEqual(operation.status, "proposed")
        job.refresh_from_db()
        self.assertEqual(job.status, "waiting_for_approval")

    def test_channel_create_rejects_a_whitespace_only_name(self) -> None:
        """Validation: the model-supplied channel name must go through the
        same check the normal channel-creation path uses, so a whitespace-
        or control-character-only name cannot create a nameless channel."""
        receipt = execute_team_tool(
            self.owner,
            self.profile,
            ChannelCreateInput(
                tool="channel.create",
                name="   ",
                description="",
                is_private=False,
                subscriber_user_ids=[],
            ),
        )
        self.assertEqual(receipt["outcome"], "failed")
        self.assertFalse(Stream.objects.filter(realm=self.realm, name="").exists())


class AgentTeamToolsTransactionTests(ZulipTransactionTestCase):
    """Runs without ZulipTestCase's wrapping transaction, the same as
    execute_operation's phase 2 in production (contract 2.6 item 3: it runs
    outside agent_transaction so the chat-table lock stays short). That
    ambient transaction hides a select_for_update() call that needs its own
    transaction.atomic(), so a plain ZulipTestCase test cannot catch it.

    ZulipTransactionTestCase requires exact cleanup (it asserts every
    table's row set is unchanged at tearDown), so this builds the profile
    directly instead of through create_profile, to avoid also cleaning up a
    new bot user and setup operation.
    """

    def test_group_add_and_remove_members_tools_run_outside_an_ambient_transaction(self) -> None:
        owner = self.example_user("hamlet")
        member = self.example_user("othello")
        realm = owner.realm
        initial_audits = set(RealmAuditLog.objects.values_list("id", flat=True))
        initial_groups = set(UserGroup.objects.values_list("id", flat=True))
        initial_recipients = set(Recipient.objects.values_list("id", flat=True))
        initial_dm_groups = set(DirectMessageGroup.objects.values_list("id", flat=True))
        runner = agents.AgentRunner.objects.create(
            realm=realm, owner=owner, name="txn-runner", fingerprint="c" * 64
        )
        profile = agents.AgentProfile.objects.create(
            realm=realm,
            owner=owner,
            runner=runner,
            bot_user=self.example_user("default_bot"),
            name="Txn agent",
            adapter_id="acp",
            adapter_version="1",
        )
        try:
            group = check_add_user_group(realm, "txn-group", [owner], "", acting_user=owner)

            receipt = execute_team_tool(
                owner,
                profile,
                GroupAddMembersInput(
                    tool="group.add_members", group_id=group.id, user_ids=[member.id]
                ),
            )
            self.assertEqual(receipt["outcome"], "succeeded")

            receipt = execute_team_tool(
                owner,
                profile,
                GroupRemoveMembersInput(
                    tool="group.remove_members", group_id=group.id, user_ids=[member.id]
                ),
            )
            self.assertEqual(receipt["outcome"], "succeeded")
        finally:
            profile.delete()
            runner.delete()
            RealmAuditLog.objects.exclude(id__in=initial_audits).delete()
            UserGroup.objects.exclude(id__in=initial_groups).delete()
            DirectMessageGroup.objects.exclude(id__in=initial_dm_groups).delete()
            Recipient.objects.exclude(id__in=initial_recipients).delete()
