"""Spec 13: one card message per job, its reactions, and its reason codes."""

import hashlib
import json
import tempfile
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from datetime import timedelta
from typing import Any
from unittest import mock
from uuid import uuid4

import time_machine
from django.conf import settings
from django.test import override_settings
from django.utils.timezone import now
from typing_extensions import override

from zerver.actions import agent_jobs as actions
from zerver.actions.agents import create_profile, enable_profile, record_readiness
from zerver.actions.tasks import do_create_task
from zerver.lib import agent_protocol as p
from zerver.lib.agent_context import AgentBusy, AudienceChanged
from zerver.lib.agent_job_requests import Draft
from zerver.lib.agent_reconcile import reconcile_agents
from zerver.lib.agent_results import (
    DRAFT_WRITING_MARK,
    _fit_message_limit,
    publish_draft,
    publish_result,
    store_artifact,
    update_job_card,
)
from zerver.lib.tasks import get_or_create_default_board
from zerver.lib.test_classes import ZulipTestCase
from zerver.lib.test_helpers import queries_captured
from zerver.lib.validator import check_widget_content
from zerver.models import Message, PushDeviceToken, Reaction, UserMessage, UserProfile, agents
from zerver.models.tasks import Task

NEUTRAL_LINE = "This task's answer is no longer shown here."


class AgentJobCardTests(ZulipTestCase):
    @override
    def setUp(self) -> None:
        super().setUp()
        self.owner = self.example_user("hamlet")
        agents.AgentRealmSettings.objects.update_or_create(
            realm=self.owner.realm, defaults={"enabled": True}
        )
        self.runner = self._runner(self.owner, "Card", "c")
        self.profile = self._profile(self.owner, self.runner, "Hana")
        self.sequence = 0

    def _runner(self, owner: UserProfile, name: str, fingerprint: str) -> agents.AgentRunner:
        return agents.AgentRunner.objects.create(
            realm=owner.realm,
            owner=owner,
            name=name,
            fingerprint=fingerprint * 64,
            status="online",
            last_heartbeat_at=now(),
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

    def _profile(
        self, owner: UserProfile, runner: agents.AgentRunner, name: str
    ) -> agents.AgentProfile:
        profile = create_profile(
            owner,
            name=name,
            runner=runner,
            adapter_id="acp",
            adapter_version="1",
            idempotency_key=uuid4(),
        )
        setup = agents.AgentSetupOperation.objects.get(profile=profile)
        record_readiness(
            runner,
            setup,
            {
                "schema_version": 1,
                "profile_id": str(profile.id),
                "profile_revision": 1,
                "runner_id": str(runner.id),
                "descriptor_digest": setup.descriptor_digest,
                "configuration_digest": setup.configuration_digest,
                "state": "ready",
                "capabilities": {"chat_ready": True, "config_version": 1},
            },
        )
        return enable_profile(owner, profile, expected_revision=1)

    def _mention(self) -> str:
        return f"@**{self.profile.bot_user.full_name}|{self.profile.bot_user_id}**"

    def _ask(self, sender: UserProfile | None = None) -> int:
        """A direct message to the agent. The send helper runs every
        on_commit hook itself, so the card exists when it returns."""
        return self.send_personal_message(
            sender or self.owner, self.profile.bot_user, f"Please answer {self._mention()}"
        )

    def _job(self) -> agents.AgentJob:
        return agents.AgentJob.objects.get(profile=self.profile)

    def _card(self, job: agents.AgentJob) -> Message:
        job.refresh_from_db()
        assert job.result_message_id is not None
        return Message.objects.get(id=job.result_message_id)

    def _widget(self, job: agents.AgentJob) -> dict[str, Any]:
        """The card's latest snapshot: the first submessage declares the
        widget, and every later one is a full extra_data replacement."""
        submessages = list(self._card(job).submessage_set.order_by("id"))
        data = json.loads(submessages[-1].content)
        return data["extra_data"] if len(submessages) == 1 else data

    def _claim(self, job: agents.AgentJob) -> agents.AgentAttempt:
        with self.captureOnCommitCallbacks(execute=True):
            actions.claim_work(self.runner, claim_key=uuid4())
        attempt = agents.AgentAttempt.objects.get(job=job)
        self._event(job, attempt, "attempt.started", {"process_state": "active"})
        return attempt

    def _event(
        self, job: agents.AgentJob, attempt: agents.AgentAttempt, kind: str, payload: object
    ) -> None:
        self.sequence += 1
        with self.captureOnCommitCallbacks(execute=True):
            actions.record_event(
                self.runner,
                p.RunnerEvent.model_validate(
                    {
                        "schema_version": 1,
                        "job_id": str(job.id),
                        "attempt_id": str(attempt.id),
                        "lease_epoch": 1,
                        "event_id": str(uuid4()),
                        "sequence": self.sequence,
                        "type": kind,
                        "occurred_at": now().isoformat(),
                        "payload": payload,
                    }
                ),
            )

    def _draft(self, job: agents.AgentJob, attempt: agents.AgentAttempt, text: str) -> None:
        job.refresh_from_db()
        with self.captureOnCommitCallbacks(execute=True):
            publish_draft(
                self.runner,
                Draft.model_validate(
                    {
                        "schema_version": 1,
                        "job_id": str(job.id),
                        "attempt_id": str(attempt.id),
                        "lease_epoch": 1,
                        "job_version": job.version,
                        "text": text,
                    }
                ),
            )

    def _answer(self, job: agents.AgentJob, attempt: agents.AgentAttempt, text: str) -> None:
        """The runner's real path: the full answer as the summary artifact,
        and at most 4096 characters of it as ResultPayload.summary."""
        data = text.encode()
        artifact = store_artifact(
            self.runner,
            job.id,
            attempt.id,
            1,
            chunks=[data],
            checksum=hashlib.sha256(data).hexdigest(),
            kind="summary",
            filename="answer.txt",
            media_type="text/plain",
        )
        self._event(
            job,
            attempt,
            "result.prepared",
            {"artifact_ids": [str(artifact.id)], "summary": text[:4096]},
        )
        self._event(
            job, attempt, "attempt.stopped", {"process_state": "stopped", "stop_confirmed": True}
        )

    @contextmanager
    def _events(self) -> Iterator[list[Mapping[str, Any]]]:
        """Events sent inside the block. The helpers above run their own
        on_commit hooks; nesting another capture would run them twice."""
        notices: list[Mapping[str, Any]] = []
        with mock.patch("zerver.tornado.event_queue.process_notification", notices.append):
            yield notices

    # ---- one card message per job (13-R1..R3, SD-03, SD-05) ----

    def test_mention_reacts_eyes_and_posts_one_queued_card(self) -> None:
        with self._events() as notices:
            message_id = self._ask()
        reaction = Reaction.objects.get(message_id=message_id)
        self.assertEqual(reaction.emoji_name, "eyes")
        self.assertEqual(reaction.user_profile_id, self.profile.bot_user_id)
        job = self._job()
        card = self._card(job)
        self.assertEqual(card.sender_id, self.profile.bot_user_id)
        self.assertEqual(card.content, "Hana · Queued")
        self.assertEqual(Message.objects.filter(sender=self.profile.bot_user).count(), 1)
        widget = self._widget(job)
        self.assertEqual((widget["status"], widget["progress"]), ("queued", 0.05))
        self.assertEqual(widget["step_label"], "job.queued")
        # SD-01: the bot starts typing in the direct message.
        typing = [notice["event"] for notice in notices if notice["event"]["type"] == "typing"]
        self.assertEqual([event["op"] for event in typing], ["start"])

    def test_channel_mention_posts_the_card_in_the_same_topic(self) -> None:
        self.subscribe(self.profile.bot_user, "Denmark")
        message_id = self.send_stream_message(
            self.owner, "Denmark", f"{self._mention()} what changed?", topic_name="release"
        )
        self.assertEqual(Reaction.objects.get(message_id=message_id).emoji_name, "eyes")
        card = self._card(self._job())
        self.assertEqual(card.recipient_id, Message.objects.get(id=message_id).recipient_id)
        self.assertEqual(card.topic_name(), "release")

    def test_drafts_and_the_answer_edit_one_message_without_edit_history(self) -> None:
        self._ask()
        job = self._job()
        attempt = self._claim(job)
        self.assertEqual(self._widget(job)["status"], "running")
        self.assertEqual(self._card(job).content, "Hana · Working")
        with (
            self._events() as notices,
            tempfile.TemporaryDirectory() as directory,
            override_settings(AGENT_ARTIFACT_ROOT=directory),
        ):
            self._draft(job, attempt, "A first")
            with time_machine.travel(now() + timedelta(seconds=2), tick=False):
                self._answer(job, attempt, "A final answer.")
        card = self._card(job)
        self.assertIn("A final answer.", card.content)
        self.assertNotIn(DRAFT_WRITING_MARK, card.content)
        self.assertNotIn("#agent-jobs/", card.content)
        # SD-05/FL-19: the server's own edits leave no trace of a user edit.
        self.assertIsNone(card.last_edit_time)
        self.assertIsNone(card.edit_history)
        self.assertEqual(Message.objects.filter(sender=self.profile.bot_user).count(), 1)
        self.assertEqual(self._widget(job)["status"], "completed")
        updates = [n["event"] for n in notices if n["event"]["type"] == "update_message"]
        self.assertTrue(updates)
        for event in updates:
            self.assertTrue(event["rendering_only"])
            self.assertIn("edit_timestamp", event)
        # SD-02: the first draft stops the typing indicator.
        typing = [n["event"]["op"] for n in notices if n["event"]["type"] == "typing"]
        self.assertEqual(typing[0], "stop")

    def test_the_answer_notifies_its_requester_once(self) -> None:
        """SD-06: drafts notify nobody; the finished answer counts as a
        mention of its requester and queues one push/email notification."""
        self._ask()
        job = self._job()
        attempt = self._claim(job)
        PushDeviceToken.objects.create(user=self.owner, token="card", kind=PushDeviceToken.FCM)
        notification_queues = {"missedmessage_emails", "missedmessage_mobile_notifications"}
        with (
            mock.patch("zerver.lib.queue.queue_json_publish_rollback_unsafe") as publish,
            mock.patch(
                "zerver.actions.message_send.filter_presence_idle_user_ids",
                return_value=[self.owner.id],
            ),
            tempfile.TemporaryDirectory() as directory,
            override_settings(AGENT_ARTIFACT_ROOT=directory),
        ):
            self._draft(job, attempt, "Working on it")
            self.assertFalse(
                [c for c in publish.call_args_list if c.args[0] in notification_queues]
            )
            self._answer(job, attempt, "Done.")
        card = self._card(job)
        user_message = UserMessage.objects.get(message=card, user_profile=self.owner)
        self.assertTrue(user_message.flags.mentioned)
        notices = sorted(c.args for c in publish.call_args_list if c.args[0] in notification_queues)
        self.assertEqual(
            [queue for queue, _event in notices],
            ["missedmessage_emails", "missedmessage_mobile_notifications"],
        )
        for _queue, event in notices:
            self.assertEqual((event["message_id"], event["trigger"]), (card.id, "direct_message"))

    def test_drafts_edit_the_card_at_most_once_per_second(self) -> None:
        """SD-04: a draft that comes sooner is dropped; the next one after a
        second (or the final answer) shows the newer text."""
        self._ask()
        job = self._job()
        attempt = self._claim(job)
        with time_machine.travel(now(), tick=False):
            self._draft(job, attempt, "First")
            self._draft(job, attempt, "Second")
        self.assertIn("First", self._card(job).content)
        with time_machine.travel(now() + timedelta(seconds=2), tick=False):
            self._draft(job, attempt, "Third")
        self.assertIn("Third", self._card(job).content)

    def test_a_late_draft_never_replaces_the_answer(self) -> None:
        self._ask()
        job = self._job()
        attempt = self._claim(job)
        with (
            tempfile.TemporaryDirectory() as directory,
            override_settings(AGENT_ARTIFACT_ROOT=directory),
        ):
            self._answer(job, attempt, "The answer.")
        # The answer needs a stopped attempt, so a later draft has no lease.
        with (
            time_machine.travel(now() + timedelta(seconds=2), tick=False),
            self.assertRaisesRegex(ValueError, "Execution lease is unavailable."),
        ):
            self._draft(job, attempt, "An old draft")
        self.assertIn("The answer.", self._card(job).content)

    def test_card_edit_reaches_a_subscriber_without_a_usermessage_row(self) -> None:
        """A new subscriber also changes the job's audience, so the draft
        path writes the neutral line (FL-22); that edit reaches the new
        subscriber too, who has no UserMessage row for the card."""
        self.subscribe(self.profile.bot_user, "Denmark")
        self.send_stream_message(self.owner, "Denmark", f"{self._mention()} hi")
        job = self._job()
        attempt = self._claim(job)
        late = self.example_user("polonius")
        self.subscribe(late, "Denmark")
        card = self._card(job)
        self.assertFalse(UserMessage.objects.filter(message=card, user_profile=late).exists())
        with self._events() as notices, self.assertRaises(AudienceChanged):
            self._draft(job, attempt, "Hello")
        self.assertEqual(self._card(job).content, NEUTRAL_LINE)
        [notice] = [n for n in notices if n["event"]["type"] == "update_message"]
        users = {user["id"]: user["flags"] for user in notice["users"]}
        self.assertEqual(users[late.id], ["read"])

    def test_card_refresh_and_typing_tolerate_a_job_without_a_card(self) -> None:
        from zerver.lib.agent_results import send_card_typing

        # No on_commit hook runs here, so admission never posts the card.
        self.send_personal_message(
            self.owner,
            self.profile.bot_user,
            "Please answer",
            skip_capture_on_commit_callbacks=True,
        )
        job = self._job()
        with self._events() as notices:
            update_job_card(job.id)
            send_card_typing(job.id, "start")
        self.assertEqual(notices, [])
        job.refresh_from_db()
        self.assertIsNone(job.result_message_id)

    def test_a_claim_before_the_card_is_posted_shows_on_the_card(self) -> None:
        """The card's first snapshot reads the job after locking it, so a
        claim that commits first never leaves the card at "queued"."""
        with self.captureOnCommitCallbacks(execute=False) as callbacks:
            self.send_personal_message(
                self.owner,
                self.profile.bot_user,
                "Please answer",
                skip_capture_on_commit_callbacks=True,
            )
        job = self._job()
        actions.claim_work(self.runner, claim_key=uuid4())
        for callback in callbacks:
            callback()
        self.assertEqual(self._widget(job)["status"], "running")

    def test_a_retried_admission_hook_posts_no_second_card(self) -> None:
        from zerver.lib.agent_results import post_admission_card

        message_id = self._ask()
        job = self._job()
        with self.captureOnCommitCallbacks(execute=True):
            post_admission_card(job.id, acknowledge=True)
        self.assertEqual(Message.objects.filter(sender=self.profile.bot_user).count(), 1)
        self.assertEqual(Reaction.objects.filter(message_id=message_id).count(), 1)

    def test_a_draft_for_a_job_that_is_not_an_answer_is_ignored(self) -> None:
        self._ask()
        job = self._job()
        attempt = self._claim(job)
        agents.AgentJob.objects.filter(id=job.id).update(job_kind="manage")
        self._draft(job, attempt, "Not shown")
        self.assertEqual(self._card(job).content, "Hana · Working")

    def test_blocked_admission_gets_a_stopped_card_and_eyes(self) -> None:
        agents.AgentProfile.objects.filter(id=self.profile.id).update(
            readiness_state="needs_action"
        )
        message_id = self._ask()
        self.assertEqual(Reaction.objects.get(message_id=message_id).emoji_name, "eyes")
        job = self._job()
        self.assertEqual(job.status, "blocked")
        self.assertEqual(self._card(job).content, "Hana · Stopped")
        widget = self._widget(job)
        self.assertEqual(
            (widget["status"], widget["reason_code"]), ("blocked", "profile_needs_action")
        )
        self.assertFalse(widget["can_retry"])

    # ---- typing (SD-01, SD-02) ----

    def test_heartbeat_repeats_typing_every_ten_seconds_until_a_draft(self) -> None:
        self._ask()
        job = self._job()
        attempt = self._claim(job)
        identity = p.LeaseIdentity(job_id=job.id, attempt_id=attempt.id, lease_epoch=1)

        def typing_after_heartbeat() -> list[str]:
            with self._events() as notices, self.captureOnCommitCallbacks(execute=True):
                actions.heartbeat(self.runner, [identity])
            return [n["event"]["op"] for n in notices if n["event"]["type"] == "typing"]

        with time_machine.travel(now() + timedelta(seconds=11), tick=False):
            self.assertEqual(typing_after_heartbeat(), ["start"])
            self.assertEqual(typing_after_heartbeat(), [])
        self._draft(job, attempt, "A draft")
        with time_machine.travel(now() + timedelta(seconds=22), tick=False):
            self.assertEqual(typing_after_heartbeat(), [])

    # ---- reason codes and retry (13-R8, SD-13) ----

    def test_card_reason_code_names_the_cause_and_retry_follows_resume(self) -> None:
        self._ask()
        job = self._job()
        attempt = self._claim(job)
        agents.AgentAttempt.objects.filter(id=attempt.id).update(
            lease_expires_at=now() - timedelta(seconds=1)
        )
        with self.captureOnCommitCallbacks(execute=True):
            reconcile_agents()
        job.refresh_from_db()
        self.assertEqual((job.status, job.blocked_reason), ("interrupted", "lease_lost"))
        widget = self._widget(job)
        self.assertEqual(widget["reason_code"], "runner_offline")
        # The attempt is still marked active until its stop is confirmed.
        self.assertFalse(widget["can_retry"])
        agents.AgentAttempt.objects.filter(id=attempt.id).update(
            active=False, process_state="stopped", stopped_at=now(), ended_at=now()
        )
        self.assertTrue(actions.card_extra_data(job)["can_retry"])
        # A decision that a retry cannot change never offers one.
        agents.AgentJob.objects.filter(id=job.id).update(blocked_reason="approval_rejected")
        job.refresh_from_db()
        self.assertFalse(actions.card_extra_data(job)["can_retry"])
        # Spec 13 principle 4: no retry while the runner is offline, the
        # same answer the job detail's resume_available gives.
        agents.AgentJob.objects.filter(id=job.id).update(blocked_reason="lease_lost")
        agents.AgentRunner.objects.filter(id=self.runner.id).update(status="offline")
        job.refresh_from_db()
        self.assertFalse(actions.card_extra_data(job)["can_retry"])

    def test_queued_card_calls_a_runner_offline_after_45_silent_seconds(self) -> None:
        self._ask()
        job = self._job()
        from zerver.views.agent_jobs import job_data

        agents.AgentRunner.objects.filter(id=self.runner.id).update(
            last_heartbeat_at=now() - timedelta(seconds=30)
        )
        job.refresh_from_db()
        self.assertIsNone(actions.card_extra_data(job)["reason_code"])
        agents.AgentRunner.objects.filter(id=self.runner.id).update(
            last_heartbeat_at=now() - timedelta(seconds=50)
        )
        job.refresh_from_db()
        self.assertEqual(actions.card_extra_data(job)["reason_code"], "runner_offline")
        self.assertEqual(job_data(self.owner, job)["reason_code"], "runner_offline")

    # ---- the card's fields and text (02-D3, 13-D1, 13-R7) ----

    def test_card_title_and_fallback_hold_no_markdown(self) -> None:
        agents.AgentProfile.objects.filter(id=self.profile.id).update(
            name="[x](https://example.com)"
        )
        self._ask()
        job = self._job()
        self.assertEqual(actions.card_extra_data(job)["title"], "Please answer @Hana")
        card = self._card(job)
        self.assertEqual(card.content, "(x)(https://example.com) · Queued")
        self.assertNotIn(">x</a>", card.rendered_content or "")

    def test_card_chips_link_files_pull_requests_and_checks(self) -> None:
        self._ask()
        job = self._job()
        attempt = self._claim(job)
        with (
            tempfile.TemporaryDirectory() as directory,
            override_settings(AGENT_ARTIFACT_ROOT=directory),
        ):
            data = b"A file."
            file = store_artifact(
                self.runner,
                job.id,
                attempt.id,
                1,
                chunks=[data],
                checksum=hashlib.sha256(data).hexdigest(),
                kind="file",
                filename="notes.txt",
                media_type="text/plain",
            )
            log = store_artifact(
                self.runner,
                job.id,
                attempt.id,
                1,
                chunks=[data],
                checksum=hashlib.sha256(data).hexdigest(),
                kind="verification",
                filename="test.log",
                media_type="text/plain",
            )
        moment = now()
        operation = agents.AgentOperation.objects.create(
            realm=job.realm,
            attempt=attempt,
            operation_id=uuid4(),
            tool_class="git.draft_pr",
            status="succeeded",
            argument_digest="d" * 64,
            arguments={},
            remote_receipt={
                "remote": "origin",
                "branch": "agent/fix",
                "commit": "e" * 40,
                "operation_id": str(uuid4()),
                "pull_request_id": "214",
                "pull_request_url": "https://example.com/pulls/214",
                "observed_at": now().isoformat(),
            },
        )
        agents.AgentVerification.objects.create(
            realm=job.realm,
            operation=operation,
            attempt=attempt,
            check_id="test",
            command=["make", "test"],
            cwd=".",
            exit_code=0,
            started_at=moment,
            finished_at=moment,
            tree_hash="f" * 40,
            output_artifact=log,
        )
        chips = actions.card_extra_data(job)["artifacts"]
        self.assertEqual(
            chips,
            [
                {
                    "kind": "file",
                    "label": "notes.txt",
                    "url": f"/json/agent/artifacts/{file.id}",
                    "task_id": None,
                },
                {
                    "kind": "pr",
                    "label": "PR #214",
                    "url": "https://example.com/pulls/214",
                    "task_id": None,
                },
                {
                    "kind": "check",
                    "label": "test ✓",
                    "url": f"/json/agent/artifacts/{log.id}",
                    "task_id": None,
                },
            ],
        )
        check_widget_content(
            {"widget_type": "agent_job", "extra_data": actions.card_extra_data(job)}
        )

    def test_widget_card_carries_the_job_kind(self) -> None:
        self._ask()
        job = self._job()
        agents.AgentJob.objects.filter(id=job.id).update(job_kind="answer")
        job.refresh_from_db()
        card = actions.widget_card_data(job)
        self.assertEqual(card["kind"], "answer")
        check_widget_content({"widget_type": "agent_job", "extra_data": card})
        self.assertNotIn("kind", actions.card_extra_data(job))

    def test_card_chip_opens_a_linked_task(self) -> None:
        self._ask()
        job = self._job()
        board = get_or_create_default_board(self.owner.realm)
        task = do_create_task(
            user_profile=self.owner,
            board=board,
            column=board.columns.order_by("order")[0],
            title="Ship the release",
        )
        Task.objects.filter(id=task.id).update(agent_job=job)
        self.assertEqual(
            actions.card_extra_data(job)["artifacts"],
            [{"kind": "task", "label": "Ship the release", "url": None, "task_id": task.id}],
        )

    def test_fallback_line_names_every_card_state(self) -> None:
        """13-R7: clients that show only the message text still see the
        card's state, in the agent language."""
        from zerver.lib.agent_results import _card_fallback

        self._ask()
        job = self._job()
        for status, stop_target, text in [
            ("queued", "", "Queued"),
            ("running", "", "Working"),
            ("verifying", "", "Working"),
            ("waiting_for_approval", "", "Waiting for approval"),
            ("waiting_for_input", "", "Waiting for a decision"),
            ("completed", "", "Done"),
            ("cancel_requested", "cancelled", "Cancelled"),
            ("cancelled", "cancelled", "Cancelled"),
            ("cancel_requested", "blocked", "Stopped"),
            ("failed", "", "Stopped"),
        ]:
            job.status = status
            job.stop_target = stop_target
            self.assertEqual(_card_fallback(job), f"Hana · {text}")

    def test_card_texts_use_the_agent_language(self) -> None:
        """Spec 13 principle 6: the agent language setting, not the
        organization's default language, picks the card's own text."""
        from django.utils.translation import override as override_language

        realm = self.owner.realm
        realm.default_language = "de"
        realm.save(update_fields=["default_language"])
        agents.AgentRealmSettings.objects.filter(realm=realm).update(agent_language="en")
        with mock.patch(
            "zerver.lib.agent_results.override_language", wraps=override_language
        ) as language:
            self._ask()
        self.assertTrue(language.call_args_list)
        self.assertEqual({call.args[0] for call in language.call_args_list}, {"en"})

    # ---- cancel, failure, and a changed audience (FL-21, FL-22) ----

    def test_cancel_strips_the_draft_marker_and_adds_a_cancel_note(self) -> None:
        self._ask()
        job = self._job()
        attempt = self._claim(job)
        self._draft(job, attempt, "Working on it")
        self.assertTrue(self._card(job).content.endswith(DRAFT_WRITING_MARK))
        job.refresh_from_db()
        with self.captureOnCommitCallbacks(execute=True):
            actions.cancel_job(self.owner, job.id, job.version)
        content = self._card(job).content
        self.assertTrue(content.endswith("Working on it Cancelled."))
        job.refresh_from_db()
        # request_stop() moves an active attempt's job here first, and only
        # to "cancelled" once the runner later confirms the stop.
        self.assertEqual((job.status, job.stop_target), ("cancel_requested", "cancelled"))

    def test_interrupted_job_keeps_its_draft_without_the_marker(self) -> None:
        self._ask()
        job = self._job()
        attempt = self._claim(job)
        self._draft(job, attempt, "Half an answer")
        agents.AgentAttempt.objects.filter(id=attempt.id).update(
            lease_expires_at=now() - timedelta(seconds=1)
        )
        with self.captureOnCommitCallbacks(execute=True):
            reconcile_agents()
        content = self._card(job).content
        self.assertTrue(content.endswith("Half an answer"))

    def test_cancelled_queued_job_shows_cancelled(self) -> None:
        self._ask()
        job = self._job()
        with self.captureOnCommitCallbacks(execute=True):
            actions.cancel_job(self.owner, job.id, job.version)
        self.assertEqual(self._card(job).content, "Hana · Cancelled")
        self.assertEqual(self._widget(job)["status"], "cancelled")

    def test_job_that_never_started_shows_stopped_with_the_cause(self) -> None:
        self._ask()
        job = self._job()
        agents.AgentJob.objects.filter(id=job.id).update(start_deadline=now())
        with self.captureOnCommitCallbacks(execute=True):
            reconcile_agents()
        self.assertEqual(self._card(job).content, "Hana · Stopped")
        widget = self._widget(job)
        self.assertEqual((widget["status"], widget["reason_code"]), ("blocked", "runner_offline"))

    def test_changed_audience_hides_a_draft_already_shown(self) -> None:
        self._ask()
        job = self._job()
        attempt = self._claim(job)
        self._draft(job, attempt, "Private detail")
        binding = job.conversation.audience_binding
        assert binding is not None
        agents.AgentConversation.objects.filter(id=job.conversation_id).update(
            audience_binding={**binding, "epoch": binding["epoch"] + 1}
        )
        with self.assertRaises(AudienceChanged):
            self._draft(job, attempt, "More private detail")
        self.assertEqual(self._card(job).content, NEUTRAL_LINE)

    # ---- best-effort hooks (RL-1) ----

    def test_card_refresh_gives_up_and_logs_after_persistent_contention(self) -> None:
        self._ask()
        job = self._job()
        with (
            mock.patch(
                "zerver.lib.agent_results.update_job_card", side_effect=AgentBusy("busy")
            ) as refresh,
            mock.patch("time.sleep"),
            self.assertLogs("zerver.actions.agent_jobs", level="ERROR"),
            self.captureOnCommitCallbacks(execute=True),
        ):
            actions.cancel_job(self.owner, job.id, job.version)
        self.assertEqual(refresh.call_count, 8)

    # ---- rejected admissions (13-R5, 06-P2, 10-M11) ----

    def test_guest_mention_is_rejected_with_role_not_allowed(self) -> None:
        guest = self.example_user("polonius")
        self.assertEqual(guest.role, UserProfile.ROLE_GUEST)
        message_id = self._ask(guest)
        reaction = Reaction.objects.get(message_id=message_id)
        self.assertEqual(reaction.emoji_name, "prohibited")
        receipt = agents.AgentDispatchReceipt.objects.get()
        self.assertEqual((receipt.decision, receipt.reason), ("rejected", "role_not_allowed"))
        self.assertEqual(agents.AgentJob.objects.count(), 0)

    def test_guest_cannot_create_a_job_through_the_api(self) -> None:
        guest = self.example_user("polonius")
        source_id = self.send_personal_message(guest, self.profile.bot_user, "Hello")
        self.login_user(guest)
        response = self.client_post(
            "/json/agent/jobs",
            {
                "payload": json.dumps(
                    {
                        "schema_version": 1,
                        "profile_id": str(self.profile.id),
                        "source_message_id": source_id,
                        "request": "Please answer",
                        "idempotency_key": str(uuid4()),
                    }
                )
            },
        )
        self.assert_json_error(response, "Agent request rejected.")
        self.assertEqual(agents.AgentJob.objects.count(), 0)

    def test_paused_agent_mention_is_rejected_with_profile_paused(self) -> None:
        agents.AgentProfile.objects.filter(id=self.profile.id).update(desired_state="paused")
        message_id = self._ask()
        self.assertEqual(Reaction.objects.get(message_id=message_id).emoji_name, "prohibited")
        receipt = agents.AgentDispatchReceipt.objects.get()
        self.assertEqual((receipt.decision, receipt.reason), ("rejected", "profile_paused"))

    # ---- list_jobs (O2) ----

    def _other_job(self) -> agents.AgentJob:
        other_owner = self.example_user("cordelia")
        profile = self._profile(other_owner, self._runner(other_owner, "Other", "d"), "Other")
        source_id = self.send_personal_message(other_owner, profile.bot_user, "Private request")
        return actions.create_job(
            other_owner,
            profile=profile,
            source=Message.objects.get(id=source_id),
            request="Private request",
            idempotency_key=uuid4(),
            job_kind="answer",
            delivery_target="answer",
        )

    def test_list_jobs_prefilter_excludes_a_job_the_viewer_cannot_access(self) -> None:
        self._ask()
        my_job = self._job()
        other_job = self._other_job()
        response = self.api_get(self.owner, "/api/v1/agent/jobs")
        job_ids = {item["id"] for item in self.assert_json_success(response)["jobs"]}
        self.assertIn(str(my_job.id), job_ids)
        self.assertNotIn(str(other_job.id), job_ids)

    def test_list_jobs_query_count_ignores_jobs_the_viewer_cannot_see(self) -> None:
        self._ask()
        self.login_user(self.owner)
        with self.assert_database_query_count(27):
            self.assert_json_success(self.client_get("/json/agent/jobs"))
        self._other_job()
        with queries_captured() as queries:
            self.assert_json_success(self.client_get("/json/agent/jobs"))
        self.assert_length(queries, 27)

    # ---- the language sentence in the descriptor ----

    def _team_instructions(self, text: str, language: str) -> p.InstructionText | None:
        agents.AgentRealmSettings.objects.filter(realm=self.owner.realm).update(
            team_instructions=text, agent_language=language
        )
        self._ask()
        job = self._job()
        actions.claim_work(self.runner, claim_key=uuid4())
        descriptor = agents.AgentAttempt.objects.get(job=job).descriptor
        instructions = p.AttemptDescriptor.model_validate(descriptor).instructions
        assert instructions is not None
        return instructions.team

    def test_descriptor_language_sentence_follows_the_agent_language(self) -> None:
        team = self._team_instructions("Be brief.", "en")
        assert team is not None
        self.assertEqual(team.text, "Be brief.\n\nRespond in English.")

    def test_descriptor_language_sentence_without_team_instructions(self) -> None:
        team = self._team_instructions("", "id")
        assert team is not None
        self.assertEqual(team.text, "Respond in Indonesian.")

    def test_descriptor_language_sentence_fits_full_team_instructions(self) -> None:
        # 4000 emoji are 8000 UTF-16 units: the whole instructions limit.
        team = self._team_instructions("\U0001f600" * 4000, "id")
        assert team is not None
        self.assertTrue(team.text.endswith("\n\nRespond in Indonesian."))
        self.assertLessEqual(len(team.text.encode("utf-16-le")) // 2, p.INSTRUCTIONS_MAX_CHARS)

    # ---- publication timeout (FL-30) ----

    def test_verifying_answer_times_out_after_the_publication_deadline(self) -> None:
        self._ask()
        job = self._job()
        attempt = self._claim(job)
        agents.AgentAttempt.objects.filter(id=attempt.id).update(
            active=False, process_state="stopped", stopped_at=now(), ended_at=now()
        )
        agents.AgentJob.objects.filter(id=job.id).update(
            status="verifying", updated_at=now() - timedelta(seconds=31)
        )
        outbox = agents.AgentOutbox.objects.create(
            realm=job.realm, job=job, delivery_key=f"result:{job.id}", event_type="result.publish"
        )
        with self.captureOnCommitCallbacks(execute=True):
            counts = reconcile_agents()
        self.assertEqual(counts["publication_timeouts"], 1)
        job.refresh_from_db()
        self.assertEqual((job.status, job.blocked_reason), ("failed", "publication_timeout"))
        outbox.refresh_from_db()
        self.assertEqual(outbox.status, "blocked")
        self.assertTrue(
            agents.AgentAuditEvent.objects.filter(
                job=job, type="publication.blocked", payload__reason="publication_timeout"
            ).exists()
        )
        self.assertEqual(self._widget(job)["reason_code"], "publication_timeout")

    def test_publication_timeout_stops_a_still_active_attempt(self) -> None:
        self._ask()
        job = self._job()
        attempt = self._claim(job)
        agents.AgentJob.objects.filter(id=job.id).update(
            status="verifying", updated_at=now() - timedelta(seconds=31)
        )
        with self.captureOnCommitCallbacks(execute=True):
            reconcile_agents()
        job.refresh_from_db()
        self.assertEqual((job.status, job.stop_target), ("cancel_requested", "failed"))
        attempt.refresh_from_db()
        self.assertEqual(attempt.process_state, "stopping")

    def test_answer_held_for_private_delivery_never_times_out(self) -> None:
        self._ask()
        job = self._job()
        agents.AgentJob.objects.filter(id=job.id).update(
            status="verifying",
            blocked_reason="audience_changed",
            updated_at=now() - timedelta(minutes=5),
        )
        self.assertEqual(reconcile_agents()["publication_timeouts"], 0)
        job.refresh_from_db()
        self.assertEqual(job.status, "verifying")

    # ---- long answers (Q-19, 13-Q2) ----

    def test_answer_longer_than_the_summary_limit_is_shown_whole(self) -> None:
        self._ask()
        job = self._job()
        attempt = self._claim(job)
        answer = "a" * 5999 + "Z"
        with (
            tempfile.TemporaryDirectory() as directory,
            override_settings(AGENT_ARTIFACT_ROOT=directory),
        ):
            self._answer(job, attempt, answer)
        card = self._card(job)
        self.assertTrue(card.content.endswith(answer))
        self.assertEqual(self._widget(job)["artifacts"], [])

    def test_answer_longer_than_one_message_is_cut_and_attached(self) -> None:
        self._ask()
        job = self._job()
        attempt = self._claim(job)
        with (
            tempfile.TemporaryDirectory() as directory,
            override_settings(AGENT_ARTIFACT_ROOT=directory),
        ):
            self._answer(job, attempt, "a" * (settings.MAX_MESSAGE_LENGTH + 2000))
        card = self._card(job)
        self.assertEqual(len(card.content), settings.MAX_MESSAGE_LENGTH)
        self.assertTrue(card.content.endswith("\n\nThe full answer is attached as a file."))
        job.refresh_from_db()
        assert job.result_receipt is not None
        summary = agents.AgentArtifact.objects.get(attempt=attempt, kind="summary")
        self.assertEqual(job.result_receipt["full_text_artifact_id"], str(summary.id))
        self.assertEqual(
            self._widget(job)["artifacts"],
            [
                {
                    "kind": "file",
                    "label": "answer.txt",
                    "url": f"/json/agent/artifacts/{summary.id}",
                    "task_id": None,
                }
            ],
        )
        self.assertFalse(agents.AgentArtifact.objects.filter(kind="file").exists())

    def test_long_answer_sent_privately_is_cut_and_attached(self) -> None:
        from zerver.lib.agent_results import deliver_result_privately

        self._ask()
        job = self._job()
        attempt = self._claim(job)
        binding = job.conversation.audience_binding
        assert binding is not None
        with (
            tempfile.TemporaryDirectory() as directory,
            override_settings(AGENT_ARTIFACT_ROOT=directory),
        ):
            # The conversation moves on after the result is prepared.
            with mock.patch("zerver.actions.agent_jobs._publish_after_commit"):
                self._answer(job, attempt, "a" * (settings.MAX_MESSAGE_LENGTH + 2000))
            agents.AgentConversation.objects.filter(id=job.conversation_id).update(
                audience_binding={**binding, "epoch": binding["epoch"] + 1}
            )
            with self.captureOnCommitCallbacks(execute=True), self.assertRaises(AudienceChanged):
                publish_result(job.id)
            job.refresh_from_db()
            self.assertEqual(job.blocked_reason, "audience_changed")
            with self.captureOnCommitCallbacks(execute=True):
                job = deliver_result_privately(self.owner, job.id, job.version)
        assert job.result_receipt is not None
        summary = agents.AgentArtifact.objects.get(attempt=attempt, kind="summary")
        self.assertEqual(job.result_receipt["full_text_artifact_id"], str(summary.id))
        message = Message.objects.get(id=job.result_receipt["message_id"])
        self.assertTrue(message.content.endswith("\n\nThe full answer is attached as a file."))
        self.assertEqual(self._card(job).content, NEUTRAL_LINE)

    def test_short_answer_is_not_cut(self) -> None:
        self._ask()
        self.assertEqual(
            _fit_message_limit(self._job(), "A short answer."), ("A short answer.", False)
        )
