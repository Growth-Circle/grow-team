"""Write-side operations for the daily plan. Every change is sent only to
the plan's owner, so a person's plan is never visible to anyone else."""

import datetime

from django.db import transaction
from django.utils.translation import gettext as _

from zerver.lib.exceptions import JsonableError
from zerver.lib.task_plans import position_at_end_of_plan
from zerver.models import Task, TaskPlan, UserProfile
from zerver.tornado.django_api import send_event_on_commit


def access_plan_entry(
    user_profile: UserProfile, task_id: int, plan_date: datetime.date
) -> TaskPlan:
    try:
        return TaskPlan.objects.get(user=user_profile, task_id=task_id, date=plan_date)
    except TaskPlan.DoesNotExist:
        raise JsonableError(_("Task is not in this plan."))


@transaction.atomic(durable=True)
def do_add_to_plan(
    *,
    user_profile: UserProfile,
    task: Task,
    plan_date: datetime.date,
    position: float | None,
) -> TaskPlan:
    """Adding a card that is already planned for the day changes nothing,
    so a double click is not an error."""
    entry, created = TaskPlan.objects.get_or_create(
        user=user_profile,
        task=task,
        date=plan_date,
        defaults={
            "position": position_at_end_of_plan(user_profile, plan_date)
            if position is None
            else position
        },
    )
    if created:
        send_event_on_commit(
            user_profile.realm,
            {"type": "task_plan", "op": "add", **entry.to_api_dict()},
            [user_profile.id],
        )
    return entry


@transaction.atomic(durable=True)
def do_remove_from_plan(
    *, user_profile: UserProfile, task_id: int, plan_date: datetime.date
) -> None:
    access_plan_entry(user_profile, task_id, plan_date).delete()
    event = {
        "type": "task_plan",
        "op": "remove",
        "task_id": task_id,
        "date": plan_date.isoformat(),
    }
    send_event_on_commit(user_profile.realm, event, [user_profile.id])


@transaction.atomic(durable=True)
def do_reorder_plan(
    *, user_profile: UserProfile, task_id: int, plan_date: datetime.date, position: float
) -> TaskPlan:
    entry = access_plan_entry(user_profile, task_id, plan_date)
    entry.position = position
    entry.save(update_fields=["position"])
    send_event_on_commit(
        user_profile.realm,
        {"type": "task_plan", "op": "reorder", **entry.to_api_dict()},
        [user_profile.id],
    )
    return entry
