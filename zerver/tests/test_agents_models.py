import json
from datetime import timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils.timezone import now
from typing_extensions import override

from zerver.lib.test_classes import ZulipTestCase
from zerver.models import UserGroup, agents


class AgentModelTests(ZulipTestCase):
    @override
    def setUp(self) -> None:
        super().setUp()
        self.owner = self.example_user("hamlet")
        self.realm = self.owner.realm
        self.runner = agents.AgentRunner.objects.create(
            realm=self.realm,
            owner=self.owner,
            name="Fixture",
            fingerprint="a" * 64,
        )
        self.profile = agents.AgentProfile.objects.create(
            realm=self.realm,
            owner=self.owner,
            runner=self.runner,
            bot_user=self.example_user("default_bot"),
            name="Fixture",
            adapter_id="codex-acp",
            adapter_version="1.12.0",
        )
        self.message_id = self.send_stream_message(self.owner, "Denmark", "Agent fixture")
        self.conversation = agents.AgentConversation.objects.create(
            realm=self.realm,
            profile=self.profile,
            anchor_message_id=self.message_id,
        )
        self.job = agents.AgentJob.objects.create(
            realm=self.realm,
            requester=self.owner,
            conversation=self.conversation,
            profile=self.profile,
            runner=self.runner,
            request="Explain",
            idempotency_key=uuid4(),
            payload_digest="a" * 64,
            admission_revision=1,
        )

    def attempt(self, **kwargs: Any) -> agents.AgentAttempt:
        fields = dict(
            realm=self.realm,
            job=self.job,
            runner=self.runner,
            number=1,
            lease_epoch=1,
            lease_expires_at=now() + timedelta(seconds=90),
            descriptor_digest="a" * 64,
        )
        return agents.AgentAttempt.objects.create(**(fields | kwargs))

    def assert_duplicate(self, model: type[agents.AgentRecord], fields: dict[str, Any]) -> None:
        model._default_manager.create(**fields)
        with self.assertRaises(IntegrityError), transaction.atomic():
            model._default_manager.create(**fields)

    def test_only_one_active_attempt(self) -> None:
        self.attempt()
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.attempt(number=2, lease_epoch=2)
        self.assertEqual(agents.AgentAttempt.objects.filter(job=self.job, active=True).count(), 1)
        agents.AgentAttempt.objects.filter(job=self.job).update(
            active=False, process_state="stopped", ended_at=now()
        )
        self.attempt(number=2, lease_epoch=2)

    def test_attempt_number_is_unique_after_stop(self) -> None:
        self.attempt(active=False, process_state="stopped", ended_at=now())
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.attempt()

    def test_trigger_receipt_unique_includes_rejections(self) -> None:
        self.assert_duplicate(
            agents.AgentDispatchReceipt,
            dict(
                realm=self.realm,
                source_message_id=self.message_id,
                profile=self.profile,
                requester=self.owner,
                trigger_kind="mention",
                decision="rejected",
                reason="no_grant",
            ),
        )

    def test_send_key_is_scoped_to_sender(self) -> None:
        fields = dict(
            realm=self.realm, sender=self.owner, client_key=uuid4(), payload_digest="a" * 64
        )
        self.assert_duplicate(agents.AgentSendIntent, fields)
        agents.AgentSendIntent.objects.create(**(fields | {"sender": self.example_user("othello")}))

    def test_job_idempotency_is_scoped_to_requester(self) -> None:
        fields = dict(
            realm=self.realm,
            requester=self.owner,
            conversation=self.conversation,
            profile=self.profile,
            runner=self.runner,
            request="Explain",
            idempotency_key=self.job.idempotency_key,
            payload_digest="b" * 64,
        )
        with self.assertRaises(IntegrityError), transaction.atomic():
            agents.AgentJob.objects.create(**fields)

    def test_event_id_and_sequence_are_unique(self) -> None:
        attempt = self.attempt()
        fields = dict(
            realm=self.realm,
            job=self.job,
            attempt=attempt,
            event_id=uuid4(),
            sequence=1,
            type="attempt.started",
            authority="runner",
            payload={},
        )
        self.assert_duplicate(agents.AgentAuditEvent, fields)
        with self.assertRaises(IntegrityError), transaction.atomic():
            agents.AgentAuditEvent.objects.create(**(fields | {"event_id": uuid4()}))
        with self.assertRaises(IntegrityError), transaction.atomic():
            agents.AgentAuditEvent.objects.create(**(fields | {"sequence": 2}))

    def test_operation_and_delivery_ids_are_unique(self) -> None:
        attempt = self.attempt()
        self.assert_duplicate(
            agents.AgentOperation,
            dict(
                realm=self.realm,
                attempt=attempt,
                operation_id=uuid4(),
                tool_class="git.push",
                argument_digest="a" * 64,
            ),
        )
        self.assert_duplicate(
            agents.AgentOutbox,
            dict(
                realm=self.realm,
                job=self.job,
                delivery_key="result:fixture",
                event_type="result.publish",
            ),
        )

    def test_setup_retry_is_idempotent(self) -> None:
        self.assert_duplicate(
            agents.AgentSetupOperation,
            dict(
                realm=self.realm,
                owner=self.owner,
                profile=self.profile,
                runner=self.runner,
                profile_revision=1,
                retry_key=uuid4(),
                payload_digest="a" * 64,
            ),
        )

    def test_input_keys_and_sequence_are_unique(self) -> None:
        fields = dict(
            realm=self.realm,
            job=self.job,
            author=self.owner,
            client_key=uuid4(),
            source_message_id=self.message_id,
            sequence=1,
            text="Continue",
            payload_digest="a" * 64,
        )
        self.assert_duplicate(agents.AgentInput, fields)
        replacements: list[dict[str, Any]] = [
            {"client_key": uuid4()},
            {"sequence": 2, "source_message_id": None},
            {"client_key": uuid4(), "sequence": 2},
        ]
        for replacement in replacements:
            with (
                self.subTest(replacement=replacement),
                self.assertRaises(IntegrityError),
                transaction.atomic(),
            ):
                agents.AgentInput.objects.create(**(fields | replacement))

    def test_invalid_job_kind_target_and_states_fail_at_database(self) -> None:
        cases: list[dict[str, Any]] = [
            {"status": "model_says_done"},
            {"job_kind": "answer", "delivery_target": "patch"},
            {"job_kind": "code", "delivery_target": "answer"},
            {"job_kind": "manage", "delivery_target": "patch"},
            {"job_kind": "manage", "delivery_target": "draft_pr"},
            {"version": 0},
        ]
        for changes in cases:
            with (
                self.subTest(changes=changes),
                self.assertRaises(IntegrityError),
                transaction.atomic(),
            ):
                agents.AgentJob.objects.filter(pk=self.job.pk).update(**changes)

    def test_default_feature_flag_and_status_axes(self) -> None:
        settings = agents.AgentRealmSettings.objects.create(realm=self.realm)
        self.assertFalse(settings.enabled)
        self.assertFalse(settings.retention_cleanup_enabled)
        self.assertEqual(self.profile.desired_state, "draft")
        self.assertEqual(self.profile.readiness_state, "unchecked")
        self.assertEqual(self.runner.status, "unknown")
        self.assertEqual(self.job.status, "draft")
        self.assertEqual(self.attempt().process_state, "starting")

    def test_instruction_fields_default_empty_and_follows_job_is_nullable(self) -> None:
        settings = agents.AgentRealmSettings.objects.create(realm=self.realm)
        self.assertEqual(self.profile.instructions, "")
        self.assertEqual(settings.team_instructions, "")
        self.assertEqual(settings.team_instructions_revision, 1)
        self.assertIsNone(self.job.follows_job)
        follow_up = agents.AgentJob.objects.create(
            realm=self.realm,
            requester=self.owner,
            conversation=self.conversation,
            profile=self.profile,
            runner=self.runner,
            request="Follow up on the earlier answer",
            idempotency_key=uuid4(),
            payload_digest="b" * 64,
            admission_revision=1,
            follows_job=self.job,
        )
        self.assertEqual(follow_up.follows_job, self.job)

    def test_references_require_same_realm(self) -> None:
        other = self.mit_user("sipbtest")
        self.profile.owner = other
        with self.assertRaises(ValidationError):
            agents.validate_agent_references(self.profile)
        self.profile.owner = self.owner
        agents.validate_agent_references(self.profile)

    def test_verification_preserves_final_tree_and_output(self) -> None:
        attempt = self.attempt()
        artifact = agents.AgentArtifact.objects.create(
            realm=self.realm,
            attempt=attempt,
            kind="verification",
            checksum="b" * 64,
            size=10,
            storage_ref="private/test",
            expires_at=now() + timedelta(days=7),
        )
        verification = agents.AgentVerification.objects.create(
            realm=self.realm,
            attempt=attempt,
            check_id="unit",
            command=["pytest"],
            cwd=".",
            exit_code=0,
            tree_hash="a" * 40,
            started_at=now(),
            finished_at=now(),
            output_artifact=artifact,
        )
        verification.refresh_from_db()
        self.assertEqual(verification.tree_hash, "a" * 40)
        self.assertEqual(verification.output_artifact_id, artifact.id)

    def test_rejected_receipt_cannot_have_execution_job(self) -> None:
        with self.assertRaises(IntegrityError), transaction.atomic():
            agents.AgentDispatchReceipt.objects.create(
                realm=self.realm,
                source_message_id=self.message_id,
                profile=self.profile,
                requester=self.owner,
                trigger_kind="manual",
                decision="rejected",
                job=self.job,
            )

    def test_enabled_profile_can_have_stale_readiness(self) -> None:
        agents.AgentProfile.objects.filter(pk=self.profile.pk).update(
            desired_state="enabled",
            revision=2,
            enabled_revision=1,
            readiness_revision=1,
            readiness_state="checking",
        )
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.desired_state, "enabled")
        self.assertEqual(self.profile.enabled_revision, 1)
        with self.assertRaises(IntegrityError), transaction.atomic():
            agents.AgentProfile.objects.filter(pk=self.profile.pk).update(enabled_revision=3)

    def test_pairing_has_no_tenant_before_approval(self) -> None:
        pairing = agents.AgentPairing.objects.create(
            device_name="Fixture",
            fingerprint="a" * 64,
            polling_secret_hash="b" * 64,
            user_code_hash="c" * 64,
            expires_at=now() + timedelta(minutes=10),
        )
        self.assertIsNone(pairing.realm_id)
        self.assertIsNone(pairing.owner_id)
        with self.assertRaises(IntegrityError), transaction.atomic():
            agents.AgentPairing.objects.filter(pk=pairing.pk).update(state="approved")
        with self.assertRaises(IntegrityError), transaction.atomic():
            agents.AgentPairing.objects.filter(pk=pairing.pk).update(realm=self.realm)

    def test_grant_can_share_runner_before_profile_exists(self) -> None:
        grant = agents.AgentGrant.objects.create(
            realm=self.realm,
            owner=self.owner,
            principal_user=self.example_user("othello"),
            target_kind="runner",
            runner=self.runner,
            actions=["runner.use"],
        )
        self.assertIsNone(grant.profile_id)
        with self.assertRaises(IntegrityError), transaction.atomic():
            agents.AgentGrant.objects.filter(pk=grant.pk).update(profile=self.profile)

    def test_same_realm_group_and_cross_job_references(self) -> None:
        group = UserGroup.objects.create(realm=self.realm)
        grant = agents.AgentGrant(
            realm=self.realm,
            owner=self.owner,
            principal_group=group,
            target_kind="runner",
            runner=self.runner,
            actions=["runner.use"],
        )
        agents.validate_agent_references(grant)
        attempt = self.attempt()
        another = agents.AgentJob.objects.create(
            realm=self.realm,
            requester=self.owner,
            conversation=self.conversation,
            profile=self.profile,
            runner=self.runner,
            request="Another",
            idempotency_key=uuid4(),
            payload_digest="a" * 64,
        )
        event = agents.AgentAuditEvent(realm=self.realm, job=another, attempt=attempt, sequence=1)
        with self.assertRaises(ValidationError):
            agents.validate_agent_references(event)

    def test_only_one_approval_consumes_an_operation(self) -> None:
        attempt = self.attempt()
        operation = agents.AgentOperation.objects.create(
            realm=self.realm,
            attempt=attempt,
            operation_id=uuid4(),
            tool_class="git.push",
            argument_digest="a" * 64,
        )
        fields = dict(
            realm=self.realm,
            job=self.job,
            attempt=attempt,
            operation=operation,
            approver=self.owner,
            operation_hash="a" * 64,
            policy_version=1,
            tree_hash="b" * 40,
            decision="consumed",
            expires_at=now() + timedelta(minutes=15),
            consumed_at=now(),
        )
        self.assert_duplicate(agents.AgentApproval, fields)

    def test_grant_validation_rejects_unrelated_actions(self) -> None:
        grant = agents.AgentGrant(
            realm=self.realm,
            owner=self.owner,
            principal_user=self.owner,
            target_kind="runner",
            runner=self.runner,
            actions=["repository.edit"],
        )
        with self.assertRaises(ValidationError):
            grant.clean()

    def test_audit_validation_rejects_runner_approval_decision(self) -> None:
        attempt = self.attempt()
        event = agents.AgentAuditEvent(
            realm=self.realm,
            job=self.job,
            attempt=attempt,
            sequence=1,
            authority="runner",
            type="approval.resolved",
            payload={},
        )
        with self.assertRaises(ValidationError):
            event.clean()

    def test_readiness_snapshot_survives_profile_revision_change(self) -> None:
        fixtures = json.loads(
            (Path(__file__).parent / "fixtures/agents/protocol-v1.json").read_text()
        )
        snapshot = fixtures["valid"][0]["payload"]["tested_configuration"]
        self.assertIsNone(self.profile.readiness_configuration)
        self.profile.readiness_configuration = snapshot
        self.profile.readiness_revision = 1
        self.profile.save(update_fields=["readiness_configuration", "readiness_revision"])
        agents.AgentProfile.objects.filter(pk=self.profile.pk).update(
            revision=2, readiness_state="checking"
        )
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.readiness_configuration, snapshot)
        self.assertEqual(self.profile.readiness_revision, 1)

    def test_manage_job_kind_requires_answer_delivery_target(self) -> None:
        agents.AgentJob.objects.filter(pk=self.job.pk).update(
            job_kind="manage", delivery_target="answer"
        )
        self.job.refresh_from_db()
        self.assertEqual(self.job.job_kind, "manage")
        self.assertEqual(self.job.delivery_target, "answer")

    def test_profile_default_mode_can_be_manage(self) -> None:
        agents.AgentProfile.objects.filter(pk=self.profile.pk).update(default_mode="manage")
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.default_mode, "manage")

    def test_operation_accepts_team_manage_and_stores_server_receipt(self) -> None:
        operation = agents.AgentOperation(
            realm=self.realm,
            attempt=self.attempt(),
            operation_id=uuid4(),
            tool_class="team.manage",
            argument_digest="a" * 64,
            arguments={
                "action": "team.manage",
                "input": {"tool": "channel.subscribe", "channel_id": 1, "user_ids": [1]},
            },
            server_receipt={
                "tool": "channel.subscribe",
                "outcome": "succeeded",
                "summary": "Budi is now subscribed to #launch.",
                "objects": {"channel_id": 1, "user_ids": [1]},
                "error": None,
            },
        )
        operation.clean()
        operation.server_receipt = {
            "tool": "channel.subscribe",
            "outcome": "succeeded",
            "summary": "ok",
            "objects": {},
            "error": None,
            "unexpected": True,
        }
        with self.assertRaises(ValidationError):
            operation.clean()

    def test_operation_accepts_commit_and_binds_tool_class(self) -> None:
        operation = agents.AgentOperation(
            realm=self.realm,
            attempt=self.attempt(),
            operation_id=uuid4(),
            tool_class="git.commit",
            argument_digest="a" * 64,
            arguments={
                "action": "git.commit",
                "repository_id": str(uuid4()),
                "message": "Fix fixture",
                "tree_hash": "a" * 40,
                "expected_parent": "b" * 40,
            },
        )
        operation.clean()
        operation.tool_class = "git.push"
        with self.assertRaises(ValidationError):
            operation.clean()
