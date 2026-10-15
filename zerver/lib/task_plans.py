"""Read-side helpers for the daily plan. A plan is private to its owner."""

import datetime

from django.db.models import Max

from zerver.lib.tasks import readable_stream_ids
from zerver.models import TaskPlan, UserProfile

# Gap between two cards appended to a day; wide enough that many reorders
# between the same pair never exhaust float precision.
PLAN_POSITION_STEP = 1000.0


def planned_entries(user_profile: UserProfile, plan_date: datetime.date) -> list[TaskPlan]:
    """The owner's plan for the day, in order. A card that was deleted, or
    that the owner can no longer read, is not part of the plan."""
    entries = list(
        TaskPlan.objects.filter(user=user_profile, date=plan_date)
        .select_related("task")
        .order_by("position", "id")
    )
    readable = readable_stream_ids(
        user_profile,
        {entry.task.stream_id for entry in entries if entry.task.stream_id is not None},
    )
    return [
        entry
        for entry in entries
        if entry.task.stream_id is None or entry.task.stream_id in readable
    ]


def position_at_end_of_plan(user_profile: UserProfile, plan_date: datetime.date) -> float:
    highest = TaskPlan.objects.filter(user=user_profile, date=plan_date).aggregate(Max("position"))[
        "position__max"
    ]
    return PLAN_POSITION_STEP if highest is None else highest + PLAN_POSITION_STEP
