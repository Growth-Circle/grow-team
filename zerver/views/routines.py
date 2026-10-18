from datetime import time
from typing import Annotated, Any, Literal
from uuid import UUID

from django.http import HttpRequest, HttpResponse
from pydantic import Json, StringConstraints

from zerver.actions.routines import (
    do_create_routine,
    do_delete_routine,
    do_set_routine_enabled,
    do_update_routine,
)
from zerver.lib.response import json_success
from zerver.lib.routine_access import (
    access_routine_by_id,
    check_can_edit_routine,
    check_can_use_routines,
    default_routine_agent,
    default_routine_timezone,
    readable_routines,
    selectable_agents,
    user_can_edit_routine,
)
from zerver.lib.routine_templates import ROUTINE_TEMPLATES
from zerver.lib.routines import run_routine_now
from zerver.lib.typed_endpoint import PathOnly, typed_endpoint, typed_endpoint_without_parameters
from zerver.models import AgentRoutine, AgentRoutineRun, UserProfile

RoutineTitle = Annotated[
    str,
    StringConstraints(
        min_length=1, max_length=AgentRoutine.MAX_TITLE_LENGTH, strip_whitespace=True
    ),
]
RoutineInstructions = Annotated[
    str,
    StringConstraints(
        min_length=1, max_length=AgentRoutine.MAX_INSTRUCTIONS_LENGTH, strip_whitespace=True
    ),
]
RoutineTopic = Annotated[
    str,
    StringConstraints(
        min_length=1, max_length=AgentRoutine.MAX_TOPIC_LENGTH, strip_whitespace=True
    ),
]
AgentProfileId = Annotated[
    str, StringConstraints(pattern=r"^[0-9a-fA-F]{8}-([0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}$")
]
TimeOfDay = Annotated[str, StringConstraints(pattern=r"^([01]\d|2[0-3]):[0-5]\d$")]
PatternKind = Literal["every_minutes", "every_hours", "daily", "weekdays", "weekly", "monthly"]
ModelPreset = Literal["", "fast", "balanced", "best"]

RUN_HISTORY_LIMIT = 50


def _parse_agent_profile_id(value: str | None) -> UUID | None:
    return None if value is None else UUID(value)


def _parse_time_of_day(value: str | None) -> time | None:
    return None if value is None else time.fromisoformat(value)


def _latest_runs(routines: list[AgentRoutine]) -> dict[Any, AgentRoutineRun]:
    runs = AgentRoutineRun.objects.filter(routine__in=routines).order_by(
        "routine_id", "-scheduled_for"
    )
    return {run.routine_id: run for run in runs.distinct("routine_id")}


def routine_data(
    user_profile: UserProfile, routine: AgentRoutine, latest: AgentRoutineRun | None
) -> dict[str, Any]:
    return {
        **routine.to_api_dict(),
        "can_edit": user_can_edit_routine(user_profile, routine),
        "last_run": None if latest is None else latest.to_api_dict(),
    }


@typed_endpoint_without_parameters
def list_routines(request: HttpRequest, user_profile: UserProfile) -> HttpResponse:
    routines = readable_routines(user_profile)
    latest = _latest_runs(routines)
    return json_success(
        request,
        data={
            "routines": [
                routine_data(user_profile, routine, latest.get(routine.id)) for routine in routines
            ]
        },
    )


@typed_endpoint_without_parameters
def list_routine_templates(request: HttpRequest, user_profile: UserProfile) -> HttpResponse:
    check_can_use_routines(user_profile)
    return json_success(
        request, data={"templates": [template.to_api_dict() for template in ROUTINE_TEMPLATES]}
    )


@typed_endpoint_without_parameters
def list_routine_agents(request: HttpRequest, user_profile: UserProfile) -> HttpResponse:
    default = default_routine_agent(user_profile.realm)
    return json_success(
        request,
        data={
            "agents": [
                {"id": str(profile.id), "name": profile.name}
                for profile in selectable_agents(user_profile)
            ],
            "default_agent_id": None if default is None else str(default.id),
            "default_timezone": default_routine_timezone(user_profile.realm),
        },
    )


@typed_endpoint
def create_routine(
    request: HttpRequest,
    user_profile: UserProfile,
    *,
    agent_profile_id: AgentProfileId | None = None,
    instructions: RoutineInstructions,
    interval: Json[int] | None = None,
    model_preset: ModelPreset = "",
    month_day: Json[int] | None = None,
    pattern_kind: PatternKind,
    stream_id: Json[int],
    time_of_day: TimeOfDay | None = None,
    timezone: str | None = None,
    title: RoutineTitle,
    topic: RoutineTopic,
    weekdays: Json[list[int]] | None = None,
) -> HttpResponse:
    routine = do_create_routine(
        user_profile=user_profile,
        title=title,
        instructions=instructions,
        agent_profile_id=_parse_agent_profile_id(agent_profile_id),
        stream_id=stream_id,
        topic=topic,
        model_preset=model_preset,
        pattern_kind=pattern_kind,
        interval=interval,
        time_of_day=_parse_time_of_day(time_of_day),
        weekdays=weekdays or [],
        month_day=month_day,
        timezone=timezone,
    )
    return json_success(request, data={"routine": routine_data(user_profile, routine, None)})


@typed_endpoint
def get_routine(
    request: HttpRequest, user_profile: UserProfile, *, routine_id: PathOnly[UUID]
) -> HttpResponse:
    routine = access_routine_by_id(user_profile, routine_id)
    latest = _latest_runs([routine])
    return json_success(
        request,
        data={"routine": routine_data(user_profile, routine, latest.get(routine.id))},
    )


@typed_endpoint
def update_routine(
    request: HttpRequest,
    user_profile: UserProfile,
    *,
    agent_profile_id: AgentProfileId | None = None,
    instructions: RoutineInstructions | None = None,
    interval: Json[int] | None = None,
    model_preset: ModelPreset | None = None,
    month_day: Json[int] | None = None,
    pattern_kind: PatternKind | None = None,
    routine_id: PathOnly[UUID],
    stream_id: Json[int] | None = None,
    time_of_day: TimeOfDay | None = None,
    timezone: str | None = None,
    title: RoutineTitle | None = None,
    topic: RoutineTopic | None = None,
    weekdays: Json[list[int]] | None = None,
) -> HttpResponse:
    given: dict[str, Any] = {
        "agent_profile_id": _parse_agent_profile_id(agent_profile_id),
        "instructions": instructions,
        "interval": interval,
        "model_preset": model_preset,
        "month_day": month_day,
        "pattern_kind": pattern_kind,
        "stream_id": stream_id,
        "time_of_day": _parse_time_of_day(time_of_day),
        "timezone": timezone,
        "title": title,
        "topic": topic,
        "weekdays": weekdays,
    }
    routine = do_update_routine(
        user_profile=user_profile,
        routine_id=routine_id,
        changes={name: value for name, value in given.items() if value is not None},
    )
    return json_success(
        request,
        data={
            "routine": routine_data(user_profile, routine, _latest_runs([routine]).get(routine.id))
        },
    )


@typed_endpoint
def delete_routine(
    request: HttpRequest, user_profile: UserProfile, *, routine_id: PathOnly[UUID]
) -> HttpResponse:
    do_delete_routine(user_profile=user_profile, routine_id=routine_id)
    return json_success(request)


@typed_endpoint
def pause_routine(
    request: HttpRequest, user_profile: UserProfile, *, routine_id: PathOnly[UUID]
) -> HttpResponse:
    routine = do_set_routine_enabled(
        user_profile=user_profile, routine_id=routine_id, enabled=False
    )
    return json_success(
        request,
        data={
            "routine": routine_data(user_profile, routine, _latest_runs([routine]).get(routine.id))
        },
    )


@typed_endpoint
def resume_routine(
    request: HttpRequest, user_profile: UserProfile, *, routine_id: PathOnly[UUID]
) -> HttpResponse:
    routine = do_set_routine_enabled(user_profile=user_profile, routine_id=routine_id, enabled=True)
    return json_success(
        request,
        data={
            "routine": routine_data(user_profile, routine, _latest_runs([routine]).get(routine.id))
        },
    )


@typed_endpoint
def run_routine(
    request: HttpRequest, user_profile: UserProfile, *, routine_id: PathOnly[UUID]
) -> HttpResponse:
    routine = access_routine_by_id(user_profile, routine_id)
    check_can_edit_routine(user_profile, routine)
    run = run_routine_now(routine)
    return json_success(request, data={"run": run.to_api_dict()})


@typed_endpoint
def list_routine_runs(
    request: HttpRequest, user_profile: UserProfile, *, routine_id: PathOnly[UUID]
) -> HttpResponse:
    routine = access_routine_by_id(user_profile, routine_id)
    runs = routine.runs.order_by("-scheduled_for")[:RUN_HISTORY_LIMIT]
    return json_success(request, data={"runs": [run.to_api_dict() for run in runs]})
