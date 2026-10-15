"""Run every 15 minutes. Once the clock of a workspace reaches 07:00, send
each member who has open cards that are overdue or due today one direct
message that lists them."""

from argparse import ArgumentParser
from typing import Any

from typing_extensions import override

from zerver.lib.management import ZulipBaseCommand
from zerver.lib.task_reminders import reminder_content, reminders_to_send, send_task_reminder
from zerver.models import Realm


class Command(ZulipBaseCommand):
    help = "Send each member a morning reminder of the cards due today or overdue."

    @override
    def add_arguments(self, parser: ArgumentParser) -> None:
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Print who would get which reminder and send nothing.",
        )
        self.add_realm_args(parser, help="Only remind the members of this organization.")

    @override
    def handle(self, *args: Any, **options: Any) -> None:
        realm = self.get_realm(options)
        realms = [realm] if realm is not None else Realm.objects.filter(deactivated=False)

        sent = 0
        for realm in realms:
            for reminder in reminders_to_send(realm):
                if options["dry_run"]:
                    self.stdout.write(
                        f"{realm.string_id}: {reminder.user.delivery_email} "
                        f"would get {len(reminder.cards)} card(s):\n{reminder_content(reminder)}\n"
                    )
                elif send_task_reminder(reminder):
                    sent += 1
        if not options["dry_run"]:
            self.stdout.write(f"Sent {sent} reminder(s).")
