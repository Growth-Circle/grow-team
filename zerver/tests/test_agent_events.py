"""Realtime events for job status, runner status, pairing, and room meta."""

from datetime import date, timedelta
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

from django.db import transaction
from django.utils.timezone import now
from typing_extensions import override

from zerver.actions.agent_jobs import heartbeat, transition
from zerver.actions.agents import (
    PAIRING_MAX_FAILURES,
    approve_pairing,
    exchange_pairing,
    revoke_runner,
    start_pairing,
)
from zerver.actions.user_groups import check_add_user_group
from zerver.lib.agent_context import current_audience
from zerver.lib.agent_events import (
    send_agent_realm_settings_event,
    send_realm_permissions_event,
    send_room_meta_event,
)
from zerver.lib.agent_presence import observed_runner_status
from zerver.lib.event_schema import (
    check_agent_job_update,
    check_agent_runner_pairing,
    check_agent_runner_update,
)
from zerver.lib.test_classes import ZulipTestCase
from zerver.models import UserProfile, agents


class AgentEventsTests(ZulipTestCase):
    @staticmethod
    def _stamped(event: dict[str, Any]) -> dict[str, Any]:
        """A client's queue stamps `id` when it appends an event
        (`EventQueue.append`); `capture_send_event_calls` captures the
        notice one step before that, so the checkers need it added back."""
        return {**event, "id": 1}

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
        message_id = self.send_stream_message(self.owner, "Denmark", "Agent fixture")
        stream_id = self.get_stream_id("Denmark")
        self.conversation = agents.AgentConversation.objects.create(
            realm=self.realm,
            profile=self.profile,
            anchor_message_id=message_id,
            audience_binding={"stream_id": stream_id},
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

    def _anchor_job_to_audience(self, job: agents.AgentJob) -> None:
        """Give `job` a real source message and a matching audience binding.
        `require_job_access` checks a job with no source message against
        only its literal requester and owner; a grant-based reviewer needs
        this to be part of the job's message audience instead."""
        binding = current_audience(job.conversation, job.requester, job.profile.bot_user)
        job.conversation.audience_binding = binding.model_dump(mode="json")
        job.conversation.save(update_fields=["audience_binding"])
        job.source_message_id = job.conversation.anchor_message_id
        job.save(update_fields=["source_message"])

    def _grant_review_access(
        self,
        *,
        principal_user: UserProfile | None = None,
        principal_group_id: int | None = None,
    ) -> None:
        """A realistic `job.review` grant: profile and runner access
        together, the same bundle `share_agent_profile` would create."""
        agents.AgentGrant.objects.create(
            realm=self.realm,
            owner=self.profile.owner,
            principal_user=principal_user,
            principal_group_id=principal_group_id,
            target_kind="profile",
            profile=self.profile,
            actions=["profile.use", "job.review"],
        )
        agents.AgentGrant.objects.create(
            realm=self.realm,
            owner=self.profile.owner,
            principal_user=principal_user,
            principal_group_id=principal_group_id,
            target_kind="runner",
            runner=self.runner,
            actions=["runner.use"],
        )

    def test_transition_sends_agent_job_event_to_requester_owner_and_reviewer(self) -> None:
        reviewer_owner = self.example_user("othello")
        reviewer = self.example_user("cordelia")
        self.profile.owner = reviewer_owner
        self.profile.save(update_fields=["owner"])
        self.runner.owner = reviewer_owner
        self.runner.save(update_fields=["owner"])
        self._grant_review_access(principal_user=self.owner)  # the requester's own access
        self._grant_review_access(principal_user=reviewer)
        self._anchor_job_to_audience(self.job)

        with self.capture_send_event_calls(expected_num_events=1) as events:
            transition(self.job, "waiting_for_approval")

        event = self._stamped(events[0]["event"])
        check_agent_job_update("events[0]", event)
        self.assertEqual(event["job_id"], str(self.job.id))
        self.assertEqual(event["status"], "waiting_for_approval")
        self.assertEqual(event["phase"], self.job.phase)
        self.assertEqual(event["profile_id"], str(self.profile.id))
        self.assertEqual(event["stream_id"], self.get_stream_id("Denmark"))
        self.assertEqual(set(events[0]["users"]), {self.owner.id, reviewer_owner.id, reviewer.id})

    def test_transition_sends_agent_job_event_to_a_group_grant_reviewer(self) -> None:
        reviewer = self.example_user("cordelia")
        group = check_add_user_group(
            self.realm, "wp05-reviewers", [reviewer], acting_user=self.owner
        )
        self._grant_review_access(principal_group_id=group.id)
        self._anchor_job_to_audience(self.job)

        with self.capture_send_event_calls(expected_num_events=1) as events:
            transition(self.job, "waiting_for_approval")

        self.assertIn(reviewer.id, events[0]["users"])

    def test_agent_job_event_excludes_a_grant_scoped_to_a_different_room(self) -> None:
        reviewer = self.example_user("cordelia")
        other_room = self.make_stream("wp05-other-room")
        agents.AgentGrant.objects.create(
            realm=self.realm,
            owner=self.owner,
            principal_user=reviewer,
            target_kind="profile",
            profile=self.profile,
            actions=["profile.use", "job.review"],
            scope={"kind": "stream", "stream_id": other_room.id},
        )
        agents.AgentGrant.objects.create(
            realm=self.realm,
            owner=self.owner,
            principal_user=reviewer,
            target_kind="runner",
            runner=self.runner,
            actions=["runner.use"],
        )
        self._anchor_job_to_audience(self.job)  # the job's own message stays in Denmark

        with self.capture_send_event_calls(expected_num_events=1) as events:
            transition(self.job, "waiting_for_approval")

        self.assertNotIn(reviewer.id, events[0]["users"])

    def test_agent_job_event_excludes_a_reviewer_outside_the_message_audience(self) -> None:
        reviewer = self.example_user("cordelia")
        self._grant_review_access(principal_user=reviewer)
        private_room = self.make_stream("wp05-private-room", invite_only=True)
        self.subscribe(self.owner, private_room.name)
        self.subscribe(self.profile.bot_user, private_room.name)
        # `reviewer` is never subscribed: a valid grant, but outside this room.
        self.conversation.anchor_message_id = self.send_stream_message(
            self.owner, private_room.name, "Private job"
        )
        self.conversation.save(update_fields=["anchor_message_id"])
        self._anchor_job_to_audience(self.job)

        with self.capture_send_event_calls(expected_num_events=1) as events:
            transition(self.job, "waiting_for_approval")

        self.assertNotIn(reviewer.id, events[0]["users"])
        self.assertIn(self.owner.id, events[0]["users"])

    def test_agent_job_event_not_sent_before_commit(self) -> None:
        with (
            self.capture_send_event_calls(expected_num_events=0),
            self.assertRaises(ZeroDivisionError),
            transaction.atomic(),
        ):
            transition(self.job, "waiting_for_approval")
            raise ZeroDivisionError

    def test_heartbeat_sends_event_only_on_status_change(self) -> None:
        self.assertEqual(self.runner.status, "unknown")

        with self.capture_send_event_calls(expected_num_events=1) as events:
            heartbeat(self.runner, [])
        event = self._stamped(events[0]["event"])
        check_agent_runner_update("events[0]", event)
        self.assertEqual(event["runner_id"], str(self.runner.id))
        self.assertEqual(event["status"], "online")
        self.assertIn(self.owner.id, events[0]["users"])

        self.runner.refresh_from_db()
        with self.capture_send_event_calls(expected_num_events=0):
            heartbeat(self.runner, [])

    def test_heartbeat_sends_event_for_a_runner_observed_stale(self) -> None:
        """The status column never left "online", but a silent runner reads
        back "unknown" once its heartbeat goes stale (agent_presence)."""
        self.runner.status = "online"
        self.runner.last_heartbeat_at = now() - timedelta(minutes=5)
        self.runner.save(update_fields=["status", "last_heartbeat_at"])
        self.assertEqual(observed_runner_status(self.runner), "unknown")

        with self.capture_send_event_calls(expected_num_events=1) as events:
            heartbeat(self.runner, [])
        event = self._stamped(events[0]["event"])
        check_agent_runner_update("events[0]", event)
        self.assertEqual(event["status"], "online")

    def test_runner_event_reaches_the_owner_and_every_admin(self) -> None:
        admin = self.example_user("iago")
        member = self.example_user("polonius")

        with self.capture_send_event_calls(expected_num_events=1) as events:
            heartbeat(self.runner, [])

        self.assertIn(self.owner.id, events[0]["users"])
        self.assertIn(admin.id, events[0]["users"])
        self.assertNotIn(member.id, events[0]["users"])

    def test_revoke_runner_sends_event_once(self) -> None:
        with self.capture_send_event_calls(expected_num_events=1) as events:
            revoke_runner(self.runner)
        event = self._stamped(events[0]["event"])
        check_agent_runner_update("events[0]", event)
        self.assertEqual(event["status"], "revoked")
        self.assertIn(self.owner.id, events[0]["users"])
        self.assertNotIn(self.example_user("polonius").id, events[0]["users"])

        # Already revoked: a second call is a no-op, so no second event.
        with self.capture_send_event_calls(expected_num_events=0):
            revoke_runner(self.runner)

    def test_revoke_runner_sends_an_agent_job_event_for_every_stopped_job(self) -> None:
        agents.AgentAttempt.objects.create(
            realm=self.realm,
            job=self.job,
            runner=self.runner,
            number=1,
            lease_epoch=1,
            lease_expires_at=now() + timedelta(seconds=90),
            descriptor_digest="a" * 64,
            active=True,
        )
        self.job.status = "running"
        self.job.save(update_fields=["status"])

        with self.capture_send_event_calls(expected_num_events=2) as events:
            revoke_runner(self.runner)

        job_events = [call["event"] for call in events if call["event"]["type"] == "agent_job"]
        self.assert_length(job_events, 1)
        event = self._stamped(job_events[0])
        check_agent_job_update("events[0]", event)
        self.assertEqual(event["job_id"], str(self.job.id))
        self.assertEqual(event["status"], "cancel_requested")

    def test_pairing_events_through_approve_and_deny(self) -> None:
        with self.capture_send_event_calls(expected_num_events=0):
            # Anonymous: no realm yet, so nothing is queued (contract §4.3).
            pairing = start_pairing("Laptop", "f" * 64, "ABCD1234", "s" * 40)

        with self.capture_send_event_calls(expected_num_events=1) as events:
            approve_pairing(self.owner, pairing, "ABCD1234")
        event = self._stamped(events[0]["event"])
        check_agent_runner_pairing("events[0]", event)
        self.assertEqual(event["pairing_id"], str(pairing.id))
        self.assertEqual(event["state"], "approved")
        self.assertEqual(list(events[0]["users"]), [self.owner.id])

        pairing.refresh_from_db()
        with self.capture_send_event_calls(expected_num_events=1) as events:
            for _ in range(PAIRING_MAX_FAILURES):
                with self.assertRaises(ValueError):
                    exchange_pairing(pairing, "wrong-secret")
        event = self._stamped(events[0]["event"])
        check_agent_runner_pairing("events[0]", event)
        self.assertEqual(event["state"], "denied")
        self.assertEqual(list(events[0]["users"]), [self.owner.id])

    def test_exchange_pairing_expires_and_notifies_the_owner_once_the_ttl_lapses(self) -> None:
        pairing = start_pairing("Laptop", "f" * 64, "ABCD1234", "s" * 40)
        approve_pairing(self.owner, pairing, "ABCD1234")
        pairing.refresh_from_db()
        pairing.expires_at = now() - timedelta(seconds=1)
        pairing.save(update_fields=["expires_at"])

        with (
            self.capture_send_event_calls(expected_num_events=1) as events,
            self.assertRaises(ValueError),
        ):
            exchange_pairing(pairing, "s" * 40)
        event = self._stamped(events[0]["event"])
        check_agent_runner_pairing("events[0]", event)
        self.assertEqual(event["state"], "expired")
        self.assertEqual(list(events[0]["users"]), [self.owner.id])

        pairing.refresh_from_db()
        self.assertEqual(pairing.state, "expired")

    def test_send_room_meta_event_reaches_stream_metadata_viewers(self) -> None:
        stream = self.make_stream("announce-events")
        room_meta = SimpleNamespace(
            stream_id=stream.id, due_date=date(2026, 12, 31), summary_enabled=True
        )
        with self.capture_send_event_calls(expected_num_events=1) as events:
            send_room_meta_event(room_meta)
        self.assertIn(self.owner.id, events[0]["users"])

    def test_send_realm_permissions_event_reaches_every_active_user(self) -> None:
        with self.capture_send_event_calls(expected_num_events=1) as events:
            send_realm_permissions_event(self.realm)
        self.assertIn(self.owner.id, events[0]["users"])

    def test_send_agent_realm_settings_event_reaches_every_active_user(self) -> None:
        with self.capture_send_event_calls(expected_num_events=1) as events:
            send_agent_realm_settings_event(self.realm, {"timezone": "Asia/Jakarta"})
        self.assertIn(self.owner.id, events[0]["users"])
