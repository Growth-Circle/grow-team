"""Daily job: Kaki DMs each room owner once about rooms that went quiet
or passed their project due date (PLAN.md WP14 step 5)."""

from typing import Any

from typing_extensions import override

from zerver.actions.room_meta import send_quiet_room_notices
from zerver.lib.agent_context import agent_realm
from zerver.lib.management import ZulipBaseCommand
from zerver.models import Realm


class Command(ZulipBaseCommand):
    help = "DM room owners once about quiet rooms and overdue project rooms."

    @override
    def handle(self, *args: Any, **options: Any) -> None:
        notified_owners = 0
        for realm in Realm.objects.filter(deactivated=False):
            with agent_realm(realm.id):
                notified_owners += send_quiet_room_notices(realm)
        self.stdout.write(f"Notified {notified_owners} owner(s).")
