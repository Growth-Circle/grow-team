"""Run every minute. Start the job of each routine that is due, and close the
runs whose job has ended."""

from typing import Any

from typing_extensions import override

from zerver.lib.agent_context import agent_realm
from zerver.lib.management import ZulipBaseCommand
from zerver.lib.routines import reconcile_routine_runs, run_due_routines
from zerver.models import Realm


class Command(ZulipBaseCommand):
    help = "Start the routines that are due and close the runs that ended."

    @override
    def handle(self, *args: Any, **options: Any) -> None:
        started = closed = 0
        for realm in Realm.objects.filter(deactivated=False):
            with agent_realm(realm.id):
                closed += reconcile_routine_runs(realm)
                started += run_due_routines(realm)
        self.stdout.write(f"Started {started} routine(s), closed {closed} run(s).")
