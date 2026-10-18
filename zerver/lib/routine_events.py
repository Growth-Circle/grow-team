from typing import Any

from zerver.lib.tasks import channel_reader_user_ids
from zerver.models import AgentRoutine
from zerver.models.users import active_non_guest_user_ids
from zerver.tornado.django_api import send_event_on_commit


def routine_event_audience(routine: AgentRoutine) -> list[int]:
    """The members who can read the routine's channel. A routine that runs
    outside Sanji has no channel and reaches every member. A guest never
    gets a routine event, because routines are for members."""
    members = set(active_non_guest_user_ids(routine.realm_id))
    if routine.stream is not None:
        members &= set(channel_reader_user_ids(routine.realm, routine.stream))
    return sorted(members)


def send_routine_event(routine: AgentRoutine, op: str) -> None:
    event: dict[str, Any] = {"type": "routine", "op": op}
    if op == "remove":
        event["routine_id"] = str(routine.id)
    else:
        event["routine"] = routine.to_api_dict()
    send_event_on_commit(routine.realm, event, routine_event_audience(routine))
