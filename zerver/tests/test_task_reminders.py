from datetime import datetime, timedelta, timezone
from io import StringIO
from unittest import mock

import time_machine
from django.conf import settings
from django.core.management import call_command
from typing_extensions import override

from zerver.actions.create_user import do_create_user
from zerver.actions.tasks import do_create_task
from zerver.actions.user_settings import do_change_user_setting
from zerver.actions.users import do_deactivate_user
from zerver.lib.task_reminders import MAX_LISTED_CARDS, reminders_to_send
from zerver.lib.tasks import get_or_create_default_board
from zerver.lib.test_classes import ZulipTestCase
from zerver.lib.test_helpers import most_recent_message
from zerver.models import AgentRealmSettings, Message, Task, TaskReminderLog, UserProfile
from zerver.models.realms import get_realm
from zerver.models.users import get_system_bot

# 07:00 on 16 Oct 2026 in Jakarta (UTC+7).
AT_SEVEN_JAKARTA = datetime(2026, 10, 16, 0, 0, tzinfo=timezone.utc)
TODAY_JAKARTA = AT_SEVEN_JAKARTA.date()


def jakarta(day_offset: int, hour: int, minute: int = 0) -> datetime:
    """A moment on the Jakarta calendar, `day_offset` days from 16 Oct."""
    local_midnight = datetime(2026, 10, 16, tzinfo=timezone(timedelta(hours=7)))
    return local_midnight + timedelta(days=day_offset, hours=hour, minutes=minute)


class TaskReminderTestCase(ZulipTestCase):
    @override
    def setUp(self) -> None:
        super().setUp()
        self.hamlet = self.example_user("hamlet")
        self.realm = self.hamlet.realm
        AgentRealmSettings.objects.update_or_create(
            realm=self.realm, defaults={"timezone": "Asia/Jakarta"}
        )
        self.board = get_or_create_default_board(self.realm)
        self.columns = list(self.board.columns.all())

    def create_card(
        self,
        title: str,
        due_at: datetime | None,
        *,
        assignee: UserProfile | None = None,
        stream_id: int | None = None,
    ) -> Task:
        return do_create_task(
            user_profile=self.hamlet,
            board=self.board,
            column=self.columns[0],
            title=title,
            assignee=assignee or self.hamlet,
            due_at=due_at,
            stream_id=stream_id,
        )

    def run_reminders(self, *args: str) -> str:
        out = StringIO()
        call_command("send_task_reminders", *args, stdout=out)
        return out.getvalue().strip()

    def reminder_count(self, user: UserProfile) -> int:
        return Message.objects.filter(
            realm=user.realm,
            sender=get_system_bot(settings.NOTIFICATION_BOT, user.realm_id),
            content__contains="due today or overdue",
            usermessage__user_profile=user,
        ).count()


class TaskReminderContentTest(TaskReminderTestCase):
    def test_lists_overdue_and_due_today_cards_with_links(self) -> None:
        overdue = self.create_card("Send the invoice", jakarta(-2, 10))
        due_today = self.create_card("Review the brief", jakarta(0, 18))
        self.create_card("Plan next week", jakarta(1, 0, 30))
        self.create_card("Someday", None)
        finished = self.create_card("Already done", jakarta(-1, 9))
        Task.objects.filter(id=finished.id).update(completed_at=jakarta(-1, 12))
        self.create_card("Someone else card", jakarta(0, 9), assignee=self.example_user("iago"))

        with time_machine.travel(AT_SEVEN_JAKARTA, tick=False):
            self.assertEqual(self.run_reminders(), "Sent 2 reminder(s).")

        message = most_recent_message(self.hamlet)
        self.assertEqual(message.sender.email, settings.NOTIFICATION_BOT)
        content = message.content
        self.assertIn("due today or overdue", content)
        self.assertIn(f"GT-{overdue.counter} Send the invoice", content)
        self.assertIn("Days overdue: 2 (due Oct 14)", content)
        self.assertIn(f"GT-{due_today.counter} Review the brief", content)
        self.assertIn("Due today", content)
        self.assertLess(content.index("Send the invoice"), content.index("Review the brief"))
        self.assertIn(f"{self.realm.url}/#tasks/{overdue.id})", content)
        self.assertIn(f"{self.realm.url}/#tasks/{due_today.id})", content)
        self.assertIn(f"{self.realm.url}/#tasks/mine/list", content)
        for left_out in ("Plan next week", "Someday", "Already done"):
            self.assertNotIn(left_out, content)

    def test_a_title_cannot_mention_anyone(self) -> None:
        self.create_card("Ask @**all** about `this`", jakarta(0, 9))

        with time_machine.travel(AT_SEVEN_JAKARTA, tick=False):
            self.run_reminders()

        content = most_recent_message(self.hamlet).content
        self.assertIn("`GT-", content)
        self.assertIn("Ask @**all** about 'this'`", content)

    def test_a_long_list_is_cut_with_a_count_of_the_rest(self) -> None:
        for number in range(MAX_LISTED_CARDS + 2):
            self.create_card(f"Card {number:02}", jakarta(-1, 9, number))

        with time_machine.travel(AT_SEVEN_JAKARTA, tick=False):
            self.run_reminders()

        content = most_recent_message(self.hamlet).content
        self.assertIn("Card 09", content)
        self.assertNotIn("Card 10", content)
        self.assertIn("And 2 more.", content)

    def test_the_reminder_is_written_in_the_language_of_the_member(self) -> None:
        do_change_user_setting(self.hamlet, "default_language", "id", acting_user=None)
        self.create_card("Kirim invoice", jakarta(-2, 10))
        self.create_card("Cek brief", jakarta(0, 18))
        for number in range(MAX_LISTED_CARDS - 2):
            self.create_card(f"Card {number:02}", jakarta(-1, 9, number))
        for number in range(2):
            self.create_card(f"Late {number}", jakarta(0, 19, number))

        with time_machine.travel(AT_SEVEN_JAKARTA, tick=False):
            self.run_reminders()

        content = most_recent_message(self.hamlet).content
        self.assertIn("Selamat pagi. Card ini jatuh tempo hari ini atau sudah terlambat:", content)
        self.assertIn("Terlambat 2 hari", content)
        self.assertIn("Hari ini", content)
        self.assertIn("[Buka card](", content)
        self.assertIn("Dan 2 lainnya.", content)
        self.assertIn("[Buka Tugas saya](", content)
        self.assertNotIn("Days overdue", content)


class TaskReminderScheduleTest(TaskReminderTestCase):
    def test_nothing_goes_out_before_seven_on_the_workspace_clock(self) -> None:
        self.create_card("Send the invoice", jakarta(-1, 10))

        with time_machine.travel(AT_SEVEN_JAKARTA - timedelta(minutes=1), tick=False):
            self.assertEqual(self.run_reminders(), "Sent 0 reminder(s).")
        self.assertEqual(self.reminder_count(self.hamlet), 0)

        with time_machine.travel(AT_SEVEN_JAKARTA, tick=False):
            self.assertEqual(self.run_reminders(), "Sent 1 reminder(s).")
        self.assertEqual(self.reminder_count(self.hamlet), 1)

    def test_the_window_is_seven_to_ten_on_the_workspace_clock(self) -> None:
        self.create_card("Send the invoice", jakarta(-1, 10))

        for hour, minute, sent in [(6, 59, 0), (9, 59, 1)]:
            with self.subTest(hour=hour, minute=minute):
                TaskReminderLog.objects.all().delete()
                with time_machine.travel(jakarta(0, hour, minute), tick=False):
                    self.assertEqual(self.run_reminders(), f"Sent {sent} reminder(s).")
        TaskReminderLog.objects.all().delete()
        with time_machine.travel(jakarta(0, 10), tick=False):
            self.assertEqual(self.run_reminders(), "Sent 0 reminder(s).")
        TaskReminderLog.objects.all().delete()
        with time_machine.travel(jakarta(0, 7), tick=False):
            self.assertEqual(self.run_reminders(), "Sent 1 reminder(s).")

    def test_a_card_that_falls_due_after_ten_waits_for_the_next_morning(self) -> None:
        with time_machine.travel(jakarta(0, 11), tick=False):
            self.create_card("Due this afternoon", jakarta(0, 15))
            self.assertEqual(self.run_reminders(), "Sent 0 reminder(s).")
        self.assertEqual(self.reminder_count(self.hamlet), 0)

        with time_machine.travel(jakarta(1, 7), tick=False):
            self.assertEqual(self.run_reminders(), "Sent 1 reminder(s).")

    def test_a_timer_that_resumes_inside_the_window_still_sends_once(self) -> None:
        self.create_card("Send the invoice", jakarta(-1, 10))

        with time_machine.travel(jakarta(0, 8, 30), tick=False):
            self.assertEqual(self.run_reminders(), "Sent 1 reminder(s).")
            self.assertEqual(self.run_reminders(), "Sent 0 reminder(s).")
        self.assertEqual(self.reminder_count(self.hamlet), 1)

    def test_the_workspace_timezone_decides_the_hour_and_the_day(self) -> None:
        AgentRealmSettings.objects.filter(realm=self.realm).update(timezone="America/Los_Angeles")
        # 23:30 on 16 Oct in Los Angeles, which is already 17 Oct in Jakarta.
        self.create_card("Late today", datetime(2026, 10, 17, 6, 30, tzinfo=timezone.utc))
        # Just past midnight of 17 Oct in Los Angeles: due tomorrow.
        self.create_card("Next morning", datetime(2026, 10, 17, 7, 30, tzinfo=timezone.utc))
        seven_la = datetime(2026, 10, 16, 14, 0, tzinfo=timezone.utc)

        with time_machine.travel(seven_la - timedelta(minutes=1), tick=False):
            self.assertEqual(self.run_reminders(), "Sent 0 reminder(s).")
        with time_machine.travel(seven_la, tick=False):
            self.assertEqual(self.run_reminders(), "Sent 1 reminder(s).")

        content = most_recent_message(self.hamlet).content
        self.assertIn("Late today", content)
        self.assertIn("Due today", content)
        self.assertNotIn("Next morning", content)

    def test_a_second_run_on_the_same_day_sends_nothing(self) -> None:
        self.create_card("Send the invoice", jakarta(-1, 10))

        with time_machine.travel(AT_SEVEN_JAKARTA, tick=False):
            self.assertEqual(self.run_reminders(), "Sent 1 reminder(s).")
        with time_machine.travel(AT_SEVEN_JAKARTA + timedelta(hours=2), tick=False):
            self.create_card("A new card for today", jakarta(0, 20))
            self.assertEqual(self.run_reminders(), "Sent 0 reminder(s).")

        self.assertEqual(self.reminder_count(self.hamlet), 1)
        self.assertEqual(
            list(TaskReminderLog.objects.values_list("user_id", "date")),
            [(self.hamlet.id, TODAY_JAKARTA)],
        )

    def test_the_next_day_reminds_again(self) -> None:
        self.create_card("Send the invoice", jakarta(-1, 10))

        with time_machine.travel(AT_SEVEN_JAKARTA, tick=False):
            self.run_reminders()
        with time_machine.travel(AT_SEVEN_JAKARTA + timedelta(days=1), tick=False):
            self.assertEqual(self.run_reminders(), "Sent 1 reminder(s).")

        self.assertEqual(self.reminder_count(self.hamlet), 2)
        self.assertIn("Days overdue: 2 (due Oct 15)", most_recent_message(self.hamlet).content)

    def test_nobody_is_messaged_when_nothing_is_due(self) -> None:
        self.create_card("Next week", jakarta(7, 10))
        self.create_card("No deadline", None)

        with time_machine.travel(AT_SEVEN_JAKARTA, tick=False):
            self.assertEqual(self.run_reminders(), "Sent 0 reminder(s).")

        self.assertEqual(self.reminder_count(self.hamlet), 0)
        self.assertFalse(TaskReminderLog.objects.exists())

    def test_a_failed_send_leaves_the_day_open(self) -> None:
        self.create_card("Send the invoice", jakarta(-1, 10))

        with time_machine.travel(AT_SEVEN_JAKARTA, tick=False):
            with (
                mock.patch(
                    "zerver.lib.task_reminders.internal_send_private_message",
                    side_effect=RuntimeError("queue is down"),
                ),
                self.assertRaisesRegex(RuntimeError, "queue is down"),
            ):
                self.run_reminders()
            self.assertFalse(TaskReminderLog.objects.exists())

            self.assertEqual(self.run_reminders(), "Sent 1 reminder(s).")


class TaskReminderRecipientsTest(TaskReminderTestCase):
    def test_guests_bots_and_deactivated_members_are_skipped(self) -> None:
        guest = self.example_user("polonius")
        bot = do_create_user(
            "reminder-bot@zulip.com",
            "password",
            self.realm,
            "Reminder bot",
            bot_type=UserProfile.DEFAULT_BOT,
            bot_owner=self.hamlet,
            acting_user=None,
        )
        gone = self.example_user("othello")
        for assignee in (guest, bot, gone):
            self.create_card(f"For {assignee.full_name}", jakarta(-1, 10), assignee=assignee)
        do_deactivate_user(gone, acting_user=None)

        with time_machine.travel(AT_SEVEN_JAKARTA, tick=False):
            self.assertEqual(reminders_to_send(self.realm), [])
            self.assertEqual(self.run_reminders(), "Sent 0 reminder(s).")

    def test_a_card_from_a_channel_the_member_cannot_read_stays_out(self) -> None:
        cordelia = self.example_user("cordelia")
        private = self.make_stream("private reminders", invite_only=True)
        self.subscribe(self.hamlet, private.name)
        secret = self.create_card(
            "Secret plan", jakarta(-1, 10), assignee=cordelia, stream_id=private.id
        )
        self.create_card("Open plan", jakarta(0, 10), assignee=cordelia)

        with time_machine.travel(AT_SEVEN_JAKARTA, tick=False):
            self.run_reminders()

        content = most_recent_message(cordelia).content
        self.assertIn("Open plan", content)
        self.assertNotIn("Secret plan", content)
        self.assertNotIn(f"#tasks/{secret.id})", content)

    def test_each_member_gets_their_own_list(self) -> None:
        iago = self.example_user("iago")
        self.create_card("Hamlet card", jakarta(0, 10))
        self.create_card("Iago card", jakarta(0, 11), assignee=iago)

        with time_machine.travel(AT_SEVEN_JAKARTA, tick=False):
            self.assertEqual(self.run_reminders(), "Sent 2 reminder(s).")

        self.assertIn("Hamlet card", most_recent_message(self.hamlet).content)
        self.assertNotIn("Iago card", most_recent_message(self.hamlet).content)
        self.assertIn("Iago card", most_recent_message(iago).content)
        self.assertNotIn("Hamlet card", most_recent_message(iago).content)


class TaskReminderCommandTest(TaskReminderTestCase):
    def test_dry_run_prints_the_reminder_and_sends_nothing(self) -> None:
        self.create_card("Send the invoice", jakarta(-1, 10))
        message_count = Message.objects.count()

        with time_machine.travel(AT_SEVEN_JAKARTA, tick=False):
            output = self.run_reminders("--dry-run")

        self.assertIn(self.hamlet.delivery_email, output)
        self.assertIn("would get 1 card(s)", output)
        self.assertIn("Send the invoice", output)
        self.assertEqual(Message.objects.count(), message_count)
        self.assertFalse(TaskReminderLog.objects.exists())

        with time_machine.travel(AT_SEVEN_JAKARTA, tick=False):
            self.assertEqual(self.run_reminders(), "Sent 1 reminder(s).")

    def test_realm_flag_limits_the_run_to_one_organization(self) -> None:
        lear = get_realm("lear")
        lear_user = self.lear_user("cordelia")
        lear_board = get_or_create_default_board(lear)
        do_create_task(
            user_profile=lear_user,
            board=lear_board,
            column=lear_board.columns.get(order=0),
            title="Lear card",
            assignee=lear_user,
            due_at=jakarta(-1, 10),
        )
        self.create_card("Zulip card", jakarta(-1, 10))

        with time_machine.travel(AT_SEVEN_JAKARTA, tick=False):
            self.assertEqual(self.run_reminders("--realm", "zulip"), "Sent 1 reminder(s).")

        self.assertEqual(self.reminder_count(self.hamlet), 1)
        self.assertEqual(self.reminder_count(lear_user), 0)

        with time_machine.travel(AT_SEVEN_JAKARTA, tick=False):
            self.assertEqual(self.run_reminders(), "Sent 1 reminder(s).")
        self.assertEqual(self.reminder_count(lear_user), 1)
