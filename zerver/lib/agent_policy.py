"""Current-revision access checks for agent resources."""

from django.db.models import Q, QuerySet
from django.utils.timezone import now

from zerver.lib import agent_protocol as protocol
from zerver.lib.display_recipient import get_display_recipient_by_id
from zerver.lib.exceptions import JsonableError
from zerver.lib.message import access_message
from zerver.lib.streams import access_stream_by_id
from zerver.lib.user_groups import get_recursive_group_members
from zerver.models import Message, Recipient, UserProfile, agents


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


def scope_matches(
    scope_data: dict[str, object],
    message: Message | None,
    destination: protocol.ConversationScope | None = None,
) -> bool:
    if message is None:
        if destination is None:
            return False
        try:
            required = protocol.ConversationScope.model_validate(scope_data)
        except ValueError:
            return False
        if required.anchor_message_id is not None or required.kind != destination.kind:
            return False
        if required.kind == "stream":
            return required.stream_id == destination.stream_id and (
                required.topic is None or required.topic == destination.topic
            )
        return required.kind == "direct" and set(required.participant_user_ids) == set(
            destination.participant_user_ids
        )
    try:
        scope = protocol.ConversationScope.model_validate(scope_data)
    except ValueError:
        return False
    if scope.anchor_message_id is not None and scope.anchor_message_id != message.id:
        return False
    if scope.kind == "selected":
        return True
    if scope.kind == "stream":
        return (
            message.recipient.type == Recipient.STREAM
            and message.recipient.type_id == scope.stream_id
            and (scope.topic is None or message.topic_name() == scope.topic)
        )
    if message.recipient.type == Recipient.STREAM:
        return False
    recipients = get_display_recipient_by_id(message.recipient_id, message.recipient.type, None)
    participants = {user["id"] for user in recipients}
    return participants == set(scope.participant_user_ids)


def _grant_matches(
    actor: UserProfile,
    *,
    target_kind: str,
    target_id: object,
    action: str,
    repository: agents.AgentRepository | None = None,
    source_message: Message | None = None,
    destination: protocol.ConversationScope | None = None,
) -> bool:
    filters = Q(realm_id=actor.realm_id, target_kind=target_kind, revoked_at__isnull=True)
    filters &= Q(expires_at__isnull=True) | Q(expires_at__gt=now())
    filters &= Q(**{f"{target_kind}_id": target_id})
    grants = agents.AgentGrant.objects.filter(filters)
    for grant in grants:
        if action not in grant.actions or not _principal_matches(actor, grant):
            continue
        if (
            grant.target_kind == "profile"
            and grant.repository_id is not None
            and (repository is None or grant.repository_id != repository.id)
        ):
            continue
        if grant.scope is not None and not scope_matches(grant.scope, source_message, destination):
            continue
        return True
    return False


def _owner_or_grant(
    actor: UserProfile,
    resource: (
        agents.AgentRunner | agents.AgentProvider | agents.AgentRepository | agents.AgentProfile
    ),
    *,
    target_kind: str,
    action: str,
    repository: agents.AgentRepository | None = None,
    source_message: Message | None = None,
    destination: protocol.ConversationScope | None = None,
) -> bool:
    return getattr(resource, "owner_id", None) == actor.id or _grant_matches(
        actor,
        target_kind=target_kind,
        target_id=resource.id,
        action=action,
        repository=repository,
        source_message=source_message,
        destination=destination,
    )


def require_agent_resource_access(
    actor: UserProfile,
    resource: (
        agents.AgentRunner | agents.AgentProvider | agents.AgentRepository | agents.AgentProfile
    ),
    *,
    target_kind: str,
    action: str,
) -> None:
    if (
        not actor.is_active
        or resource.realm_id != actor.realm_id
        or not _owner_or_grant(actor, resource, target_kind=target_kind, action=action)
    ):
        _deny()
    if isinstance(resource, agents.AgentRunner) and resource.revoked_at is not None:
        _deny()
    if (
        isinstance(resource, (agents.AgentProvider, agents.AgentRepository))
        and resource.disabled_at is not None
    ):
        _deny()


def _readable_scope(
    actor: UserProfile, bot: UserProfile, data: dict[str, object] | None
) -> tuple[bool, protocol.ConversationScope | None]:
    if data is None:
        return True, None
    try:
        scope = protocol.ConversationScope.model_validate(data)
        if scope.anchor_message_id is not None:
            message = access_message(actor, scope.anchor_message_id, is_modifying_message=False)
            access_message(bot, message.id, is_modifying_message=False)
            if not scope_matches(data, message):
                return False, None
            if scope.kind == "selected":
                if message.recipient.type == Recipient.STREAM:
                    scope = protocol.ConversationScope(
                        kind="stream",
                        stream_id=message.recipient.type_id,
                        topic=message.topic_name(),
                        anchor_message_id=message.id,
                    )
                else:
                    recipients = get_display_recipient_by_id(
                        message.recipient_id, message.recipient.type, None
                    )
                    scope = protocol.ConversationScope(
                        kind="direct",
                        participant_user_ids=[item["id"] for item in recipients],
                        anchor_message_id=message.id,
                    )
        if scope.kind == "stream":
            assert scope.stream_id is not None
            access_stream_by_id(actor, scope.stream_id, require_active_channel=False)
            access_stream_by_id(bot, scope.stream_id, require_active_channel=False)
        elif scope.kind == "direct":
            participants = set(scope.participant_user_ids)
            if (
                actor.id not in participants
                or bot.id not in participants
                or UserProfile.objects.filter(realm=actor.realm, id__in=participants).count()
                != len(participants)
            ):
                return False, None
        return True, scope
    except (ValueError, JsonableError):
        return False, None


def _intersect_scopes(
    left: protocol.ConversationScope | None, right: protocol.ConversationScope | None
) -> tuple[bool, protocol.ConversationScope | None]:
    if left is None:
        return True, right
    if right is None:
        return True, left
    if left.kind != right.kind or (
        left.anchor_message_id is not None
        and right.anchor_message_id is not None
        and left.anchor_message_id != right.anchor_message_id
    ):
        return False, None
    if left.kind == "stream" and (
        left.stream_id != right.stream_id
        or (left.topic is not None and right.topic is not None and left.topic != right.topic)
    ):
        return False, None
    if left.kind == "direct" and set(left.participant_user_ids) != set(right.participant_user_ids):
        return False, None
    return True, left.model_copy(
        update={
            "anchor_message_id": left.anchor_message_id or right.anchor_message_id,
            "topic": left.topic if left.topic is not None else right.topic,
        }
    )


def _visibility_scopes(
    actor: UserProfile,
    profile: agents.AgentProfile,
    resource: (
        agents.AgentRunner | agents.AgentProvider | agents.AgentRepository | agents.AgentProfile
    ),
    kind: str,
    actions: set[str],
) -> list[protocol.ConversationScope | None]:
    if resource.realm_id != actor.realm_id:
        return []
    if resource.owner_id == actor.id:
        return [None]
    scopes: list[protocol.ConversationScope | None] = []
    grants = agents.AgentGrant.objects.filter(
        realm=actor.realm, target_kind=kind, revoked_at__isnull=True, **{f"{kind}_id": resource.id}
    ).filter(Q(expires_at__isnull=True) | Q(expires_at__gt=now()))
    for grant in grants:
        if not actions.intersection(grant.actions) or not _principal_matches(actor, grant):
            continue
        if (
            kind == "profile"
            and grant.repository_id is not None
            and grant.repository_id != profile.default_repository_id
        ):
            continue
        readable, scope = _readable_scope(actor, profile.bot_user, grant.scope)
        if readable:
            scopes.append(scope)
    return scopes


def accessible_profiles(actor: UserProfile) -> QuerySet[agents.AgentProfile]:
    """Apply current grants and compatible readable scopes before count or pagination.

    Availability does not remove authorized history. Execution checks remain stricter.
    Owners retain their profile history after runner grants or availability change.
    """
    visible = []
    for profile in agents.AgentProfile.objects.filter(realm=actor.realm).select_related(
        "runner", "provider", "default_repository", "bot_user"
    ):
        if profile.owner_id == actor.id:
            visible.append(profile.id)
            continue
        scopes = _visibility_scopes(
            actor, profile, profile, "profile", {"profile.use", "profile.manage"}
        )
        resources: list[
            tuple[agents.AgentRunner | agents.AgentProvider | agents.AgentRepository, str, str]
        ] = [(profile.runner, "runner", "runner.use")]
        if profile.provider is not None:
            resources.append((profile.provider, "provider", "provider.use"))
        if profile.default_repository is not None:
            resources.append((profile.default_repository, "repository", "repository.read"))
        for resource, kind, action in resources:
            allowed = _visibility_scopes(actor, profile, resource, kind, {action})
            intersections: dict[bytes, protocol.ConversationScope | None] = {}
            for left in scopes:
                for right in allowed:
                    compatible, scope = _intersect_scopes(left, right)
                    if compatible:
                        key = (
                            protocol.canonical_json(protocol.serialize_payload(scope))
                            if scope
                            else b""
                        )
                        intersections[key] = scope
            scopes = list(intersections.values())
            if not scopes:
                break
        if scopes:
            visible.append(profile.id)
    return agents.AgentProfile.objects.filter(realm=actor.realm, id__in=visible)


def check_agent_access(
    actor: UserProfile,
    profile: agents.AgentProfile,
    repository: agents.AgentRepository | None,
    source_message: Message | None,
    action: str,
    *,
    destination: protocol.ConversationScope | None = None,
) -> None:
    profile = agents.AgentProfile.objects.select_related(
        "runner", "provider", "provider__secret"
    ).get(id=profile.id)
    provider = profile.provider
    if repository is not None:
        repository = agents.AgentRepository.objects.get(id=repository.id)
    if not actor.is_active:
        _deny()
    if action != "profile.manage":
        if (
            profile.runner.revoked_at is not None
            or profile.desired_state == "archived"
            or (
                provider is not None
                and (
                    provider.disabled_at is not None
                    or (provider.secret is not None and provider.secret.revoked_at is not None)
                )
            )
            or (repository is not None and repository.disabled_at is not None)
        ):
            _deny()
        if action != "profile.use" and action not in profile.policy.get("actions", []):
            _deny()
    if (provider is not None and provider.runner_id != profile.runner_id) or (
        repository is not None and repository.runner_id != profile.runner_id
    ):
        _deny()
    if not _same_realm(actor, profile, profile.runner, provider, repository, source_message):
        _deny()
    if destination is not None:
        if source_message is not None:
            _deny()
        readable, _scope = _readable_scope(
            actor, profile.bot_user, protocol.serialize_payload(destination)
        )
        if not readable:
            _deny()
    if source_message is not None:
        try:
            access_message(actor, source_message.id, is_modifying_message=False)
            access_message(profile.bot_user, source_message.id, is_modifying_message=False)
        except JsonableError:
            _deny()
    if not _owner_or_grant(
        actor,
        profile,
        target_kind="profile",
        action=action,
        repository=repository,
        source_message=source_message,
        destination=destination,
    ):
        _deny()
    if not _owner_or_grant(
        actor,
        profile.runner,
        target_kind="runner",
        action="runner.use",
        source_message=source_message,
        destination=destination,
    ):
        _deny()
    if provider is not None and not _owner_or_grant(
        actor,
        provider,
        target_kind="provider",
        action="provider.use",
        source_message=source_message,
        destination=destination,
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
        destination=destination,
    ):
        _deny()
