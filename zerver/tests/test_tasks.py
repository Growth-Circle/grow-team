import orjson
from django.utils.timezone import now as timezone_now
from typing_extensions import override
from zerver.lib.test_classes import ZulipTestCase

from zerver.actions.tasks import do_create_task
from zerver.lib.tasks import (
    get_or_create_default_board,
    hidden_done_task_ids,
    task_event_audience,
    visible_tasks,
)
from zerver.models import Task, TaskBoard, TaskBoardColumn, TaskHistory


class TaskBoardTestCase(ZulipTestCase):
    @override
    def setUp(self) -> None:
        super().setUp()
        self.hamlet = self.example_user("hamlet")
        self.board = get_or_create_default_board(self.hamlet.realm)
        self.columns = list(self.board.columns.all())

    def column_named(self, name: str) -> TaskBoardColumn:
        return next(column for column in self.columns if column.name == name)

    def create_card(
        self, *, title: str = "Write the regression test", column: TaskBoardColumn | None = None
    ) -> Task:
        return do_create_task(
            user_profile=self.hamlet,
            board=self.board,
            column=column or self.columns[0],
            title=title,
        )


class TaskBoardFetchTest(TaskBoardTestCase):
    def test_default_board_is_created_once(self) -> None:
        self.login_user(self.hamlet)

        result = self.client_get("/json/tasks")
        response = self.assert_json_success(result)

        self.assertEqual(response["board"]["id"], self.board.id)
        self.assert_length(response["columns"], 4)
        self.assert_length(response["tasks"], 0)
        self.assertEqual(response["columns"][1]["work_limit"], 3)
        self.assertEqual(response["columns"][3]["done_window_days"], 7)

        # A second fetch reuses the same board rather than making another.
        self.client_get("/json/tasks")
        self.assertEqual(self.hamlet.realm.taskboard_set.count(), 1)

    def test_unknown_board_is_rejected(self) -> None:
        self.login_user(self.hamlet)
        result = self.client_get("/json/tasks", {"board_id": self.board.id + 999})
        self.assert_json_error(result, "Task board does not exist.")


class TaskCreateTest(TaskBoardTestCase):
    def test_create_card_records_origin_and_history(self) -> None:
        self.login_user(self.hamlet)
        stream = self.make_stream("release-plan")
        self.subscribe(self.hamlet, "release-plan")
        message_id = self.send_stream_message(self.hamlet, "release-plan", topic_name="cut-off")

        result = self.client_post(
            "/json/tasks",
            {
                "title": "Lock the October feature list",
                "body": "Decide the list before the cut-off.",
                "column_id": self.columns[0].id,
                "stream_id": stream.id,
                "topic": "cut-off",
                "origin_message_id": message_id,
                "labels": orjson.dumps(["ops"]).decode(),
                "checklist": orjson.dumps([{"text": "Ask product", "done": False}]).decode(),
            },
        )
        response = self.assert_json_success(result)
        self.assertEqual(response["display_id"], "GT-1")

        task = Task.objects.get(id=response["task_id"])
        self.assertEqual(task.stream_id, stream.id)
        self.assertEqual(task.topic, "cut-off")
        self.assertEqual(task.origin_message_id, message_id)
        self.assertEqual(task.labels, ["ops"])
        self.assertEqual(task.checklist, [{"text": "Ask product", "done": False}])

        history = TaskHistory.objects.filter(task=task)
        self.assert_length(history, 1)
        self.assertEqual(history[0].kind, TaskHistory.CREATED)

    def test_counter_never_repeats(self) -> None:
        first = self.create_card(title="First")
        second = self.create_card(title="Second")
        self.assertEqual(first.display_id, "GT-1")
        self.assertEqual(second.display_id, "GT-2")

        first.delete()
        third = self.create_card(title="Third")
        self.assertEqual(third.display_id, "GT-3")

    def test_origin_channel_the_user_cannot_read_is_rejected(self) -> None:
        self.login_user(self.hamlet)
        private_stream = self.make_stream("secret-plan", invite_only=True)
        self.subscribe(self.example_user("cordelia"), "secret-plan")

        result = self.client_post(
            "/json/tasks",
            {
                "title": "Read the private channel",
                "column_id": self.columns[0].id,
                "stream_id": private_stream.id,
            },
        )
        self.assert_json_error(result, "Invalid channel ID")

    def test_unknown_column_is_rejected(self) -> None:
        self.login_user(self.hamlet)
        result = self.client_post(
            "/json/tasks",
            {"title": "Card without a column", "column_id": self.columns[-1].id + 999},
        )
        self.assert_json_error(result, "Task board column does not exist.")

    def test_label_limits(self) -> None:
        self.login_user(self.hamlet)
        result = self.client_post(
            "/json/tasks",
            {
                "title": "Too many labels",
                "column_id": self.columns[0].id,
                "labels": orjson.dumps([f"label-{index}" for index in range(11)]).decode(),
            },
        )
        self.assert_json_error(result, "Too many labels.")

        result = self.client_post(
            "/json/tasks",
            {
                "title": "Label too long",
                "column_id": self.columns[0].id,
                "labels": orjson.dumps(["x" * 41]).decode(),
            },
        )
        self.assert_json_error(result, "Label is too long.")


class TaskAccessTest(TaskBoardTestCase):
    def test_card_from_a_private_channel_stays_hidden(self) -> None:
        cordelia = self.example_user("cordelia")
        private_stream = self.make_stream("secret-plan", invite_only=True)
        self.subscribe(cordelia, "secret-plan")

        card = do_create_task(
            user_profile=cordelia,
            board=self.board,
            column=self.columns[0],
            title="Card only Cordelia may read",
            stream_id=private_stream.id,
            topic="plan",
        )
        manual_card = self.create_card(title="Card with no origin channel")

        visible_to_cordelia = {task.id for task in visible_tasks(cordelia, self.board)}
        self.assertEqual(visible_to_cordelia, {card.id, manual_card.id})

        visible_to_hamlet = {task.id for task in visible_tasks(self.hamlet, self.board)}
        self.assertEqual(visible_to_hamlet, {manual_card.id})

        self.login_user(self.hamlet)
        result = self.client_patch(f"/json/tasks/{card.id}", {"title": "Rename it"})
        self.assert_json_error(result, "Task does not exist.")

        result = self.client_get(f"/json/tasks/{card.id}/history")
        self.assert_json_error(result, "Task does not exist.")

    def test_event_audience_follows_the_origin_channel(self) -> None:
        cordelia = self.example_user("cordelia")
        private_stream = self.make_stream("secret-plan", invite_only=True)
        self.subscribe(cordelia, "secret-plan")

        card = do_create_task(
            user_profile=cordelia,
            board=self.board,
            column=self.columns[0],
            title="Card only Cordelia may read",
            stream_id=private_stream.id,
        )
        self.assertEqual(task_event_audience(cordelia.realm, card), [cordelia.id])

        manual_card = self.create_card(title="Manual card")
        self.assertIn(self.hamlet.id, task_event_audience(self.hamlet.realm, manual_card))


class TaskUpdateTest(TaskBoardTestCase):
    def test_move_records_history_and_marks_done(self) -> None:
        self.login_user(self.hamlet)
        card = self.create_card()
        done_column = self.column_named("Done")

        result = self.client_patch(f"/json/tasks/{card.id}", {"column_id": done_column.id})
        self.assert_json_success(result)

        card.refresh_from_db()
        self.assertEqual(card.column_id, done_column.id)
        self.assertIsNotNone(card.completed_at)

        moves = TaskHistory.objects.filter(task=card, kind=TaskHistory.MOVED)
        self.assert_length(moves, 1)
        self.assertEqual(moves[0].extra_data["to_column_name"], "Done")

        # Moving back out of the done column clears the completion time.
        result = self.client_patch(f"/json/tasks/{card.id}", {"column_id": self.columns[0].id})
        self.assert_json_success(result)
        card.refresh_from_db()
        self.assertIsNone(card.completed_at)

    def test_reorder_inside_a_column_writes_no_move_history(self) -> None:
        self.login_user(self.hamlet)
        card = self.create_card()

        result = self.client_patch(f"/json/tasks/{card.id}", {"position": "500.0"})
        self.assert_json_success(result)

        card.refresh_from_db()
        self.assertEqual(card.position, 500.0)
        self.assert_length(TaskHistory.objects.filter(task=card, kind=TaskHistory.MOVED), 0)

    def test_assign_and_unassign(self) -> None:
        self.login_user(self.hamlet)
        cordelia = self.example_user("cordelia")
        card = self.create_card()

        result = self.client_patch(f"/json/tasks/{card.id}", {"assignee_id": cordelia.id})
        self.assert_json_success(result)
        card.refresh_from_db()
        self.assertEqual(card.assignee_id, cordelia.id)
        self.assert_length(TaskHistory.objects.filter(task=card, kind=TaskHistory.ASSIGNED), 1)

        result = self.client_patch(f"/json/tasks/{card.id}", {"clear_assignee": "true"})
        self.assert_json_success(result)
        card.refresh_from_db()
        self.assertIsNone(card.assignee_id)

    def test_edit_fields(self) -> None:
        self.login_user(self.hamlet)
        card = self.create_card()

        result = self.client_patch(
            f"/json/tasks/{card.id}",
            {
                "title": "Renamed card",
                "body": "New description.",
                "blocked": "true",
                "due_at": "1790000000",
            },
        )
        self.assert_json_success(result)

        card.refresh_from_db()
        self.assertEqual(card.title, "Renamed card")
        self.assertEqual(card.body, "New description.")
        self.assertTrue(card.blocked)
        self.assertIsNotNone(card.due_at)

        result = self.client_patch(f"/json/tasks/{card.id}", {"clear_due_at": "true"})
        self.assert_json_success(result)
        card.refresh_from_db()
        self.assertIsNone(card.due_at)

    def test_empty_update_writes_no_history(self) -> None:
        self.login_user(self.hamlet)
        card = self.create_card()

        result = self.client_patch(f"/json/tasks/{card.id}", {})
        self.assert_json_success(result)
        self.assert_length(TaskHistory.objects.filter(task=card), 1)

    def test_history_endpoint(self) -> None:
        self.login_user(self.hamlet)
        card = self.create_card()
        self.client_patch(f"/json/tasks/{card.id}", {"title": "Renamed card"})

        result = self.client_get(f"/json/tasks/{card.id}/history")
        response = self.assert_json_success(result)
        self.assertEqual(
            [entry["kind"] for entry in response["history"]],
            [TaskHistory.EDITED, TaskHistory.CREATED],
        )

    def test_delete_card(self) -> None:
        self.login_user(self.hamlet)
        card = self.create_card()

        result = self.client_delete(f"/json/tasks/{card.id}")
        self.assert_json_success(result)
        self.assertFalse(Task.objects.filter(id=card.id).exists())

        result = self.client_delete(f"/json/tasks/{card.id}")
        self.assert_json_error(result, "Task does not exist.")


class TaskBoardRenameTest(TaskBoardTestCase):
    def test_rename_board(self) -> None:
        self.login_user(self.hamlet)
        result = self.client_patch(
            f"/json/task_boards/{self.board.id}", {"name": "October release"}
        )
        self.assert_json_success(result)

        self.board.refresh_from_db()
        self.assertEqual(self.board.name, "October release")

        result = self.client_patch(f"/json/task_boards/{self.board.id + 999}", {"name": "Nope"})
        self.assert_json_error(result, "Task board does not exist.")


class DoneWindowTest(TaskBoardTestCase):
    def test_old_done_cards_are_folded_away(self) -> None:
        done_column = self.column_named("Done")
        recent = self.create_card(title="Finished today", column=done_column)
        old = self.create_card(title="Finished long ago", column=done_column)

        recent.completed_at = timezone_now()
        recent.save(update_fields=["completed_at"])
        old.completed_at = timezone_now().replace(year=timezone_now().year - 1)
        old.save(update_fields=["completed_at"])

        folded = hidden_done_task_ids(self.columns, [recent, old])
        self.assertEqual(folded, {old.id})

    def test_column_without_a_window_folds_nothing(self) -> None:
        card = self.create_card()
        self.assertEqual(hidden_done_task_ids([self.columns[0]], [card]), set())


class WorkCountsTest(TaskBoardTestCase):
    def test_counts_before_any_board_exists(self) -> None:
        self.board.delete()
        self.login_user(self.hamlet)

        result = self.client_get("/json/tasks/counts")
        counts = self.assert_json_success(result)["counts"]
        self.assertEqual(
            counts,
            {"task_board": 0, "my_tasks": 0, "awaiting_my_review": 0, "agent_running": 0},
        )
        # Reading the counts does not create a board.
        self.assertFalse(TaskBoard.objects.filter(realm=self.hamlet.realm).exists())

    def test_counts_follow_assignee_reviewer_and_columns(self) -> None:
        cordelia = self.example_user("cordelia")
        review_column = self.column_named("Awaiting review")
        self.assertTrue(review_column.is_review)

        mine = self.create_card(title="Mine")
        mine.assignee = self.hamlet
        mine.save(update_fields=["assignee"])

        to_review = self.create_card(title="Review this", column=review_column)
        to_review.reviewer = self.hamlet
        to_review.save(update_fields=["reviewer"])

        # A reviewer outside the review column is not waiting on anyone yet.
        not_yet = self.create_card(title="Not ready")
        not_yet.reviewer = self.hamlet
        not_yet.save(update_fields=["reviewer"])

        finished = self.create_card(title="Finished", column=self.column_named("Done"))
        finished.assignee = self.hamlet
        finished.completed_at = timezone_now()
        finished.save(update_fields=["assignee", "completed_at"])

        someone_else = self.create_card(title="Cordelia's")
        someone_else.assignee = cordelia
        someone_else.save(update_fields=["assignee"])

        self.login_user(self.hamlet)
        counts = self.assert_json_success(self.client_get("/json/tasks/counts"))["counts"]
        self.assertEqual(counts["task_board"], 5)
        self.assertEqual(counts["my_tasks"], 1)
        self.assertEqual(counts["awaiting_my_review"], 1)
        self.assertEqual(counts["agent_running"], 0)

    def test_counts_skip_cards_the_user_cannot_read(self) -> None:
        cordelia = self.example_user("cordelia")
        private_stream = self.make_stream("secret-plan", invite_only=True)
        self.subscribe(cordelia, "secret-plan")
        do_create_task(
            user_profile=cordelia,
            board=self.board,
            column=self.columns[0],
            title="Private card",
            stream_id=private_stream.id,
            assignee=self.hamlet,
        )

        self.login_user(self.hamlet)
        counts = self.assert_json_success(self.client_get("/json/tasks/counts"))["counts"]
        self.assertEqual(counts["task_board"], 0)
        self.assertEqual(counts["my_tasks"], 0)

    def test_set_and_clear_reviewer(self) -> None:
        self.login_user(self.hamlet)
        cordelia = self.example_user("cordelia")
        card = self.create_card()

        result = self.client_patch(f"/json/tasks/{card.id}", {"reviewer_id": cordelia.id})
        self.assert_json_success(result)
        card.refresh_from_db()
        self.assertEqual(card.reviewer_id, cordelia.id)

        result = self.client_patch(f"/json/tasks/{card.id}", {"clear_reviewer": "true"})
        self.assert_json_success(result)
        card.refresh_from_db()
        self.assertIsNone(card.reviewer_id)

    def test_create_with_reviewer(self) -> None:
        self.login_user(self.hamlet)
        cordelia = self.example_user("cordelia")
        result = self.client_post(
            "/json/tasks",
            {"title": "Needs review", "column_id": self.columns[0].id, "reviewer_id": cordelia.id},
        )
        task = Task.objects.get(id=self.assert_json_success(result)["task_id"])
        self.assertEqual(task.reviewer_id, cordelia.id)
