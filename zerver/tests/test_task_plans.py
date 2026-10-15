from typing import Any

from typing_extensions import override

from zerver.actions.tasks import do_create_task, do_delete_task
from zerver.lib.event_schema import (
    check_task_plan_add,
    check_task_plan_remove,
    check_task_plan_reorder,
)
from zerver.lib.tasks import get_or_create_default_board
from zerver.lib.test_classes import ZulipTestCase
from zerver.models import Task, TaskPlan, UserProfile

DAY = "2026-10-16"
NEXT_DAY = "2026-10-17"


class TaskPlanTestCase(ZulipTestCase):
    @override
    def setUp(self) -> None:
        super().setUp()
        self.hamlet = self.example_user("hamlet")
        self.iago = self.example_user("iago")
        self.board = get_or_create_default_board(self.hamlet.realm)
        self.column = self.board.columns.get(order=0)

    def create_card(self, title: str = "Write the brief", *, stream_id: int | None = None) -> Task:
        return do_create_task(
            user_profile=self.hamlet,
            board=self.board,
            column=self.column,
            title=title,
            stream_id=stream_id,
        )

    def plan_url(self, task: Task) -> str:
        return f"/json/tasks/{task.id}/plan"

    def add_to_plan(
        self, user: UserProfile, task: Task, date: str = DAY, **extra: Any
    ) -> dict[str, Any]:
        self.login_user(user)
        result = self.client_post(self.plan_url(task), {"date": date, **extra})
        return self.assert_json_success(result)

    def planned_ids(self, user: UserProfile, date: str = DAY) -> list[int]:
        self.login_user(user)
        result = self.client_get("/json/tasks/plan", {"date": date})
        return [entry["task_id"] for entry in self.assert_json_success(result)["plan"]]


class TaskPlanTest(TaskPlanTestCase):
    def test_a_member_adds_removes_and_lists_cards_for_a_day(self) -> None:
        first = self.create_card("First")
        second = self.create_card("Second")

        self.add_to_plan(self.hamlet, first)
        self.add_to_plan(self.hamlet, second)
        self.assertEqual(self.planned_ids(self.hamlet), [first.id, second.id])
        self.assertEqual(self.planned_ids(self.hamlet, NEXT_DAY), [])

        result = self.client_get("/json/tasks/plan", {"date": DAY})
        entry = self.assert_json_success(result)["plan"][0]
        self.assertEqual(entry["date"], DAY)
        self.assertEqual(entry["position"], 1000.0)

        result = self.client_delete(self.plan_url(first), {"date": DAY})
        self.assert_json_success(result)
        self.assertEqual(self.planned_ids(self.hamlet), [second.id])
        self.assertEqual(Task.objects.filter(id=first.id).count(), 1)

    def test_a_member_can_plan_any_card_they_can_see(self) -> None:
        card = self.create_card("Made by hamlet")

        self.add_to_plan(self.iago, card)

        self.assertEqual(self.planned_ids(self.iago), [card.id])

    def test_reordering_changes_the_order_of_the_day(self) -> None:
        cards = [self.create_card(f"Card {number}") for number in range(3)]
        for card in cards:
            self.add_to_plan(self.hamlet, card)

        result = self.client_patch(self.plan_url(cards[2]), {"date": DAY, "position": 500})
        self.assert_json_success(result)

        self.assertEqual(self.planned_ids(self.hamlet), [cards[2].id, cards[0].id, cards[1].id])

    def test_adding_with_a_position_places_the_card_there(self) -> None:
        first = self.create_card("First")
        second = self.create_card("Second")
        self.add_to_plan(self.hamlet, first)

        self.add_to_plan(self.hamlet, second, position=10)

        self.assertEqual(self.planned_ids(self.hamlet), [second.id, first.id])

    def test_adding_a_card_twice_keeps_one_entry_and_sends_one_event(self) -> None:
        card = self.create_card()
        self.add_to_plan(self.hamlet, card)

        with self.capture_send_event_calls(expected_num_events=0):
            self.add_to_plan(self.hamlet, card)

        self.assertEqual(TaskPlan.objects.filter(user=self.hamlet, task=card).count(), 1)

    def test_the_same_card_can_be_planned_for_two_days(self) -> None:
        card = self.create_card()

        self.add_to_plan(self.hamlet, card, DAY)
        self.add_to_plan(self.hamlet, card, NEXT_DAY)

        self.assertEqual(self.planned_ids(self.hamlet, DAY), [card.id])
        self.assertEqual(self.planned_ids(self.hamlet, NEXT_DAY), [card.id])

    def test_removing_or_reordering_a_card_that_is_not_planned_fails(self) -> None:
        card = self.create_card()
        self.login_user(self.hamlet)

        result = self.client_delete(self.plan_url(card), {"date": DAY})
        self.assert_json_error(result, "Task is not in this plan.")
        result = self.client_patch(self.plan_url(card), {"date": DAY, "position": 1})
        self.assert_json_error(result, "Task is not in this plan.")

    def test_bad_input_changes_nothing(self) -> None:
        card = self.create_card()
        self.login_user(self.hamlet)

        result = self.client_post(self.plan_url(card), {"date": "tomorrow"})
        self.assert_json_error_contains(result, "date")
        result = self.client_post(self.plan_url(card), {})
        self.assert_json_error_contains(result, "date")
        result = self.client_post(self.plan_url(card), {"date": DAY, "position": "high"})
        self.assert_json_error_contains(result, "position")
        result = self.client_post("/json/tasks/99999/plan", {"date": DAY})
        self.assert_json_error(result, "Task does not exist.")

        self.assertFalse(TaskPlan.objects.exists())


class TaskPlanPrivacyTest(TaskPlanTestCase):
    def test_other_members_never_see_a_plan(self) -> None:
        card = self.create_card()
        self.add_to_plan(self.hamlet, card)

        for user in (self.iago, self.example_user("cordelia")):
            self.assertEqual(self.planned_ids(user), [])

    def test_a_member_cannot_remove_or_reorder_someone_elses_entry(self) -> None:
        card = self.create_card()
        self.add_to_plan(self.hamlet, card)
        self.login_user(self.iago)

        result = self.client_delete(self.plan_url(card), {"date": DAY})
        self.assert_json_error(result, "Task is not in this plan.")
        result = self.client_patch(self.plan_url(card), {"date": DAY, "position": 1})
        self.assert_json_error(result, "Task is not in this plan.")

        self.assertEqual(self.planned_ids(self.hamlet), [card.id])

    def test_events_reach_only_the_owner(self) -> None:
        card = self.create_card()
        self.login_user(self.hamlet)

        with self.capture_send_event_calls(expected_num_events=1) as events:
            self.assert_json_success(self.client_post(self.plan_url(card), {"date": DAY}))
        self.assertEqual(events[0]["users"], [self.hamlet.id])
        add_event: dict[str, Any] = events[0]["event"]
        check_task_plan_add("event", {**add_event, "id": 0})
        self.assertEqual(add_event["task_id"], card.id)
        self.assertEqual(add_event["date"], DAY)

        with self.capture_send_event_calls(expected_num_events=1) as events:
            self.assert_json_success(
                self.client_patch(self.plan_url(card), {"date": DAY, "position": 5})
            )
        self.assertEqual(events[0]["users"], [self.hamlet.id])
        check_task_plan_reorder("event", {**events[0]["event"], "id": 0})
        self.assertEqual(events[0]["event"]["position"], 5.0)

        with self.capture_send_event_calls(expected_num_events=1) as events:
            self.assert_json_success(self.client_delete(self.plan_url(card), {"date": DAY}))
        self.assertEqual(events[0]["users"], [self.hamlet.id])
        check_task_plan_remove("event", {**events[0]["event"], "id": 0})


class TaskPlanAccessTest(TaskPlanTestCase):
    def private_card(self) -> Task:
        private = self.make_stream("private plans", invite_only=True)
        self.subscribe(self.hamlet, private.name)
        return self.create_card("Private card", stream_id=private.id)

    def test_a_card_the_member_cannot_read_cannot_be_planned(self) -> None:
        card = self.private_card()
        self.login_user(self.iago)

        result = self.client_post(self.plan_url(card), {"date": DAY})

        self.assert_json_error(result, "Task does not exist.")
        self.assertFalse(TaskPlan.objects.exists())

    def test_a_card_that_becomes_unreadable_leaves_the_plan(self) -> None:
        card = self.private_card()
        assert card.stream is not None
        self.subscribe(self.iago, card.stream.name)
        self.add_to_plan(self.iago, card)
        self.assertEqual(self.planned_ids(self.iago), [card.id])

        self.unsubscribe(self.iago, card.stream.name)

        self.assertEqual(self.planned_ids(self.iago), [])

    def test_a_deleted_card_leaves_the_plan(self) -> None:
        card = self.create_card()
        keep = self.create_card("Keep")
        self.add_to_plan(self.hamlet, card)
        self.add_to_plan(self.iago, card)
        self.add_to_plan(self.hamlet, keep)

        do_delete_task(user_profile=self.hamlet, task=card)

        self.assertEqual(self.planned_ids(self.hamlet), [keep.id])
        self.assertEqual(self.planned_ids(self.iago), [])
        self.assertEqual(list(TaskPlan.objects.values_list("task_id", flat=True)), [keep.id])

    def test_deleting_a_member_removes_their_plan(self) -> None:
        card = self.create_card()
        self.add_to_plan(self.iago, card)

        self.iago.delete()

        self.assertFalse(TaskPlan.objects.exists())
