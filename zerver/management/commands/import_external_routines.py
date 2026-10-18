"""Register routines that something outside Sanji runs, so that the
Scheduled page shows them. Sanji never runs them: they are listed with their
schedule and can be moved into Sanji one by one.

The file is a JSON list. Each item has `title`, `owner_email`,
`agent_name` (shown where an agent's name would be) and a schedule:
`pattern_kind` with `interval`, `time_of_day` ("HH:MM"), `weekdays`,
`month_day` and `timezone` as the pattern needs. `stream` (a channel
name), `topic` and `instructions` are optional.
"""

import json
from argparse import ArgumentParser
from datetime import time
from typing import Any

from django.core.management.base import CommandError
from django.utils.timezone import now as timezone_now
from typing_extensions import override

from zerver.lib.exceptions import JsonableError
from zerver.lib.management import ZulipBaseCommand
from zerver.lib.routine_schedule import validate_schedule
from zerver.lib.routines import next_run_from, routine_schedule
from zerver.models import AgentRoutine, Realm, Stream, UserProfile
from zerver.models.routines import ROUTINE_EXECUTOR_EXTERNAL
from zerver.models.streams import get_stream


def build_routine(realm: Realm, item: dict[str, Any]) -> AgentRoutine:
    """The routine for one item of the file, not saved. Raises CommandError
    for an item that cannot be registered."""
    try:
        title = item["title"]
        owner = UserProfile.objects.get(
            realm=realm, delivery_email__iexact=item["owner_email"], is_active=True, is_bot=False
        )
        pattern_kind = item["pattern_kind"]
    except KeyError as error:
        raise CommandError(f"{item.get('title', item)!r}: missing {error}") from None
    except UserProfile.DoesNotExist:
        raise CommandError(f"{title!r}: no active member has the email {item['owner_email']}.")

    stream = None
    if item.get("stream"):
        try:
            stream = get_stream(item["stream"], realm)
        except Stream.DoesNotExist:
            raise CommandError(f"{title!r}: no channel is named {item['stream']!r}.") from None

    try:
        time_of_day = (
            None if item.get("time_of_day") is None else time.fromisoformat(item["time_of_day"])
        )
    except ValueError:
        raise CommandError(f"{title!r}: time_of_day must be HH:MM.") from None
    routine = AgentRoutine(
        realm=realm,
        title=title[: AgentRoutine.MAX_TITLE_LENGTH],
        instructions=item.get("instructions", "")[: AgentRoutine.MAX_INSTRUCTIONS_LENGTH],
        owner=owner,
        external_agent_name=item.get("agent_name", "")[:100],
        stream=stream,
        topic=item.get("topic", "")[: AgentRoutine.MAX_TOPIC_LENGTH],
        pattern_kind=pattern_kind,
        interval=item.get("interval"),
        time_of_day=time_of_day,
        weekdays=item.get("weekdays", []),
        month_day=item.get("month_day"),
        timezone=item.get("timezone", "Asia/Jakarta"),
        executor=ROUTINE_EXECUTOR_EXTERNAL,
    )
    try:
        validate_schedule(routine_schedule(routine))
    except JsonableError as error:
        raise CommandError(f"{title!r}: {error.msg}") from None
    routine.next_run_at = next_run_from(routine, timezone_now())
    return routine


class Command(ZulipBaseCommand):
    help = "Register routines that run outside Sanji from a JSON file, so they show as read-only."

    @override
    def add_arguments(self, parser: ArgumentParser) -> None:
        parser.add_argument("file", help="The JSON file with the routines.")
        parser.add_argument(
            "--dry-run", action="store_true", help="Check the file and save nothing."
        )
        self.add_realm_args(parser, required=True)

    @override
    def handle(self, *args: Any, **options: Any) -> None:
        realm = self.get_realm(options)
        assert realm is not None
        with open(options["file"], "rb") as file:
            items = json.load(file)
        if not isinstance(items, list):
            raise CommandError("The file must hold a JSON list of routines.")

        # Check every item before saving any, so a mistake registers none.
        routines = [build_routine(realm, item) for item in items]
        created = 0
        for routine in routines:
            exists = AgentRoutine.objects.filter(
                realm=realm,
                executor=ROUTINE_EXECUTOR_EXTERNAL,
                owner=routine.owner,
                title=routine.title,
            ).exists()
            if exists:
                self.stdout.write(f"{routine.title}: already registered.")
                continue
            if not options["dry_run"]:
                routine.save()
            self.stdout.write(
                f"{routine.title}: {'would register' if options['dry_run'] else 'registered'}."
            )
            created += 1
        self.stdout.write(
            f"{created} routine(s) {'to register' if options['dry_run'] else 'registered'}."
        )
