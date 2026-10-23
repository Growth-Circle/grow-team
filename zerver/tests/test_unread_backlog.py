from datetime import timedelta
from unittest import mock

import orjson
from django.utils.timezone import now as timezone_now

from zerver.lib.test_classes import ZulipTestCase
from zerver.lib.unread_backlog import STALE_UNREAD_DAYS
from zerver.models import Message, UserMessage


class StaleUnreadTest(ZulipTestCase):
    def send_aged_message(self, sender_name: str, recipient_name: str, age_days: int) -> int:
        message_id = self.send_personal_message(
            self.example_user(sender_name), self.example_user(recipient_name)
        )
        Message.objects.filter(id=message_id).update(
            date_sent=timezone_now() - timedelta(days=age_days)
        )
        return message_id

    def is_unread(self, user_name: str, message_id: int) -> bool:
        user_message = UserMessage.objects.get(
            user_profile=self.example_user(user_name), message_id=message_id
        )
        return not user_message.flags.read

    def test_count_only_unread_messages_older_than_the_limit(self) -> None:
        hamlet = self.example_user("hamlet")
        self.send_aged_message("cordelia", "hamlet", STALE_UNREAD_DAYS + 3)
        self.send_aged_message("cordelia", "hamlet", STALE_UNREAD_DAYS + 1)
        self.send_aged_message("cordelia", "hamlet", STALE_UNREAD_DAYS - 1)
        read_old_id = self.send_aged_message("cordelia", "hamlet", STALE_UNREAD_DAYS + 5)
        self.login_user(hamlet)
        result = self.client_post(
            "/json/messages/flags",
            {"messages": orjson.dumps([read_old_id]).decode(), "op": "add", "flag": "read"},
        )
        self.assert_json_success(result)

        result = self.client_get("/json/messages/stale_unread")
        data = self.assert_json_success(result)
        self.assertEqual(data["count"], 2)
        self.assertEqual(data["days"], STALE_UNREAD_DAYS)

    def test_count_is_zero_without_old_unread_messages(self) -> None:
        self.send_aged_message("cordelia", "hamlet", 1)
        self.login("hamlet")
        data = self.assert_json_success(self.client_get("/json/messages/stale_unread"))
        self.assertEqual(data["count"], 0)

    def test_mark_stale_unread_as_read_for_that_user_only(self) -> None:
        old_id = self.send_aged_message("cordelia", "hamlet", STALE_UNREAD_DAYS + 2)
        recent_id = self.send_aged_message("cordelia", "hamlet", 1)
        other_old_id = self.send_aged_message("cordelia", "othello", STALE_UNREAD_DAYS + 2)
        self.login("hamlet")

        data = self.assert_json_success(self.client_post("/json/messages/stale_unread"))

        self.assertEqual(data["count"], 1)
        self.assertFalse(self.is_unread("hamlet", old_id))
        self.assertTrue(self.is_unread("hamlet", recent_id))
        self.assertTrue(self.is_unread("othello", other_old_id))
        data = self.assert_json_success(self.client_get("/json/messages/stale_unread"))
        self.assertEqual(data["count"], 0)

    def test_mark_stale_unread_as_read_in_several_batches(self) -> None:
        message_ids = [
            self.send_aged_message("cordelia", "hamlet", STALE_UNREAD_DAYS + 2) for _ in range(5)
        ]
        self.login("hamlet")

        with mock.patch("zerver.lib.unread_backlog.BATCH_SIZE", 2):
            data = self.assert_json_success(self.client_post("/json/messages/stale_unread"))

        self.assertEqual(data["count"], 5)
        for message_id in message_ids:
            self.assertFalse(self.is_unread("hamlet", message_id))
