"""Task cards follow their linked agent job's status (05-R1, 05-R2)."""

from datetime import timedelta
from uuid import uuid4

from django.utils.timezone import now
from typing_extensions import override

from zerver.actions.agent_jobs import add_input, transition
from zerver.actions.tasks import (
    check_agent_task_move,
    do_create_task,
    do_move_task,
    sync_agent_task,
)
from zerver.lib.agent_context import current_audience
from zerver.lib.exceptions import JsonableError
from zerver.lib.tasks import get_or_create_default_board
from zerver.lib.test_classes import ZulipTestCase
from zerver.models import TaskHistory, agents


class AgentTaskSyncTest(ZulipTestCase):
    @override
    def setUp(self) -> None:
        super().setUp()
        self.hamlet = self.example_user("hamlet")
        self.realm = self.hamlet.realm
        self.board = get_or_create_default_board(self.realm)
        self.columns = list(self.board.columns.all())
        self.runner = agents.AgentRunner.objects.create(
            realm=self.realm, owner=self.hamlet, name="Fixture", fingerprint="a" * 64
        )
        self.profile = agents.AgentProfile.objects.create(
            realm=self.realm,
            owner=self.hamlet,
            runner=self.runner,
            bot_user=self.example_user("default_bot"),
            name="Fixture",
            adapter_id="codex-acp",
            adapter_version="1.12.0",
        )
        message_id = self.send_stream_message(self.hamlet, "Denmark", "Agent fixture")
        self.conversation = agents.AgentConversation.objects.create(
            realm=self.realm, profile=self.profile, anchor_message_id=message_id
        )
        self.job = agents.AgentJob.objects.create(
            realm=self.realm,
            requester=self.hamlet,
            conversation=self.conversation,
            profile=self.profile,
            runner=self.runner,
            request="Explain",
            idempotency_key=uuid4(),
            payload_digest="a" * 64,
            admission_revision=1,
        )
        self.card = do_create_task(
            user_profile=self.hamlet, board=self.board, column=self.columns[0], title="Agent task"
        )
        self.card.agent_job = self.job
        self.card.save(update_fields=["agent_job"])

    def test_running_moves_card_to_second_column(self) -> None:
        self.job.status = "running"
        self.job.save(update_fields=["status"])
        sync_agent_task(self.job)
        self.card.refresh_from_db()
        self.assertEqual(self.card.column_id, self.columns[1].id)

    def test_waiting_for_approval_moves_card_to_review_column(self) -> None:
        self.job.status = "waiting_for_approval"
        self.job.save(update_fields=["status"])
        sync_agent_task(self.job)
        self.card.refresh_from_db()
        self.assertEqual(self.card.column_id, self.columns[2].id)
        self.assertTrue(self.columns[2].is_review)

    def test_completed_without_a_published_result_leaves_the_card(self) -> None:
        self.job.status = "completed"
        self.job.save(update_fields=["status"])
        sync_agent_task(self.job)
        self.card.refresh_from_db()
        self.assertEqual(self.card.column_id, self.columns[0].id)

    def test_completed_and_published_moves_card_to_done_and_records_history(self) -> None:
        self.job.status = "completed"
        self.job.result_message_id = self.conversation.anchor_message_id
        self.job.save(update_fields=["status", "result_message"])

        sync_agent_task(self.job)

        self.card.refresh_from_db()
        self.assertEqual(self.card.column_id, self.columns[3].id)
        self.assertIsNotNone(self.card.completed_at)
        history = TaskHistory.objects.filter(task=self.card, kind=TaskHistory.MOVED)
        self.assert_length(history, 1)
        self.assertEqual(history[0].acting_user_id, self.profile.bot_user_id)

    def test_other_statuses_leave_the_card_where_a_person_put_it(self) -> None:
        self.card.column = self.columns[2]
        self.card.save(update_fields=["column"])
        self.job.status = "blocked"
        self.job.save(update_fields=["status"])

        sync_agent_task(self.job)

        self.card.refresh_from_db()
        self.assertEqual(self.card.column_id, self.columns[2].id)

    def test_sync_agent_task_is_a_noop_once_the_card_is_already_there(self) -> None:
        self.job.status = "running"
        self.job.save(update_fields=["status"])
        sync_agent_task(self.job)
        self.card.refresh_from_db()
        self.assertEqual(self.card.column_id, self.columns[1].id)

        history_count = TaskHistory.objects.filter(task=self.card).count()
        sync_agent_task(self.job)  # Same status, same column: nothing to do.
        self.card.refresh_from_db()
        self.assertEqual(self.card.column_id, self.columns[1].id)
        self.assertEqual(TaskHistory.objects.filter(task=self.card).count(), history_count)

    def test_sync_agent_task_is_a_noop_when_no_review_column_exists(self) -> None:
        self.columns[2].is_review = False
        self.columns[2].save(update_fields=["is_review"])
        self.job.status = "waiting_for_approval"
        self.job.save(update_fields=["status"])

        sync_agent_task(self.job)  # No column to sync to: must not raise.

        self.card.refresh_from_db()
        self.assertEqual(self.card.column_id, self.columns[0].id)

    def test_transition_calls_sync_agent_task(self) -> None:
        transition(self.job, "running")
        self.card.refresh_from_db()
        self.assertEqual(self.card.column_id, self.columns[1].id)

    def test_add_input_revives_a_waiting_job_and_moves_its_card(self) -> None:
        """`add_input` writes `job.status` directly instead of going through
        `transition()`, so it needs its own event and sync call."""
        self.conversation.audience_binding = current_audience(
            self.conversation, self.hamlet, self.profile.bot_user
        ).model_dump(mode="json")
        self.conversation.save(update_fields=["audience_binding"])
        self.job.status = "waiting_for_approval"
        self.job.save(update_fields=["status"])
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

        with self.capture_send_event_calls(expected_num_events=2) as events:
            add_input(
                self.hamlet,
                self.job.id,
                expected_version=self.job.version,
                client_key=uuid4(),
                text="Go ahead.",
            )

        job_events = [call["event"] for call in events if call["event"]["type"] == "agent_job"]
        self.assert_length(job_events, 1)
        self.assertEqual(job_events[0]["status"], "running")

        self.job.refresh_from_db()
        self.assertEqual(self.job.status, "running")
        self.card.refresh_from_db()
        self.assertEqual(self.card.column_id, self.columns[1].id)

    def test_check_agent_task_move_rejects_done_before_job_completes(self) -> None:
        with self.assertRaisesMessage(JsonableError, "Agent tasks move to done after approval."):
            check_agent_task_move(self.card, self.columns[3])

    def test_check_agent_task_move_allows_done_once_job_completes(self) -> None:
        self.job.status = "completed"
        self.job.save(update_fields=["status"])
        check_agent_task_move(self.card, self.columns[3])  # Must not raise.

    def test_agent_card_rejected_from_done_before_job_completes(self) -> None:
        with self.assertRaisesMessage(JsonableError, "Agent tasks move to done after approval."):
            do_move_task(
                user_profile=self.hamlet, task=self.card, column=self.columns[3], position=None
            )
        self.card.refresh_from_db()
        self.assertEqual(self.card.column_id, self.columns[0].id)

    def test_agent_card_allowed_to_done_once_job_completes(self) -> None:
        self.job.status = "completed"
        self.job.save(update_fields=["status"])

        do_move_task(
            user_profile=self.hamlet, task=self.card, column=self.columns[3], position=None
        )

        self.card.refresh_from_db()
        self.assertEqual(self.card.column_id, self.columns[3].id)

    def test_non_agent_card_moves_to_done_freely(self) -> None:
        self.card.agent_job = None
        self.card.save(update_fields=["agent_job"])

        do_move_task(
            user_profile=self.hamlet, task=self.card, column=self.columns[3], position=None
        )

        self.card.refresh_from_db()
        self.assertEqual(self.card.column_id, self.columns[3].id)

    def test_agent_card_still_reorders_inside_done(self) -> None:
        self.card.column = self.columns[3]
        self.card.save(update_fields=["column"])

        do_move_task(
            user_profile=self.hamlet, task=self.card, column=self.columns[3], position=500.0
        )

        self.card.refresh_from_db()
        self.assertEqual(self.card.position, 500.0)
