"""Who can see, change and run a routine.

Routines are for members: a guest cannot use them. A member sees a routine
when they can read its channel, and a routine that runs outside Sanji has
no channel, so every member sees it. The owner of a routine, or an
administrator, can change it, pause it and run it now. A routine that runs
outside Sanji cannot be changed here by anyone.
"""

from uuid import UUID

from django.utils.translation import gettext as _

from zerver.lib.agent_policy import AgentAccessDenied, accessible_profiles, check_agent_access
from zerver.lib.exceptions import JsonableError
from zerver.lib.role_permissions import has_role_permission
from zerver.lib.streams import access_stream_by_id
from zerver.lib.tasks import readable_stream_ids
from zerver.models import (
    AgentProfile,
    AgentRealmSettings,
    AgentRoutine,
    Realm,
    Stream,
    UserProfile,
)
from zerver.models.routines import ROUTINE_EXECUTOR_AGENT


def check_can_use_routines(user_profile: UserProfile) -> None:
    if user_profile.is_guest or user_profile.is_bot:
        raise JsonableError(_("Scheduled routines are for members, not guests."))


def user_can_edit_routine(user_profile: UserProfile, routine: AgentRoutine) -> bool:
    return (
        routine.executor == ROUTINE_EXECUTOR_AGENT
        and routine.realm_id == user_profile.realm_id
        and not user_profile.is_guest
        and not user_profile.is_bot
        and (routine.owner_id == user_profile.id or user_profile.is_realm_admin)
    )


def check_can_edit_routine(user_profile: UserProfile, routine: AgentRoutine) -> None:
    if user_can_edit_routine(user_profile, routine):
        return
    if routine.executor != ROUTINE_EXECUTOR_AGENT:
        raise JsonableError(_("This routine runs outside Sanji and cannot be changed here."))
    raise JsonableError(_("Only the owner of this routine or an administrator can change it."))


def readable_routines(user_profile: UserProfile) -> list[AgentRoutine]:
    check_can_use_routines(user_profile)
    routines = list(
        AgentRoutine.objects.filter(realm=user_profile.realm)
        .select_related("agent_profile", "stream")
        .order_by("title", "id")
    )
    readable = readable_stream_ids(
        user_profile, {routine.stream_id for routine in routines if routine.stream_id is not None}
    )
    return [
        routine
        for routine in routines
        if routine.stream_id is None or routine.stream_id in readable
    ]


def access_routine_by_id(user_profile: UserProfile, routine_id: UUID) -> AgentRoutine:
    """The routine, or the error that an id that is not there gets: a member
    who cannot read the channel learns nothing about the routine."""
    check_can_use_routines(user_profile)
    error = JsonableError(_("Routine does not exist."))
    routine = (
        AgentRoutine.objects.filter(id=routine_id, realm=user_profile.realm)
        .select_related("agent_profile", "stream", "owner")
        .first()
    )
    if routine is None:
        raise error
    if routine.stream_id is not None and not readable_stream_ids(user_profile, {routine.stream_id}):
        raise error
    return routine


def default_routine_timezone(realm: Realm) -> str:
    """The timezone of a new routine: the one of the workspace's agent
    settings, as the clock of its other scheduled jobs."""
    return (
        AgentRealmSettings.objects.filter(realm=realm).values_list("timezone", flat=True).first()
    ) or "Asia/Jakarta"


def default_routine_agent(realm: Realm) -> AgentProfile | None:
    """The built-in agent that posts routines unless the author picks
    another. The role finds it, so a rename does not hide it."""
    return (
        AgentProfile.objects.filter(
            realm=realm,
            is_builtin=True,
            agent_role="custom",
            archived_at__isnull=True,
            desired_state="enabled",
            bot_user__is_active=True,
        )
        .select_related("bot_user")
        .order_by("-created_at")
        .first()
    )


def selectable_agents(user_profile: UserProfile) -> list[AgentProfile]:
    """The agents that this member can give a routine to."""
    check_can_use_routines(user_profile)
    return list(
        accessible_profiles(user_profile)
        .filter(desired_state="enabled", archived_at__isnull=True, bot_user__is_active=True)
        .order_by("name", "id")
    )


def check_routine_target(
    user_profile: UserProfile, profile: AgentProfile, stream_id: int
) -> Stream:
    """Check that `user_profile` can start jobs of `profile` and read the
    channel, so a routine never posts where its owner could not read, and
    never uses an agent its owner could not use. Returns the channel."""
    if profile.realm_id != user_profile.realm_id:
        raise JsonableError(_("Agent does not exist."))
    if not has_role_permission(user_profile, "agent_task"):
        raise AgentAccessDenied("Agent access denied.")
    if not accessible_profiles(user_profile).filter(id=profile.id).exists():
        raise AgentAccessDenied("Agent access denied.")
    check_agent_access(user_profile, profile, None, None, "profile.use")
    if (
        profile.archived_at is not None
        or profile.desired_state != "enabled"
        or not profile.bot_user.is_active
    ):
        raise JsonableError(_("This agent is not available."))
    stream, _subscription = access_stream_by_id(user_profile, stream_id)
    return stream
