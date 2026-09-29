"""Run every 15 minutes. Once the clock of a workspace reaches 07:00, start
Kaki's morning summary for each room that turned summaries on and has new
messages."""

from typing import Any

from typing_extensions import override

from zerver.lib.agent_context import agent_realm
from zerver.lib.management import ZulipBaseCommand
from zerver.lib.room_digests import send_realm_room_digests
from zerver.models import Realm


class Command(ZulipBaseCommand):
    help = "Start Kaki's morning summary for each room that turned summaries on."

    @override
    def handle(self, *args: Any, **options: Any) -> None:
        started = 0
        for realm in Realm.objects.filter(deactivated=False):
            with agent_realm(realm.id):
                started += send_realm_room_digests(realm)
        self.stdout.write(f"Started {started} summary job(s).")
