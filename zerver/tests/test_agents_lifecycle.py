"""Durable lifecycle tests use real records and public action boundaries."""

import importlib
import importlib.util
from collections.abc import Iterator
from typing import Any
from uuid import uuid4

from typing_extensions import override

from zerver.actions.agents import create_profile, record_readiness
from zerver.lib.test_classes import ZulipTestCase
from zerver.models import Message, agents


class AgentLifecycleTests(ZulipTestCase):
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
            name="Lifecycle",
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
        self.profile = create_profile(
            self.owner,
            name="Lifecycle",
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
        self.profile.refresh_from_db()
        # Sequential tests own an outer transaction. Acquire its guard before assertions.
        # Separate TransactionTestCase tests exercise real contention and rollback.
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

        # Group DM without a mention isolates manual lifecycle admission from Task 4.
        message_id = self.send_group_direct_message(
            self.owner, [self.profile.bot_user, self.example_user("iago")], "Please answer"
        )
        self.message = Message.objects.get(id=message_id)

    def test_claim_replay_keeps_one_attempt_and_narrows_answer_authority(self) -> None:
        self.assertIsNotNone(
            importlib.util.find_spec("zerver.actions.agent_jobs"),
            "The durable lifecycle service must exist",
        )
        actions = importlib.import_module("zerver.actions.agent_jobs")
        job = actions.create_job(
            self.owner,
            profile=self.profile,
            source=self.message,
            request="Please answer",
            idempotency_key=uuid4(),
            job_kind="answer",
            delivery_target="answer",
        )
        key = uuid4()
        first = actions.claim_work(self.runner, claim_key=key)
        second = actions.claim_work(self.runner, claim_key=key)
        self.assertEqual(first, second)
        self.assertEqual(agents.AgentAttempt.objects.filter(job=job, active=True).count(), 1)
        self.assertEqual(first["policy"]["actions"], ["context.read"])
        self.assertEqual(first["audience"]["epoch"], 1)
        self.assertIsNone(actions.claim_work(self.runner, claim_key=uuid4()))

    def test_admission_retry_and_changed_payload_conflict(self) -> None:
        self.assertIsNotNone(importlib.util.find_spec("zerver.actions.agent_jobs"))
        actions = importlib.import_module("zerver.actions.agent_jobs")
        key = uuid4()
        fields = dict(
            profile=self.profile,
            source=self.message,
            request="Please answer",
            idempotency_key=key,
            job_kind="answer",
            delivery_target="answer",
        )
        job = actions.create_job(self.owner, **fields)
        self.assertEqual(job.id, actions.create_job(self.owner, **fields).id)
        fields["request"] = "A different request"
        with self.assertRaises(ValueError):
            actions.create_job(self.owner, **fields)
        self.assertEqual(agents.AgentJob.objects.count(), 1)

    def test_audience_binding_is_required_and_strict(self) -> None:
        import json
        from copy import deepcopy
        from pathlib import Path

        from pydantic import ValidationError

        from zerver.lib import agent_protocol as p

        fixture = json.loads(
            (Path(__file__).parent / "fixtures/agents/protocol-v1.json").read_text()
        )["valid"][0]["payload"]
        for change in [{"profile_id": str(uuid4())}, {"epoch": True}, {"invite_only": None}]:
            data = deepcopy(fixture)
            data["audience"].update(change)
            with self.assertRaises(ValidationError):
                p.AttemptDescriptor.model_validate(data)
        data = deepcopy(fixture)
        data["audience"]["audience_user_ids"].append(data["audience"]["audience_user_ids"][0])
        with self.assertRaises(ValidationError):
            p.AttemptDescriptor.model_validate(data)

    def test_cancel_requires_empty_containment_and_exact_event_replay(self) -> None:
        from django.utils.timezone import now

        from zerver.actions import agent_jobs as actions
        from zerver.lib import agent_protocol as p

        job = actions.create_job(
            self.owner,
            profile=self.profile,
            source=self.message,
            request="Please answer",
            idempotency_key=uuid4(),
            job_kind="answer",
            delivery_target="answer",
        )
        actions.claim_work(self.runner, claim_key=uuid4())
        attempt = agents.AgentAttempt.objects.get(job=job)
        job.refresh_from_db()
        actions.cancel_job(self.owner, job.id, job.version)
        attempt.refresh_from_db()
        self.assertEqual(attempt.process_state, "stopping")
        self.assertTrue(attempt.active)
        self.assertTrue(
            hasattr(actions, "record_event"), "Stop evidence needs a durable event boundary"
        )
        event = p.RunnerEvent.model_validate(
            {
                "schema_version": 1,
                "job_id": str(job.id),
                "attempt_id": str(attempt.id),
                "lease_epoch": 1,
                "event_id": str(uuid4()),
                "sequence": 1,
                "type": "attempt.stopped",
                "occurred_at": now().isoformat(),
                "payload": {"process_state": "stopped", "stop_confirmed": True},
            }
        )
        actions.record_event(self.runner, event)
        receipt = actions.record_event(self.runner, event)
        job.refresh_from_db()
        attempt.refresh_from_db()
        self.assertEqual(job.status, "cancelled")
        self.assertFalse(attempt.active)
        self.assertEqual(receipt["event_sequence"], job.event_sequence)
        changed = event.model_copy(update={"occurred_at": now()})
        with self.assertRaises(ValueError):
            actions.record_event(self.runner, changed)

    def test_event_gap_and_model_completion_do_not_advance_cursors(self) -> None:
        from django.utils.timezone import now

        from zerver.actions import agent_jobs as actions
        from zerver.lib import agent_protocol as p

        job = actions.create_job(
            self.owner,
            profile=self.profile,
            source=self.message,
            request="Please answer",
            idempotency_key=uuid4(),
            job_kind="answer",
            delivery_target="answer",
        )
        actions.claim_work(self.runner, claim_key=uuid4())
        attempt = agents.AgentAttempt.objects.get(job=job)
        self.assertTrue(hasattr(actions, "record_event"))
        event = p.RunnerEvent.model_validate(
            {
                "schema_version": 1,
                "job_id": str(job.id),
                "attempt_id": str(attempt.id),
                "lease_epoch": 1,
                "event_id": str(uuid4()),
                "sequence": 2,
                "type": "attempt.started",
                "occurred_at": now().isoformat(),
                "payload": {"process_state": "active"},
            }
        )
        with self.assertRaises(ValueError):
            actions.record_event(self.runner, event)
        attempt.refresh_from_db()
        self.assertEqual(attempt.event_cursor, 0)
        job.refresh_from_db()
        self.assertEqual(job.status, "running")

    def test_private_artifact_and_result_need_stop_and_publish_once(self) -> None:
        import hashlib
        import tempfile

        from django.test import override_settings
        from django.utils.timezone import now

        from zerver.actions import agent_jobs as actions
        from zerver.lib import agent_protocol as p

        self.assertIsNotNone(
            importlib.util.find_spec("zerver.lib.agent_results"),
            "Private verifier/publisher is required",
        )
        results = importlib.import_module("zerver.lib.agent_results")
        job = actions.create_job(
            self.owner,
            profile=self.profile,
            source=self.message,
            request="Please answer",
            idempotency_key=uuid4(),
            job_kind="answer",
            delivery_target="answer",
        )
        actions.claim_work(self.runner, claim_key=uuid4())
        attempt = agents.AgentAttempt.objects.get(job=job)

        def event(sequence: int, kind: str, payload: dict[str, object]) -> None:
            actions.record_event(
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
        with (
            tempfile.TemporaryDirectory() as directory,
            override_settings(AGENT_ARTIFACT_ROOT=directory),
        ):
            content = b"A verified answer."
            artifact = results.store_artifact(
                self.runner,
                job.id,
                attempt.id,
                1,
                chunks=[content],
                checksum=hashlib.sha256(content).hexdigest(),
                kind="summary",
                filename="answer.txt",
                media_type="text/plain",
            )
            self.assertNotIn(directory, artifact.storage_ref)
            event(
                2,
                "result.prepared",
                {"artifact_ids": [str(artifact.id)], "summary": "A verified answer."},
            )
            with self.assertRaises(ValueError):
                results.publish_result(job.id)
            event(3, "attempt.stopped", {"process_state": "stopped", "stop_confirmed": True})
            job.refresh_from_db()
            with self.assertRaisesRegex(ValueError, "Stopped execution"):
                actions.add_input(
                    self.owner,
                    job.id,
                    expected_version=job.version,
                    client_key=uuid4(),
                    text="Too late for this attempt",
                )
            job.refresh_from_db()
            self.assertEqual(job.status, "verifying")
            self.assertIsNotNone(job.result_proposal)
            self.assertFalse(agents.AgentInput.objects.filter(job=job).exists())
            first = results.publish_result(job.id)
            second = results.publish_result(job.id)
            self.assertEqual(first, second)
            job.refresh_from_db()
            self.assertEqual(job.status, "completed")
            self.assertEqual(job.result_message_id, first["message_id"])
            assert job.result_message_id is not None
            Message.objects.filter(id=job.result_message_id).delete()
            job.refresh_from_db()
            self.assertIsNone(job.result_message_id)
            self.assertEqual(results.publish_result(job.id), first)

    def test_operation_broker_rejects_answer_mutation_and_requires_typed_authority(self) -> None:
        self.assertIsNotNone(
            importlib.util.find_spec("zerver.actions.agent_approvals"),
            "Operation authorization is required",
        )
        from zerver.actions import agent_jobs as actions

        approvals = importlib.import_module("zerver.actions.agent_approvals")
        job = actions.create_job(
            self.owner,
            profile=self.profile,
            source=self.message,
            request="Please answer",
            idempotency_key=uuid4(),
            job_kind="answer",
            delivery_target="answer",
        )
        actions.claim_work(self.runner, claim_key=uuid4())
        attempt = agents.AgentAttempt.objects.get(job=job)
        with self.assertRaises(ValueError):
            approvals.propose_operation(
                self.runner,
                job.id,
                attempt.id,
                1,
                operation_id=uuid4(),
                arguments={
                    "action": "shell.run",
                    "repository_id": str(uuid4()),
                    "argv": ["true"],
                    "cwd": ".",
                    "network": {"project_network": False},
                },
                tree_hash="a" * 40,
                diff_artifact_id=None,
            )
        self.assertEqual(agents.AgentOperation.objects.count(), 0)

    def test_human_job_http_contract_and_safe_invalid_version(self) -> None:
        import json

        self.login_user(self.owner)
        response = self.client_post(
            "/json/agent/jobs",
            {
                "payload": json.dumps(
                    {
                        "schema_version": 1,
                        "profile_id": str(self.profile.id),
                        "source_message_id": self.message.id,
                        "request": "HTTP task",
                        "idempotency_key": str(uuid4()),
                    }
                )
            },
        )
        self.assertEqual(response.status_code, 200, "Job lifecycle HTTP route must exist")
        data = self.assert_json_success(response)
        self.assertEqual(data["schema_version"], 1)
        response = self.client_post(
            "/json/agent/jobs",
            {"payload": json.dumps({"schema_version": True, "secret-marker": "do-not-show"})},
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["schema_version"], 1)
        self.assertNotIn("do-not-show", response.content.decode())

    def test_expired_lease_holds_capacity_until_stop_even_when_feature_is_off(self) -> None:
        from datetime import timedelta

        from django.utils.timezone import now

        from zerver.actions import agent_jobs as actions
        from zerver.lib import agent_protocol as p
        from zerver.lib.agent_reconcile import reconcile_agents

        job = actions.create_job(
            self.owner,
            profile=self.profile,
            source=self.message,
            request="Please answer",
            idempotency_key=uuid4(),
            job_kind="answer",
            delivery_target="answer",
        )
        actions.claim_work(self.runner, claim_key=uuid4())
        attempt = agents.AgentAttempt.objects.get(job=job)
        agents.AgentAttempt.objects.filter(id=attempt.id).update(
            lease_expires_at=now() - timedelta(seconds=1)
        )
        agents.AgentRealmSettings.objects.filter(realm=self.owner.realm).update(enabled=False)
        self.assertEqual(reconcile_agents()["interrupted"], 1)
        attempt.refresh_from_db()
        job.refresh_from_db()
        self.assertTrue(attempt.active)
        self.assertEqual(job.status, "interrupted")
        with self.assertRaises(ValueError):
            actions.resume_job(self.owner, job.id, job.version)
        actions.record_event(
            self.runner,
            p.RunnerEvent.model_validate(
                {
                    "schema_version": 1,
                    "job_id": str(job.id),
                    "attempt_id": str(attempt.id),
                    "lease_epoch": 1,
                    "sequence": 1,
                    "event_id": str(uuid4()),
                    "occurred_at": now().isoformat(),
                    "type": "attempt.stopped",
                    "payload": {"process_state": "stopped", "stop_confirmed": True},
                }
            ),
        )
        attempt.refresh_from_db()
        self.assertFalse(attempt.active)

    def test_input_delivery_ack_is_ordered_and_reconciles_without_replay(self) -> None:
        from django.utils.timezone import now

        from zerver.actions import agent_jobs as actions
        from zerver.lib import agent_protocol as p

        job = actions.create_job(
            self.owner,
            profile=self.profile,
            source=self.message,
            request="Please answer",
            idempotency_key=uuid4(),
            job_kind="answer",
            delivery_target="answer",
        )
        actions.claim_work(self.runner, claim_key=uuid4())
        job.refresh_from_db()
        attempt = agents.AgentAttempt.objects.get(job=job)
        first = actions.add_input(
            self.owner, job.id, expected_version=job.version, client_key=uuid4(), text="first"
        )
        job.refresh_from_db()
        second = actions.add_input(
            self.owner, job.id, expected_version=job.version, client_key=uuid4(), text="second"
        )
        self.assertEqual(
            [
                item["sequence"]
                for item in actions.deliver_inputs(self.runner, job.id, attempt.id, 1)
            ],
            [1, 2],
        )

        def event(item: agents.AgentInput, sequence: int) -> p.RunnerEvent:
            return p.RunnerEvent.model_validate(
                {
                    "schema_version": 1,
                    "job_id": str(job.id),
                    "attempt_id": str(attempt.id),
                    "lease_epoch": 1,
                    "sequence": sequence,
                    "event_id": str(uuid4()),
                    "occurred_at": now().isoformat(),
                    "type": "input.applied",
                    "payload": {
                        "input_id": str(item.id),
                        "input_sequence": item.sequence,
                        "delivery_state": "applied",
                    },
                }
            )

        with self.assertRaises(ValueError):
            actions.record_event(self.runner, event(second, 1))
        actions.record_event(self.runner, event(first, 1))
        actions.record_event(self.runner, event(second, 2))
        self.assertEqual(actions.deliver_inputs(self.runner, job.id, attempt.id, 1), [])
        attempt.refresh_from_db()
        self.assertEqual(attempt.input_cursor, 2)

    def test_server_journal_matches_frozen_authority_schemas(self) -> None:
        from zerver.actions import agent_jobs as actions

        job = actions.create_job(
            self.owner,
            profile=self.profile,
            source=self.message,
            request="Please answer",
            idempotency_key=uuid4(),
            job_kind="answer",
            delivery_target="answer",
        )
        actions.claim_work(self.runner, claim_key=uuid4())
        job.refresh_from_db()
        actions.cancel_job(self.owner, job.id, job.version)
        for event in agents.AgentAuditEvent.objects.filter(job=job):
            event.clean()

    def test_code_verifier_rejects_missing_failed_and_stale_final_tree_checks(self) -> None:
        import hashlib
        import tempfile
        from copy import deepcopy

        from django.test import override_settings
        from django.utils.timezone import now

        from zerver.actions import agent_jobs as actions
        from zerver.actions.agents import register_repository
        from zerver.lib import agent_protocol as p
        from zerver.lib import agent_results as results

        repository = register_repository(
            self.owner,
            self.runner,
            workspace_alias="code",
            canonical_origin="https://example.com/team/repo",
            allowed_refs=["main"],
            required_checks=[{"id": "required", "argv": ["true"]}],
        )
        policy = deepcopy(self.profile.policy)
        policy["actions"] = [
            "context.read",
            "repository.read",
            "repository.edit",
            "checks.run",
            "git.push",
            "git.draft_pr",
        ]
        profile = create_profile(
            self.owner,
            name="Code",
            runner=self.runner,
            adapter_id="acp",
            adapter_version="1",
            repository=repository,
            policy=policy,
            default_mode="code",
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
                "capabilities": {
                    "chat_ready": True,
                    "code_ready": True,
                    "tool_calling": "passed",
                    "sandbox": "passed",
                    "config_version": 1,
                },
            },
        )
        message = Message.objects.get(
            id=self.send_group_direct_message(
                self.owner, [profile.bot_user, self.example_user("iago")], "Code task"
            )
        )
        job = actions.create_job(
            self.owner,
            profile=profile,
            source=message,
            request="Fix it",
            idempotency_key=uuid4(),
            job_kind="code",
            delivery_target="patch",
            repository=repository,
            base_ref="main",
        )
        actions.claim_work(self.runner, claim_key=uuid4())
        attempt = agents.AgentAttempt.objects.get(job=job)
        sequence = 0

        def event(kind: str, payload: dict[str, object]) -> None:
            nonlocal sequence
            sequence += 1
            actions.record_event(
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
            artifacts = []
            for kind in ["summary", "diff", "verification"]:
                content = kind.encode()
                artifacts.append(
                    results.store_artifact(
                        self.runner,
                        job.id,
                        attempt.id,
                        1,
                        chunks=[content],
                        checksum=hashlib.sha256(content).hexdigest(),
                        kind=kind,
                        filename=kind + ".txt",
                        media_type="text/plain",
                    )
                )
            from django.core.exceptions import ObjectDoesNotExist

            from zerver.actions.agent_approvals import consume_operation, propose_operation

            with self.assertRaises(ObjectDoesNotExist):
                event(
                    "verification.finished",
                    {
                        "operation_id": str(uuid4()),
                        "check_id": "required",
                        "command": ["true"],
                        "cwd": ".",
                        "exit_code": 0,
                        "started_at": now().isoformat(),
                        "finished_at": now().isoformat(),
                        "tree_hash": "b" * 40,
                        "artifact_id": str(artifacts[2].id),
                    },
                )
            sequence -= 1
            self.assertFalse(agents.AgentVerification.objects.filter(attempt=attempt).exists())
            operation = propose_operation(
                self.runner,
                job.id,
                attempt.id,
                1,
                operation_id=uuid4(),
                arguments={
                    "action": "checks.run",
                    "repository_id": str(repository.id),
                    "check_ids": ["required"],
                    "tree_hash": "b" * 40,
                },
                tree_hash="b" * 40,
                diff_artifact_id=None,
            )
            consume_operation(
                self.runner,
                job.id,
                attempt.id,
                1,
                operation_id=operation.operation_id,
                expected_version=operation.version,
                operation_hash=operation.argument_digest,
            )
            event(
                "verification.finished",
                {
                    "operation_id": str(operation.operation_id),
                    "check_id": "required",
                    "command": ["true"],
                    "cwd": ".",
                    "exit_code": 1,
                    "started_at": now().isoformat(),
                    "finished_at": now().isoformat(),
                    "tree_hash": "b" * 40,
                    "artifact_id": str(artifacts[2].id),
                },
            )
            event(
                "tool.finished",
                {
                    "operation_id": str(operation.operation_id),
                    "argument_digest": operation.argument_digest,
                    "tool_class": "checks.run",
                    "status": "failed",
                    "exit_code": 1,
                },
            )
            event(
                "result.prepared",
                {
                    "artifact_ids": [str(item.id) for item in artifacts],
                    "tree_hash": "b" * 40,
                    "summary": "Done",
                },
            )
            event("attempt.stopped", {"process_state": "stopped", "stop_confirmed": True})
            with self.assertRaises(ValueError):
                results.publish_result(job.id)
            verification = agents.AgentVerification.objects.get(attempt=attempt)
            # A newer failed outcome must not be hidden by an older success on the same tree.
            operation.status = "succeeded"
            operation.save(update_fields=["status"])
            from datetime import timedelta

            older = agents.AgentVerification.objects.create(
                realm=job.realm,
                attempt=attempt,
                operation=operation,
                check_id=verification.check_id,
                command=verification.command,
                cwd=verification.cwd,
                exit_code=0,
                started_at=verification.started_at - timedelta(seconds=2),
                finished_at=verification.finished_at - timedelta(seconds=1),
                tree_hash=verification.tree_hash,
                output_artifact=verification.output_artifact,
            )
            with self.assertRaisesRegex(ValueError, "Required checks"):
                results.publish_result(job.id)
            older.delete()
            verification.exit_code = 0
            verification.tree_hash = "c" * 40
            verification.save()
            with self.assertRaises(ValueError):
                results.publish_result(job.id)
            verification.tree_hash = "b" * 40
            verification.save()
            receipt = results.publish_result(job.id)
            self.assertIsNotNone(receipt["message_id"])

    def test_revoked_daemon_posts_stop_with_old_descriptor_and_cannot_read_context(self) -> None:
        import json
        from datetime import timedelta

        from django.utils.timezone import now

        from zerver.actions import agent_jobs as actions
        from zerver.actions.agents import revoke_runner
        from zerver.lib.agent_reconcile import reconcile_agents
        from zerver.lib.agent_secrets import hash_agent_credential

        token = "synthetic-stop-token" + "x" * 40
        agents.AgentRunnerCredential.objects.create(
            realm=self.owner.realm,
            runner=self.runner,
            token_hash=hash_agent_credential(token),
            refresh_hash=hash_agent_credential("refresh" + token),
            expires_at=now() + timedelta(hours=24),
            refresh_expires_at=now() + timedelta(days=30),
        )
        job = actions.create_job(
            self.owner,
            profile=self.profile,
            source=self.message,
            request="Stop",
            idempotency_key=uuid4(),
            job_kind="answer",
            delivery_target="answer",
        )
        actions.claim_work(self.runner, claim_key=uuid4())
        attempt = agents.AgentAttempt.objects.get(job=job)
        old_version = job.version
        revoke_runner(self.runner)
        agents.AgentAttempt.objects.filter(id=attempt.id).update(
            lease_expires_at=now() - timedelta(seconds=1)
        )
        reconcile_agents()
        forbidden = self.client.post(
            "/api/v1/agent/runner/context",
            data=json.dumps(
                {
                    "schema_version": 1,
                    "job_id": str(job.id),
                    "attempt_id": str(attempt.id),
                    "lease_epoch": 1,
                    "job_version": old_version,
                    "reference_ids": [str(agents.AgentContextRef.objects.get(job=job).id)],
                }
            ),
            content_type="application/json",
            HTTP_AUTHORIZATION="Bearer " + token,
        )
        self.assertEqual(forbidden.status_code, 401)
        self.assertEqual(forbidden.json()["code"], "credential_revoked")
        event = {
            "schema_version": 1,
            "job_id": str(job.id),
            "attempt_id": str(attempt.id),
            "lease_epoch": 1,
            "sequence": 1,
            "event_id": str(uuid4()),
            "occurred_at": now().isoformat(),
            "type": "attempt.stopped",
            "payload": {"process_state": "stopped", "stop_confirmed": True},
        }
        # The daemon lost its last execution ACK. Stop evidence must not depend on that cursor.
        event["sequence"] = 79
        body = json.dumps({"schema_version": 1, "event": event})
        response = self.client.post(
            "/api/v1/agent/runner/stop-evidence",
            data=body,
            content_type="application/json",
            HTTP_AUTHORIZATION="Bearer " + token,
        )
        self.assertEqual(response.status_code, 200)
        replay = self.client.post(
            "/api/v1/agent/runner/stop-evidence",
            data=body,
            content_type="application/json",
            HTTP_AUTHORIZATION="Bearer " + token,
        )
        self.assertEqual(response.json(), replay.json())
        job.refresh_from_db()
        attempt.refresh_from_db()
        self.assertEqual(job.status, "cancelled")
        self.assertFalse(attempt.active)

    def test_cross_realm_device_and_human_surfaces_disclose_no_job_data(self) -> None:
        import json
        from datetime import timedelta

        from django.utils.timezone import now

        from zerver.actions import agent_jobs as actions
        from zerver.lib.agent_secrets import hash_agent_credential

        job = actions.create_job(
            self.owner,
            profile=self.profile,
            source=self.message,
            request="Private task marker",
            idempotency_key=uuid4(),
            job_kind="answer",
            delivery_target="answer",
        )
        actions.claim_work(self.runner, claim_key=uuid4())
        job.refresh_from_db()
        attempt = agents.AgentAttempt.objects.get(job=job)
        foreign = self.example_user("cordelia")
        device = agents.AgentRunner.objects.create(
            realm=foreign.realm, owner=foreign, name="Foreign", fingerprint="z" * 64
        )
        token = "foreign-synthetic-token" + "x" * 40
        agents.AgentRunnerCredential.objects.create(
            realm=foreign.realm,
            runner=device,
            token_hash=hash_agent_credential(token),
            refresh_hash=hash_agent_credential("refresh" + token),
            expires_at=now() + timedelta(hours=24),
            refresh_expires_at=now() + timedelta(days=30),
        )
        identity = {
            "schema_version": 1,
            "job_id": str(job.id),
            "attempt_id": str(attempt.id),
            "lease_epoch": 1,
            "job_version": job.version,
        }
        cases: list[tuple[str, dict[str, object]]] = [
            ("context", {"reference_ids": [str(agents.AgentContextRef.objects.get(job=job).id)]}),
            ("inputs", {}),
            ("operations", {}),
            ("credential-access", {"provider_id": str(uuid4()), "secret_version": 1}),
        ]
        for route, extra in cases:
            response = self.client.post(
                "/api/v1/agent/runner/" + route,
                data=json.dumps(identity | extra),
                content_type="application/json",
                HTTP_AUTHORIZATION="Bearer " + token,
            )
            self.assertEqual(response.status_code, 400)
            self.assertNotIn("Private task marker", response.content.decode())
            self.assertEqual(response.json()["schema_version"], 1)
        self.login_user(foreign)
        response = self.client_get("/json/agent/jobs")
        self.assertEqual(self.assert_json_success(response)["count"], 0)
        for route in [
            f"jobs/{job.id}",
            f"jobs/{job.id}/events",
            f"jobs/{job.id}/inputs",
            f"artifacts/{uuid4()}",
        ]:
            response = self.client_get("/json/agent/" + route)
            self.assertEqual(response.status_code, 400)
            self.assertNotIn("Private task marker", response.content.decode())
        attempt.refresh_from_db()
        self.assertEqual(attempt.event_cursor, 0)

    def test_artifact_rejects_bad_checksum_oversize_and_symlink_root(self) -> None:
        import hashlib
        import tempfile
        from pathlib import Path

        from django.test import override_settings

        from zerver.actions import agent_jobs as actions
        from zerver.lib import agent_protocol as p
        from zerver.lib.agent_results import store_artifact

        job = actions.create_job(
            self.owner,
            profile=self.profile,
            source=self.message,
            request="Artifacts",
            idempotency_key=uuid4(),
            job_kind="answer",
            delivery_target="answer",
        )
        actions.claim_work(self.runner, claim_key=uuid4())
        attempt = agents.AgentAttempt.objects.get(job=job)
        with (
            tempfile.TemporaryDirectory() as directory,
            override_settings(AGENT_ARTIFACT_ROOT=directory),
        ):
            values: dict[str, Any] = {
                "chunks": [b"x"],
                "checksum": hashlib.sha256(b"x").hexdigest(),
                "kind": "summary",
                "filename": "x.txt",
                "media_type": "text/plain",
            }
            invalid_cases: list[dict[str, Any]] = [
                {"checksum": "a" * 64},
                {"filename": "../outside"},
                {"chunks": [b"x" * (p.MAX_ARTIFACT_BYTES + 1)]},
                {"media_type": "text/html"},
            ]
            for invalid in invalid_cases:
                with self.assertRaises(ValueError):
                    store_artifact(self.runner, job.id, attempt.id, 1, **(values | invalid))
            self.assertEqual(list(Path(directory).iterdir()), [])
            link = Path(directory) / "link"
            link.symlink_to(directory, target_is_directory=True)
            with override_settings(AGENT_ARTIFACT_ROOT=str(link)), self.assertRaises(ValueError):
                store_artifact(self.runner, job.id, attempt.id, 1, **values)
        self.assertEqual(agents.AgentArtifact.objects.count(), 0)

    def test_old_epoch_cannot_resume_events_artifacts_or_context(self) -> None:
        from django.utils.timezone import now

        from zerver.actions import agent_jobs as actions
        from zerver.lib import agent_protocol as p

        job = actions.create_job(
            self.owner,
            profile=self.profile,
            source=self.message,
            request="Resume",
            idempotency_key=uuid4(),
            job_kind="answer",
            delivery_target="answer",
        )
        actions.claim_work(self.runner, claim_key=uuid4())
        job.refresh_from_db()
        old = agents.AgentAttempt.objects.get(job=job)
        actions.cancel_job(self.owner, job.id, job.version)
        actions.record_event(
            self.runner,
            p.RunnerEvent.model_validate(
                {
                    "schema_version": 1,
                    "job_id": str(job.id),
                    "attempt_id": str(old.id),
                    "lease_epoch": 1,
                    "sequence": 1,
                    "event_id": str(uuid4()),
                    "occurred_at": now().isoformat(),
                    "type": "attempt.stopped",
                    "payload": {"process_state": "stopped", "stop_confirmed": True},
                }
            ),
        )
        job.refresh_from_db()
        actions.resume_job(self.owner, job.id, job.version)
        descriptor = actions.claim_work(self.runner, claim_key=uuid4())
        self.assertIsNotNone(descriptor)
        assert descriptor is not None
        self.assertEqual(descriptor["lease_epoch"], 2)
        with self.assertRaises(ValueError):
            from zerver.lib.agent_context import agent_transaction

            with agent_transaction():
                actions.locked_attempt(self.runner, job.id, old.id, 1)
        old.refresh_from_db()
        self.assertEqual(old.event_cursor, 1)

    def test_artifact_failed_registration_removes_every_untracked_file(self) -> None:
        import hashlib
        import tempfile
        from pathlib import Path

        from django.test import override_settings

        from zerver.actions import agent_jobs as actions
        from zerver.lib.agent_results import store_artifact

        job = actions.create_job(
            self.owner,
            profile=self.profile,
            source=self.message,
            request="Quota",
            idempotency_key=uuid4(),
            job_kind="answer",
            delivery_target="answer",
        )
        actions.claim_work(self.runner, claim_key=uuid4())
        attempt = agents.AgentAttempt.objects.get(job=job)
        attempt.descriptor["budget"]["job_artifact_bytes"] = 1
        attempt.save(update_fields=["descriptor"])
        with (
            tempfile.TemporaryDirectory() as directory,
            override_settings(AGENT_ARTIFACT_ROOT=directory),
        ):
            for _ in range(3):
                with self.assertRaisesRegex(ValueError, "Job artifact limit"):
                    store_artifact(
                        self.runner,
                        job.id,
                        attempt.id,
                        1,
                        chunks=[b"xx"],
                        checksum=hashlib.sha256(b"xx").hexdigest(),
                        kind="summary",
                        filename="x.txt",
                        media_type="text/plain",
                    )
                self.assertEqual(list(Path(directory).iterdir()), [])
            attempt.descriptor["budget"]["job_artifact_bytes"] = 1024
            attempt.save(update_fields=["descriptor"])

            def chunks() -> Iterator[bytes]:
                from zerver.actions.agents import revoke_runner

                yield b"x"
                revoke_runner(self.runner)

            with self.assertRaises(ValueError):
                store_artifact(
                    self.runner,
                    job.id,
                    attempt.id,
                    1,
                    chunks=chunks(),
                    checksum=hashlib.sha256(b"x").hexdigest(),
                    kind="summary",
                    filename="x.txt",
                    media_type="text/plain",
                )
            self.assertEqual(list(Path(directory).iterdir()), [])
        self.assertFalse(agents.AgentArtifact.objects.filter(attempt=attempt).exists())

    def test_claimed_provider_probe_credential_http_uses_frozen_lease(self) -> None:
        import base64
        import json
        import tempfile
        from datetime import timedelta
        from pathlib import Path

        from django.test import override_settings
        from django.utils.timezone import now

        from zerver.actions.agents import claim_setup, create_provider_probe, register_provider
        from zerver.lib.agent_secrets import hash_agent_credential

        token = "synthetic-probe-token-" + "a" * 40
        agents.AgentRunnerCredential.objects.create(
            runner=self.runner,
            realm=self.owner.realm,
            token_hash=hash_agent_credential(token),
            refresh_hash=hash_agent_credential("refresh" + token),
            expires_at=now() + timedelta(hours=1),
            refresh_expires_at=now() + timedelta(days=1),
        )
        with tempfile.TemporaryDirectory() as directory:
            key = Path(directory) / "key.json"
            key.write_text(
                json.dumps({"current": "v1", "keys": {"v1": base64.b64encode(b"a" * 32).decode()}})
            )
            with override_settings(AGENT_SECRET_MASTER_KEY_FILE=str(key)):
                provider = register_provider(
                    self.owner,
                    self.runner,
                    name="Synthetic",
                    base_url="https://example.com/v1",
                    model_id="fixture",
                    allowed_models=["fixture"],
                    context_window_tokens=4096,
                    max_output_tokens=1024,
                    credential="synthetic-secret-only",
                )
                setup = create_provider_probe(self.owner, provider, retry_key=uuid4())
                setup = claim_setup(self.runner, setup.id, uuid4())
                body = {
                    "schema_version": 1,
                    "setup_id": str(setup.id),
                    "claim_key": str(setup.claim_key),
                    "lease_epoch": setup.lease_epoch,
                    "descriptor_digest": setup.descriptor_digest,
                    "configuration_digest": setup.configuration_digest,
                    "provider_id": str(provider.id),
                    "secret_version": 1,
                }

                def post(value: dict[str, object]) -> Any:
                    return self.client.post(
                        "/api/v1/agent/runner/probe-credential-access",
                        data=json.dumps(value),
                        content_type="application/json",
                        HTTP_AUTHORIZATION="Bearer " + token,
                    )

                response = post(body)
                self.assertEqual(response.status_code, 200, response.content)
                self.assertEqual(response.json()["secret"], "synthetic-secret-only")
                self.assertEqual(response["Cache-Control"], "no-store")
                changes: list[dict[str, object]] = [
                    {"lease_epoch": 2},
                    {"claim_key": str(uuid4())},
                    {"secret_version": 2},
                ]
                for change in changes:
                    denied = post(body | change)
                    self.assertEqual(denied.status_code, 400)
                    self.assertNotIn("synthetic-secret-only", denied.content.decode())
                setup.lease_expires_at = now() - timedelta(seconds=1)
                setup.save(update_fields=["lease_expires_at"])
                self.assertEqual(post(body).status_code, 400)

    def test_typescript_callbacks_interleave_against_real_backend(self) -> None:
        import json
        import subprocess
        import tempfile
        import time
        from datetime import timedelta
        from http.server import BaseHTTPRequestHandler, HTTPServer
        from pathlib import Path

        from django.conf import settings
        from django.test import override_settings
        from django.utils.timezone import now

        from zerver.actions import agent_jobs as actions
        from zerver.lib.agent_secrets import hash_agent_credential

        token = "synthetic-callback-token-" + "a" * 40
        agents.AgentRunnerCredential.objects.create(
            runner=self.runner,
            realm=self.owner.realm,
            token_hash=hash_agent_credential(token),
            refresh_hash=hash_agent_credential("refresh" + token),
            expires_at=now() + timedelta(hours=1),
            refresh_expires_at=now() + timedelta(days=1),
        )
        job = actions.create_job(
            self.owner,
            profile=self.profile,
            source=self.message,
            request="Callback fixture",
            idempotency_key=uuid4(),
            job_kind="answer",
            delivery_target="answer",
        )
        directory = Path(tempfile.mkdtemp(prefix="grow-task7-backend-"))
        (directory / "artifacts").mkdir(mode=0o700)
        client = self.client
        records: list[tuple[str, int]] = []
        outer = self
        inserted = False

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format: str, *args: object) -> None:
                pass

            def do_GET(self) -> None:
                self.handle_request()

            def do_POST(self) -> None:
                self.handle_request()

            def handle_request(self) -> None:
                nonlocal inserted
                body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
                response = client.generic(
                    self.command,
                    self.path,
                    body,
                    content_type=self.headers.get("Content-Type", "application/json"),
                    HTTP_AUTHORIZATION=self.headers.get("Authorization", ""),
                )
                records.append((self.path, response.status_code))
                if response.status_code >= 400:
                    (directory / "errors.log").open("a").write(
                        self.path + " " + response.content.decode() + "\n"
                    )
                if (
                    self.path.endswith("/runner/claims")
                    and response.status_code == 200
                    and not inserted
                ):
                    inserted = True
                    job.refresh_from_db()
                    actions.add_input(
                        outer.owner,
                        job.id,
                        expected_version=job.version,
                        client_key=uuid4(),
                        text="Synthetic steering",
                        input_type="steering",
                    )
                self.send_response(response.status_code)
                self.send_header("Content-Type", response.get("Content-Type", "application/json"))
                self.send_header("Content-Length", str(len(response.content)))
                self.end_headers()
                self.wfile.write(response.content)

        server = HTTPServer(("127.0.0.1", 0), Handler)
        server.timeout = 0.1
        config = directory / "config.json"
        config.write_text(
            json.dumps(
                {
                    "origin": f"http://127.0.0.1:{server.server_port}",
                    "runner_id": str(self.runner.id),
                    "token": token,
                    "state": str(directory / "state"),
                }
            )
        )
        config.chmod(0o600)
        root = Path(settings.DEPLOY_ROOT)
        script = root / "services/grow-agent-runner/test/backend-callback-fixture.mjs"
        with override_settings(AGENT_ARTIFACT_ROOT=str(directory / "artifacts")):
            process = subprocess.Popen(
                ["node", str(script), str(config)], stdout=subprocess.PIPE, stderr=subprocess.PIPE
            )
            try:
                deadline = time.monotonic() + 30
                while process.poll() is None and time.monotonic() < deadline:
                    server.handle_request()
                if process.poll() is None:
                    process.kill()
                stdout, stderr = process.communicate(timeout=5)
                self.assertEqual(
                    process.returncode, 0, stderr.decode() + " evidence=" + str(directory)
                )
                self.assertTrue(json.loads(stdout)["passed"])
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait()
                server.server_close()
        (directory / "requests.json").write_text(json.dumps(records))
        self.assertEqual(agents.AgentArtifact.objects.filter(attempt__job=job).count(), 1)
        self.assertEqual(agents.AgentOperation.objects.get(attempt__job=job).status, "succeeded")
        self.assertEqual(agents.AgentInput.objects.get(job=job).delivery_state, "applied")
        self.assertEqual(agents.AgentCheckpoint.objects.filter(attempt__job=job).count(), 1)

    def test_local_provider_setup_authority_observes_without_renewal(self) -> None:
        import json
        from datetime import timedelta

        from django.utils.timezone import now

        from zerver.actions.agents import claim_setup, create_provider_probe, register_provider
        from zerver.lib.agent_secrets import hash_agent_credential

        token = "synthetic-authority-token-" + "a" * 40
        agents.AgentRunnerCredential.objects.create(
            runner=self.runner,
            realm=self.owner.realm,
            token_hash=hash_agent_credential(token),
            refresh_hash=hash_agent_credential("refresh" + token),
            expires_at=now() + timedelta(hours=1),
            refresh_expires_at=now() + timedelta(days=1),
        )
        provider = register_provider(
            self.owner,
            self.runner,
            name="Local fixture",
            base_url="https://example.com/v1",
            model_id="fixture",
            allowed_models=["fixture"],
            context_window_tokens=4096,
            max_output_tokens=512,
            local_credential_ref="owner-key",
        )
        setup = create_provider_probe(self.owner, provider, retry_key=uuid4())
        setup = claim_setup(self.runner, setup.id, uuid4())
        body = {
            "schema_version": 1,
            "setup_id": str(setup.id),
            "claim_key": str(setup.claim_key),
            "lease_epoch": setup.lease_epoch,
            "descriptor_digest": setup.descriptor_digest,
            "configuration_digest": setup.configuration_digest,
        }
        before = (setup.claim_key, setup.lease_epoch, setup.lease_expires_at)

        def post(value: dict[str, object]) -> Any:
            return self.client.post(
                "/api/v1/agent/runner/setup-authority",
                data=json.dumps(value),
                content_type="application/json",
                HTTP_AUTHORIZATION="Bearer " + token,
            )

        response = post(body)
        self.assertEqual(response.status_code, 200, response.content)
        self.assertNotIn("secret", response.json())
        self.assertEqual(response["Cache-Control"], "no-store")
        setup.refresh_from_db()
        self.assertEqual((setup.claim_key, setup.lease_epoch, setup.lease_expires_at), before)
        for change in [
            {"lease_epoch": 99},
            {"claim_key": str(uuid4())},
            {"configuration_digest": "0" * 64},
        ]:
            self.assertEqual(post(body | change).status_code, 400)
        grant = agents.AgentProbeGrant.objects.get(setup_operation=setup)
        grant.revoked_at = now()
        grant.save(update_fields=["revoked_at"])
        self.assertEqual(post(body).status_code, 400)
        setup.refresh_from_db()
        self.assertEqual((setup.claim_key, setup.lease_epoch, setup.lease_expires_at), before)

    def test_uncertain_local_operation_and_input_need_explicit_reconciliation(self) -> None:
        from django.utils.timezone import now

        from zerver.actions import agent_approvals as approvals
        from zerver.actions import agent_jobs as actions
        from zerver.lib import agent_protocol as p

        job = actions.create_job(
            self.owner,
            profile=self.profile,
            source=self.message,
            request="Reconcile",
            idempotency_key=uuid4(),
            job_kind="answer",
            delivery_target="answer",
        )
        actions.claim_work(self.runner, claim_key=uuid4())
        attempt = agents.AgentAttempt.objects.get(job=job)
        job.refresh_from_db()
        item = actions.add_input(
            self.owner,
            job.id,
            expected_version=job.version,
            client_key=uuid4(),
            text="steer",
            input_type="steering",
        )
        actions.deliver_inputs(self.runner, job.id, attempt.id, 1)
        operation = approvals.propose_operation(
            self.runner,
            job.id,
            attempt.id,
            1,
            operation_id=uuid4(),
            arguments={
                "action": "context.read",
                "context_ids": [str(agents.AgentContextRef.objects.get(job=job).id)],
            },
            tree_hash=None,
            diff_artifact_id=None,
        )
        approvals.consume_operation(
            self.runner,
            job.id,
            attempt.id,
            1,
            operation_id=operation.operation_id,
            expected_version=operation.version,
            operation_hash=operation.argument_digest,
        )
        actions.record_event(
            self.runner,
            p.RunnerEvent.model_validate(
                {
                    "schema_version": 1,
                    "job_id": str(job.id),
                    "attempt_id": str(attempt.id),
                    "lease_epoch": 1,
                    "sequence": 1,
                    "event_id": str(uuid4()),
                    "occurred_at": now().isoformat(),
                    "type": "attempt.stopped",
                    "payload": {"process_state": "stopped", "stop_confirmed": True},
                }
            ),
        )
        job.refresh_from_db()
        with self.assertRaisesRegex(ValueError, "Operation reconciliation"):
            actions.resume_job(self.owner, job.id, job.version)
        receipt = p.LocalOperationReceipt(
            operation_id=operation.operation_id,
            argument_digest=operation.argument_digest,
            base_commit=None,
            tree_hash=None,
            outcome="no_effect",
            observed_at=now(),
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

        def recovery_post(route: str, data: dict[str, Any], bearer: str = token) -> Any:
            return self.client.post(
                "/api/v1/agent/runner/operations" + route,
                data=json.dumps(data),
                content_type="application/json",
                HTTP_AUTHORIZATION="Bearer " + bearer,
            )

        listed = self.assert_json_success(recovery_post("", lease))
        self.assertEqual(listed["operations"][0]["operation_id"], str(operation.operation_id))
        route = "/reconcile-local"
        for selected, payload in [("", lease), (route, body)]:
            self.assertEqual(recovery_post(selected, payload, "foreign-operation").status_code, 400)
            self.assertEqual(
                recovery_post(selected, {**payload, "lease_epoch": 2}).status_code, 400
            )
        self.assertEqual(
            recovery_post(
                route, {**body, "receipt": {**body["receipt"], "argument_digest": "f" * 64}}
            ).status_code,
            400,
        )
        self.assertEqual(
            recovery_post(
                route,
                {**body, "receipt": {**body["receipt"], "observed_at": "2000-01-01T00:00:00Z"}},
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
        with self.assertRaisesRegex(ValueError, "Input reconciliation"):
            actions.resume_job(self.owner, job.id, job.version)
        receipt_id = uuid4()
        actions.reconcile_input(
            self.runner,
            job.id,
            attempt.id,
            1,
            input_id=item.id,
            input_sequence=item.sequence,
            outcome="not_applied",
            receipt_id=receipt_id,
        )
        actions.reconcile_input(
            self.runner,
            job.id,
            attempt.id,
            1,
            input_id=item.id,
            input_sequence=item.sequence,
            outcome="not_applied",
            receipt_id=receipt_id,
        )
        with self.assertRaisesRegex(ValueError, "conflict"):
            actions.reconcile_input(
                self.runner,
                job.id,
                attempt.id,
                1,
                input_id=item.id,
                input_sequence=item.sequence,
                outcome="applied",
                receipt_id=receipt_id,
            )
        actions.resume_job(self.owner, job.id, job.version)
        actions.claim_work(self.runner, claim_key=uuid4())
        next_attempt = agents.AgentAttempt.objects.get(job=job, number=2)
        delivered = actions.deliver_inputs(self.runner, job.id, next_attempt.id, 2)
        self.assertEqual([row["id"] for row in delivered], [str(item.id)])
        self.assertEqual(recovery_post(route, body).status_code, 400)

    def test_selected_attachment_http_rechecks_source_and_budget(self) -> None:
        import json
        from datetime import timedelta

        from django.utils.timezone import now

        from zerver.actions import agent_jobs as actions
        from zerver.lib.agent_secrets import hash_agent_credential
        from zerver.lib.upload import upload_message_attachment
        from zerver.models import Attachment

        path, _ = upload_message_attachment(
            "selected.txt", "text/plain", b"selected fixture", self.owner
        )
        attachment = Attachment.objects.get(path_id=path.removeprefix("/user_uploads/"))
        attachment.messages.add(self.message)
        job = actions.create_job(
            self.owner,
            profile=self.profile,
            source=self.message,
            request="Read file",
            idempotency_key=uuid4(),
            job_kind="answer",
            delivery_target="answer",
            context_attachment_ids=[attachment.id],
        )
        actions.claim_work(self.runner, claim_key=uuid4())
        attempt = agents.AgentAttempt.objects.get(job=job)
        ref = agents.AgentContextRef.objects.get(job=job, kind="attachment")
        job.refresh_from_db()
        token = "selected-file-token-" + "a" * 40
        agents.AgentRunnerCredential.objects.create(
            runner=self.runner,
            realm=self.owner.realm,
            token_hash=hash_agent_credential(token),
            refresh_hash=hash_agent_credential("refresh" + token),
            expires_at=now() + timedelta(hours=1),
            refresh_expires_at=now() + timedelta(days=1),
        )
        body = {
            "schema_version": 1,
            "job_id": str(job.id),
            "attempt_id": str(attempt.id),
            "lease_epoch": 1,
            "job_version": job.version,
            "reference_id": str(ref.id),
        }

        def post() -> Any:
            return self.client.post(
                "/api/v1/agent/runner/context-file",
                data=json.dumps(body),
                content_type="application/json",
                HTTP_AUTHORIZATION="Bearer " + token,
            )

        response = post()
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.content, b"selected fixture")
        self.assertEqual(response["Cache-Control"], "no-store")
        attachment.messages.remove(self.message)
        denied = post()
        self.assertEqual(denied.status_code, 400)
        self.assertNotIn(b"selected fixture", denied.content)

    def test_outbox_recovers_without_notifications_and_deadlines_stay_durable(self) -> None:
        from datetime import timedelta

        from django.utils.timezone import now

        from zerver.actions import agent_jobs as actions
        from zerver.lib.agent_reconcile import reconcile_agents

        job = actions.create_job(
            self.owner,
            profile=self.profile,
            source=self.message,
            request="Wait",
            idempotency_key=uuid4(),
            job_kind="answer",
            delivery_target="answer",
        )

        def fail_notification(event: dict[str, object]) -> None:
            raise RuntimeError("synthetic notification failure")

        result = reconcile_agents(notify=fail_notification)
        self.assertEqual(result["blocked"], 1)
        self.assertEqual(agents.AgentOutbox.objects.get(job=job).status, "pending")
        self.assertIsNotNone(actions.claim_work(self.runner, claim_key=uuid4()))
        agents.AgentOutbox.objects.filter(job=job).update(next_attempt_at=now())
        reconcile_agents()
        self.assertEqual(agents.AgentOutbox.objects.get(job=job).status, "delivered")
        # The deadline scanner also runs while admission is disabled.
        source = Message.objects.get(
            id=self.send_group_direct_message(
                self.owner, [self.profile.bot_user, self.example_user("iago")], "deadline"
            )
        )
        second = actions.create_job(
            self.owner,
            profile=self.profile,
            source=source,
            request="Deadline",
            idempotency_key=uuid4(),
            job_kind="answer",
            delivery_target="answer",
        )
        agents.AgentJob.objects.filter(id=second.id).update(
            start_deadline=now() - timedelta(seconds=1)
        )
        agents.AgentRealmSettings.objects.filter(realm=self.owner.realm).update(enabled=False)
        self.assertEqual(reconcile_agents()["expired_queues"], 1)
        second.refresh_from_db()
        self.assertEqual(second.status, "blocked")
        self.assertEqual(second.blocked_reason, "start_deadline_expired")
        self.assertEqual(reconcile_agents()["expired_queues"], 0)

    def test_history_grants_must_match_this_job_before_count_and_download(self) -> None:
        import hashlib
        import tempfile

        from django.test import override_settings

        from zerver.actions import agent_jobs as actions
        from zerver.lib.agent_results import store_artifact
        from zerver.models import Stream

        actor = self.example_user("iago")
        channel_a = Stream.objects.get(realm=self.owner.realm, name="Verona")
        channel_b = Stream.objects.get(realm=self.owner.realm, name="Denmark")
        source = Message.objects.get(
            id=self.send_stream_message(self.owner, "Denmark", "Scoped private job marker")
        )
        job = actions.create_job(
            self.owner,
            profile=self.profile,
            source=source,
            request="Scoped private job marker",
            idempotency_key=uuid4(),
            job_kind="answer",
            delivery_target="answer",
        )
        actions.claim_work(self.runner, claim_key=uuid4())
        attempt = agents.AgentAttempt.objects.get(job=job)
        grant = agents.AgentGrant.objects.create(
            realm=self.owner.realm,
            owner=self.owner,
            principal_user=actor,
            target_kind="profile",
            profile=self.profile,
            actions=["profile.use"],
            scope={"kind": "stream", "stream_id": channel_a.id},
        )
        agents.AgentGrant.objects.create(
            realm=self.owner.realm,
            owner=self.owner,
            principal_user=actor,
            target_kind="runner",
            runner=self.runner,
            actions=["runner.use"],
        )
        with (
            tempfile.TemporaryDirectory() as directory,
            override_settings(AGENT_ARTIFACT_ROOT=directory),
        ):
            artifact = store_artifact(
                self.runner,
                job.id,
                attempt.id,
                1,
                chunks=[b"scoped artifact"],
                checksum=hashlib.sha256(b"scoped artifact").hexdigest(),
                kind="summary",
                filename="summary.txt",
                media_type="text/plain",
            )
            self.login_user(actor)
            self.assertEqual(
                self.assert_json_success(self.client_get("/json/agent/jobs"))["count"], 0
            )
            for route in [
                f"jobs/{job.id}",
                f"jobs/{job.id}/events",
                f"jobs/{job.id}/inputs",
                f"artifacts/{artifact.id}",
            ]:
                response = self.client_get("/json/agent/" + route)
                self.assertEqual(response.status_code, 400)
                self.assertNotIn(b"Scoped private job marker", response.content)
                self.assertNotIn(b"scoped artifact", response.content)
            grant.scope = {"kind": "stream", "stream_id": channel_b.id}
            grant.save(update_fields=["scope"])
            self.assertEqual(
                self.assert_json_success(self.client_get("/json/agent/jobs"))["count"], 1
            )
            self.assertEqual(
                self.client_get(f"/json/agent/artifacts/{artifact.id}").content, b"scoped artifact"
            )

    def test_stopped_input_http_receipt_ignores_stale_job_version_only(self) -> None:
        import json
        from datetime import timedelta

        from django.utils.timezone import now

        from zerver.actions import agent_jobs as actions
        from zerver.lib import agent_protocol as p
        from zerver.lib.agent_secrets import hash_agent_credential

        job = actions.create_job(
            self.owner,
            profile=self.profile,
            source=self.message,
            request="Receipt recovery",
            idempotency_key=uuid4(),
            job_kind="answer",
            delivery_target="answer",
        )
        actions.claim_work(self.runner, claim_key=uuid4())
        attempt = agents.AgentAttempt.objects.get(job=job)
        job.refresh_from_db()
        item = actions.add_input(
            self.owner, job.id, expected_version=job.version, client_key=uuid4(), text="steer"
        )
        actions.deliver_inputs(self.runner, job.id, attempt.id, 1)
        actions.record_event(
            self.runner,
            p.RunnerEvent.model_validate(
                {
                    "schema_version": 1,
                    "job_id": str(job.id),
                    "attempt_id": str(attempt.id),
                    "lease_epoch": 1,
                    "sequence": 1,
                    "event_id": str(uuid4()),
                    "occurred_at": now().isoformat(),
                    "type": "attempt.stopped",
                    "payload": {"process_state": "stopped", "stop_confirmed": True},
                }
            ),
        )
        job.refresh_from_db()
        stale_version = job.version
        actions.cancel_job(self.owner, job.id, job.version)
        job.refresh_from_db()
        self.assertGreater(job.version, stale_version)
        token = "receipt-recovery-synthetic"
        agents.AgentRunnerCredential.objects.create(
            realm=self.owner.realm,
            runner=self.runner,
            token_hash=hash_agent_credential(token),
            refresh_hash=hash_agent_credential("receipt-refresh-synthetic"),
            expires_at=now() + timedelta(hours=1),
            refresh_expires_at=now() + timedelta(days=1),
        )
        payload = {
            "schema_version": 1,
            "job_id": str(job.id),
            "attempt_id": str(attempt.id),
            "lease_epoch": 1,
            "job_version": stale_version,
            "input_id": str(item.id),
            "input_sequence": item.sequence,
            "outcome": "not_applied",
            "receipt_id": str(uuid4()),
        }

        def post(data: dict[str, object], bearer: str = token) -> Any:
            return self.client.post(
                "/api/v1/agent/runner/inputs/reconcile",
                data=json.dumps(data),
                content_type="application/json",
                HTTP_AUTHORIZATION="Bearer " + bearer,
            )

        first = self.assert_json_success(post(payload))
        self.assertEqual(first, self.assert_json_success(post(payload)))
        self.assertEqual(first["input"]["delivery_state"], "pending")
        self.assertEqual(post({**payload, "outcome": "applied"}).status_code, 400)
        self.assertEqual(post({**payload, "lease_epoch": 2}).status_code, 400)
        foreign = agents.AgentRunner.objects.create(
            realm=self.owner.realm, owner=self.owner, name="Foreign", fingerprint="different"
        )
        agents.AgentRunnerCredential.objects.create(
            realm=self.owner.realm,
            runner=foreign,
            token_hash=hash_agent_credential("foreign-receipt"),
            refresh_hash=hash_agent_credential("foreign-refresh"),
            expires_at=now() + timedelta(hours=1),
            refresh_expires_at=now() + timedelta(days=1),
        )
        self.assertEqual(post(payload, "foreign-receipt").status_code, 400)
        attempt.refresh_from_db()
        job.refresh_from_db()
        self.assertFalse(attempt.active)
        self.assertEqual(attempt.process_state, "stopped")
        self.assertEqual(agents.AgentAttempt.objects.filter(job=job).count(), 1)
        # Only the separate owner resume action can create another attempt.
        actions.resume_job(self.owner, job.id, job.version)
        actions.claim_work(self.runner, claim_key=uuid4())
        self.assertEqual(post(payload).status_code, 400)

        second = agents.AgentAttempt.objects.get(job=job, number=2)
        actions.deliver_inputs(self.runner, job.id, second.id, second.lease_epoch)
        actions.record_event(
            self.runner,
            p.RunnerEvent.model_validate(
                {
                    "schema_version": 1,
                    "job_id": str(job.id),
                    "attempt_id": str(second.id),
                    "lease_epoch": second.lease_epoch,
                    "sequence": 1,
                    "event_id": str(uuid4()),
                    "occurred_at": now().isoformat(),
                    "type": "attempt.stopped",
                    "payload": {"process_state": "stopped", "stop_confirmed": True},
                }
            ),
        )
        next_payload = {
            **payload,
            "attempt_id": str(second.id),
            "lease_epoch": second.lease_epoch,
            "outcome": "applied",
            "receipt_id": str(uuid4()),
        }
        # A receipt ID cannot describe a different attempt or outcome.
        self.assertEqual(
            post({**next_payload, "receipt_id": payload["receipt_id"]}).status_code, 400
        )
        applied = self.assert_json_success(post(next_payload))
        self.assertEqual(applied["input"]["delivery_state"], "applied")
        self.assertEqual(applied, self.assert_json_success(post(next_payload)))
        self.assertEqual(post({**next_payload, "receipt_id": str(uuid4())}).status_code, 400)
        item.refresh_from_db()
        assert item.reconciliation_receipt is not None
        self.assertEqual(item.reconciliation_receipt["attempt_id"], str(second.id))
        self.assertEqual(
            item.reconciliation_receipt["history"][0]["receipt_id"], payload["receipt_id"]
        )
        second.refresh_from_db()
        self.assertFalse(second.active)
        self.assertEqual(second.process_state, "stopped")
        self.assertEqual(agents.AgentAttempt.objects.filter(job=job).count(), 2)
