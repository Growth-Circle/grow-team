"""Reconcile bounded agent leases and durable publication work."""

import json
from typing import Any

from django.core.management.base import CommandParser
from typing_extensions import override

from zerver.lib.agent_reconcile import reconcile_agents
from zerver.lib.management import ZulipBaseCommand


class Command(ZulipBaseCommand):
    help = "Reconcile agent leases and publication outbox entries."

    @override
    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--limit", type=int, default=100)

    @override
    def handle(self, *args: Any, **options: Any) -> None:
        self.stdout.write(json.dumps(reconcile_agents(limit=options["limit"]), sort_keys=True))
