"""Agent message admission uses renderer provenance and durable lifecycle records."""

import json
from uuid import uuid4

from django.db import transaction
from typing_extensions import override

from zerver.actions.agents import create_profile, enable_profile, record_readiness
from zerver.lib.markdown import render_message_markdown
from zerver.lib.test_classes import ZulipTestCase
from zerver.models import Message, agents
from zerver.models.clients import get_client


class AgentMessageAdmissionTests(ZulipTestCase):
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
            name="Message admission",
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
        self.profile = create_profile(
            self.owner,
            name="Message admission",
            runner=self.runner,
            adapter_id="acp",
            adapter_version="1",
            idempotency_key=uuid4(),
        )
        setup = agents.AgentSetupOperation.objects.get(profile=self.profile)
        record_readiness(
            self.runner,
            setup,
            {
                "schema_version": 1,
                "profile_id": str(self.profile.id),
                "profile_revision": 1,
                "runner_id": str(self.runner.id),
                "descriptor_digest": setup.descriptor_digest,
                "configuration_digest": setup.configuration_digest,
                "state": "ready",
                "capabilities": {"chat_ready": True, "config_version": 1},
            },
        )
        self.profile = enable_profile(self.owner, self.profile, expected_revision=1)

    def test_personal_agent_mention_creates_one_durable_job(self) -> None:
        self.send_personal_message(
            self.owner,
            self.profile.bot_user,
            f"Please answer @**{self.profile.bot_user.full_name}|{self.profile.bot_user_id}**",
        )

        self.assertEqual(agents.AgentDispatchReceipt.objects.count(), 1)
        receipt = agents.AgentDispatchReceipt.objects.get()
        self.assertEqual(receipt.decision, "accepted", receipt.reason)
        self.assertEqual(agents.AgentJob.objects.count(), 1)
        self.assertEqual(agents.AgentOutbox.objects.filter(event_type="job.wake").count(), 1)

    def test_same_agent_send_key_replays_the_first_message(self) -> None:
        key = str(uuid4())
        payload = {
            "type": "direct",
            "to": json.dumps([self.profile.bot_user.email]),
            "content": f"Please answer @**{self.profile.bot_user.full_name}|{self.profile.bot_user_id}**",
            "agent_send_key": key,
        }
        first = self.api_post(self.owner, "/api/v1/messages", payload)
        second = self.api_post(self.owner, "/api/v1/messages", payload)

        self.assertEqual(first.status_code, 200, first.content)
        self.assertEqual(second.status_code, 200, second.content)
        self.assertEqual(json.loads(first.content)["id"], json.loads(second.content)["id"])
        self.assertEqual(agents.AgentSendIntent.objects.count(), 1)
        self.assertEqual(agents.AgentJob.objects.count(), 1)

        with transaction.atomic():
            changed = self.api_post(
                self.owner,
                "/api/v1/messages",
                {**payload, "content": "A changed payload"},
            )
        self.assertEqual(changed.status_code, 400)
        self.assertEqual(agents.AgentSendIntent.objects.count(), 1)
        self.assertEqual(agents.AgentJob.objects.count(), 1)

    def test_one_human_one_agent_direct_message_is_an_implicit_trigger(self) -> None:
        self.send_personal_message(self.owner, self.profile.bot_user, "Please answer")

        receipt = agents.AgentDispatchReceipt.objects.get()
        self.assertEqual(receipt.trigger_kind, "direct_message")
        self.assertEqual(receipt.decision, "accepted")
        self.assertEqual(agents.AgentJob.objects.count(), 1)

    def test_renderer_records_only_non_silent_personal_mention_provenance(self) -> None:
        message = Message(
            sender=self.owner, sending_client=get_client("test"), realm=self.owner.realm
        )
        mention = f"@**{self.profile.bot_user.full_name}|{self.profile.bot_user_id}**"

        self.assertEqual(
            render_message_markdown(message, mention).personal_mention_user_ids,
            {self.profile.bot_user_id},
        )
        for content in [
            f"@_**{self.profile.bot_user.full_name}|{self.profile.bot_user_id}**",
            f"`{mention}`",
            f"> {mention}",
        ]:
            with self.subTest(content=content):
                self.assertEqual(
                    render_message_markdown(message, content).personal_mention_user_ids, set()
                )

    def test_deleted_send_keeps_identity_and_lookup(self) -> None:
        key = str(uuid4())
        data = {
            "type": "direct",
            "to": json.dumps([self.profile.bot_user_id]),
            "content": "original",
            "agent_send_key": key,
        }
        first = self.api_post(self.owner, "/api/v1/messages", data)
        self.assert_json_success(first)
        message_id = first.json()["id"]
        Message.objects.filter(id=message_id).delete()
        second = self.api_post(self.owner, "/api/v1/messages", data)
        self.assert_json_success(second)
        self.assertEqual(second.json()["id"], message_id)
        self.assertFalse(Message.objects.filter(id=message_id).exists())
        lookup = self.api_get(self.owner, f"/api/v1/agent/send-intents/{key}")
        self.assert_json_success(lookup)
        self.assertTrue(lookup.json()["deleted"])

    def test_mixed_batch_and_duplicate_keys_preserve_order(self) -> None:
        from uuid import UUID

        from zerver.actions.message_send import check_message, do_send_messages
        from zerver.lib.addressee import Addressee
        from zerver.lib.message import SendMessageRequest

        key = uuid4()

        def prepare(text: str, send_key: UUID | None = None) -> SendMessageRequest:
            return check_message(
                self.owner,
                get_client("test"),
                Addressee.for_user_profile(self.profile.bot_user),
                text,
                realm=self.owner.realm,
                agent_send_key=send_key,
            )

        first = do_send_messages([prepare("first", key)])[0]
        results = do_send_messages(
            [prepare("new"), prepare("first", key), prepare("duplicate", uuid4())]
        )
        self.assertEqual(
            [Message.objects.get(id=item.message_id).content for item in results],
            ["new", "first", "duplicate"],
        )
        self.assertEqual(results[1].message_id, first.message_id)
        other = uuid4()
        repeated = do_send_messages([prepare("same", other), None, prepare("same", other)])
        self.assertEqual(repeated[0].message_id, repeated[1].message_id)
        self.assertEqual(agents.AgentJob.objects.count(), 4)

    def test_provenance_matrix_and_bot_and_edit_exclusions(self) -> None:
        from zerver.actions.user_groups import check_add_user_group

        mention = f"@**{self.profile.bot_user.full_name}|{self.profile.bot_user_id}**"
        group = check_add_user_group(
            self.owner.realm, "Agent group", [self.profile.bot_user], acting_user=self.owner
        )
        self.subscribe(self.profile.bot_user, "Denmark")
        excluded = [
            f"@_**{self.profile.bot_user.full_name}|{self.profile.bot_user_id}**",
            f"`{mention}`",
            f"```\n{mention}\n```",
            f"> {mention}",
            f"@*{group.name}*",
            "@**all**",
            "@**topic**",
        ]
        for content in excluded:
            with self.subTest(content=content):
                msg_id = self.send_stream_message(self.owner, "Denmark", content)
                self.assertFalse(
                    agents.AgentDispatchReceipt.objects.filter(source_message_id=msg_id).exists()
                )
        accepted = self.send_stream_message(
            self.owner, "Denmark", f"@*{group.name}* {mention} {mention}"
        )
        self.assertEqual(
            agents.AgentDispatchReceipt.objects.filter(source_message_id=accepted).count(), 1
        )
        self.assertEqual(agents.AgentJob.objects.count(), 1)
        self.send_stream_message(self.profile.bot_user, "Denmark", mention)
        self.assertEqual(agents.AgentJob.objects.count(), 1)
        original = self.send_stream_message(self.owner, "Denmark", "Before edit")
        self.login_user(self.owner)
        response = self.client_patch(f"/json/messages/{original}", {"content": mention})
        self.assert_json_success(response)
        self.assertEqual(agents.AgentJob.objects.count(), 1)
        self.send_group_direct_message(
            self.owner, [self.profile.bot_user, self.example_user("iago")], "Group DM"
        )
        self.assertEqual(agents.AgentJob.objects.count(), 1)
        self.send_group_direct_message(
            self.owner, [self.profile.bot_user, self.example_user("iago")], mention
        )
        self.assertEqual(agents.AgentJob.objects.count(), 2)

    def test_preflight_destination_denial_and_queue_receipt(self) -> None:
        self.subscribe(self.profile.bot_user, "Denmark")
        from zerver.models import Stream

        stream = Stream.objects.get(realm=self.owner.realm, name="Denmark")
        data = {
            "schema_version": 1,
            "profile_ids": [str(self.profile.id)],
            "destination": {"kind": "stream", "stream_id": stream.id, "topic": "test"},
        }
        before = Message.objects.count()
        response = self.api_post(
            self.owner, "/api/v1/agent/message-preflight", {"payload": json.dumps(data)}
        )
        self.assert_json_success(response)
        self.assertEqual(response.json()["decisions"][0]["decision"], "accepted")
        self.assertEqual(Message.objects.count(), before)
        agents.AgentProfile.objects.filter(id=self.profile.id).update(desired_state="paused")
        message_id = self.send_stream_message(
            self.owner,
            "Denmark",
            f"@**{self.profile.bot_user.full_name}|{self.profile.bot_user_id}**",
        )
        self.assertEqual(
            agents.AgentDispatchReceipt.objects.get(source_message_id=message_id).decision,
            "rejected",
        )
        self.assertFalse(agents.AgentJob.objects.exists())
        self.assertFalse(agents.AgentOutbox.objects.exists())
        agents.AgentProfile.objects.filter(id=self.profile.id).update(desired_state="enabled")
        agents.AgentRealmSettings.objects.filter(realm=self.owner.realm).update(
            profile_queue_limit=1
        )
        self.send_personal_message(self.owner, self.profile.bot_user, "First")
        full_queue_id = self.send_personal_message(self.owner, self.profile.bot_user, "Full queue")
        self.assertEqual(agents.AgentJob.objects.count(), 1)
        self.assertEqual(agents.AgentDispatchReceipt.objects.filter(decision="rejected").count(), 2)
        self.assertEqual(
            agents.AgentDispatchReceipt.objects.get(source_message_id=full_queue_id).reason,
            "queue_full",
        )

    def test_receipts_require_current_resource_access_and_sender_identity(self) -> None:
        message_id = self.send_personal_message(self.owner, self.profile.bot_user, "Private task")
        url = f"/api/v1/agent/messages/{message_id}/dispatch"
        self.assert_json_success(self.api_get(self.owner, url))
        self.assertEqual(self.api_get(self.example_user("iago"), url).status_code, 400)
        # A resource owner change removes the sender's runner grant, but chat remains readable.
        agents.AgentRunner.objects.filter(id=self.runner.id).update(owner=self.example_user("iago"))
        response = self.api_get(self.owner, url)
        self.assert_json_success(response)
        self.assertEqual(response.json()["dispatch_receipts"], [])

    def test_message_and_admission_rollback_together(self) -> None:
        from unittest.mock import patch

        before = Message.objects.count()
        with (
            patch(
                "zerver.actions.agent_dispatch._receipt", side_effect=RuntimeError("commit failure")
            ),
            self.assertRaisesRegex(RuntimeError, "commit failure"),
        ):
            self.send_personal_message(self.owner, self.profile.bot_user, "rollback")
        self.assertEqual(Message.objects.count(), before)
        self.assertFalse(agents.AgentJob.objects.exists())
        self.assertFalse(agents.AgentOutbox.objects.exists())

    def test_metadata_binds_target_and_draft_snapshot(self) -> None:
        key = str(uuid4())
        metadata = {
            "schema_version": 1,
            "profile_ids": [str(self.profile.id)],
            "draft_key": "draft-A",
            "visit_token": str(uuid4()),
            "draft_revision": 1,
        }
        data = {
            "type": "direct",
            "to": json.dumps([self.profile.bot_user_id]),
            "content": "Task",
            "agent_send_key": key,
            "agent_send_metadata": json.dumps(metadata),
        }
        first = self.api_post(self.owner, "/api/v1/messages", data)
        self.assert_json_success(first)
        replay = self.api_post(self.owner, "/api/v1/messages", data)
        self.assertEqual(first.json()["id"], replay.json()["id"])
        changed = self.api_post(
            self.owner,
            "/api/v1/messages",
            {**data, "agent_send_metadata": json.dumps({**metadata, "draft_revision": 2})},
        )
        self.assertEqual(changed.status_code, 400)
        mismatch = self.api_post(
            self.owner,
            "/api/v1/messages",
            {
                **data,
                "agent_send_key": str(uuid4()),
                "agent_send_metadata": json.dumps({**metadata, "profile_ids": [str(uuid4())]}),
            },
        )
        self.assertEqual(mismatch.status_code, 400)
        self.assertEqual(agents.AgentJob.objects.count(), 1)

    def test_complete_code_queues_and_incomplete_draft_promotes_same_id(self) -> None:
        from copy import deepcopy

        from zerver.actions import agent_jobs
        from zerver.actions.agents import register_repository, retry_profile_setup

        incomplete = create_profile(
            self.owner,
            name="Incomplete code",
            runner=self.runner,
            adapter_id="acp",
            adapter_version="1",
            default_mode="code",
            idempotency_key=uuid4(),
        )

        def ready(profile: agents.AgentProfile, code: bool) -> None:
            setup = agents.AgentSetupOperation.objects.filter(profile=profile).latest("created_at")
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
                        "config_version": profile.revision,
                    },
                },
            )
            enable_profile(self.owner, profile, expected_revision=profile.revision)

        ready(incomplete, False)
        self.send_personal_message(self.owner, incomplete.bot_user, "Fix this")
        draft = agents.AgentJob.objects.get(profile=incomplete)
        self.assertEqual(draft.status, "draft")
        self.assertEqual(agents.AgentDispatchReceipt.objects.get(job=draft).decision, "needs_input")
        self.assertFalse(agents.AgentOutbox.objects.filter(job=draft).exists())
        self.assertIsNone(agent_jobs.claim_work(self.runner, claim_key=uuid4()))
        repository = register_repository(
            self.owner,
            self.runner,
            workspace_alias="code",
            canonical_origin=None,
            allowed_refs=["main"],
            required_checks=[{"id": "test", "argv": ["true"]}],
        )
        endpoint = f"/api/v1/agent/jobs/{draft.id}/configure"
        request = {
            "schema_version": 1,
            "expected_version": draft.version,
            "repository_id": str(repository.id),
            "base_ref": "main",
        }
        denied = self.api_post(self.owner, endpoint, {"payload": json.dumps(request)})
        self.assertEqual(denied.status_code, 400)
        self.assertFalse(agents.AgentOutbox.objects.filter(job=draft).exists())
        # Simulate the owner configuration edit; only a matching new probe can authorize it.
        policy = deepcopy(incomplete.policy)
        policy["actions"] = ["context.read", "repository.read", "repository.edit", "checks.run"]
        agents.AgentProfile.objects.filter(id=incomplete.id).update(
            default_repository=repository,
            policy=policy,
            revision=2,
            enabled_revision=2,
            readiness_state="checking",
        )
        incomplete.refresh_from_db()
        denied = self.api_post(self.owner, endpoint, {"payload": json.dumps(request)})
        self.assertEqual(denied.status_code, 400)
        retry_profile_setup(self.owner, incomplete, retry_key=uuid4(), expected_revision=2)
        ready(incomplete, True)
        original_checks = repository.required_checks
        agents.AgentRepository.objects.filter(id=repository.id).update(
            required_checks=[{"id": "changed", "argv": ["false"]}]
        )
        stale = self.api_post(self.owner, endpoint, {"payload": json.dumps(request)})
        self.assertEqual(stale.status_code, 400)
        self.assertFalse(agents.AgentOutbox.objects.filter(job=draft).exists())
        agents.AgentRepository.objects.filter(id=repository.id).update(
            required_checks=original_checks
        )
        response = self.api_post(self.owner, endpoint, {"payload": json.dumps(request)})
        self.assert_json_success(response)
        self.assertEqual(response.json()["job"]["id"], str(draft.id))
        self.assertEqual(response.json()["job"]["status"], "queued")
        replay = self.api_post(self.owner, endpoint, {"payload": json.dumps(request)})
        self.assert_json_success(replay)
        self.assertEqual(
            agents.AgentOutbox.objects.filter(job=draft, event_type="job.wake").count(), 1
        )
        conflict = self.api_post(
            self.owner, endpoint, {"payload": json.dumps({**request, "base_ref": "other"})}
        )
        self.assertEqual(conflict.status_code, 400)
        second_id = self.send_personal_message(
            self.owner, incomplete.bot_user, "Another complete task"
        )
        second = agents.AgentJob.objects.get(source_message_id=second_id)
        self.assertEqual(
            (second.status, second.job_kind, second.delivery_target, second.base_ref),
            ("queued", "code", "patch", "main"),
        )
        # One wake row plus one accepted-notice marker row (contract 9.7).
        self.assertEqual(
            agents.AgentOutbox.objects.filter(job=second, event_type="job.wake").count(), 1
        )
        self.assertEqual(
            agents.AgentOutbox.objects.filter(job=second, event_type="status.notice").count(), 1
        )

    def test_followup_uses_selected_job_and_ordinary_mention_is_new(self) -> None:
        self.send_personal_message(self.owner, self.profile.bot_user, "First task")
        self.send_personal_message(self.owner, self.profile.bot_user, "Continue")
        jobs = list(agents.AgentJob.objects.order_by("created_at"))
        self.assertEqual(len(jobs), 2)
        key = str(uuid4())
        body = {
            "schema_version": 1,
            "expected_version": jobs[0].version,
            "client_key": key,
            "text": "Explicit follow-up",
        }
        url = f"/api/v1/agent/jobs/{jobs[0].id}/inputs"
        self.assert_json_success(self.api_post(self.owner, url, {"payload": json.dumps(body)}))
        self.assert_json_success(self.api_post(self.owner, url, {"payload": json.dumps(body)}))
        self.assertEqual(agents.AgentInput.objects.get().job_id, jobs[0].id)

    def test_scoped_grant_revocation_after_preflight_and_multiple_targets(self) -> None:
        from django.utils.timezone import now

        from zerver.models import Stream

        actor = self.example_user("iago")
        stream = Stream.objects.get(realm=self.owner.realm, name="Denmark")
        self.subscribe(self.profile.bot_user, "Denmark")
        scope = {"kind": "stream", "stream_id": stream.id, "topic": "test"}
        grant = agents.AgentGrant.objects.create(
            realm=self.owner.realm,
            owner=self.owner,
            target_kind="profile",
            profile=self.profile,
            principal_user=actor,
            actions=["profile.use", "context.read"],
            scope=scope,
        )
        agents.AgentGrant.objects.create(
            realm=self.owner.realm,
            owner=self.owner,
            target_kind="runner",
            runner=self.runner,
            principal_user=actor,
            actions=["runner.use"],
        )
        body = {"schema_version": 1, "profile_ids": [str(self.profile.id)], "destination": scope}
        response = self.api_post(
            actor, "/api/v1/agent/message-preflight", {"payload": json.dumps(body)}
        )
        self.assert_json_success(response)
        self.assertEqual(response.json()["decisions"][0]["decision"], "accepted")
        wrong = self.api_post(
            actor,
            "/api/v1/agent/message-preflight",
            {"payload": json.dumps({**body, "destination": {**scope, "topic": "other"}})},
        )
        self.assertEqual(wrong.json()["decisions"][0]["decision"], "rejected")
        grant.revoked_at = now()
        grant.save(update_fields=["revoked_at"])
        mention = f"@**{self.profile.bot_user.full_name}|{self.profile.bot_user_id}**"
        message_id = self.send_stream_message(actor, "Denmark", mention, topic_name="test")
        self.assertEqual(
            agents.AgentDispatchReceipt.objects.get(source_message_id=message_id).decision,
            "rejected",
        )
        self.assertFalse(agents.AgentJob.objects.exists())
        self.assertFalse(agents.AgentAttempt.objects.exists())
        self.assertFalse(agents.AgentOutbox.objects.exists())
        second = create_profile(
            self.owner,
            name="Second",
            runner=self.runner,
            adapter_id="acp",
            adapter_version="1",
            idempotency_key=uuid4(),
        )
        setup = agents.AgentSetupOperation.objects.get(profile=second)
        record_readiness(
            self.runner,
            setup,
            {
                "schema_version": 1,
                "profile_id": str(second.id),
                "profile_revision": 1,
                "runner_id": str(self.runner.id),
                "descriptor_digest": setup.descriptor_digest,
                "configuration_digest": setup.configuration_digest,
                "state": "ready",
                "capabilities": {"chat_ready": True, "config_version": 1},
            },
        )
        enable_profile(self.owner, second, expected_revision=1)
        self.subscribe(second.bot_user, "Denmark")
        message_id = self.send_stream_message(
            self.owner,
            "Denmark",
            mention + f" @**{second.bot_user.full_name}|{second.bot_user_id}**",
        )
        self.assertEqual(
            agents.AgentDispatchReceipt.objects.filter(
                source_message_id=message_id, decision="accepted"
            ).count(),
            2,
        )
        self.assertEqual(agents.AgentJob.objects.count(), 2)

    def test_queued_reasons_distinguish_offline_busy_and_unknown(self) -> None:
        from django.utils.timezone import now

        from zerver.actions import agent_jobs

        agents.AgentRunner.objects.filter(id=self.runner.id).update(status="offline")
        first = self.send_personal_message(self.owner, self.profile.bot_user, "Offline queue")
        receipt = agents.AgentDispatchReceipt.objects.get(source_message_id=first)
        self.assertEqual(receipt.reason, "runner_offline")
        assert receipt.job is not None
        self.assertEqual(receipt.job.status, "queued")
        self.assertFalse(agents.AgentAttempt.objects.exists())
        # A fresh heartbeat, not just the status field: the server treats a stale
        # heartbeat as unknown even when status still says "online".
        agents.AgentRunner.objects.filter(id=self.runner.id).update(
            status="online", last_heartbeat_at=now()
        )
        agent_jobs.claim_work(self.runner, claim_key=uuid4())
        second = self.send_personal_message(self.owner, self.profile.bot_user, "Busy queue")
        self.assertEqual(
            agents.AgentDispatchReceipt.objects.get(source_message_id=second).reason, "runner_busy"
        )
        agents.AgentRunner.objects.filter(id=self.runner.id).update(status="unknown")
        third = self.send_personal_message(self.owner, self.profile.bot_user, "Unknown queue")
        self.assertEqual(
            agents.AgentDispatchReceipt.objects.get(source_message_id=third).reason,
            "runner_unknown",
        )
        self.assertEqual(agents.AgentJob.objects.filter(status="queued").count(), 2)

    def test_not_shared_reason_when_sender_has_no_grant(self) -> None:
        member = self.example_user("othello")
        message_id = self.send_personal_message(member, self.profile.bot_user, "Unshared mention")
        receipt = agents.AgentDispatchReceipt.objects.get(source_message_id=message_id)
        self.assertEqual(receipt.decision, "rejected")
        self.assertEqual(receipt.reason, "not_shared")
        self.assertIsNone(receipt.job)

    def test_generic_rejection_stores_admission_denied(self) -> None:
        # Any create_job rejection other than a full queue is a coded, generic
        # "admission_denied" receipt (contract 9.9), never the raw exception text.
        agents.AgentRealmSettings.objects.filter(realm=self.owner.realm).update(enabled=False)
        message_id = self.send_personal_message(
            self.owner, self.profile.bot_user, "Please answer while disabled"
        )
        receipt = agents.AgentDispatchReceipt.objects.get(source_message_id=message_id)
        self.assertEqual(receipt.decision, "rejected")
        self.assertEqual(receipt.reason, "admission_denied")
        self.assertIsNone(receipt.job)

    def test_incomplete_coding_stores_configuration_needed(self) -> None:
        incomplete = create_profile(
            self.owner,
            name="Incomplete coding reason",
            runner=self.runner,
            adapter_id="acp",
            adapter_version="1",
            default_mode="code",
            idempotency_key=uuid4(),
        )
        setup = agents.AgentSetupOperation.objects.get(profile=incomplete)
        record_readiness(
            self.runner,
            setup,
            {
                "schema_version": 1,
                "profile_id": str(incomplete.id),
                "profile_revision": incomplete.revision,
                "runner_id": str(self.runner.id),
                "descriptor_digest": setup.descriptor_digest,
                "configuration_digest": setup.configuration_digest,
                "state": "ready",
                "capabilities": {"chat_ready": True, "config_version": incomplete.revision},
            },
        )
        incomplete.refresh_from_db()
        incomplete = enable_profile(self.owner, incomplete, expected_revision=incomplete.revision)
        message_id = self.send_personal_message(self.owner, incomplete.bot_user, "Fix this")
        receipt = agents.AgentDispatchReceipt.objects.get(source_message_id=message_id)
        self.assertEqual(receipt.decision, "needs_input")
        self.assertEqual(receipt.reason, "configuration_needed")

    def test_enabled_runtime_repair_admits_blocked_without_wake(self) -> None:
        from zerver.actions import agent_jobs

        agents.AgentProfile.objects.filter(id=self.profile.id).update(
            readiness_state="needs_action"
        )
        message_id = self.send_personal_message(
            self.owner, self.profile.bot_user, "After authentication expired"
        )
        receipt = agents.AgentDispatchReceipt.objects.get(source_message_id=message_id)
        self.assertEqual(receipt.decision, "accepted")
        assert receipt.job is not None
        self.assertEqual(receipt.job.status, "blocked")
        self.assertEqual(receipt.reason, "profile_needs_action")
        self.assertFalse(agents.AgentOutbox.objects.exists())
        self.assertIsNone(agent_jobs.claim_work(self.runner, claim_key=uuid4()))
        detail = self.api_get(self.owner, f"/api/v1/agent/jobs/{receipt.job_id}")
        self.assert_json_success(detail)
        self.assertEqual(detail.json()["job"]["requirements"][0]["action"], "probe_again")
        with self.assertRaises(ValueError):
            agent_jobs.resume_job(self.owner, receipt.job.id, receipt.job.version)

    def test_owner_completes_member_draft_without_rebinding_requester(self) -> None:
        from copy import deepcopy

        from django.utils.timezone import now

        from zerver.actions.agents import register_repository, retry_profile_setup

        member = self.example_user("iago")
        self.subscribe(self.profile.bot_user, "Denmark")
        agents.AgentProfile.objects.filter(id=self.profile.id).update(default_mode="code")
        actions = [
            "profile.use",
            "context.read",
            "repository.read",
            "repository.edit",
            "checks.run",
        ]
        grant = agents.AgentGrant.objects.create(
            realm=self.owner.realm,
            owner=self.owner,
            target_kind="profile",
            profile=self.profile,
            principal_user=member,
            actions=actions,
        )
        agents.AgentGrant.objects.create(
            realm=self.owner.realm,
            owner=self.owner,
            target_kind="runner",
            runner=self.runner,
            principal_user=member,
            actions=["runner.use"],
        )
        mention = f"@**{self.profile.bot_user.full_name}|{self.profile.bot_user_id}**"
        source_id = self.send_stream_message(member, "Denmark", mention)
        draft = agents.AgentJob.objects.get(source_message_id=source_id)
        self.assertEqual(draft.status, "draft")
        original_conversation = draft.conversation_id
        original_binding = deepcopy(draft.conversation.audience_binding)
        repository = register_repository(
            self.owner,
            self.runner,
            workspace_alias="member-code",
            canonical_origin=None,
            allowed_refs=["main"],
            required_checks=[{"id": "test", "argv": ["true"]}],
        )
        agents.AgentGrant.objects.create(
            realm=self.owner.realm,
            owner=self.owner,
            target_kind="repository",
            repository=repository,
            principal_user=member,
            actions=["repository.read", "repository.edit", "checks.run"],
        )
        policy = deepcopy(self.profile.policy)
        policy["actions"] = actions[1:]
        agents.AgentProfile.objects.filter(id=self.profile.id).update(
            default_repository=repository,
            policy=policy,
            revision=2,
            enabled_revision=2,
            readiness_state="checking",
        )
        self.profile.refresh_from_db()
        setup = retry_profile_setup(
            self.owner, self.profile, retry_key=uuid4(), expected_revision=2
        )
        record_readiness(
            self.runner,
            setup,
            {
                "schema_version": 1,
                "profile_id": str(self.profile.id),
                "profile_revision": 2,
                "runner_id": str(self.runner.id),
                "descriptor_digest": setup.descriptor_digest,
                "configuration_digest": setup.configuration_digest,
                "state": "ready",
                "capabilities": {
                    "chat_ready": True,
                    "code_ready": True,
                    "tool_calling": "passed",
                    "sandbox": "passed",
                    "config_version": 2,
                },
            },
        )
        enable_profile(self.owner, self.profile, expected_revision=2)
        body = {
            "schema_version": 1,
            "expected_version": draft.version,
            "repository_id": str(repository.id),
            "base_ref": "main",
        }
        url = f"/api/v1/agent/jobs/{draft.id}/configure"
        grant.revoked_at = now()
        grant.save(update_fields=["revoked_at"])
        denied = self.api_post(self.owner, url, {"payload": json.dumps(body)})
        self.assertEqual(denied.status_code, 400)
        draft.refresh_from_db()
        self.assertEqual(draft.status, "draft")
        self.assertEqual(draft.requester_id, member.id)
        self.assertFalse(agents.AgentOutbox.objects.filter(job=draft).exists())
        grant.revoked_at = None
        grant.save(update_fields=["revoked_at"])
        completed = self.api_post(self.owner, url, {"payload": json.dumps(body)})
        self.assert_json_success(completed)
        draft.refresh_from_db()
        self.assertEqual(draft.status, "queued")
        self.assertEqual(draft.requester_id, member.id)
        self.assertEqual(draft.conversation_id, original_conversation)
        self.assertEqual(draft.conversation.audience_binding, original_binding)
        self.assertEqual(
            agents.AgentAuditEvent.objects.get(job=draft, type="job.queued").actor_id, self.owner.id
        )
        self.assertEqual(agents.AgentOutbox.objects.filter(job=draft).count(), 1)
