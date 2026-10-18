"""Write-side operations for routines. A routine's next run is worked out
again whenever its schedule changes, and a resume starts it from now."""

from datetime import time
from typing import Any
from uuid import UUID

from django.db import transaction
from django.utils.timezone import now as timezone_now
from django.utils.translation import gettext as _

from zerver.lib.exceptions import JsonableError
from zerver.lib.routine_access import (
    access_routine_by_id,
    check_can_edit_routine,
    check_can_use_routines,
    check_routine_target,
    default_routine_agent,
    default_routine_timezone,
)
from zerver.lib.routine_events import send_routine_event
from zerver.lib.routine_schedule import validate_schedule
from zerver.lib.routines import next_run_from, routine_schedule
from zerver.models import AgentProfile, AgentRoutine, UserProfile

SCHEDULE_FIELDS = ("pattern_kind", "interval", "time_of_day", "weekdays", "month_day", "timezone")


def _normalize_schedule(routine: AgentRoutine) -> None:
    """Clear the fields that the routine's pattern does not use, so that
    a change of pattern leaves nothing behind."""
    if routine.pattern_kind in ("every_minutes", "every_hours"):
        routine.time_of_day = None
        routine.weekdays = []
        routine.month_day = None
    elif routine.pattern_kind in ("daily", "weekdays"):
        routine.interval = None
        routine.weekdays = []
        routine.month_day = None
    elif routine.pattern_kind == "weekly":
        routine.interval = None
        routine.month_day = None
        routine.weekdays = sorted(routine.weekdays)
    else:
        routine.interval = None
        routine.weekdays = []


def _set_schedule(routine: AgentRoutine, fields: dict[str, Any]) -> bool:
    """Apply the schedule fields that were given. Raises JsonableError
    for a schedule that cannot run, before anything is saved. Returns
    whether the schedule changed."""
    before = routine_schedule(routine)
    for name in SCHEDULE_FIELDS:
        if name in fields:
            setattr(routine, name, fields[name])
    _normalize_schedule(routine)
    after = routine_schedule(routine)
    validate_schedule(after)
    return after != before


def _get_agent(user_profile: UserProfile, agent_profile_id: UUID | None) -> AgentProfile:
    if agent_profile_id is None:
        profile = default_routine_agent(user_profile.realm)
        if profile is None:
            raise JsonableError(_("No agent is available to post routines. Ask an admin."))
        return profile
    profile = (
        AgentProfile.objects.filter(id=agent_profile_id, realm=user_profile.realm)
        .select_related("bot_user")
        .first()
    )
    if profile is None:
        raise JsonableError(_("Agent does not exist."))
    return profile


@transaction.atomic(durable=True)
def do_create_routine(
    *,
    user_profile: UserProfile,
    title: str,
    instructions: str,
    agent_profile_id: UUID | None,
    stream_id: int,
    topic: str,
    model_preset: str,
    pattern_kind: str,
    interval: int | None,
    time_of_day: time | None,
    weekdays: list[int],
    month_day: int | None,
    timezone: str | None,
) -> AgentRoutine:
    check_can_use_routines(user_profile)
    profile = _get_agent(user_profile, agent_profile_id)
    stream = check_routine_target(user_profile, profile, stream_id)
    routine = AgentRoutine(
        realm=user_profile.realm,
        title=title,
        instructions=instructions,
        agent_profile=profile,
        owner=user_profile,
        stream=stream,
        topic=topic,
        model_preset=model_preset,
        pattern_kind=pattern_kind,
        timezone=timezone or default_routine_timezone(user_profile.realm),
    )
    _set_schedule(
        routine,
        {
            "interval": interval,
            "time_of_day": time_of_day,
            "weekdays": weekdays,
            "month_day": month_day,
        },
    )
    routine.next_run_at = next_run_from(routine, timezone_now())
    routine.save()
    send_routine_event(routine, "add")
    return routine


def _access_routine_to_edit(user_profile: UserProfile, routine_id: UUID) -> AgentRoutine:
    routine = access_routine_by_id(user_profile, routine_id)
    check_can_edit_routine(user_profile, routine)
    return (
        AgentRoutine.objects.select_for_update(of=("self",), no_key=True)
        .select_related("agent_profile", "stream", "owner", "realm")
        .get(id=routine.id)
    )


@transaction.atomic(durable=True)
def do_update_routine(
    *, user_profile: UserProfile, routine_id: UUID, changes: dict[str, Any]
) -> AgentRoutine:
    """Apply the fields in `changes`. A field that is not in it stays."""
    routine = _access_routine_to_edit(user_profile, routine_id)
    assert routine.agent_profile is not None

    for name in ("title", "instructions", "topic", "model_preset"):
        if name in changes:
            setattr(routine, name, changes[name])
    schedule_changed = _set_schedule(routine, changes)

    if "agent_profile_id" in changes or "stream_id" in changes:
        profile = (
            _get_agent(user_profile, changes["agent_profile_id"])
            if "agent_profile_id" in changes
            else routine.agent_profile
        )
        stream_id = changes["stream_id"] if "stream_id" in changes else routine.stream_id
        assert stream_id is not None
        # The job runs for the owner, so the owner must be able to use the
        # agent and read the channel. The person who changes it must read it too.
        routine.stream = check_routine_target(routine.owner, profile, stream_id)
        if user_profile.id != routine.owner_id:
            check_routine_target(user_profile, profile, stream_id)
        routine.agent_profile = profile

    if schedule_changed:
        routine.next_run_at = next_run_from(routine, timezone_now())
    routine.save()
    send_routine_event(routine, "update")
    return routine


@transaction.atomic(durable=True)
def do_set_routine_enabled(
    *, user_profile: UserProfile, routine_id: UUID, enabled: bool
) -> AgentRoutine:
    """Pause a routine, or resume it. A resumed routine counts its next
    run from now, so the runs it missed while it was paused do not
    run, and a streak of failures starts over."""
    routine = _access_routine_to_edit(user_profile, routine_id)
    routine.enabled = enabled
    if enabled:
        routine.paused_reason = ""
        routine.failure_streak = 0
        routine.next_run_at = next_run_from(routine, timezone_now())
    else:
        routine.paused_reason = "user"
    routine.save()
    send_routine_event(routine, "update")
    return routine


@transaction.atomic(durable=True)
def do_delete_routine(*, user_profile: UserProfile, routine_id: UUID) -> None:
    routine = _access_routine_to_edit(user_profile, routine_id)
    send_routine_event(routine, "remove")
    routine.delete()
