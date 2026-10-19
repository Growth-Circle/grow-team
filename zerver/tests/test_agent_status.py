"""Tests for the Status panel of an agent (V3), zerver/lib/agent_status.py and
GET /json/agent/profiles/<id>/status."""

from datetime import timedelta
from uuid import uuid4

import time_machine
from django.utils.timezone import now

from zerver.actions import agent_jobs as actions
from zerver.actions.streams import bulk_remove_subscriptions
from zerver.lib import agent_status
from zerver.models import Message, UserProfile, agents
from zerver.models.streams import get_stream
from zerver.tests.test_needs import NeedsTestCase


class AgentStatusTestCase(NeedsTestCase):
    def _status(
        self, profile: agents.AgentProfile | None = None
    ) -> dict[str, list[dict[str, object]]]:
        self.login_user(self.owner)
        return self._status_for(self.owner, profile or self.profile)

    def _status_for(
        self, user: UserProfile, profile: agents.AgentProfile
    ) -> dict[str, list[dict[str, object]]]:
        self.login_user(user)
        payload = self.assert_json_success(
            self.client_get(f"/json/agent/profiles/{profile.id}/status")
        )
        return {
            section: payload[section] for section in ("in_progress", "waiting_on_you", "decided")
        }

    def _ids(self, items: list[dict[str, object]]) -> list[object]:
        return [item["id"] for item in items]

    def _queued_job(
        self, *, stream: str = "Denmark", user: UserProfile | None = None
    ) -> agents.AgentJob:
        sender = user or self.owner
        message_id = self.send_stream_message(sender, stream, "Please look at this", "status-test")
        return actions.create_job(
            sender,
            profile=self.profile,
            source=Message.objects.get(id=message_id),
            request="Please look at this",
            idempotency_key=uuid4(),
            job_kind="answer",
            delivery_target="answer",
        )

    def _private_channel(self) -> None:
        """A channel the owner and the agent read, and nobody else."""
        self.make_stream("status-secret", invite_only=True)
        self.subscribe(self.owner, "status-secret")
        self.subscribe(self.profile.bot_user, "status-secret")

    def _clone_jobs(self, job: agents.AgentJob, count: int, *, status: str = "running") -> None:
        """Rows without a source message are cheap to make, and the owner of
        the profile can open them."""
        for number in range(count):
            clone = agents.AgentJob.objects.get(id=job.id)
            clone.pk = uuid4()
            clone.idempotency_key = uuid4()
            clone.source_message = None
            clone.status = status
            clone.created_at = now() + timedelta(minutes=number + 1)
            clone.save(force_insert=True)

    def _share_with(self, user: UserProfile) -> None:
        agents.AgentGrant.objects.create(
            realm=self.owner.realm,
            owner=self.owner,
            principal_user=user,
            target_kind="profile",
            actions=["profile.use", "context.read"],
            profile=self.profile,
        )
        agents.AgentGrant.objects.create(
            realm=self.owner.realm,
            owner=self.owner,
            principal_user=user,
            target_kind="runner",
            actions=["runner.use"],
            runner=self.runner,
        )


class AgentStatusSectionTests(AgentStatusTestCase):
    def test_a_queued_job_is_in_progress(self) -> None:
        job = self._queued_job()
        status = self._status()
        [item] = status["in_progress"]
        self.assertEqual(item["id"], f"job:{job.id}")
        self.assertEqual(item["kind"], "job")
        self.assertEqual(item["title"], "Please look at this")
        self.assertEqual(item["status"], "queued")
        self.assertIsNone(item["outcome"])
        self.assertEqual(item["job_id"], str(job.id))
        self.assertEqual(item["message_id"], job.source_message_id)
        self.assertEqual(item["stream_id"], self.get_stream_id("Denmark"))
        self.assertEqual(item["topic"], "status-test")
        self.assertEqual(item["time"], job.created_at.isoformat())
        self.assertEqual(status["waiting_on_you"], [])
        self.assertEqual(status["decided"], [])

    def test_finished_jobs_are_not_in_progress(self) -> None:
        job = self._queued_job()
        for status in ("completed", "failed", "cancelled", "blocked", "interrupted", "draft"):
            agents.AgentJob.objects.filter(id=job.id).update(status=status)
            self.assertEqual(self._status()["in_progress"], [])

    def test_a_decision_waits_on_the_user_and_leaves_when_answered(self) -> None:
        job, _attempt = self._pending_decision("Deploy to production?", ["Ya", "Tidak"])
        status = self._status()
        [waiting] = status["waiting_on_you"]
        self.assertEqual(waiting["id"], f"decision:{job.id}")
        self.assertEqual(waiting["kind"], "decision")
        self.assertEqual(waiting["title"], "Deploy to production?")
        self.assertEqual(waiting["status"], "waiting_for_input")
        self.assertEqual(waiting["job_id"], str(job.id))
        self.assertEqual(waiting["message_id"], job.source_message_id)
        self.assertEqual(self._ids(status["in_progress"]), [f"job:{job.id}"])
        self.assertEqual(status["decided"], [])

        self._answer(job, "Ya")
        status = self._status()
        self.assertEqual(status["waiting_on_you"], [])
        [decided] = status["decided"]
        self.assertEqual(decided["id"], f"decision:{job.id}")
        self.assertEqual(decided["outcome"], "answered")
        self.assertEqual(decided["detail"], "Ya")
        self.assertIsNone(decided["status"])
        answer = agents.AgentInput.objects.get(job=job, text="Ya")
        self.assertEqual(decided["time"], answer.created_at.isoformat())

    def test_an_approval_waits_and_then_is_decided(self) -> None:
        job, _attempt, approval = self._pending_approval()
        status = self._status(self.code_profile)
        [waiting] = status["waiting_on_you"]
        self.assertEqual(waiting["id"], f"approval:{approval.id}")
        self.assertEqual(waiting["kind"], "approval")
        self.assertEqual(waiting["status"], "waiting_for_approval")
        self.assertEqual(waiting["job_id"], str(job.id))
        self.assertEqual(waiting["message_id"], job.source_message_id)
        self.assertEqual(self._ids(status["in_progress"]), [f"job:{job.id}"])

        self._decide(approval, decision="rejected")
        status = self._status(self.code_profile)
        self.assertEqual(status["waiting_on_you"], [])
        [decided] = status["decided"]
        self.assertEqual(decided["id"], f"approval:{approval.id}")
        self.assertEqual(decided["outcome"], "rejected")
        self.assertIsNone(decided["detail"])

    def test_an_approval_that_was_approved_is_decided_as_approved(self) -> None:
        _job, _attempt, approval = self._pending_approval()
        self._decide(approval)
        [decided] = self._status(self.code_profile)["decided"]
        self.assertEqual(decided["outcome"], "approved")

    def test_each_agent_shows_only_its_own_items(self) -> None:
        self._pending_decision()
        _job, _attempt, approval = self._pending_approval()
        answer_status = self._status(self.profile)
        code_status = self._status(self.code_profile)
        self.assertEqual([item["kind"] for item in answer_status["waiting_on_you"]], ["decision"])
        self.assertEqual(self._ids(code_status["waiting_on_you"]), [f"approval:{approval.id}"])
        self.assertEqual(len(answer_status["in_progress"]), 1)
        self.assertEqual(len(code_status["in_progress"]), 1)

    def test_decided_covers_seven_days(self) -> None:
        job, _attempt = self._pending_decision()
        self._answer(job, "Ya")
        answered_at = agents.AgentInput.objects.get(job=job, text="Ya").created_at
        with time_machine.travel(answered_at + timedelta(days=6, hours=23), tick=False):
            self.assertEqual(len(self._status()["decided"]), 1)
        with time_machine.travel(answered_at + timedelta(days=7, hours=1), tick=False):
            self.assertEqual(self._status()["decided"], [])

    def test_decided_is_what_this_user_decided(self) -> None:
        job, _attempt = self._pending_decision()
        self._answer(job, "Ya")
        self._share_with(self.example_user("iago"))
        status = self._status_for(self.example_user("iago"), self.profile)
        self.assertEqual(status["decided"], [])

    def test_a_decision_waits_only_on_someone_who_may_answer_it(self) -> None:
        self._pending_decision()
        iago = self.example_user("iago")
        self._share_with(iago)
        self.subscribe(iago, "Denmark")
        # Iago can use the agent, but may not steer the job of another person.
        self.assertEqual(self._status_for(iago, self.profile)["waiting_on_you"], [])
        self.assertEqual(len(self._status()["waiting_on_you"]), 1)

    def test_sections_are_newest_first_and_capped_at_twenty(self) -> None:
        job = self._queued_job()
        self._clone_jobs(job, 24)
        in_progress = self._status()["in_progress"]
        self.assertEqual(len(in_progress), agent_status.ITEM_LIMIT)
        times = [str(item["time"]) for item in in_progress]
        self.assertEqual(times, sorted(times, reverse=True))
        # The original job is the oldest of 25, so it is the one cut.
        self.assertNotIn(f"job:{job.id}", self._ids(in_progress))

    def test_the_title_is_the_first_line_of_the_request(self) -> None:
        job = self._queued_job()
        agents.AgentJob.objects.filter(id=job.id).update(request="First line\nSecond line")
        [item] = self._status()["in_progress"]
        self.assertEqual(item["title"], "First line")


class AgentStatusAccessTests(AgentStatusTestCase):
    def test_a_user_without_access_to_the_agent_gets_the_error_of_an_unknown_id(self) -> None:
        self._queued_job()
        self.login_user(self.example_user("cordelia"))
        hidden = self.client_get(f"/json/agent/profiles/{self.profile.id}/status")
        unknown = self.client_get(f"/json/agent/profiles/{uuid4()}/status")
        self.assert_json_error(hidden, "Agent request rejected.")
        self.assert_json_error(unknown, "Agent request rejected.")
        self.assertEqual(hidden.json(), unknown.json())
        self.assertEqual(hidden.status_code, unknown.status_code)

    def test_a_profile_of_another_realm_is_unknown(self) -> None:
        profile = self.profile
        self.login_user(self.mit_user("starnine"))
        result = self.client_get(f"/json/agent/profiles/{profile.id}/status", subdomain="zephyr")
        self.assert_json_error(result, "Agent request rejected.")

    def test_a_malformed_profile_id_is_not_a_route(self) -> None:
        self.login_user(self.owner)
        result = self.client_get("/json/agent/profiles/not-a-uuid/status")
        self.assertEqual(result.status_code, 404)

    def test_the_endpoint_needs_login_and_get(self) -> None:
        result = self.client_get(f"/json/agent/profiles/{self.profile.id}/status")
        self.assertEqual(result.status_code, 401)
        self.login_user(self.owner)
        result = self.client_post(f"/json/agent/profiles/{self.profile.id}/status")
        self.assertEqual(result.status_code, 405)

    def test_a_job_the_user_cannot_open_is_left_out_of_every_section(self) -> None:
        iago = self.example_user("iago")
        self._share_with(iago)
        self.subscribe(iago, "Denmark")
        self._private_channel()
        open_job = self._queued_job()
        hidden_job = self._queued_job(stream="status-secret")

        # The owner opens both, so each shows in progress.
        owner_status = self._status()
        self.assertEqual(
            set(self._ids(owner_status["in_progress"])),
            {f"job:{open_job.id}", f"job:{hidden_job.id}"},
        )
        status = self._status_for(iago, self.profile)
        self.assertEqual(self._ids(status["in_progress"]), [f"job:{open_job.id}"])

    def test_a_decision_in_a_channel_the_user_cannot_read_is_left_out(self) -> None:
        iago = self.example_user("iago")
        self._share_with(iago)
        self._private_channel()
        job = self._queued_job(stream="status-secret")
        self.assertEqual(self._status_for(iago, self.profile)["in_progress"], [])
        self.assertEqual(self._status_for(iago, self.profile)["waiting_on_you"], [])
        self.assertEqual(len(self._status()["in_progress"]), 1)
        self.assertEqual(agents.AgentJob.objects.get(id=job.id).status, "queued")

    def test_a_decided_item_leaves_when_the_user_loses_access_to_the_job(self) -> None:
        self._private_channel()
        job = self._queued_job(stream="status-secret")
        actions.claim_work(self.runner, claim_key=uuid4())
        self._record_event(
            job,
            agents.AgentAttempt.objects.get(job=job),
            "input.requested",
            {"question": "Continue?", "options": ["Ya", "Tidak"]},
        )
        self._answer(job, "Ya")
        self.assertEqual(len(self._status()["decided"]), 1)

        # The owner still sees the agent, but can no longer read the message
        # the job came from.
        bulk_remove_subscriptions(
            self.owner.realm,
            [self.owner],
            [get_stream("status-secret", self.owner.realm)],
            acting_user=None,
        )
        status = self._status()
        self.assertEqual(status["decided"], [])
        self.assertEqual(status["in_progress"], [])


class AgentStatusQueryTests(AgentStatusTestCase):
    def test_the_number_of_queries_does_not_grow_with_the_number_of_jobs(self) -> None:
        job = self._queued_job()
        self._clone_jobs(job, 25)
        agent_status.get_agent_status(self.owner, self.profile)
        with self.assert_database_query_count(56):
            first = agent_status.get_agent_status(self.owner, self.profile)
        self._clone_jobs(job, 30)
        with self.assert_database_query_count(56):
            second = agent_status.get_agent_status(self.owner, self.profile)
        self.assertEqual(len(first["in_progress"]), agent_status.ITEM_LIMIT)
        self.assertEqual(len(second["in_progress"]), agent_status.ITEM_LIMIT)
