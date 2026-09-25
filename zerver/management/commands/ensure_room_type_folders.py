"""Create the Proyek/Klien/Tim channel folders for a realm, idempotently
(PLAN.md WP14 step 6)."""

from argparse import ArgumentParser
from typing import Any

from django.core.management.base import CommandError
from typing_extensions import override

from zerver.actions.room_meta import NoRealmOwnerError, ensure_room_type_folders
from zerver.lib.management import ZulipBaseCommand


class Command(ZulipBaseCommand):
    help = "Create the Proyek/Klien/Tim channel folders for a realm."

    @override
    def add_arguments(self, parser: ArgumentParser) -> None:
        self.add_realm_args(parser, required=True, help="realm to create the folders in")

    @override
    def handle(self, *args: Any, **options: Any) -> None:
        realm = self.get_realm(options)
        assert realm is not None  # Should be ensured by parser

        try:
            created = ensure_room_type_folders(realm)
        except NoRealmOwnerError as e:
            raise CommandError(str(e))
        self.stdout.write(f"Created {len(created)} folder(s).")
