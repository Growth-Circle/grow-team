"""Current-revision access checks for agent resources."""

from django.db.models import Q
from django.utils.timezone import now

from zerver.lib.exceptions import JsonableError
from zerver.lib.message import access_message
from zerver.lib.user_groups import get_recursive_group_members
from zerver.models import Message, UserProfile, agents


class AgentAccessDenied(JsonableError):  # noqa: N818
    pass


def _deny() -> None:
    raise AgentAccessDenied("Agent access denied.")


def _same_realm(actor: UserProfile, *objects: object | None) -> bool:
    return all(obj is None or getattr(obj, "realm_id", None) == actor.realm_id for obj in objects)


def _principal_matches(actor: UserProfile, grant: agents.AgentGrant) -> bool:
    if grant.principal_user_id == actor.id:
        return True
    if grant.principal_group_id is None:
        return False
    return get_recursive_group_members(grant.principal_group_id).filter(id=actor.id).exists()


def _grant_matches(
    actor: UserProfile,
    *,
    target_kind: str,
    target_id: object,
    action: str,
    repository: agents.AgentRepository | None = None,
    source_message: Message | None = None,
) -> bool:
    filters = Q(realm_id=actor.realm_id, target_kind=target_kind, revoked_at__isnull=True)
    filters &= Q(expires_at__isnull=True) | Q(expires_at__gt=now())
    filters &= Q(**{f"{target_kind}_id": target_id})
    grants = agents.AgentGrant.objects.filter(filters)
    for grant in grants:
        if action not in grant.actions or not _principal_matches(actor, grant):
            continue
        if repository is not None and grant.repository_id not in (None, repository.id):
            continue
        if grant.scope is not None and (
            source_message is None
            or grant.scope.get("anchor_message_id") not in (None, source_message.id)
        ):
            continue
        return True
    return False


def _owner_or_grant(
    actor: UserProfile,
    resource: object,
    *,
    target_kind: str,
    action: str,
    repository: agents.AgentRepository | None = None,
    source_message: Message | None = None,
) -> bool:
    return getattr(resource, "owner_id", None) == actor.id or _grant_matches(
        actor,
        target_kind=target_kind,
        target_id=resource.id,
        action=action,
        repository=repository,
        source_message=source_message,
    )


def check_agent_access(
    actor: UserProfile,
    profile: agents.AgentProfile,
    repository: agents.AgentRepository | None,
    source_message: Message | None,
    action: str,
) -> None:
    provider = profile.provider
    if not _same_realm(actor, profile, profile.runner, provider, repository, source_message):
        _deny()
    if source_message is not None:
        try:
            access_message(actor, source_message.id, is_modifying_message=False)
        except JsonableError:
            _deny()
    if not _owner_or_grant(
        actor,
        profile,
        target_kind="profile",
        action=action,
        repository=repository,
        source_message=source_message,
    ):
        _deny()
    if not _owner_or_grant(
        actor,
        profile.runner,
        target_kind="runner",
        action="runner.use",
        source_message=source_message,
    ):
        _deny()
    if provider is not None and not _owner_or_grant(
        actor,
        provider,
        target_kind="provider",
        action="provider.use",
        source_message=source_message,
    ):
        _deny()
    if repository is not None and not _owner_or_grant(
        actor,
        repository,
        target_kind="repository",
        action=(
            action
            if action.startswith(("repository.", "checks.", "shell.", "dependencies.", "git."))
            else "repository.read"
        ),
        source_message=source_message,
    ):
        _deny()
