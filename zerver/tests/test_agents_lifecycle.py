"""Durable lifecycle tests use real records and public action boundaries."""

import importlib
import importlib.util
from collections.abc import Iterator
from typing import Any
from uuid import uuid4

from typing_extensions import override

from zerver.actions.agents import create_profile, enable_profile, record_readiness
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
        enable_profile(self.owner, self.profile, expected_revision=self.profile.revision)
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
            # RL-3: the auto-publish below is scheduled on transaction commit, so
            # a plain ZulipTestCase test needs captureOnCommitCallbacks to run it.
            with self.captureOnCommitCallbacks(execute=True):
                event(3, "attempt.stopped", {"process_state": "stopped", "stop_confirmed": True})
            job.refresh_from_db()
            # The attempt stopping while the job is verifying publishes right away
            # instead of waiting for the reconcile timer.
            self.assertEqual(job.status, "completed")
            self.assertIsNotNone(job.result_proposal)
            self.assertFalse(agents.AgentInput.objects.filter(job=job).exists())
            with self.assertRaisesRegex(ValueError, "Job cannot accept input"):
                actions.add_input(
                    self.owner,
                    job.id,
                    expected_version=job.version,
                    client_key=uuid4(),
                    text="Too late for this attempt",
                )
            first = results.publish_result(job.id)
            second = results.publish_result(job.id)
            self.assertEqual(first, second)
            self.assertEqual(job.result_message_id, first["message_id"])
            assert job.result_message_id is not None
            Message.objects.filter(id=job.result_message_id).delete()
            job.refresh_from_db()
            self.assertIsNone(job.result_message_id)
            self.assertEqual(results.publish_result(job.id), first)

    def test_accepted_job_posts_one_queued_notice(self) -> None:
        from django.utils.timezone import now

        from zerver.actions import agent_jobs as actions

        agents.AgentRunner.objects.filter(id=self.runner.id).update(
            status="online", last_heartbeat_at=now()
        )
        with self.captureOnCommitCallbacks(execute=True):
            job = actions.create_job(
                self.owner,
                profile=self.profile,
                source=self.message,
                request="Please answer",
                idempotency_key=uuid4(),
                job_kind="answer",
                delivery_target="answer",
            )
        message = Message.objects.get(sender=self.profile.bot_user)
        self.assertEqual(
            message.content, f"This task is queued. {self.owner.realm.url}/#agent-jobs/{job.id}"
        )
        self.assertEqual(Message.objects.filter(sender=self.profile.bot_user).count(), 1)

    def test_accepted_notice_for_an_offline_runner_says_it_waits(self) -> None:
        from zerver.actions import agent_jobs as actions

        agents.AgentRunner.objects.filter(id=self.runner.id).update(status="offline")
        with self.captureOnCommitCallbacks(execute=True):
            job = actions.create_job(
                self.owner,
                profile=self.profile,
                source=self.message,
                request="Please answer",
                idempotency_key=uuid4(),
                job_kind="answer",
                delivery_target="answer",
            )
        message = Message.objects.get(sender=self.profile.bot_user)
        self.assertEqual(
            message.content,
            "This task is saved. It starts when the agent's device connects."
            f" {self.owner.realm.url}/#agent-jobs/{job.id}",
        )

    def test_post_job_notice_marks_once_and_never_admits(self) -> None:
        from zerver.actions import agent_jobs as actions
        from zerver.lib.agent_results import post_job_notice

        job = actions.create_job(
            self.owner,
            profile=self.profile,
            source=self.message,
            request="Please answer",
            idempotency_key=uuid4(),
            job_kind="answer",
            delivery_target="answer",
        )
        jobs_before = agents.AgentJob.objects.count()
        sent = post_job_notice(job, "status:test:once", "This task needs your approval.")
        self.assertTrue(sent)
        self.assertEqual(
            agents.AgentOutbox.objects.filter(delivery_key="status:test:once").count(), 1
        )
        message = Message.objects.get(sender=self.profile.bot_user)
        self.assertEqual(
            message.content,
            f"@**{self.owner.full_name}|{self.owner.id}** This task needs your approval."
            f" {self.owner.realm.url}/#agent-jobs/{job.id}",
        )
        repeated = post_job_notice(job, "status:test:once", "This task needs your approval.")
        self.assertFalse(repeated)
        self.assertEqual(Message.objects.filter(sender=self.profile.bot_user).count(), 1)
        # Bot messages never trigger agent admission.
        self.assertEqual(agents.AgentJob.objects.count(), jobs_before)

    def test_status_notice_posts_after_commit_and_never_undoes_the_transition(self) -> None:
        """RL-4: notify_conversation only schedules the post; the caller's own
        transition already committed by the time (or whether) it ever runs."""
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
        actions.record_event(
            self.runner,
            p.RunnerEvent.model_validate(
                {
                    "schema_version": 1,
                    "job_id": str(job.id),
                    "attempt_id": str(attempt.id),
                    "lease_epoch": 1,
                    "event_id": str(uuid4()),
                    "sequence": 1,
                    "type": "attempt.started",
                    "occurred_at": now().isoformat(),
                    "payload": {"process_state": "active"},
                }
            ),
        )
        with self.captureOnCommitCallbacks() as callbacks:
            actions.record_event(
                self.runner,
                p.RunnerEvent.model_validate(
                    {
                        "schema_version": 1,
                        "job_id": str(job.id),
                        "attempt_id": str(attempt.id),
                        "lease_epoch": 1,
                        "event_id": str(uuid4()),
                        "sequence": 2,
                        "type": "input.requested",
                        "occurred_at": now().isoformat(),
                        "payload": {"question": "Which environment?", "options": []},
                    }
                ),
            )
            # The transition already committed; the notice is still only scheduled.
            job.refresh_from_db()
            self.assertEqual(job.status, "waiting_for_input")
            self.assertFalse(Message.objects.filter(sender=self.profile.bot_user).exists())
        self.assertEqual(len(callbacks), 1)
        for callback in callbacks:
            callback()
        message = Message.objects.get(sender=self.profile.bot_user)
        self.assertEqual(
            message.content,
            f"@**{self.owner.full_name}|{self.owner.id}** This task needs your answer."
            f" {self.owner.realm.url}/#agent-jobs/{job.id}",
        )
        job.refresh_from_db()
        self.assertEqual(job.status, "waiting_for_input")

    def test_failed_notice_send_leaves_no_marker(self) -> None:
        from unittest.mock import patch

        from zerver.actions import agent_jobs as actions
        from zerver.lib.agent_results import post_job_notice
        from zerver.models.clients import get_client

        job = actions.create_job(
            self.owner,
            profile=self.profile,
            source=self.message,
            request="Please answer",
            idempotency_key=uuid4(),
            job_kind="answer",
            delivery_target="answer",
        )
        # Warm the cached "Grow Agent" Client row outside the mocked attempt
        # below: get_client caches its result across the row's own rollback,
        # so creating it for the first time inside that rolled-back savepoint
        # would leave the cache holding a Client id no longer in the database.
        get_client("Grow Agent")
        with (
            patch("zerver.actions.message_send.do_send_messages", side_effect=ValueError("boom")),
            self.assertRaises(ValueError),
        ):
            post_job_notice(job, "status:test:fails", "This task needs your approval.")
        self.assertFalse(
            agents.AgentOutbox.objects.filter(delivery_key="status:test:fails").exists()
        )
        self.assertFalse(Message.objects.filter(sender=self.profile.bot_user).exists())
        # A later, unpatched call with the same key can still succeed.
        sent = post_job_notice(job, "status:test:fails", "This task needs your approval.")
        self.assertTrue(sent)
        self.assertEqual(
            agents.AgentOutbox.objects.filter(delivery_key="status:test:fails").count(), 1
        )

    def test_robust_on_commit_notice_failure_never_blocks_a_later_hook(self) -> None:
        """RL-4: functools.partial has no __qualname__, so a raised exception
        used to make Django's own robust=True logging raise AttributeError and
        skip every later on_commit hook registered in the same transaction."""
        from unittest.mock import patch

        from django.db import transaction

        from zerver.actions import agent_jobs as actions
        from zerver.models.clients import get_client

        job = actions.create_job(
            self.owner,
            profile=self.profile,
            source=self.message,
            request="Please answer",
            idempotency_key=uuid4(),
            job_kind="answer",
            delivery_target="answer",
        )
        # Warm the cached "Grow Agent" Client row outside the mocked send
        # below, the same reason test_failed_notice_send_leaves_no_marker does.
        get_client("Grow Agent")
        later_hook_ran: list[bool] = []
        with (
            patch("zerver.actions.message_send.do_send_messages", side_effect=ValueError("boom")),
            self.assertLogs("zerver.actions.agent_jobs", level="ERROR"),
            self.captureOnCommitCallbacks(execute=True),
        ):
            actions.notify_conversation(job, "status:test:raises", "This will fail to send.")
            transaction.on_commit(lambda: later_hook_ran.append(True))
        self.assertEqual(later_hook_ran, [True])
        self.assertFalse(Message.objects.filter(sender=self.profile.bot_user).exists())

    def test_job_notice_retries_agent_busy_before_giving_up(self) -> None:
        """RL-1: post_job_notice runs its own agent_transaction with
        try-locks; a single AgentBusy used to drop the notice for good instead
        of retrying it the way the phase-3 team receipt store does."""
        from unittest.mock import patch

        from zerver.actions import agent_jobs as actions
        from zerver.lib.agent_context import AgentBusy
        from zerver.lib.agent_results import post_job_notice as real_post_job_notice

        job = actions.create_job(
            self.owner,
            profile=self.profile,
            source=self.message,
            request="Please answer",
            idempotency_key=uuid4(),
            job_kind="answer",
            delivery_target="answer",
        )
        calls = 0

        def flaky_once(*args: object, **kwargs: object) -> bool:
            nonlocal calls
            calls += 1
            if calls == 1:
                raise AgentBusy("Agent authority is busy. Retry this request.")
            return real_post_job_notice(*args, **kwargs)  # type: ignore[arg-type]

        with (
            patch("zerver.lib.agent_results.post_job_notice", side_effect=flaky_once),
            patch("time.sleep"),
            self.captureOnCommitCallbacks(execute=True),
        ):
            actions.notify_conversation(job, "status:test:busy", "Retry me.")
        self.assertEqual(calls, 2)
        message = Message.objects.get(sender=self.profile.bot_user)
        self.assertIn("Retry me.", message.content)
        self.assertEqual(
            agents.AgentOutbox.objects.filter(delivery_key="status:test:busy").count(), 1
        )

    def test_eager_publish_runs_after_the_events_transaction_commits(self) -> None:
        import hashlib
        import tempfile

        from django.test import override_settings
        from django.utils.timezone import now

        from zerver.actions import agent_jobs as actions
        from zerver.lib import agent_protocol as p
        from zerver.lib.agent_results import store_artifact

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
                        "event_id": str(uuid4()),
                        "sequence": sequence,
                        "type": kind,
                        "occurred_at": now().isoformat(),
                        "payload": payload,
                    }
                ),
            )

        event("attempt.started", {"process_state": "active"})
        with (
            tempfile.TemporaryDirectory() as directory,
            override_settings(AGENT_ARTIFACT_ROOT=directory),
        ):
            content = b"A verified answer."
            artifact = store_artifact(
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
            event(
                "result.prepared",
                {"artifact_ids": [str(artifact.id)], "summary": "A verified answer."},
            )
            with self.captureOnCommitCallbacks() as callbacks:
                event("attempt.stopped", {"process_state": "stopped", "stop_confirmed": True})
                job.refresh_from_db()
                self.assertEqual(job.status, "verifying")
            self.assertEqual(len(callbacks), 1)
            for callback in callbacks:
                callback()
            job.refresh_from_db()
            self.assertEqual(job.status, "completed")

    def test_stop_evidence_publishes_after_commit(self) -> None:
        import hashlib
        import tempfile
        from datetime import timedelta

        from django.test import override_settings
        from django.utils.timezone import now

        from zerver.actions import agent_jobs as actions
        from zerver.lib import agent_protocol as p
        from zerver.lib.agent_results import store_artifact
        from zerver.lib.agent_secrets import hash_agent_credential

        token = "stop-evidence-publish-token" + "x" * 40
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
            request="Please answer",
            idempotency_key=uuid4(),
            job_kind="answer",
            delivery_target="answer",
        )
        actions.claim_work(self.runner, claim_key=uuid4())
        attempt = agents.AgentAttempt.objects.get(job=job)
        actions.record_event(
            self.runner,
            p.RunnerEvent.model_validate(
                {
                    "schema_version": 1,
                    "job_id": str(job.id),
                    "attempt_id": str(attempt.id),
                    "lease_epoch": 1,
                    "event_id": str(uuid4()),
                    "sequence": 1,
                    "type": "attempt.started",
                    "occurred_at": now().isoformat(),
                    "payload": {"process_state": "active"},
                }
            ),
        )
        with (
            tempfile.TemporaryDirectory() as directory,
            override_settings(AGENT_ARTIFACT_ROOT=directory),
        ):
            content = b"A verified answer."
            artifact = store_artifact(
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
            actions.record_event(
                self.runner,
                p.RunnerEvent.model_validate(
                    {
                        "schema_version": 1,
                        "job_id": str(job.id),
                        "attempt_id": str(attempt.id),
                        "lease_epoch": 1,
                        "event_id": str(uuid4()),
                        "sequence": 2,
                        "type": "result.prepared",
                        "occurred_at": now().isoformat(),
                        "payload": {
                            "artifact_ids": [str(artifact.id)],
                            "summary": "A verified answer.",
                        },
                    }
                ),
            )
            stop_event = p.RunnerEvent.model_validate(
                {
                    "schema_version": 1,
                    "job_id": str(job.id),
                    "attempt_id": str(attempt.id),
                    "lease_epoch": 1,
                    "event_id": str(uuid4()),
                    "sequence": 3,
                    "type": "attempt.stopped",
                    "occurred_at": now().isoformat(),
                    "payload": {"process_state": "stopped", "stop_confirmed": True},
                }
            )
            with self.captureOnCommitCallbacks() as callbacks:
                actions.stop_evidence(token, stop_event)
                job.refresh_from_db()
                self.assertEqual(job.status, "verifying")
            self.assertEqual(len(callbacks), 1)
            for callback in callbacks:
                callback()
            job.refresh_from_db()
            self.assertEqual(job.status, "completed")

    def test_waiting_for_input_posts_one_bot_notice(self) -> None:
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
        with self.captureOnCommitCallbacks(execute=True):
            event(2, "input.requested", {"question": "Which environment?", "options": []})
        job.refresh_from_db()
        self.assertEqual(job.status, "waiting_for_input")
        message = Message.objects.get(sender=self.profile.bot_user)
        self.assertEqual(
            message.content,
            f"@**{self.owner.full_name}|{self.owner.id}** This task needs your answer."
            f" {self.owner.realm.url}/#agent-jobs/{job.id}",
        )
        self.assertEqual(agents.AgentJob.objects.count(), 1)

    def test_job_interrupted_posts_one_short_message(self) -> None:
        from datetime import timedelta

        from django.utils.timezone import now

        from zerver.actions import agent_jobs as actions
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
        with self.captureOnCommitCallbacks(execute=True):
            reconcile_agents()
        job.refresh_from_db()
        self.assertEqual(job.status, "interrupted")
        message = Message.objects.get(sender=self.profile.bot_user)
        self.assertEqual(
            message.content,
            "The server lost contact with the runner before the task stopped."
            f" {self.owner.realm.url}/#agent-jobs/{job.id}",
        )
        from zerver.views.agent_jobs import job_data

        self.assertEqual(job_data(self.owner, job)["reason_code"], "lease_lost")
        # A later reconcile pass must not add a second notice for the same job.
        reconcile_agents()
        self.assertEqual(Message.objects.filter(sender=self.profile.bot_user).count(), 1)

    def test_stop_before_start_gets_reason_start_failed(self) -> None:
        from django.utils.timezone import now

        from zerver.actions import agent_jobs as actions
        from zerver.lib import agent_protocol as p
        from zerver.views.agent_jobs import job_data

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
        self.assertIsNone(attempt.started_at)
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
        self.assertEqual(job.status, "interrupted")
        detail = job_data(self.owner, job)
        self.assertEqual(detail["reason_code"], "start_failed")
        self.assertTrue(detail["resume_available"])
        self.assertIsNone(detail["resume_unavailable_reason"])
        self.assertIn("resume", detail["allowed_actions"])

    def test_resume_unavailable_while_attempt_active_or_runner_down(self) -> None:
        from datetime import timedelta

        from django.utils.timezone import now

        from zerver.actions import agent_jobs as actions
        from zerver.views.agent_jobs import job_data

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
        agents.AgentJob.objects.filter(id=job.id).update(
            status="interrupted", blocked_reason="stop_unconfirmed"
        )

        def detail() -> dict[str, object]:
            return job_data(self.owner, agents.AgentJob.objects.get(id=job.id))

        self.assertFalse(detail()["resume_available"])
        self.assertEqual(detail()["resume_unavailable_reason"], "attempt_active")
        self.assertNotIn("resume", detail()["allowed_actions"])
        agents.AgentAttempt.objects.filter(id=attempt.id).update(active=False, ended_at=now())
        self.assertTrue(detail()["resume_available"])
        self.assertIsNone(detail()["resume_unavailable_reason"])
        self.assertIn("resume", detail()["allowed_actions"])
        agents.AgentRunner.objects.filter(id=self.runner.id).update(status="offline")
        self.assertFalse(detail()["resume_available"])
        self.assertEqual(detail()["resume_unavailable_reason"], "runner_offline")
        agents.AgentRunner.objects.filter(id=self.runner.id).update(
            status="unknown", revoked_at=now() - timedelta(seconds=1)
        )
        self.assertEqual(detail()["resume_unavailable_reason"], "runner_revoked")

    def test_empty_result_summary_gets_reason_result_invalid_and_survives_a_later_stop(
        self,
    ) -> None:
        from django.utils.timezone import now

        from zerver.actions import agent_jobs as actions
        from zerver.lib import agent_protocol as p
        from zerver.views.agent_jobs import job_data

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

        event("attempt.started", {"process_state": "active"})
        event("result.prepared", {"artifact_ids": [str(uuid4())], "summary": "   "})
        job.refresh_from_db()
        self.assertEqual(job.status, "interrupted")
        self.assertEqual(job_data(self.owner, job)["reason_code"], "result_invalid")
        # A later attempt.stopped must not clobber the more specific reason.
        event("attempt.stopped", {"process_state": "stopped", "stop_confirmed": True})
        job.refresh_from_db()
        self.assertEqual(job.status, "interrupted")
        self.assertEqual(job_data(self.owner, job)["reason_code"], "result_invalid")

    def test_interrupted_over_its_time_budget_gets_reason_budget_exhausted(self) -> None:
        from datetime import timedelta

        from django.utils.timezone import now

        from zerver.actions import agent_jobs as actions
        from zerver.views.agent_jobs import job_data

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
            created_at=now() - timedelta(hours=2),
            stopped_at=now(),
            ended_at=now(),
            active=False,
        )
        agents.AgentJob.objects.filter(id=job.id).update(
            status="interrupted", blocked_reason="runtime_stopped"
        )
        job.refresh_from_db()
        self.assertEqual(job_data(self.owner, job)["reason_code"], "budget_exhausted")

    def test_publication_blocked_by_a_failed_check_gets_reason_verification_failed(self) -> None:
        from copy import deepcopy

        from django.utils.timezone import now

        from zerver.actions import agent_jobs as actions
        from zerver.actions.agents import register_repository
        from zerver.views.agent_jobs import job_data

        repository = register_repository(
            self.owner,
            self.runner,
            workspace_alias="verifycode",
            canonical_origin="https://example.com/team/repo",
            allowed_refs=["main"],
            required_checks=[{"id": "required", "argv": ["true"]}],
        )
        policy = deepcopy(self.profile.policy)
        policy["actions"] = ["context.read", "repository.read", "repository.edit", "checks.run"]
        profile = create_profile(
            self.owner,
            name="VerifyCode",
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
        profile.refresh_from_db()
        enable_profile(self.owner, profile, expected_revision=profile.revision)
        message = Message.objects.get(
            id=self.send_group_direct_message(
                self.owner, [profile.bot_user, self.example_user("iago")], "Code task"
            )
        )
        job = actions.create_job(
            self.owner,
            profile=profile,
            source=message,
            request="Run a script",
            idempotency_key=uuid4(),
            job_kind="code",
            delivery_target="patch",
            repository=repository,
            base_ref="main",
        )
        actions.claim_work(self.runner, claim_key=uuid4())
        attempt = agents.AgentAttempt.objects.get(job=job)
        # No AgentVerification row exists for the required check: publish_result's
        # own verify_result loop would raise "Required checks do not prove the
        # final tree." here, distinct from any other publish-time rejection.
        agents.AgentAttempt.objects.filter(id=attempt.id).update(
            tree_hash="b" * 40,
            process_state="stopped",
            active=False,
            stopped_at=now(),
            ended_at=now(),
        )
        agents.AgentJob.objects.filter(id=job.id).update(
            status="verifying", blocked_reason="publication_blocked"
        )
        job.refresh_from_db()
        self.assertEqual(job_data(self.owner, job)["reason_code"], "verification_failed")

    def test_waiting_for_approval_posts_one_bot_notice_and_ignores_replay(self) -> None:
        import hashlib
        import tempfile
        from copy import deepcopy

        from django.test import override_settings
        from django.utils.timezone import now

        from zerver.actions import agent_jobs as actions
        from zerver.actions.agent_approvals import propose_operation
        from zerver.actions.agents import register_repository
        from zerver.lib import agent_protocol as p
        from zerver.lib.agent_results import store_artifact

        repository = register_repository(
            self.owner,
            self.runner,
            workspace_alias="approvalcode",
            canonical_origin="https://example.com/team/repo",
            allowed_refs=["main"],
            required_checks=[{"id": "required", "argv": ["true"]}],
        )
        policy = deepcopy(self.profile.policy)
        # git.push always needs approval (agent_approvals.APPROVAL_ACTIONS), unlike an
        # action such as shell.run that only needs it when absent from the policy
        # ceiling below, which the profile owner's own job would never hit.
        policy["actions"] = [
            "context.read",
            "repository.read",
            "repository.edit",
            "checks.run",
            "git.push",
        ]
        profile = create_profile(
            self.owner,
            name="ApprovalCode",
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
        profile.refresh_from_db()
        enable_profile(self.owner, profile, expected_revision=profile.revision)
        message = Message.objects.get(
            id=self.send_group_direct_message(
                self.owner, [profile.bot_user, self.example_user("iago")], "Code task"
            )
        )
        job = actions.create_job(
            self.owner,
            profile=profile,
            source=message,
            request="Run a script",
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

        with (
            tempfile.TemporaryDirectory() as directory,
            override_settings(AGENT_ARTIFACT_ROOT=directory),
        ):
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
            operation_id = uuid4()
            operation_arguments = {
                "action": "git.push",
                "repository_id": str(repository.id),
                "remote": repository.canonical_origin,
                "branch": f"grow-agent/{job.id}/result",
                "commit": "c" * 40,
                "expected_remote_head": None,
            }
            with self.captureOnCommitCallbacks(execute=True):
                operation = propose_operation(
                    self.runner,
                    job.id,
                    attempt.id,
                    1,
                    operation_id=operation_id,
                    arguments=operation_arguments,
                    tree_hash="b" * 40,
                    diff_artifact_id=artifact.id,
                )
            job.refresh_from_db()
            self.assertEqual(job.status, "waiting_for_approval")
            message = Message.objects.get(sender=profile.bot_user)
            self.assertEqual(
                message.content,
                f"@**{self.owner.full_name}|{self.owner.id}** This task needs your approval."
                f" {self.owner.realm.url}/#agent-jobs/{job.id}",
            )
            # A replayed proposal with the same operation_id returns the existing
            # operation and must not post a second notice.
            replay = propose_operation(
                self.runner,
                job.id,
                attempt.id,
                1,
                operation_id=operation_id,
                arguments=operation_arguments,
                tree_hash="b" * 40,
                diff_artifact_id=artifact.id,
            )
            self.assertEqual(replay.id, operation.id)
            self.assertEqual(Message.objects.filter(sender=profile.bot_user).count(), 1)

    def test_prepared_result_poll_fences_input_then_stop_unlocks_publication(self) -> None:
        import hashlib
        import tempfile
        from datetime import timedelta

        from django.test import override_settings
        from django.utils.timezone import now

        from zerver.actions import agent_jobs as actions
        from zerver.lib import agent_protocol as p
        from zerver.lib import agent_results as results
        from zerver.lib.agent_secrets import hash_agent_credential
        from zerver.lib.mention import silent_mention_syntax_for_user
        from zerver.views.agent_jobs import job_data

        token = "synthetic-completion-token-" + "a" * 40
        agents.AgentRunnerCredential.objects.create(
            runner=self.runner,
            realm=self.owner.realm,
            token_hash=hash_agent_credential(token),
            refresh_hash=hash_agent_credential("refresh" + token),
            expires_at=now() + timedelta(hours=1),
            refresh_expires_at=now() + timedelta(days=1),
        )
        directory = tempfile.mkdtemp(prefix="grow-fix1-completion-")
        with override_settings(AGENT_ARTIFACT_ROOT=directory):
            for route, late_result in [
                ("controls", False),
                ("heartbeat", False),
                ("controls", True),
                ("heartbeat", True),
            ]:
                with self.subTest(route=route, late_result=late_result):
                    message_id = self.send_group_direct_message(
                        self.owner,
                        [self.profile.bot_user, self.example_user("iago")],
                        content=f"Completion boundary {route} {late_result}",
                    )
                    job = actions.create_job(
                        self.owner,
                        profile=self.profile,
                        source=Message.objects.get(id=message_id),
                        request="Bounded completion",
                        idempotency_key=uuid4(),
                        job_kind="answer",
                        delivery_target="answer",
                    )
                    actions.claim_work(self.runner, claim_key=uuid4())
                    attempt = agents.AgentAttempt.objects.get(job=job)

                    def event(
                        kind: str,
                        payload: dict[str, object],
                        job: agents.AgentJob = job,
                        attempt: agents.AgentAttempt = attempt,
                    ) -> None:
                        attempt.refresh_from_db()
                        actions.record_event(
                            self.runner,
                            p.RunnerEvent.model_validate(
                                {
                                    "schema_version": 1,
                                    "job_id": str(job.id),
                                    "attempt_id": str(attempt.id),
                                    "lease_epoch": attempt.lease_epoch,
                                    "event_id": str(uuid4()),
                                    "sequence": attempt.event_cursor + 1,
                                    "type": kind,
                                    "occurred_at": now().isoformat(),
                                    "payload": payload,
                                }
                            ),
                        )

                    def poll(
                        route: str = route,
                        job: agents.AgentJob = job,
                        attempt: agents.AgentAttempt = attempt,
                    ) -> str:
                        if route == "controls":
                            response = self.client.get(
                                "/api/v1/agent/runner/controls",
                                HTTP_AUTHORIZATION="Bearer " + token,
                            )
                            self.assertEqual(response.status_code, 200, response.content)
                            return str(response.json()["controls"][0]["control"])
                        value = actions.heartbeat(
                            self.runner,
                            [p.LeaseIdentity(job_id=job.id, attempt_id=attempt.id, lease_epoch=1)],
                        )
                        return str(value[0]["control"])

                    event("attempt.started", {"process_state": "active"})
                    content = b"Completed answer"
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
                    proposal = {"artifact_ids": [str(artifact.id)], "summary": "Completed answer"}
                    self.assertEqual(poll(), "continue")
                    if not late_result:
                        event("result.prepared", proposal)
                    job.refresh_from_db()
                    item = actions.add_input(
                        self.owner,
                        job.id,
                        expected_version=job.version,
                        client_key=uuid4(),
                        text="Accepted before the stop boundary",
                    )
                    if late_result:
                        event("result.prepared", proposal)
                    self.assertEqual(poll(), "continue")
                    job.refresh_from_db()
                    if not late_result:
                        self.assertEqual(job.status, "running")
                        self.assertIsNone(job.result_proposal)
                    actions.deliver_inputs(self.runner, job.id, attempt.id, 1)
                    event(
                        "input.applied",
                        {
                            "input_id": str(item.id),
                            "input_sequence": item.sequence,
                            "delivery_state": "applied",
                        },
                    )
                    self.assertEqual(poll(), "continue")
                    job.refresh_from_db()
                    self.assertEqual(job.status, "running")
                    self.assertIsNone(job.result_proposal)
                    replacement = b"Replacement includes accepted steering"
                    artifact = results.store_artifact(
                        self.runner,
                        job.id,
                        attempt.id,
                        1,
                        chunks=[replacement],
                        checksum=hashlib.sha256(replacement).hexdigest(),
                        kind="summary",
                        filename="replacement.txt",
                        media_type="text/plain",
                    )
                    event(
                        "result.prepared",
                        {"artifact_ids": [str(artifact.id)], "summary": replacement.decode()},
                    )
                    self.assertEqual(poll(), "stop")
                    job.refresh_from_db()
                    attempt.refresh_from_db()
                    self.assertEqual(attempt.process_state, "stopping")
                    self.assertTrue(attempt.active)
                    self.assertNotIn("input", job_data(self.owner, job)["allowed_actions"])
                    with self.assertRaisesRegex(ValueError, "Stopping execution"):
                        actions.add_input(
                            self.owner,
                            job.id,
                            expected_version=job.version,
                            client_key=uuid4(),
                            text="After stop boundary",
                        )
                    with self.assertRaisesRegex(ValueError, "Empty containment"):
                        results.verify_result(job, attempt, [artifact])
                    event(
                        "attempt.stopped",
                        {
                            "process_state": "stopped",
                            "stop_confirmed": True,
                            "summary": "",
                            "adapter_session_ref": None,
                        },
                    )
                    receipt = results.publish_result(job.id)
                    job.refresh_from_db()
                    self.assertEqual(job.status, "completed")
                    self.assertEqual(receipt, results.publish_result(job.id))
                    expected_content = (
                        f"{silent_mention_syntax_for_user(job.requester)} "
                        f"{replacement.decode()} {results.job_task_link(job)}"
                    )
                    self.assertEqual(
                        Message.objects.get(id=receipt["message_id"]).content, expected_content
                    )

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

    def test_resume_checkpoint_preserves_nonzero_cursor_without_new_input(self) -> None:
        from copy import deepcopy

        from django.utils.timezone import now

        from zerver.actions import agent_jobs as actions
        from zerver.actions.agents import register_repository
        from zerver.lib import agent_protocol as p

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
        profile.refresh_from_db()
        enable_profile(self.owner, profile, expected_revision=profile.revision)
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

        def event(kind: str, payload: dict[str, object]) -> None:
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

        prepared = {
            "repository_id": str(repository.id),
            "workspace_reference": "fixture",
            "base_ref": "main",
            "base_commit": "a" * 40,
            "tree_hash": "b" * 40,
            "user_worktree_dirty": False,
        }
        event("workspace.prepared", prepared)
        event("attempt.started", {"process_state": "active"})
        job.refresh_from_db()
        item = actions.add_input(
            self.owner,
            job.id,
            expected_version=job.version,
            client_key=uuid4(),
            text="Accepted steering",
        )
        actions.deliver_inputs(self.runner, job.id, attempt.id, attempt.lease_epoch)
        event(
            "input.applied",
            {"input_id": str(item.id), "input_sequence": 1, "delivery_state": "applied"},
        )
        checkpoint = p.Checkpoint.model_validate(
            {
                "id": str(uuid4()),
                "source_attempt_id": str(attempt.id),
                "base_commit": "a" * 40,
                "tree_hash": "b" * 40,
                "summary": "Verified checkpoint",
                "context_ref_ids": [],
                "artifact_ids": [],
                "remaining_work": [],
                "next_step": "Continue",
                "adapter_session_ref": None,
                "input_cursor": 1,
            }
        )
        saved = actions.save_checkpoint(
            self.runner, job.id, attempt.id, attempt.lease_epoch, checkpoint
        )
        event("attempt.stopped", {"process_state": "stopped", "stop_confirmed": True})
        job.refresh_from_db()
        actions.resume_job(self.owner, job.id, job.version, saved.id)
        descriptor = actions.claim_work(self.runner, claim_key=uuid4())
        attempt = agents.AgentAttempt.objects.get(job=job, number=2)
        self.assertEqual(descriptor["checkpoint"]["input_cursor"], 1)
        self.assertEqual(attempt.input_cursor, 1)
        event("workspace.prepared", prepared)
        event("attempt.started", {"process_state": "active"})
        self.assertEqual(
            actions.deliver_inputs(self.runner, job.id, attempt.id, attempt.lease_epoch), []
        )
        next_checkpoint = checkpoint.model_copy(
            update={"id": uuid4(), "source_attempt_id": attempt.id}
        )
        accepted = actions.save_checkpoint(
            self.runner, job.id, attempt.id, attempt.lease_epoch, next_checkpoint
        )
        self.assertEqual(accepted.input_cursor, 1)
        with self.assertRaisesRegex(ValueError, "Checkpoint attempt or cursor mismatch"):
            actions.save_checkpoint(
                self.runner,
                job.id,
                attempt.id,
                attempt.lease_epoch,
                next_checkpoint.model_copy(update={"id": uuid4(), "input_cursor": 0}),
            )

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
        profile.refresh_from_db()
        enable_profile(self.owner, profile, expected_revision=profile.revision)
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
            # verify_result itself (not the job-transitioning publish_result wrapper)
            # exercises these rejections: r25 makes a required-check failure end the
            # job (contract 9.5), so a fixed-and-retried publish_result would no
            # longer see job.status == "verifying" on the later, successful call.
            job.refresh_from_db()
            attempt.refresh_from_db()
            with self.assertRaises(ValueError):
                results.verify_result(job, attempt, artifacts)
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
                results.verify_result(job, attempt, artifacts)
            older.delete()
            verification.exit_code = 0
            verification.tree_hash = "c" * 40
            verification.save()
            with self.assertRaises(ValueError):
                results.verify_result(job, attempt, artifacts)
            verification.tree_hash = "b" * 40
            verification.save()
            receipt = results.publish_result(job.id)
            self.assertIsNotNone(receipt["message_id"])

    def test_failed_required_check_fails_the_job_and_parks_the_outbox(self) -> None:
        import hashlib
        import tempfile
        from copy import deepcopy

        from django.test import override_settings
        from django.utils.timezone import now

        from zerver.actions import agent_jobs as actions
        from zerver.actions.agents import register_repository
        from zerver.lib import agent_protocol as p
        from zerver.lib.agent_reconcile import reconcile_agents
        from zerver.lib.agent_results import publish_result, store_artifact

        repository = register_repository(
            self.owner,
            self.runner,
            workspace_alias="failedcheck",
            canonical_origin="https://example.com/team/repo",
            allowed_refs=["main"],
            required_checks=[{"id": "required", "argv": ["true"]}],
        )
        policy = deepcopy(self.profile.policy)
        policy["actions"] = ["context.read", "repository.read", "repository.edit", "checks.run"]
        profile = create_profile(
            self.owner,
            name="FailedCheck",
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
        profile.refresh_from_db()
        enable_profile(self.owner, profile, expected_revision=profile.revision)
        message = Message.objects.get(
            id=self.send_group_direct_message(
                self.owner, [profile.bot_user, self.example_user("iago")], "Code task"
            )
        )
        job = actions.create_job(
            self.owner,
            profile=profile,
            source=message,
            request="Run a script",
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
                        "event_id": str(uuid4()),
                        "sequence": sequence,
                        "type": kind,
                        "occurred_at": now().isoformat(),
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
            diff = store_artifact(
                self.runner,
                job.id,
                attempt.id,
                1,
                chunks=[b"diff"],
                checksum=hashlib.sha256(b"diff").hexdigest(),
                kind="diff",
                filename="diff.patch",
                media_type="text/x-diff",
            )
            summary = store_artifact(
                self.runner,
                job.id,
                attempt.id,
                1,
                chunks=[b"Done"],
                checksum=hashlib.sha256(b"Done").hexdigest(),
                kind="summary",
                filename="summary.txt",
                media_type="text/plain",
            )
            event(
                "result.prepared",
                {
                    "artifact_ids": [str(diff.id), str(summary.id)],
                    "tree_hash": "b" * 40,
                    "summary": "Done",
                },
            )
            event("attempt.stopped", {"process_state": "stopped", "stop_confirmed": True})
            # No AgentVerification row exists for the required check, so
            # publication fails the job outright instead of leaving it stuck
            # in "verifying" forever.
            with self.assertRaisesRegex(ValueError, "Required checks"):
                publish_result(job.id)
            job.refresh_from_db()
            self.assertEqual(job.status, "failed")
            self.assertEqual(job.blocked_reason, "verification_failed")
            outbox = agents.AgentOutbox.objects.get(delivery_key=f"result:{job.id}")
            self.assertEqual(outbox.status, "blocked")
            reconcile_agents()
            outbox.refresh_from_db()
            self.assertEqual(outbox.status, "blocked")

    def test_resume_after_required_checks_failed_resets_the_outbox_row(self) -> None:
        """A resumed attempt's freshly staged result must not inherit an
        earlier attempt's parked ("blocked") outbox row: reconcile_agents
        never retries a "blocked" row, so a stale one is stuck forever."""
        import hashlib
        import tempfile
        from copy import deepcopy

        from django.test import override_settings
        from django.utils.timezone import now

        from zerver.actions import agent_jobs as actions
        from zerver.actions.agents import register_repository
        from zerver.lib import agent_protocol as p
        from zerver.lib.agent_results import publish_result, store_artifact

        repository = register_repository(
            self.owner,
            self.runner,
            workspace_alias="resumecheck",
            canonical_origin="https://example.com/team/resume-repo",
            allowed_refs=["main"],
            required_checks=[{"id": "required", "argv": ["true"]}],
        )
        policy = deepcopy(self.profile.policy)
        policy["actions"] = ["context.read", "repository.read", "repository.edit", "checks.run"]
        profile = create_profile(
            self.owner,
            name="ResumeCheck",
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
        profile.refresh_from_db()
        enable_profile(self.owner, profile, expected_revision=profile.revision)
        message = Message.objects.get(
            id=self.send_group_direct_message(
                self.owner, [profile.bot_user, self.example_user("iago")], "Code task"
            )
        )
        job = actions.create_job(
            self.owner,
            profile=profile,
            source=message,
            request="Run a script",
            idempotency_key=uuid4(),
            job_kind="code",
            delivery_target="patch",
            repository=repository,
            base_ref="main",
        )
        sequence = 0

        def event(attempt: agents.AgentAttempt, kind: str, payload: dict[str, object]) -> None:
            nonlocal sequence
            sequence += 1
            actions.record_event(
                self.runner,
                p.RunnerEvent.model_validate(
                    {
                        "schema_version": 1,
                        "job_id": str(job.id),
                        "attempt_id": str(attempt.id),
                        "lease_epoch": attempt.lease_epoch,
                        "event_id": str(uuid4()),
                        "sequence": sequence,
                        "type": kind,
                        "occurred_at": now().isoformat(),
                        "payload": payload,
                    }
                ),
            )

        def run_attempt(attempt: agents.AgentAttempt, tree_hash: str) -> None:
            nonlocal sequence
            sequence = 0  # Each attempt's event_cursor starts at 0 again.
            event(
                attempt,
                "workspace.prepared",
                {
                    "repository_id": str(repository.id),
                    "workspace_reference": "fixture",
                    "base_ref": "main",
                    "base_commit": "a" * 40,
                    "tree_hash": tree_hash,
                    "user_worktree_dirty": False,
                },
            )
            event(attempt, "attempt.started", {"process_state": "active"})
            diff = store_artifact(
                self.runner,
                job.id,
                attempt.id,
                attempt.lease_epoch,
                chunks=[b"diff"],
                checksum=hashlib.sha256(b"diff").hexdigest(),
                kind="diff",
                filename="diff.patch",
                media_type="text/x-diff",
            )
            summary = store_artifact(
                self.runner,
                job.id,
                attempt.id,
                attempt.lease_epoch,
                chunks=[b"Done"],
                checksum=hashlib.sha256(b"Done").hexdigest(),
                kind="summary",
                filename="summary.txt",
                media_type="text/plain",
            )
            event(
                attempt,
                "result.prepared",
                {
                    "artifact_ids": [str(diff.id), str(summary.id)],
                    "tree_hash": tree_hash,
                    "summary": "Done",
                },
            )
            event(attempt, "attempt.stopped", {"process_state": "stopped", "stop_confirmed": True})

        with (
            tempfile.TemporaryDirectory() as directory,
            override_settings(AGENT_ARTIFACT_ROOT=directory),
        ):
            actions.claim_work(self.runner, claim_key=uuid4())
            first_attempt = agents.AgentAttempt.objects.get(job=job, number=1)
            run_attempt(first_attempt, "b" * 40)
            # No AgentVerification row exists for the required check: this
            # parks the outbox row, as in test_failed_required_check_... above.
            with self.assertRaisesRegex(ValueError, "Required checks"):
                publish_result(job.id)
            job.refresh_from_db()
            self.assertEqual(job.status, "failed")
            outbox = agents.AgentOutbox.objects.get(delivery_key=f"result:{job.id}")
            self.assertEqual(outbox.status, "blocked")
            self.assertEqual(outbox.payload_ref, first_attempt.id)

            actions.resume_job(self.owner, job.id, job.version)
            actions.claim_work(self.runner, claim_key=uuid4())
            second_attempt = agents.AgentAttempt.objects.get(job=job, number=2)
            run_attempt(second_attempt, "c" * 40)

        # The second attempt just staged a brand-new result: the row must be
        # ready for a fresh delivery, not stuck on the first attempt's parked
        # row that reconcile_agents will never retry.
        outbox.refresh_from_db()
        self.assertEqual(outbox.status, "pending")
        self.assertEqual(outbox.payload_ref, second_attempt.id)
        self.assertEqual(outbox.attempt_count, 0)

    def test_audience_change_holds_the_result_and_parks_the_outbox(self) -> None:
        import hashlib
        import tempfile

        from django.test import override_settings
        from django.utils.timezone import now

        from zerver.actions import agent_jobs as actions
        from zerver.lib import agent_protocol as p
        from zerver.lib.agent_reconcile import reconcile_agents
        from zerver.lib.agent_results import publish_result, store_artifact

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
                        "event_id": str(uuid4()),
                        "sequence": sequence,
                        "type": kind,
                        "occurred_at": now().isoformat(),
                        "payload": payload,
                    }
                ),
            )

        event("attempt.started", {"process_state": "active"})
        with (
            tempfile.TemporaryDirectory() as directory,
            override_settings(AGENT_ARTIFACT_ROOT=directory),
        ):
            content = b"A verified answer."
            artifact = store_artifact(
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
            event(
                "result.prepared",
                {"artifact_ids": [str(artifact.id)], "summary": "A verified answer."},
            )
            event("attempt.stopped", {"process_state": "stopped", "stop_confirmed": True})
            job.refresh_from_db()
            self.assertEqual(job.status, "verifying")
            attempt.refresh_from_db()
            # Simulate a replan that moved the conversation's accepted audience
            # on without this attempt: its own frozen copy is now stale.
            tampered = {**attempt.audience_binding, "epoch": attempt.audience_binding["epoch"] + 1}
            agents.AgentAttempt.objects.filter(id=attempt.id).update(audience_binding=tampered)
            with self.assertRaisesRegex(ValueError, "Result audience changed"):
                publish_result(job.id)
            job.refresh_from_db()
            self.assertEqual(job.status, "verifying")
            self.assertEqual(job.blocked_reason, "audience_changed")
            outbox = agents.AgentOutbox.objects.get(delivery_key=f"result:{job.id}")
            self.assertEqual(outbox.status, "blocked")
            reconcile_agents()
            outbox.refresh_from_db()
            self.assertEqual(outbox.status, "blocked")

    def test_deliver_privately_posts_to_the_requester_once(self) -> None:
        import hashlib
        import tempfile

        from django.test import override_settings
        from django.utils.timezone import now

        from zerver.actions import agent_jobs as actions
        from zerver.lib import agent_protocol as p
        from zerver.lib.agent_results import (
            deliver_result_privately,
            publish_result,
            store_artifact,
        )

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
                        "event_id": str(uuid4()),
                        "sequence": sequence,
                        "type": kind,
                        "occurred_at": now().isoformat(),
                        "payload": payload,
                    }
                ),
            )

        event("attempt.started", {"process_state": "active"})
        with (
            tempfile.TemporaryDirectory() as directory,
            override_settings(AGENT_ARTIFACT_ROOT=directory),
        ):
            content = b"A verified answer."
            artifact = store_artifact(
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
            event(
                "result.prepared",
                {"artifact_ids": [str(artifact.id)], "summary": "A verified answer."},
            )
            event("attempt.stopped", {"process_state": "stopped", "stop_confirmed": True})
            # Simulate a replan that moved the conversation's accepted audience
            # on without this attempt: its own frozen copy (and its artifacts,
            # scoped to that same copy) are now stale, but only relative to
            # the conversation, so a check_audience=False call still accepts
            # them (contract 9.6 is the only caller that passes it).
            conversation = job.conversation
            tampered = {
                **conversation.audience_binding,
                "epoch": conversation.audience_binding["epoch"] + 1,
            }
            agents.AgentConversation.objects.filter(id=conversation.id).update(
                audience_binding=tampered
            )
            with self.assertRaises(ValueError):
                publish_result(job.id)
            job.refresh_from_db()
            self.assertEqual(job.blocked_reason, "audience_changed")

            delivered = deliver_result_privately(self.owner, job.id, job.version)
            self.assertEqual(delivered.status, "completed")
            assert delivered.result_message_id is not None
            message = Message.objects.get(id=delivered.result_message_id)
            self.assertTrue(
                message.content.startswith(
                    "This result was sent here because the conversation changed."
                )
            )
            self.assertIn("A verified answer.", message.content)
            self.assertEqual(delivered.result_receipt["destination"], "direct")
            outbox = agents.AgentOutbox.objects.get(delivery_key=f"result:{job.id}")
            self.assertEqual(outbox.status, "delivered")

            # A second call must not send a second message.
            again = deliver_result_privately(self.owner, job.id, delivered.version)
            self.assertEqual(again.result_message_id, delivered.result_message_id)
            self.assertEqual(Message.objects.filter(id=delivered.result_message_id).count(), 1)

    def test_deliver_privately_rejects_other_users_and_lost_source_access(self) -> None:
        import hashlib
        import tempfile

        from django.test import override_settings
        from django.utils.timezone import now

        from zerver.actions import agent_jobs as actions
        from zerver.actions.streams import bulk_remove_subscriptions
        from zerver.lib import agent_protocol as p
        from zerver.lib.agent_results import (
            deliver_result_privately,
            publish_result,
            store_artifact,
        )
        from zerver.lib.exceptions import JsonableError

        channel = self.make_stream(
            "private-delivery", invite_only=True, history_public_to_subscribers=False
        )
        self.subscribe(self.owner, channel.name)
        self.subscribe(self.profile.bot_user, channel.name)
        # No mention: this message only isolates manual create_job below from
        # the real admission hook, the same reason setUp's message has none.
        message = Message.objects.get(
            id=self.send_stream_message(self.owner, channel.name, "Please answer")
        )
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
                        "event_id": str(uuid4()),
                        "sequence": sequence,
                        "type": kind,
                        "occurred_at": now().isoformat(),
                        "payload": payload,
                    }
                ),
            )

        event("attempt.started", {"process_state": "active"})
        with (
            tempfile.TemporaryDirectory() as directory,
            override_settings(AGENT_ARTIFACT_ROOT=directory),
        ):
            content = b"A verified answer."
            artifact = store_artifact(
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
            event(
                "result.prepared",
                {"artifact_ids": [str(artifact.id)], "summary": "A verified answer."},
            )
            event("attempt.stopped", {"process_state": "stopped", "stop_confirmed": True})
            attempt.refresh_from_db()
            tampered = {**attempt.audience_binding, "epoch": attempt.audience_binding["epoch"] + 1}
            agents.AgentAttempt.objects.filter(id=attempt.id).update(audience_binding=tampered)
            with self.assertRaises(ValueError):
                publish_result(job.id)
            job.refresh_from_db()

            other = self.example_user("iago")
            with self.assertRaises(ValueError):
                deliver_result_privately(other, job.id, job.version)
            self.assertIsNone(agents.AgentJob.objects.get(id=job.id).result_receipt)

            bulk_remove_subscriptions(
                self.owner.realm, [self.owner], [channel], acting_user=self.owner
            )
            with self.assertRaises(JsonableError):
                deliver_result_privately(self.owner, job.id, job.version)
            self.assertIsNone(agents.AgentJob.objects.get(id=job.id).result_receipt)

    def test_deliver_privately_rechecks_the_requesters_profile_access(self) -> None:
        """deliver_result_privately must recheck access the same way
        _publish_result does: the requester can still read the source message
        yet have lost their own access to the profile in the meantime."""
        import hashlib
        import tempfile

        from django.test import override_settings
        from django.utils.timezone import now

        from zerver.actions import agent_jobs as actions
        from zerver.lib import agent_protocol as p
        from zerver.lib.agent_policy import AgentAccessDenied
        from zerver.lib.agent_results import (
            deliver_result_privately,
            publish_result,
            store_artifact,
        )

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
                        "event_id": str(uuid4()),
                        "sequence": sequence,
                        "type": kind,
                        "occurred_at": now().isoformat(),
                        "payload": payload,
                    }
                ),
            )

        event("attempt.started", {"process_state": "active"})
        with (
            tempfile.TemporaryDirectory() as directory,
            override_settings(AGENT_ARTIFACT_ROOT=directory),
        ):
            content = b"A verified answer."
            artifact = store_artifact(
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
            event(
                "result.prepared",
                {"artifact_ids": [str(artifact.id)], "summary": "A verified answer."},
            )
            event("attempt.stopped", {"process_state": "stopped", "stop_confirmed": True})
            conversation = job.conversation
            tampered = {
                **conversation.audience_binding,
                "epoch": conversation.audience_binding["epoch"] + 1,
            }
            agents.AgentConversation.objects.filter(id=conversation.id).update(
                audience_binding=tampered
            )
            with self.assertRaises(ValueError):
                publish_result(job.id)
            job.refresh_from_db()
            self.assertEqual(job.blocked_reason, "audience_changed")

            # The requester still owns the source message, but lost access to
            # the profile itself while the result sat blocked.
            agents.AgentProfile.objects.filter(id=self.profile.id).update(desired_state="archived")
            with self.assertRaises(AgentAccessDenied):
                deliver_result_privately(self.owner, job.id, job.version)
            self.assertIsNone(agents.AgentJob.objects.get(id=job.id).result_receipt)

    def test_code_result_ends_with_the_done_line(self) -> None:
        import hashlib
        import tempfile
        from copy import deepcopy

        from django.test import override_settings
        from django.utils.timezone import now

        from zerver.actions import agent_jobs as actions
        from zerver.actions.agent_approvals import consume_operation, propose_operation
        from zerver.actions.agents import register_repository
        from zerver.lib import agent_protocol as p
        from zerver.lib.agent_results import publish_result, store_artifact

        repository = register_repository(
            self.owner,
            self.runner,
            workspace_alias="done-line",
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
        ]
        profile = create_profile(
            self.owner,
            name="DoneLine",
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
        profile.refresh_from_db()
        enable_profile(self.owner, profile, expected_revision=profile.revision)
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
                        "event_id": str(uuid4()),
                        "sequence": sequence,
                        "type": kind,
                        "occurred_at": now().isoformat(),
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
                    store_artifact(
                        self.runner,
                        job.id,
                        attempt.id,
                        1,
                        chunks=[content],
                        checksum=hashlib.sha256(content).hexdigest(),
                        kind=kind,
                        filename=kind + ".txt",
                        media_type="text/plain" if kind != "diff" else "text/x-diff",
                    )
                )
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
                    "exit_code": 0,
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
                    "status": "succeeded",
                    "exit_code": 0,
                },
            )
            event(
                "result.prepared",
                {
                    "artifact_ids": [str(artifacts[0].id), str(artifacts[1].id)],
                    "tree_hash": "b" * 40,
                    "summary": "Fixed the bug.",
                },
            )
            event("attempt.stopped", {"process_state": "stopped", "stop_confirmed": True})
            receipt = publish_result(job.id)
        message = Message.objects.get(id=receipt["message_id"])
        self.assertIn("Fixed the bug.\n\nDone. The diff is ready for review.", message.content)

    def test_reconcile_never_retries_a_parked_outbox_row(self) -> None:
        from datetime import timedelta

        from django.utils.timezone import now

        from zerver.actions import agent_jobs as actions
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
        outbox = agents.AgentOutbox.objects.create(
            realm=self.owner.realm,
            job=job,
            delivery_key=f"result:{job.id}",
            event_type="result.publish",
            status="blocked",
            next_attempt_at=now() - timedelta(seconds=1),
        )
        reconcile_agents()
        outbox.refresh_from_db()
        self.assertEqual(outbox.status, "blocked")
        self.assertEqual(outbox.attempt_count, 0)

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
                self.assertEqual(json.loads(stdout)["completion_stops"], 1)
                from zerver.lib.agent_results import publish_result

                receipt = publish_result(job.id)
                job.refresh_from_db()
                self.assertEqual(job.status, "completed")
                self.assertEqual(receipt, publish_result(job.id))

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

    def test_descriptor_carries_team_and_profile_instructions(self) -> None:
        """Contract 7.3: build_descriptor assembles both instruction texts."""
        from zerver.actions import agent_jobs as actions
        from zerver.actions.agents import retry_profile_setup

        agents.AgentRealmSettings.objects.filter(realm=self.owner.realm).update(
            team_instructions="Reply in Bahasa Indonesia.", team_instructions_revision=3
        )
        profile = create_profile(
            self.owner,
            name="Instructed",
            runner=self.runner,
            adapter_id="acp",
            adapter_version="1",
            idempotency_key=uuid4(),
        )
        agents.AgentProfile.objects.filter(id=profile.id).update(
            instructions="Always confirm the ticket number first."
        )
        profile.refresh_from_db()
        retry_profile_setup(
            self.owner, profile, retry_key=uuid4(), expected_revision=profile.revision
        )
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
                "capabilities": {"chat_ready": True, "config_version": profile.revision},
            },
        )
        profile.refresh_from_db()
        enable_profile(self.owner, profile, expected_revision=profile.revision)
        message_id = self.send_group_direct_message(
            self.owner, [profile.bot_user, self.example_user("iago")], "Please answer"
        )
        actions.create_job(
            self.owner,
            profile=profile,
            source=Message.objects.get(id=message_id),
            request="Please answer",
            idempotency_key=uuid4(),
            job_kind="answer",
            delivery_target="answer",
        )
        descriptor = actions.claim_work(self.runner, claim_key=uuid4())
        assert descriptor is not None
        self.assertEqual(
            descriptor["instructions"],
            {
                "team": {"revision": 3, "text": "Reply in Bahasa Indonesia."},
                "profile": {
                    "revision": profile.revision,
                    "text": "Always confirm the ticket number first.",
                },
            },
        )

    def test_follow_up_job_records_its_origin_and_needs_a_terminal_origin(self) -> None:
        """Contract 9.10: follows_job_id needs a same-realm, terminal origin,
        and a terminal job's own detail offers the follow_up action."""
        from zerver.actions import agent_jobs as actions
        from zerver.views.agent_jobs import job_data

        origin = actions.create_job(
            self.owner,
            profile=self.profile,
            source=self.message,
            request="First task",
            idempotency_key=uuid4(),
            job_kind="answer",
            delivery_target="answer",
        )
        with self.assertRaisesRegex(ValueError, "has not ended"):
            actions.create_job(
                self.owner,
                profile=self.profile,
                source=self.message,
                request="Follow up too soon",
                idempotency_key=uuid4(),
                job_kind="answer",
                delivery_target="answer",
                follows_job=origin,
            )
        agents.AgentJob.objects.filter(id=origin.id).update(status="completed")
        origin.refresh_from_db()
        self.assertIn("follow_up", job_data(self.owner, origin)["allowed_actions"])
        second_message_id = self.send_group_direct_message(
            self.owner, [self.profile.bot_user, self.example_user("iago")], "Follow up"
        )
        follow_up = actions.create_job(
            self.owner,
            profile=self.profile,
            source=Message.objects.get(id=second_message_id),
            request="Follow up",
            idempotency_key=uuid4(),
            job_kind="answer",
            delivery_target="answer",
            follows_job=origin,
        )
        self.assertEqual(follow_up.follows_job_id, origin.id)
        self.assertEqual(job_data(self.owner, follow_up)["follows_job_id"], str(origin.id))

    def test_job_detail_lists_repository_base_budget_and_instructions(self) -> None:
        """Contract 9.11 and 7.5: job detail projects the repository, base
        branch, budget ceiling, and the attempt's own instruction revisions."""
        from copy import deepcopy

        from zerver.actions import agent_jobs as actions
        from zerver.actions.agents import register_repository, retry_profile_setup
        from zerver.views.agent_jobs import job_data

        repository = register_repository(
            self.owner,
            self.runner,
            workspace_alias="detail-repo",
            canonical_origin=None,
            allowed_refs=["main"],
            required_checks=[{"id": "test", "argv": ["true"]}],
        )
        policy = deepcopy(self.profile.policy)
        policy["actions"] = ["context.read", "repository.read", "repository.edit", "checks.run"]
        profile = create_profile(
            self.owner,
            name="Detail",
            runner=self.runner,
            adapter_id="acp",
            adapter_version="1",
            repository=repository,
            policy=policy,
            default_mode="code",
            idempotency_key=uuid4(),
        )
        agents.AgentProfile.objects.filter(id=profile.id).update(instructions="Write small diffs.")
        profile.refresh_from_db()
        retry_profile_setup(
            self.owner, profile, retry_key=uuid4(), expected_revision=profile.revision
        )
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
                    "code_ready": True,
                    "tool_calling": "passed",
                    "sandbox": "passed",
                    "config_version": profile.revision,
                },
            },
        )
        profile.refresh_from_db()
        enable_profile(self.owner, profile, expected_revision=profile.revision)
        message_id = self.send_group_direct_message(
            self.owner, [profile.bot_user, self.example_user("iago")], "Fix the bug"
        )
        job = actions.create_job(
            self.owner,
            profile=profile,
            source=Message.objects.get(id=message_id),
            request="Fix the bug",
            idempotency_key=uuid4(),
            job_kind="code",
            delivery_target="patch",
            repository=repository,
            base_ref="main",
        )
        actions.claim_work(self.runner, claim_key=uuid4())
        data = job_data(self.owner, job)
        self.assertEqual(data["repository"], {"id": str(repository.id), "alias": "detail-repo"})
        self.assertEqual(data["base_ref"], "main")
        self.assertEqual(
            data["budget"],
            {
                "active_seconds": job.budget["active_seconds"],
                "tool_rounds": job.budget["tool_rounds"],
                "input_tokens": job.budget["input_tokens"],
                "output_tokens": job.budget["output_tokens"],
            },
        )
        self.assertEqual(
            data["instructions"], {"team_revision": None, "profile_revision": profile.revision}
        )

    def test_test_task_sends_one_bot_message_and_one_answer_job(self) -> None:
        """Contract 9.12: one bot message, one answer job, idempotent replay."""
        from zerver.actions import agent_jobs as actions

        key = uuid4()
        job = actions.send_test_task(self.owner, self.profile, key)
        self.assertEqual(job.job_kind, "answer")
        self.assertEqual(job.delivery_target, "answer")
        self.assertEqual(job.requester_id, self.owner.id)
        message = Message.objects.get(sender=self.profile.bot_user)
        self.assertIn("Test task:", message.content)
        self.assertEqual(agents.AgentJob.objects.filter(profile=self.profile).count(), 1)
        # A replay with the same key returns the same job and sends no second message.
        again = actions.send_test_task(self.owner, self.profile, key)
        self.assertEqual(again.id, job.id)
        self.assertEqual(Message.objects.filter(sender=self.profile.bot_user).count(), 1)
