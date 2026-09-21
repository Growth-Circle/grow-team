"""PostgreSQL races use distinct connections and bounded, repeatable requests."""

import json
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from contextlib import suppress
from copy import deepcopy
from pathlib import Path
from threading import Barrier
from typing import Any
from uuid import uuid4

from django.db import connection, connections
from typing_extensions import override

from zerver.actions import agent_jobs as jobs
from zerver.lib.agent_context import AgentBusy
from zerver.lib.exceptions import JsonableError
from zerver.lib.test_classes import ZulipTransactionTestCase
from zerver.models import (
    Client,
    Message,
    Stream,
    Subscription,
    UserGroupMembership,
    UserProfile,
    UserTopic,
    agents,
)


class AgentLifecycleRaceTests(ZulipTransactionTestCase):
    @override
    def setUp(self) -> None:
        super().setUp()
        self.owner = self.example_user("hamlet")
        self.stream = Stream.objects.get(realm=self.owner.realm, name="Denmark")
        self.stream_before = Stream.objects.filter(id=self.stream.id).values().get()
        self.topics_before = list(UserTopic.objects.values())
        self.users_before = list(UserProfile.objects.values())
        self.subscriptions_before = list(Subscription.objects.values())
        self.memberships_before = list(UserGroupMembership.objects.values())
        self.messages_before = set(Message.objects.values_list("id", flat=True))
        self.clients_before = set(Client.objects.values_list("id", flat=True))
        self.realm_settings, self.realm_settings_created = (
            agents.AgentRealmSettings.objects.get_or_create(realm=self.owner.realm)
        )
        self.old_enabled = self.realm_settings.enabled
        self.realm_settings.enabled = True
        self.realm_settings.save(update_fields=["enabled"])
        fixture = json.loads(
            (Path(__file__).parent / "fixtures/agents/protocol-v1.json").read_text()
        )["valid"][0]["payload"]
        self.policy = deepcopy(fixture["policy"])
        self.policy["actions"] = ["context.read"]
        self.policy["scope"] = {"kind": "stream", "stream_id": self.stream.id}
        self.runner = agents.AgentRunner.objects.create(
            realm=self.owner.realm,
            owner=self.owner,
            name="Race",
            fingerprint="q" * 64,
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
                "sandboxes": [self.policy["sandbox"]],
            },
        )
        configuration = deepcopy(fixture["tested_configuration"])
        configuration.update(
            runner_id=str(self.runner.id),
            profile_revision=1,
            adapter={"id": "acp", "version": "1", "mode": "acp"},
            provider=None,
            workspace_binding=None,
            actions=["context.read"],
            policy_version=self.policy["version"],
            network=self.policy["network"],
            sandbox=self.policy["sandbox"],
            hard_cost_cap=self.policy["hard_cost_cap"],
            budget=fixture["budget"],
        )
        self.profile = agents.AgentProfile.objects.create(
            realm=self.owner.realm,
            owner=self.owner,
            bot_user=self.example_user("default_bot"),
            runner=self.runner,
            name="Race",
            adapter_id="acp",
            adapter_version="1",
            revision=1,
            enabled_revision=1,
            readiness_revision=1,
            desired_state="enabled",
            readiness_state="ready",
            readiness_configuration=configuration,
            readiness_configuration_digest=jobs.digest(configuration),
            policy=self.policy,
            budget=fixture["budget"],
        )
        self.source = Message.objects.get(
            id=self.send_stream_message(self.owner, "Denmark", "Race task")
        )

    def create_job(self, source: Message | None = None) -> agents.AgentJob:
        return jobs.create_job(
            self.owner,
            profile=self.profile,
            source=source or self.source,
            request="Race",
            idempotency_key=uuid4(),
            job_kind="answer",
            delivery_target="answer",
        )

    def race(self, first: Callable[[], object], second: Callable[[], object]) -> list[object]:
        barrier = Barrier(2)

        def run(call: Callable[[], object]) -> tuple[int, object]:
            try:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT pg_backend_pid()")
                    pid = cursor.fetchone()[0]
                    cursor.execute("SET statement_timeout = '5s'")
                barrier.wait(timeout=5)
                for retry in range(20):
                    try:
                        return pid, call()
                    except AgentBusy:
                        if retry == 19:
                            raise
                        time.sleep(0.02)
                raise AssertionError("Retry loop did not terminate")
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as pool:
            a, b = pool.submit(run, first), pool.submit(run, second)
            results = [a.result(timeout=15), b.result(timeout=15)]
        self.assertNotEqual(results[0][0], results[1][0])
        return [item[1] for item in results]

    @override
    def tearDown(self) -> None:
        agents.AgentApproval.objects.filter(realm=self.owner.realm).delete()
        agents.AgentVerification.objects.filter(realm=self.owner.realm).delete()
        agents.AgentAuditEvent.objects.filter(realm=self.owner.realm).delete()
        agents.AgentOutbox.objects.filter(realm=self.owner.realm).delete()
        agents.AgentOperation.objects.filter(realm=self.owner.realm).delete()
        agents.AgentInput.objects.filter(realm=self.owner.realm).delete()
        agents.AgentJob.objects.filter(runner=self.runner).update(resume_checkpoint=None)
        agents.AgentAttempt.objects.filter(runner=self.runner).update(source_checkpoint=None)
        agents.AgentCheckpoint.objects.filter(realm=self.owner.realm).delete()
        agents.AgentArtifact.objects.filter(realm=self.owner.realm).delete()
        agents.AgentContextRef.objects.filter(realm=self.owner.realm).delete()
        agents.AgentAttempt.objects.filter(runner=self.runner).delete()
        agents.AgentJob.objects.filter(runner=self.runner).delete()
        agents.AgentConversation.objects.filter(profile=self.profile).delete()
        agents.AgentGrant.objects.filter(profile=self.profile).delete()
        agents.AgentGrant.objects.filter(runner=self.runner).delete()
        self.profile.delete()
        agents.AgentRepository.objects.filter(runner=self.runner).delete()
        self.runner.delete()
        if self.realm_settings_created:
            self.realm_settings.delete()
        else:
            agents.AgentRealmSettings.objects.filter(id=self.realm_settings.id).update(
                enabled=self.old_enabled
            )
        Message.objects.exclude(id__in=self.messages_before).delete()
        Client.objects.exclude(id__in=self.clients_before).delete()
        UserTopic.objects.exclude(id__in=[row["id"] for row in self.topics_before]).delete()
        for row in self.topics_before:
            UserTopic.objects.filter(id=row["id"]).update(**row)
        Stream.objects.filter(id=self.stream.id).update(**self.stream_before)
        from zerver.models import RealmAuditLog

        RealmAuditLog.objects.exclude(
            id__in=self.models_pks_set[RealmAuditLog._meta.db_table]
        ).delete()
        UserGroupMembership.objects.exclude(
            id__in=[row["id"] for row in self.memberships_before]
        ).delete()
        Subscription.objects.exclude(
            id__in=[row["id"] for row in self.subscriptions_before]
        ).delete()
        for user_row in self.users_before:
            UserProfile.objects.update_or_create(id=user_row["id"], defaults=user_row)
        for subscription_row in self.subscriptions_before:
            Subscription.objects.update_or_create(
                id=subscription_row["id"], defaults=subscription_row
            )
        for membership_row in self.memberships_before:
            UserGroupMembership.objects.update_or_create(
                id=membership_row["id"], defaults=membership_row
            )
        from zerver.models import ArchiveTransaction

        ArchiveTransaction.objects.exclude(
            id__in=self.models_pks_set[ArchiveTransaction._meta.db_table]
        ).delete()
        from analytics.models import UserCount

        UserCount.objects.exclude(id__in=self.models_pks_set[UserCount._meta.db_table]).delete()
        super().tearDown()

    def test_two_claims_and_lost_same_key_response(self) -> None:
        job = self.create_job()
        key = uuid4()
        values = self.race(
            lambda: jobs.claim_work(self.runner, claim_key=key),
            lambda: jobs.claim_work(self.runner, claim_key=key),
        )
        self.assertEqual(values[0], values[1])
        self.assertEqual(agents.AgentAttempt.objects.filter(job=job).count(), 1)

    def test_two_jobs_compete_for_one_runner_capacity(self) -> None:
        self.create_job()
        source = Message.objects.get(id=self.send_stream_message(self.owner, "Denmark", "Second"))
        self.create_job(source)
        values = self.race(
            lambda: jobs.claim_work(self.runner, claim_key=uuid4()),
            lambda: jobs.claim_work(self.runner, claim_key=uuid4()),
        )
        self.assertEqual(sum(item is not None for item in values), 1)
        self.assertEqual(
            agents.AgentAttempt.objects.filter(runner=self.runner, active=True).count(), 1
        )

    def prepare_answer(self) -> tuple[agents.AgentJob, agents.AgentAttempt]:
        import hashlib
        import tempfile

        from django.test import override_settings
        from django.utils.timezone import now

        from zerver.lib import agent_protocol as p
        from zerver.lib.agent_results import store_artifact

        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        override = override_settings(AGENT_ARTIFACT_ROOT=directory.name)
        override.enable()
        self.addCleanup(override.disable)
        job = self.create_job()
        jobs.claim_work(self.runner, claim_key=uuid4())
        attempt = agents.AgentAttempt.objects.get(job=job)

        def event(sequence: int, kind: str, payload: dict[str, object]) -> None:
            jobs.record_event(
                self.runner,
                p.RunnerEvent.model_validate(
                    {
                        "schema_version": 1,
                        "job_id": str(job.id),
                        "attempt_id": str(attempt.id),
                        "lease_epoch": 1,
                        "event_id": str(uuid4()),
                        "sequence": sequence,
                        "type": kind,
                        "occurred_at": now().isoformat(),
                        "payload": payload,
                    }
                ),
            )

        event(1, "attempt.started", {"process_state": "active"})
        content = b"One result"
        artifact = store_artifact(
            self.runner,
            job.id,
            attempt.id,
            1,
            chunks=[content],
            checksum=hashlib.sha256(content).hexdigest(),
            kind="summary",
            filename="summary.txt",
            media_type="text/plain",
        )
        event(2, "result.prepared", {"artifact_ids": [str(artifact.id)], "summary": "One result"})
        event(3, "attempt.stopped", {"process_state": "stopped", "stop_confirmed": True})
        job.refresh_from_db()
        attempt.refresh_from_db()
        return job, attempt

    def test_two_publishers_commit_one_result_and_cancellation_has_one_winner(self) -> None:
        from zerver.lib.agent_results import publish_result

        job, _ = self.prepare_answer()
        results = self.race(lambda: publish_result(job.id), lambda: publish_result(job.id))
        self.assertEqual(results[0], results[1])
        self.assertEqual(
            Message.objects.filter(sender=self.profile.bot_user, content="One result").count(), 1
        )

    def test_cancel_and_publication_have_one_terminal_winner(self) -> None:
        from zerver.lib.agent_results import publish_result

        job, _ = self.prepare_answer()

        def cancel() -> str:
            try:
                return jobs.cancel_job(self.owner, job.id, job.version).status
            except AgentBusy:
                raise
            except ValueError:
                return "denied"

        def publish() -> str:
            try:
                publish_result(job.id)
                return "completed"
            except AgentBusy:
                raise
            except ValueError:
                return "denied"

        results = self.race(cancel, publish)
        job.refresh_from_db()
        self.assertIn(job.status, {"completed", "cancelled"})
        self.assertIn("denied", results)
        self.assertEqual(
            Message.objects.filter(sender=self.profile.bot_user, content="One result").count(),
            int(job.status == "completed"),
        )

    def test_publication_rolls_back_message_when_receipt_write_fails(self) -> None:
        from unittest.mock import patch

        from zerver.lib.agent_results import publish_result

        job, _ = self.prepare_answer()
        with (
            patch(
                "zerver.lib.agent_results.transition", side_effect=RuntimeError("synthetic crash")
            ),
            self.assertRaises(RuntimeError),
        ):
            publish_result(job.id)
        self.assertEqual(
            Message.objects.filter(sender=self.profile.bot_user, content="One result").count(), 0
        )
        job.refresh_from_db()
        self.assertIsNone(job.result_receipt)
        receipt = publish_result(job.id)
        self.assertIsNotNone(receipt["message_id"])

    def test_actual_privacy_writer_serializes_with_publication(self) -> None:
        from zerver.actions.streams import do_change_stream_permission
        from zerver.lib.agent_results import publish_result

        job, _ = self.prepare_answer()

        def mutate() -> str:
            stream = Stream.objects.get(id=self.stream.id)
            do_change_stream_permission(
                stream,
                invite_only=True,
                history_public_to_subscribers=True,
                is_web_public=False,
                acting_user=self.owner,
            )
            return "changed"

        def publish() -> str:
            try:
                publish_result(job.id)
                return "published"
            except AgentBusy:
                raise
            except ValueError:
                return "held"

        values = self.race(mutate, publish)
        job.refresh_from_db()
        self.assertEqual(bool(job.result_receipt), "published" in values)
        if job.result_receipt is None:
            self.assertEqual(
                Message.objects.filter(sender=self.profile.bot_user, content="One result").count(),
                0,
            )
        # Restore only this fixture's privacy change; test cleanup restores cached fields.
        Stream.objects.filter(id=self.stream.id).update(**self.stream_before)
        from zerver.models import RealmAuditLog

        RealmAuditLog.objects.exclude(
            id__in=self.models_pks_set[RealmAuditLog._meta.db_table]
        ).delete()
        Message.objects.exclude(id__in=self.messages_before).exclude(id=self.source.id).exclude(
            id=job.result_message_id or -1
        ).delete()

    def prepare_approval(
        self,
    ) -> tuple[agents.AgentJob, agents.AgentAttempt, agents.AgentOperation, agents.AgentApproval]:
        import hashlib
        import tempfile

        from django.test import override_settings
        from django.utils.timezone import now

        from zerver.actions import agent_approvals as approvals
        from zerver.lib import agent_protocol as p
        from zerver.lib.agent_results import store_artifact

        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        override = override_settings(AGENT_ARTIFACT_ROOT=directory.name)
        override.enable()
        self.addCleanup(override.disable)
        repository = agents.AgentRepository.objects.create(
            realm=self.owner.realm,
            owner=self.owner,
            runner=self.runner,
            workspace_alias="race",
            canonical_origin="https://example.com/team/repo",
            allowed_refs=["main"],
            required_checks=[{"id": "test", "argv": ["true"], "cwd": ".", "timeout_seconds": 120}],
        )
        actions = ["context.read", "repository.read", "repository.edit", "checks.run", "git.push"]
        self.profile.capability_report = {
            "chat_ready": True,
            "code_ready": True,
            "tool_calling": "passed",
            "sandbox": "passed",
            "config_version": 1,
        }
        self.profile.policy["actions"] = actions
        self.profile.default_repository = repository
        configuration = self.profile.readiness_configuration
        assert configuration is not None
        configuration["actions"] = sorted(actions)
        configuration["workspace_binding"] = {
            "canonical_origin": repository.canonical_origin,
            "repository_id": str(repository.id),
            "workspace_alias": "race",
            "policy_version": 1,
            "allowed_refs": ["main"],
            "checks_digest": jobs.digest(repository.required_checks),
        }
        self.profile.readiness_configuration_digest = jobs.digest(configuration)
        self.profile.save()
        job = jobs.create_job(
            self.owner,
            profile=self.profile,
            source=self.source,
            request="Code",
            idempotency_key=uuid4(),
            job_kind="code",
            delivery_target="patch",
            repository=repository,
            base_ref="main",
        )
        jobs.claim_work(self.runner, claim_key=uuid4())
        attempt = agents.AgentAttempt.objects.get(job=job)
        for sequence, kind, payload in [
            (
                1,
                "workspace.prepared",
                {
                    "repository_id": str(repository.id),
                    "workspace_reference": "race",
                    "base_ref": "main",
                    "base_commit": "a" * 40,
                    "tree_hash": "b" * 40,
                    "user_worktree_dirty": False,
                },
            ),
            (2, "attempt.started", {"process_state": "active"}),
        ]:
            jobs.record_event(
                self.runner,
                p.RunnerEvent.model_validate(
                    {
                        "schema_version": 1,
                        "job_id": str(job.id),
                        "attempt_id": str(attempt.id),
                        "lease_epoch": 1,
                        "event_id": str(uuid4()),
                        "sequence": sequence,
                        "type": kind,
                        "occurred_at": now().isoformat(),
                        "payload": payload,
                    }
                ),
            )
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
        operation = approvals.propose_operation(
            self.runner,
            job.id,
            attempt.id,
            1,
            operation_id=uuid4(),
            arguments={
                "action": "git.push",
                "repository_id": str(repository.id),
                "remote": repository.canonical_origin,
                "branch": f"grow-agent/{job.id}/result",
                "commit": "c" * 40,
                "expected_remote_head": None,
            },
            tree_hash="b" * 40,
            diff_artifact_id=artifact.id,
        )
        approval = agents.AgentApproval.objects.get(operation=operation)
        approvals.decide_approval(
            self.owner,
            approval.id,
            expected_version=approval.version,
            operation_hash=operation.argument_digest,
            nonce=approval.nonce,
            decision="approved",
        )
        job.refresh_from_db()
        return job, attempt, operation, approval

    def test_two_approval_consumers_have_one_durable_winner(self) -> None:
        from zerver.actions import agent_approvals as approvals

        job, attempt, operation, approval = self.prepare_approval()

        def consume() -> str:
            try:
                approvals.consume_operation(
                    self.runner,
                    job.id,
                    attempt.id,
                    1,
                    operation_id=operation.operation_id,
                    expected_version=1,
                    operation_hash=operation.argument_digest,
                    nonce=approval.nonce,
                )
                return "started"
            except AgentBusy:
                raise
            except ValueError:
                return "denied"

        self.assertEqual(sorted(self.race(consume, consume)), ["denied", "started"])
        approval.refresh_from_db()
        self.assertEqual(approval.decision, "consumed")
        self.assertEqual(
            agents.AgentOperation.objects.filter(attempt=attempt, status="started").count(), 1
        )

    def test_cancel_and_approval_consumption_preserve_truthful_precedence(self) -> None:
        from zerver.actions import agent_approvals as approvals

        job, attempt, operation, approval = self.prepare_approval()

        def consume() -> str:
            try:
                approvals.consume_operation(
                    self.runner,
                    job.id,
                    attempt.id,
                    1,
                    operation_id=operation.operation_id,
                    expected_version=1,
                    operation_hash=operation.argument_digest,
                    nonce=approval.nonce,
                )
                return "started"
            except AgentBusy:
                raise
            except ValueError:
                return "denied"

        values = self.race(lambda: jobs.cancel_job(self.owner, job.id, job.version).status, consume)
        job.refresh_from_db()
        operation.refresh_from_db()
        attempt.refresh_from_db()
        self.assertEqual(job.status, "cancel_requested")
        self.assertTrue(attempt.active)
        self.assertEqual(operation.status == "started", "started" in values)

    def race_writer_with_publication(self, writer: Callable[[], object]) -> None:
        from django.core.exceptions import ObjectDoesNotExist

        from zerver.lib.agent_results import publish_result

        job, _ = self.prepare_answer()

        def publish() -> str:
            try:
                publish_result(job.id)
                return "published"
            except AgentBusy:
                raise
            except (ValueError, ObjectDoesNotExist, JsonableError):
                return "held"

        values = self.race(writer, publish)
        job.refresh_from_db()
        self.assertEqual(bool(job.result_receipt), "published" in values)
        self.assertEqual(
            Message.objects.filter(sender=self.profile.bot_user, content="One result").count(),
            int(job.result_receipt is not None),
        )

    def test_actual_role_writer_and_publication(self) -> None:
        from zerver.actions.users import do_change_user_role

        self.race_writer_with_publication(
            lambda: do_change_user_role(
                UserProfile.objects.get(id=self.owner.id),
                UserProfile.ROLE_GUEST,
                acting_user=self.example_user("iago"),
                notify=False,
            )
        )

    def test_actual_deactivation_writer_and_publication(self) -> None:
        from zerver.actions.users import do_deactivate_user

        self.race_writer_with_publication(
            lambda: do_deactivate_user(
                UserProfile.objects.get(id=self.profile.bot_user_id),
                _cascade=False,
                acting_user=self.owner,
            )
        )

    def test_actual_subscription_removal_and_publication(self) -> None:
        from zerver.actions.streams import bulk_remove_subscriptions, do_change_stream_permission

        self.subscribe(self.profile.bot_user, "Denmark")
        do_change_stream_permission(
            self.stream,
            invite_only=True,
            history_public_to_subscribers=True,
            is_web_public=False,
            acting_user=self.owner,
        )
        self.race_writer_with_publication(
            lambda: bulk_remove_subscriptions(
                self.owner.realm, [self.owner], [self.stream], acting_user=self.owner
            )
        )

    def test_nested_guard_restores_shorter_timeouts_and_rollback_state(self) -> None:
        from django.db import transaction

        from zerver.lib.agent_context import agent_transaction

        with transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute("SET LOCAL lock_timeout = '100ms'")
                cursor.execute("SET LOCAL statement_timeout = '700ms'")
            with agent_transaction(), connection.cursor() as cursor:
                cursor.execute("SHOW lock_timeout")
                self.assertEqual(cursor.fetchone()[0], "100ms")
            with connection.cursor() as cursor:
                cursor.execute("SHOW statement_timeout")
                self.assertEqual(cursor.fetchone()[0], "700ms")
            with self.assertRaisesRegex(ValueError, "rollback"), agent_transaction():
                agents.AgentRunner.objects.filter(id=self.runner.id).update(name="rolled back")
                raise ValueError("rollback")
            self.runner.refresh_from_db()
            self.assertEqual(self.runner.name, "Race")
            with connection.cursor() as cursor:
                cursor.execute("SHOW lock_timeout")
                self.assertEqual(cursor.fetchone()[0], "100ms")

    def test_actual_group_grant_revocation_and_publication(self) -> None:
        from zerver.actions.user_groups import bulk_remove_members_from_user_groups
        from zerver.models import NamedUserGroup

        group = NamedUserGroup.objects.filter(realm=self.owner.realm, is_system_group=False).first()
        assert group is not None
        UserGroupMembership.objects.get_or_create(user_group=group, user_profile=self.owner)
        resource_owner = self.example_user("iago")
        self.profile.owner = resource_owner
        self.profile.save(update_fields=["owner"])
        self.runner.owner = resource_owner
        self.runner.save(update_fields=["owner"])
        agents.AgentGrant.objects.create(
            realm=self.owner.realm,
            owner=resource_owner,
            principal_group=group,
            target_kind="profile",
            profile=self.profile,
            actions=["profile.use", "context.read"],
        )
        agents.AgentGrant.objects.create(
            realm=self.owner.realm,
            owner=resource_owner,
            principal_group=group,
            target_kind="runner",
            runner=self.runner,
            actions=["runner.use"],
        )
        self.race_writer_with_publication(
            lambda: bulk_remove_members_from_user_groups(
                [group], [self.owner.id], acting_user=resource_owner
            )
        )

    def test_actual_source_deletion_and_publication(self) -> None:
        from zerver.actions.message_delete import do_delete_messages

        self.race_writer_with_publication(
            lambda: do_delete_messages(
                self.owner.realm, [Message.objects.get(id=self.source.id)], acting_user=self.owner
            )
        )

    def test_existing_row_writer_causes_bounded_guard_rollback_and_safe_retry(self) -> None:
        from threading import Event

        from django.db import transaction

        from zerver.actions.users import do_change_user_role
        from zerver.lib.agent_context import agent_transaction
        from zerver.lib.agent_results import publish_result

        job, _ = self.prepare_answer()
        row_locked, guard_locked = Event(), Event()

        def writer() -> None:
            try:
                with transaction.atomic():
                    user = UserProfile.objects.select_for_update(no_key=True).get(id=self.owner.id)
                    row_locked.set()
                    assert guard_locked.wait(3)
                    do_change_user_role(
                        user,
                        UserProfile.ROLE_GUEST,
                        acting_user=self.example_user("iago"),
                        notify=False,
                    )
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(writer)
            self.assertTrue(row_locked.wait(3))
            started = time.monotonic()
            with self.assertRaises(AgentBusy), agent_transaction():
                guard_locked.set()
                # Exercise the nested row-lock shape used by the upstream role writer.
                UserProfile.objects.select_for_update(no_key=True).get(id=self.owner.id)
                publish_result(job.id)
            self.assertLess(time.monotonic() - started, 3)
            pending.result(timeout=5)
        job.refresh_from_db()
        self.assertIsNone(job.result_receipt)
        self.assertEqual(
            Message.objects.filter(sender=self.profile.bot_user, content="One result").count(), 0
        )
        # Current audience may now be narrower; retry must recheck it instead of duplicating a send.
        with suppress(ValueError):
            publish_result(job.id)
        self.assertLessEqual(
            Message.objects.filter(sender=self.profile.bot_user, content="One result").count(), 1
        )

    def test_private_to_public_expansion_and_publication(self) -> None:
        from zerver.actions.streams import do_change_stream_permission

        self.subscribe(self.profile.bot_user, "Denmark")
        do_change_stream_permission(
            self.stream,
            invite_only=True,
            history_public_to_subscribers=True,
            is_web_public=False,
            acting_user=self.owner,
        )
        self.race_writer_with_publication(
            lambda: do_change_stream_permission(
                Stream.objects.get(id=self.stream.id),
                invite_only=False,
                history_public_to_subscribers=True,
                is_web_public=False,
                acting_user=self.owner,
            )
        )

    def test_actual_source_move_and_publication(self) -> None:
        from zerver.models import RealmAuditLog

        self.login("iago")
        destination = Stream.objects.get(realm=self.owner.realm, name="Verona")
        previous = Stream.objects.filter(id=destination.id).values().get()

        def move() -> None:
            self.assert_json_success(
                self.client_patch(f"/json/messages/{self.source.id}", {"stream_id": destination.id})
            )

        try:
            self.race_writer_with_publication(move)
        finally:
            Stream.objects.filter(id=destination.id).update(**previous)
            RealmAuditLog.objects.exclude(
                id__in=self.models_pks_set[RealmAuditLog._meta.db_table]
            ).delete()

    def test_approval_rejects_changed_nonce_hash_tree_expiry_and_current_grant(self) -> None:
        from datetime import timedelta

        from django.utils.timezone import now

        from zerver.actions.agent_approvals import consume_operation
        from zerver.lib.agent_policy import AgentAccessDenied

        job, attempt, operation, approval = self.prepare_approval()

        def consume(**changes: object) -> object:
            values: dict[str, Any] = {
                "operation_id": operation.operation_id,
                "expected_version": operation.version,
                "operation_hash": operation.argument_digest,
                "nonce": approval.nonce,
            }
            values.update(changes)
            return consume_operation(self.runner, job.id, attempt.id, 1, **values)

        changes: list[dict[str, object]] = [
            {"nonce": uuid4()},
            {"operation_hash": "a" * 64},
            {"expected_version": 999},
        ]
        for changed in changes:
            with self.assertRaises(ValueError):
                consume(**changed)
        agents.AgentAttempt.objects.filter(id=attempt.id).update(tree_hash="d" * 40)
        with self.assertRaisesRegex(ValueError, "tree"):
            consume()
        agents.AgentAttempt.objects.filter(id=attempt.id).update(tree_hash="b" * 40)
        agents.AgentApproval.objects.filter(id=approval.id).update(
            expires_at=now() - timedelta(seconds=1)
        )
        with self.assertRaisesRegex(ValueError, "Approval"):
            consume()
        agents.AgentApproval.objects.filter(id=approval.id).update(
            expires_at=now() + timedelta(minutes=1)
        )
        self.profile.owner = self.example_user("iago")
        self.profile.save(update_fields=["owner"])
        grant = agents.AgentGrant.objects.create(
            realm=self.owner.realm,
            owner=self.profile.owner,
            profile=self.profile,
            principal_user=self.owner,
            actions=["profile.use", "git.push"],
            target_kind="profile",
        )
        grant.revoked_at = now()
        grant.save(update_fields=["revoked_at"])
        with self.assertRaises(AgentAccessDenied):
            consume()
        approval.refresh_from_db()
        operation.refresh_from_db()
        self.assertEqual(approval.decision, "approved")
        self.assertIsNone(approval.consumed_at)
        self.assertEqual(operation.status, "proposed")

    def test_remote_unknown_outcome_blocks_resume_until_exact_receipt(self) -> None:
        from django.utils.timezone import now

        from zerver.actions import agent_approvals as approvals
        from zerver.lib import agent_protocol as p

        job, attempt, operation, approval = self.prepare_approval()
        approvals.consume_operation(
            self.runner,
            job.id,
            attempt.id,
            1,
            operation_id=operation.operation_id,
            expected_version=operation.version,
            operation_hash=operation.argument_digest,
            nonce=approval.nonce,
        )
        jobs.record_event(
            self.runner,
            p.RunnerEvent.model_validate(
                {
                    "schema_version": 1,
                    "job_id": str(job.id),
                    "attempt_id": str(attempt.id),
                    "lease_epoch": 1,
                    "sequence": 3,
                    "event_id": str(uuid4()),
                    "occurred_at": now().isoformat(),
                    "type": "attempt.stopped",
                    "payload": {"process_state": "stopped", "stop_confirmed": True},
                }
            ),
        )
        job.refresh_from_db()
        with self.assertRaisesRegex(ValueError, "reconciliation"):
            jobs.resume_job(self.owner, job.id, job.version)
        receipt = p.RemoteReceipt(
            operation_id=operation.operation_id,
            remote=operation.arguments["remote"],
            branch=operation.arguments["branch"],
            commit=operation.arguments["commit"],
            observed_at=now(),
        )
        with self.assertRaisesRegex(ValueError, "does not match"):
            approvals.reconcile_operation(
                self.runner,
                job.id,
                attempt.id,
                1,
                operation_id=operation.operation_id,
                receipt=receipt.model_copy(update={"commit": "d" * 40}),
            )
        import json
        from datetime import timedelta

        from zerver.lib.agent_secrets import hash_agent_credential

        token = "operation-recovery-synthetic"
        foreign = agents.AgentRunner.objects.create(
            realm=self.owner.realm,
            owner=self.owner,
            name="Foreign receipt",
            fingerprint="foreign-receipt",
        )
        try:
            for device, bearer in [(self.runner, token), (foreign, "foreign-operation")]:
                agents.AgentRunnerCredential.objects.create(
                    realm=self.owner.realm,
                    runner=device,
                    token_hash=hash_agent_credential(bearer),
                    refresh_hash=hash_agent_credential(bearer + "-refresh"),
                    expires_at=now() + timedelta(hours=1),
                    refresh_expires_at=now() + timedelta(days=1),
                )
            lease: dict[str, Any] = {
                "schema_version": 1,
                "job_id": str(job.id),
                "attempt_id": str(attempt.id),
                "lease_epoch": 1,
                "job_version": 1,
            }
            self.assertGreater(job.version, lease["job_version"])
            body: dict[str, Any] = {**lease, "receipt": p.serialize_payload(receipt)}
            body["operation_id"] = str(operation.operation_id)

            def recovery_post(route: str, data: dict[str, Any], bearer: str = token) -> Any:
                return self.client.post(
                    "/api/v1/agent/runner/operations" + route,
                    data=json.dumps(data),
                    content_type="application/json",
                    HTTP_AUTHORIZATION="Bearer " + bearer,
                )

            listed = self.assert_json_success(recovery_post("", lease))
            self.assertEqual(listed["operations"][0]["operation_id"], str(operation.operation_id))
            route = "/reconcile"
            for selected, payload in [("", lease), (route, body)]:
                self.assertEqual(
                    recovery_post(selected, payload, "foreign-operation").status_code, 400
                )
                self.assertEqual(
                    recovery_post(selected, {**payload, "lease_epoch": 2}).status_code, 400
                )
            self.assertEqual(
                recovery_post(
                    route, {**body, "receipt": {**body["receipt"], "commit": "f" * 40}}
                ).status_code,
                400,
            )
            accepted = self.assert_json_success(recovery_post(route, body))
            # Repeat the exact request after a lost acknowledgement.
            self.assertEqual(accepted, self.assert_json_success(recovery_post(route, body)))
            self.assertEqual(
                recovery_post(
                    route,
                    {
                        **body,
                        "receipt": {
                            **body["receipt"],
                            "observed_at": (now() + timedelta(seconds=1)).isoformat(),
                        },
                    },
                ).status_code,
                400,
            )
            attempt.refresh_from_db()
            current = agents.AgentJob.objects.get(id=job.id)
            self.assertFalse(attempt.active)
            self.assertEqual(attempt.process_state, "stopped")
            self.assertEqual(current.version, job.version)
            self.assertEqual(current.status, job.status)
            self.assertEqual(agents.AgentAttempt.objects.filter(job=job).count(), 1)
            self.assertEqual(
                recovery_post(
                    "/consume",
                    {
                        **lease,
                        "job_version": current.version,
                        "operation_id": str(operation.operation_id),
                        "expected_version": operation.version,
                        "operation_hash": operation.argument_digest,
                    },
                ).status_code,
                400,
            )
        finally:
            agents.AgentRunnerCredential.objects.filter(runner__in=[self.runner, foreign]).delete()
            foreign.delete()
        jobs.resume_job(self.owner, job.id, job.version)
        self.assertEqual(agents.AgentOperation.objects.filter(attempt=attempt).count(), 1)
