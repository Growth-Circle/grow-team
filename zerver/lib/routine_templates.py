"""The routines that a member can start from: a title, the instructions
for the agent, and when it runs. A template only fills the form; nothing
is created until the member saves."""

from dataclasses import dataclass
from datetime import time
from typing import Any

from django.utils.translation import gettext_lazy
from django_stubs_ext import StrPromise


@dataclass(frozen=True)
class RoutineTemplate:
    id: str
    title: StrPromise
    instructions: StrPromise
    pattern_kind: str
    time_of_day: time
    weekdays: tuple[int, ...] = ()

    def to_api_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": str(self.title),
            "instructions": str(self.instructions),
            "pattern_kind": self.pattern_kind,
            "time_of_day": self.time_of_day.strftime("%H:%M"),
            "weekdays": list(self.weekdays),
        }


ROUTINE_TEMPLATES = [
    RoutineTemplate(
        id="morning_brief",
        title=gettext_lazy("Morning brief"),
        instructions=gettext_lazy(
            "Summarize what needs attention today: cards that are due, cards that "
            "are stuck, and decisions that are waiting. At most 8 items, and no "
            "mention of everyone."
        ),
        pattern_kind="daily",
        time_of_day=time(8, 0),
    ),
    RoutineTemplate(
        id="end_of_day_summary",
        title=gettext_lazy("End-of-day summary"),
        instructions=gettext_lazy(
            "Write what was finished today, what is still open, and one thing for tomorrow."
        ),
        pattern_kind="weekdays",
        time_of_day=time(17, 30),
    ),
    RoutineTemplate(
        id="weekly_team_digest",
        title=gettext_lazy("Weekly team digest"),
        instructions=gettext_lazy(
            "Summarize this week per person: done, in progress, blocked. End with "
            "three priorities for next week."
        ),
        pattern_kind="weekly",
        time_of_day=time(16, 0),
        weekdays=(4,),
    ),
    RoutineTemplate(
        id="client_check",
        title=gettext_lazy("Client check"),
        instructions=gettext_lazy(
            "Check the client conversations in this channel: messages that have "
            "gone unanswered for more than 1 day, and promises that are due."
        ),
        pattern_kind="weekdays",
        time_of_day=time(10, 0),
    ),
]
