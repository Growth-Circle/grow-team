"""Every 15 minutes: once a realm's local time reaches 07:00, post Kaki's
morning digest for each opted-in room with new messages (PLAN.md WP24
step 1)."""

from typing import Any

from typing_extensions import override

from zerver.lib.agent_context import agent_realm
from zerver.lib.management import ZulipBaseCommand
from zerver.lib.room_digests import send_realm_room_digests
from zerver.models import Realm


class Command(ZulipBaseCommand):
    help = "Post Kaki's morning digest for each opted-in room with new messages."

    @override
    def handle(self, *args: Any, **options: Any) -> None:
        sent = 0
        for realm in Realm.objects.filter(deactivated=False):
            with agent_realm(realm.id):
                sent += send_realm_room_digests(realm)
        self.stdout.write(f"Sent {sent} digest(s).")
