import datetime

from django.http import HttpRequest, HttpResponse
from django.utils.translation import gettext as _
from pydantic import Json

from zerver.actions.task_plans import do_add_to_plan, do_remove_from_plan, do_reorder_plan
from zerver.lib.exceptions import JsonableError
from zerver.lib.response import json_success
from zerver.lib.task_plans import planned_entries
from zerver.lib.tasks import access_task_by_id
from zerver.lib.typed_endpoint import PathOnly, typed_endpoint
from zerver.models import UserProfile


def parse_plan_date(raw: str) -> datetime.date:
    try:
        return datetime.date.fromisoformat(raw)
    except ValueError:
        raise JsonableError(_("Enter the date as YYYY-MM-DD."))


@typed_endpoint
def get_task_plan(request: HttpRequest, user_profile: UserProfile, *, date: str) -> HttpResponse:
    entries = planned_entries(user_profile, parse_plan_date(date))
    return json_success(request, data={"plan": [entry.to_api_dict() for entry in entries]})


@typed_endpoint
def add_to_task_plan(
    request: HttpRequest,
    user_profile: UserProfile,
    *,
    date: str,
    position: Json[float] | None = None,
    task_id: PathOnly[int],
) -> HttpResponse:
    plan_date = parse_plan_date(date)
    task = access_task_by_id(user_profile, task_id)
    do_add_to_plan(user_profile=user_profile, task=task, plan_date=plan_date, position=position)
    return json_success(request)


@typed_endpoint
def reorder_task_plan(
    request: HttpRequest,
    user_profile: UserProfile,
    *,
    date: str,
    position: Json[float],
    task_id: PathOnly[int],
) -> HttpResponse:
    do_reorder_plan(
        user_profile=user_profile,
        task_id=task_id,
        plan_date=parse_plan_date(date),
        position=position,
    )
    return json_success(request)


@typed_endpoint
def remove_from_task_plan(
    request: HttpRequest,
    user_profile: UserProfile,
    *,
    date: str,
    task_id: PathOnly[int],
) -> HttpResponse:
    do_remove_from_plan(user_profile=user_profile, task_id=task_id, plan_date=parse_plan_date(date))
    return json_success(request)
