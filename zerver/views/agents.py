"""Human connection APIs use Zulip authentication and form-encoded payload JSON."""

from collections.abc import Callable
from functools import wraps
from typing import ParamSpec, TypeVar
from uuid import UUID

from django.core.exceptions import ObjectDoesNotExist, ValidationError
from django.db import transaction
from django.db.models import Q
from django.http import HttpRequest, HttpResponse
from django.utils.timezone import now

from zerver.actions import agents as actions
from zerver.actions.agent_approvals import OutcomeUnknownError
from zerver.lib import agent_protocol as p
from zerver.lib import agent_requests as r
from zerver.lib.agent_context import AgentBusy, log_agent_busy
from zerver.lib.agent_policy import (
    _principal_matches,
    _readable_scope,
    _visibility_scopes,
    accessible_profiles,
    check_agent_access,
    require_agent_resource_access,
)
from zerver.lib.agent_presence import observed_runner_status
from zerver.lib.agent_selection import resolve_agent_selection
from zerver.lib.exceptions import JsonableError
from zerver.lib.response import json_response, json_success
from zerver.lib.streams import access_stream_by_id
from zerver.models import Subscription, UserProfile, agents
from zerver.models.streams import Stream

P = ParamSpec("P")
T = TypeVar("T", bound=p.Versioned)


def safe_agent_endpoint(view: Callable[P, HttpResponse]) -> Callable[P, HttpResponse]:
    @wraps(view)
    def wrapped(*args: P.args, **kwargs: P.kwargs) -> HttpResponse:
        try:
            return view(*args, **kwargs)
        except actions.RunnerCredentialError as error:
            return json_response(
                "error",
                "Runner credential rejected.",
                {"schema_version": 1, "code": error.code},
                status=401,
            )
        except AgentBusy:
            response = json_response(
                "error",
                "Agent authority is busy. Retry this request.",
                {"schema_version": 1},
                status=503,
            )
            response["Retry-After"] = "1"
            request = args[0]
            assert isinstance(request, HttpRequest)
            log_agent_busy(request, response)
            return response
        except OutcomeUnknownError:
            return json_response(
                "error",
                "This step may have finished. Check the channel before you try again.",
                {"schema_version": 1, "code": "outcome_unknown"},
                status=409,
            )
        except (ValueError, ValidationError, ObjectDoesNotExist, JsonableError, OSError):
            return json_response(
                "error", "Agent request rejected.", {"schema_version": 1}, status=400
            )

    return wrapped


def payload(request: HttpRequest, schema: type[T]) -> T:
    if set(request.POST) != {"payload"} or len(request.POST.getlist("payload")) != 1:
        raise ValueError("Invalid request.")
    return schema.model_validate_json(request.POST["payload"])


def _profile_data(
    profile: agents.AgentProfile,
    actor: UserProfile | None = None,
    complete_ids: set[UUID] | None = None,
) -> dict[str, object]:
    allowed_actions: list[str] = []
    if actor is not None:
        try:
            check_agent_access(actor, profile, None, None, "profile.manage")
            if profile.desired_state != "archived":
                allowed_actions = ["edit", "pause", "probe"]
                if (
                    profile.readiness_state == "ready"
                    and profile.readiness_revision == profile.revision
                    and profile.readiness_configuration is not None
                ):
                    allowed_actions.append("enable")
                if not agents.AgentAttempt.objects.filter(
                    job__profile=profile, active=True
                ).exists():
                    allowed_actions.append("archive")
        except JsonableError:
            pass

    def visible(resource: object, kind: str, action: str) -> bool:
        if actor is None:
            return True
        return bool(_visibility_scopes(actor, profile, resource, kind, {action}))

    runner_visible = visible(profile.runner, "runner", "runner.use")
    provider_visible = profile.provider is None or visible(
        profile.provider, "provider", "provider.use"
    )
    repository_visible = profile.default_repository is None or visible(
        profile.default_repository, "repository", "repository.read"
    )
    can_edit = "edit" in allowed_actions
    command_allowed = True
    if profile.default_mode == "manage" and actor is not None:
        try:
            check_agent_access(actor, profile, None, None, "team.manage")
        except JsonableError:
            command_allowed = False
    complete = (
        True
        if actor is None
        else profile.id in complete_ids
        if complete_ids is not None
        else accessible_profiles(actor).filter(id=profile.id).exists()
    )
    configuration = None
    if can_edit and actor is not None:
        readable, _scope = _readable_scope(actor, profile.bot_user, profile.policy["scope"])
        network_visible = profile.provider is None or profile.provider.owner_id == actor.id
        configuration = {
            "policy": {
                "scope": profile.policy["scope"] if readable else None,
                "actions": profile.policy["actions"],
                "sandbox_alias": profile.policy["sandbox"]["alias"],
                "network": profile.policy["network"] if network_visible else None,
                "hard_cost_cap": profile.policy["hard_cost_cap"],
            },
            "budget": profile.budget,
            "scope_restricted": not readable,
            "network_retained": not network_visible,
        }
    return {
        "id": str(profile.id),
        "name": profile.name,
        "description": profile.description,
        "runner_id": str(profile.runner_id) if runner_visible else None,
        "provider_id": str(profile.provider_id)
        if profile.provider_id and provider_visible
        else None,
        "repository_id": (
            str(profile.default_repository_id)
            if profile.default_repository_id and repository_visible
            else None
        ),
        "state": profile.desired_state,
        "desired_state": profile.desired_state,
        "readiness_state": profile.readiness_state,
        "revision": profile.revision,
        "metadata_revision": profile.metadata_revision,
        "bot_user_id": profile.bot_user_id,
        "default_mode": profile.default_mode,
        "command_allowed": command_allowed,
        "capabilities": profile.capability_report,
        "owner_id": profile.owner_id,
        "mode": profile.mode,
        "adapter_id": profile.adapter_id,
        "adapter_version": profile.adapter_version,
        "enabled_revision": profile.enabled_revision,
        "readiness_revision": profile.readiness_revision,
        "allowed_actions": allowed_actions,
        "owner": {"id": profile.owner_id, "name": profile.owner.full_name},
        "runner": _runner_data(profile.runner, actor) if runner_visible else None,
        "provider": _provider_data(profile.provider, actor)
        if profile.provider and provider_visible
        else None,
        "repository": (
            _repository_data(profile.default_repository, actor)
            if profile.default_repository and repository_visible
            else None
        ),
        "access": {
            "complete": complete,
            "runner": runner_visible,
            "provider": provider_visible,
            "repository": repository_visible,
        },
        "configuration": configuration,
        "shared_with": (
            actions.shared_agent_principals(profile)
            if actor is not None and actor.id == profile.owner_id
            else []
        ),
    }


def _runner_data(runner: agents.AgentRunner, actor: UserProfile | None = None) -> dict[str, object]:
    owner = actor is None or actor.id == runner.owner_id
    return {
        "id": str(runner.id),
        "name": runner.name,
        "owner_id": runner.owner_id,
        "host_kind": runner.host_kind,
        "status": observed_runner_status(runner),
        "observed_presence": observed_runner_status(runner),
        "last_heartbeat_at": runner.last_heartbeat_at.isoformat()
        if runner.last_heartbeat_at
        else None,
        "observed_at": runner.last_heartbeat_at.isoformat() if runner.last_heartbeat_at else None,
        "revoked_at": runner.revoked_at.isoformat() if runner.revoked_at else None,
        "revision": runner.policy_version,
        "metadata_revision": runner.metadata_revision,
        "catalog_revision": runner.catalog_revision,
        "catalog": runner.catalog_report if owner else None,
        "catalog_summary": {
            "revision": runner.catalog_revision,
            "reported_at": runner.catalog_report.get("reported_at"),
            "adapters": [
                {"id": item["id"], "version": item["version"], "auth_state": item["auth_state"]}
                for item in runner.catalog_report.get("adapters", [])
            ],
            "sandboxes": [
                {"alias": item["alias"]} for item in runner.catalog_report.get("sandboxes", [])
            ],
        },
        "allowed_actions": ["edit", "revoke"]
        if owner and actor and actor.is_active and runner.revoked_at is None
        else [],
    }


def _provider_data(
    provider: agents.AgentProvider, actor: UserProfile | None = None
) -> dict[str, object]:
    owner = actor is None or actor.id == provider.owner_id
    result: dict[str, object] = {
        "id": str(provider.id),
        "name": provider.name,
        "owner_id": provider.owner_id,
        "runner_id": str(provider.runner_id),
        "model_id": provider.model_id,
        "allowed_models": provider.allowed_models,
        "api_mode": provider.api_mode,
        "context_window_tokens": provider.context_window_tokens,
        "max_output_tokens": provider.max_output_tokens,
        "data_scope": provider.data_scope,
        "capabilities": provider.capability_report,
        "config_version": provider.config_version,
        "disabled_at": provider.disabled_at.isoformat() if provider.disabled_at else None,
        "allowed_actions": ["edit", "probe"]
        if owner and actor and actor.is_active and provider.disabled_at is None
        else [],
    }
    if owner:
        result.update(
            {
                "metadata_revision": provider.metadata_revision,
                "base_url": provider.base_url,
                "network": provider.network_policy,
                "credential": {
                    "retained": provider.secret_id is not None
                    or bool(provider.local_credential_ref),
                    "kind": "server"
                    if provider.secret_id is not None
                    else "local"
                    if provider.local_credential_ref
                    else None,
                },
            }
        )
    return result


def _repository_data(
    repository: agents.AgentRepository, actor: UserProfile | None = None
) -> dict[str, object]:
    owner = actor is None or actor.id == repository.owner_id
    result: dict[str, object] = {
        "id": str(repository.id),
        "workspace_alias": repository.workspace_alias,
        "owner_id": repository.owner_id,
        "runner_id": str(repository.runner_id),
        "disabled_at": repository.disabled_at.isoformat() if repository.disabled_at else None,
        "allowed_actions": ["manage"]
        if owner and actor and actor.is_active and repository.disabled_at is None
        else [],
    }
    if owner:
        result.update(
            {
                "policy_version": repository.policy_version,
                "canonical_origin": repository.canonical_origin or None,
                "allowed_refs": repository.allowed_refs,
                "required_checks": repository.required_checks,
            }
        )
    return result


def _setup_data(setup: agents.AgentSetupOperation) -> dict[str, object]:
    return {
        "id": str(setup.id),
        "phase": setup.phase,
        "requirements": setup.requirements,
        "profile_revision": setup.profile_revision,
        "provider_config_version": setup.provider_config_version,
        "created_at": setup.created_at.isoformat(),
        "finished_at": setup.finished_at.isoformat() if setup.finished_at else None,
    }


def _success(request: HttpRequest, data: dict[str, object] | None = None) -> HttpResponse:
    return json_success(request, {"schema_version": 1, **(data or {})})


def _default_data(user_profile: UserProfile) -> dict[str, object]:
    settings = agents.AgentRealmSettings.objects.get(realm=user_profile.realm)
    data: dict[str, object] = {"has_default": settings.default_profile_id is not None}
    if user_profile.is_realm_admin:
        data["selection_revision"] = settings.default_selection_revision
        data["allowed_actions"] = ["clear"]
    if settings.default_profile_id is None:
        return data
    try:
        profile = accessible_profiles(user_profile).get(id=settings.default_profile_id)
    except ObjectDoesNotExist:
        return data
    data["profile"] = _profile_data(profile, user_profile)
    return data


def _list_options(request: HttpRequest) -> tuple[int, int, str, str, str, str]:
    allowed = {"offset", "limit", "ownership", "access", "host_kind", "search"}
    if set(request.GET) - allowed or any(len(request.GET.getlist(key)) != 1 for key in request.GET):
        raise ValueError("Invalid directory filter.")
    offset, limit = int(request.GET.get("offset", "0")), int(request.GET.get("limit", "50"))
    ownership = request.GET.get("ownership", "all")
    access = request.GET.get("access", "all")
    host_kind = request.GET.get("host_kind", "all")
    search = request.GET.get("search", "").strip()
    if (
        offset < 0
        or not 1 <= limit <= 100
        or ownership not in {"all", "mine", "shared"}
        or access not in {"all", "complete", "partial"}
        or host_kind not in {"all", "workstation", "server", "unknown"}
        or len(search) > 100
    ):
        raise ValueError("Invalid directory filter.")
    return offset, limit, ownership, access, host_kind, search


def _directory_profiles(actor: UserProfile) -> tuple[set[UUID], set[UUID]]:
    if not actor.is_active:
        return set(), set()
    visible: set[UUID] = set()
    owned: list[agents.AgentProfile] = []
    for profile in agents.AgentProfile.objects.filter(realm=actor.realm).select_related(
        "bot_user", "runner", "provider", "default_repository"
    ):
        if profile.owner_id == actor.id or _visibility_scopes(
            actor, profile, profile, "profile", {"profile.use", "profile.manage"}
        ):
            visible.add(profile.id)
        if profile.owner_id == actor.id:
            owned.append(profile)
    complete = set(accessible_profiles(actor).values_list("id", flat=True))
    for profile in owned:
        resources: list[
            tuple[agents.AgentRunner | agents.AgentProvider | agents.AgentRepository, str, str]
        ] = [(profile.runner, "runner", "runner.use")]
        if profile.provider is not None:
            resources.append((profile.provider, "provider", "provider.use"))
        if profile.default_repository is not None:
            resources.append((profile.default_repository, "repository", "repository.read"))
        if any(
            not _visibility_scopes(actor, profile, resource, kind, {action})
            for resource, kind, action in resources
        ):
            complete.discard(profile.id)
    return visible, complete


def _resource_ids(actor: UserProfile, kind: str) -> list[UUID]:
    if not actor.is_active:
        return []
    model, action = {
        "runner": (agents.AgentRunner, "runner.use"),
        "provider": (agents.AgentProvider, "provider.use"),
        "repository": (agents.AgentRepository, "repository.read"),
    }[kind]
    visible = []
    for item in model.objects.filter(realm=actor.realm):
        if item.owner_id == actor.id:
            visible.append(item.id)
            continue
        if _visibility_scopes(actor, None, item, kind, {action}):
            visible.append(item.id)
    return visible


def _attachments(actor: UserProfile, profile: agents.AgentProfile) -> list[dict[str, object]]:
    result = []
    for subscription in Subscription.objects.filter(user_profile=profile.bot_user, active=True):
        stream = Stream.objects.filter(realm=actor.realm, recipient=subscription.recipient).first()
        if stream is None:
            continue
        try:
            access_stream_by_id(actor, stream.id, require_active_channel=False)
        except JsonableError:
            continue
        result.append({"stream_id": stream.id, "name": stream.name, "bot_member": True})
        if len(result) == 100:
            break
    return result


@safe_agent_endpoint
def list_agent_profiles(request: HttpRequest, user_profile: UserProfile) -> HttpResponse:
    offset, limit, ownership, access, host_kind, search = _list_options(request)
    visible, complete = _directory_profiles(user_profile)
    if access == "complete":
        visible &= complete
    elif access == "partial":
        visible -= complete
    profiles = agents.AgentProfile.objects.filter(realm=user_profile.realm, id__in=visible)
    if ownership == "mine":
        profiles = profiles.filter(owner=user_profile)
    elif ownership == "shared":
        profiles = profiles.exclude(owner=user_profile)
    if host_kind != "all":
        profiles = profiles.filter(runner__host_kind=host_kind)
    if search:
        profiles = profiles.filter(Q(name__icontains=search) | Q(description__icontains=search))
    profiles = profiles.select_related(
        "owner", "runner", "provider", "default_repository"
    ).order_by("id")
    return _success(
        request,
        {
            "count": profiles.count(),
            "profiles": [
                _profile_data(item, user_profile, complete)
                for item in profiles[offset : offset + limit]
            ],
        },
    )


@safe_agent_endpoint
def get_agent_profile(
    request: HttpRequest, user_profile: UserProfile, profile_id: UUID
) -> HttpResponse:
    visible, complete = _directory_profiles(user_profile)
    if profile_id not in visible:
        raise ValueError("Profile is unavailable.")
    profile = agents.AgentProfile.objects.select_related(
        "owner", "runner", "provider", "default_repository", "bot_user"
    ).get(id=profile_id, realm=user_profile.realm)
    setup = None
    if profile.owner_id == user_profile.id:
        setup = (
            agents.AgentSetupOperation.objects.filter(profile=profile, realm=user_profile.realm)
            .order_by("-created_at")
            .first()
        )
    return _success(
        request,
        {
            "profile": _profile_data(profile, user_profile, complete),
            "setup": _setup_data(setup) if setup is not None else None,
            "attachments": _attachments(user_profile, profile),
        },
    )


@safe_agent_endpoint
def recover_agent_profile(request: HttpRequest, user_profile: UserProfile) -> HttpResponse:
    if (
        not user_profile.is_active
        or set(request.GET) != {"idempotency_key"}
        or len(request.GET.getlist("idempotency_key")) != 1
    ):
        raise ValueError("Invalid recovery request.")
    retry_key = UUID(request.GET["idempotency_key"])
    setup = agents.AgentSetupOperation.objects.select_related("profile").get(
        realm=user_profile.realm, owner=user_profile, retry_key=retry_key, profile__isnull=False
    )
    assert setup.profile is not None
    return _success(
        request,
        {"profile": _profile_data(setup.profile, user_profile), "setup": _setup_data(setup)},
    )


@safe_agent_endpoint
def list_agent_runners(request: HttpRequest, user_profile: UserProfile) -> HttpResponse:
    offset, limit, ownership, access, host_kind, search = _list_options(request)
    rows = agents.AgentRunner.objects.filter(
        realm=user_profile.realm, id__in=_resource_ids(user_profile, "runner")
    )
    if ownership == "mine":
        rows = rows.filter(owner=user_profile)
    elif ownership == "shared":
        rows = rows.exclude(owner=user_profile)
    if access == "partial":
        rows = rows.none()
    if host_kind != "all":
        rows = rows.filter(host_kind=host_kind)
    if search:
        rows = rows.filter(name__icontains=search)
    rows = rows.order_by("id")
    return _success(
        request,
        {
            "count": rows.count(),
            "runners": [_runner_data(item, user_profile) for item in rows[offset : offset + limit]],
        },
    )


@safe_agent_endpoint
def list_agent_providers(request: HttpRequest, user_profile: UserProfile) -> HttpResponse:
    offset, limit, ownership, access, host_kind, search = _list_options(request)
    rows = agents.AgentProvider.objects.filter(
        realm=user_profile.realm, id__in=_resource_ids(user_profile, "provider")
    )
    if ownership == "mine":
        rows = rows.filter(owner=user_profile)
    elif ownership == "shared":
        rows = rows.exclude(owner=user_profile)
    if access == "partial":
        rows = rows.none()
    if host_kind != "all":
        rows = rows.filter(runner__host_kind=host_kind)
    if search:
        rows = rows.filter(Q(name__icontains=search) | Q(model_id__icontains=search))
    rows = rows.order_by("id")
    return _success(
        request,
        {
            "count": rows.count(),
            "providers": [
                _provider_data(item, user_profile) for item in rows[offset : offset + limit]
            ],
        },
    )


@safe_agent_endpoint
def list_agent_repositories(request: HttpRequest, user_profile: UserProfile) -> HttpResponse:
    offset, limit, ownership, access, host_kind, search = _list_options(request)
    rows = agents.AgentRepository.objects.filter(
        realm=user_profile.realm, id__in=_resource_ids(user_profile, "repository")
    )
    if ownership == "mine":
        rows = rows.filter(owner=user_profile)
    elif ownership == "shared":
        rows = rows.exclude(owner=user_profile)
    if access == "partial":
        rows = rows.none()
    if host_kind != "all":
        rows = rows.filter(runner__host_kind=host_kind)
    if search:
        rows = rows.filter(workspace_alias__icontains=search)
    rows = rows.order_by("id")
    return _success(
        request,
        {
            "count": rows.count(),
            "repositories": [
                _repository_data(item, user_profile) for item in rows[offset : offset + limit]
            ],
        },
    )


@safe_agent_endpoint
def get_agent_runner(
    request: HttpRequest, user_profile: UserProfile, runner_id: UUID
) -> HttpResponse:
    if runner_id not in _resource_ids(user_profile, "runner"):
        raise ValueError("Runner is unavailable.")
    runner = agents.AgentRunner.objects.get(id=runner_id, realm=user_profile.realm)
    return _success(request, {"runner": _runner_data(runner, user_profile)})


@safe_agent_endpoint
def get_agent_provider(
    request: HttpRequest, user_profile: UserProfile, provider_id: UUID
) -> HttpResponse:
    if provider_id not in _resource_ids(user_profile, "provider"):
        raise ValueError("Provider is unavailable.")
    provider = agents.AgentProvider.objects.get(id=provider_id, realm=user_profile.realm)
    return _success(request, {"provider": _provider_data(provider, user_profile)})


@safe_agent_endpoint
def get_agent_repository(
    request: HttpRequest, user_profile: UserProfile, repository_id: UUID
) -> HttpResponse:
    if repository_id not in _resource_ids(user_profile, "repository"):
        raise ValueError("Repository is unavailable.")
    repository = agents.AgentRepository.objects.get(id=repository_id, realm=user_profile.realm)
    return _success(request, {"repository": _repository_data(repository, user_profile)})


@safe_agent_endpoint
def list_agent_grants(request: HttpRequest, user_profile: UserProfile) -> HttpResponse:
    if not {"target_kind", "target_id"} <= set(request.GET) or set(request.GET) - {
        "target_kind",
        "target_id",
        "offset",
        "limit",
    }:
        raise ValueError("Invalid grant query.")
    if any(len(request.GET.getlist(key)) != 1 for key in request.GET):
        raise ValueError("Invalid grant query.")
    kind = request.GET["target_kind"]
    if kind not in {"profile", "runner", "provider", "repository"}:
        raise ValueError("Invalid grant target.")
    target_id = UUID(request.GET["target_id"])
    offset, limit = int(request.GET.get("offset", "0")), int(request.GET.get("limit", "50"))
    if offset < 0 or not 1 <= limit <= 100:
        raise ValueError("Invalid grant pagination.")
    if kind == "profile":
        visible, _complete = _directory_profiles(user_profile)
        authorized = target_id in visible
        target = agents.AgentProfile.objects.filter(id=target_id, realm=user_profile.realm).first()
    else:
        authorized = target_id in _resource_ids(user_profile, kind)
        model = {
            "runner": agents.AgentRunner,
            "provider": agents.AgentProvider,
            "repository": agents.AgentRepository,
        }[kind]
        target = model.objects.filter(id=target_id, realm=user_profile.realm).first()
    if not authorized or target is None:
        raise ValueError("Grant target is unavailable.")
    owner = target.owner_id == user_profile.id
    grants = agents.AgentGrant.objects.filter(
        realm=user_profile.realm, target_kind=kind, **{f"{kind}_id": target_id}
    ).order_by("created_at", "id")
    if not owner:
        grants = [grant for grant in grants if _principal_matches(user_profile, grant)]
    count = len(grants) if isinstance(grants, list) else grants.count()
    visible_repository_ids = (
        set(_resource_ids(user_profile, "repository")) if kind == "profile" and not owner else set()
    )
    result = []
    for grant in grants[offset : offset + limit]:
        scope = grant.scope
        if scope is not None:
            bot = target.bot_user if kind == "profile" else user_profile
            readable, _ = _readable_scope(user_profile, bot, scope)
            if not readable:
                scope = None
        result.append(
            {
                "id": str(grant.id),
                "target_kind": kind,
                "target_id": str(target_id),
                "principal": (
                    {"kind": "user", "user_id": grant.principal_user_id}
                    if owner and grant.principal_user_id
                    else {"kind": "group", "group_id": grant.principal_group_id}
                    if owner
                    else {"kind": "current_user"}
                ),
                "actions": grant.actions,
                "scope": scope,
                "scope_restricted": grant.scope is not None and scope is None,
                "repository_id": (
                    str(grant.repository_id)
                    if grant.repository_id
                    and (owner or grant.repository_id in visible_repository_ids)
                    else None
                ),
                "repository_restricted": grant.repository_id is not None,
                "expires_at": grant.expires_at.isoformat() if grant.expires_at else None,
                "revision": grant.policy_version,
                "revoked_at": grant.revoked_at.isoformat() if grant.revoked_at else None,
                "revoked": grant.revoked_at is not None,
                "allowed_actions": ["revoke"]
                if owner and user_profile.is_active and grant.revoked_at is None
                else [],
            }
        )
    return _success(request, {"count": count, "grants": result})


@safe_agent_endpoint
def list_channel_agent_attachments(
    request: HttpRequest, user_profile: UserProfile, stream_id: int
) -> HttpResponse:
    if set(request.GET):
        raise ValueError("Invalid attachment query.")
    stream, _subscription = access_stream_by_id(
        user_profile, stream_id, require_active_channel=False
    )
    visible, complete = _directory_profiles(user_profile)
    rows = (
        agents.AgentProfile.objects.filter(
            realm=user_profile.realm,
            id__in=visible,
            bot_user__subscription__recipient=stream.recipient,
            bot_user__subscription__active=True,
        )
        .select_related("owner", "runner", "provider", "default_repository")
        .order_by("id")[:100]
    )
    return _success(
        request,
        {
            "attachments": [
                {
                    "profile": _profile_data(profile, user_profile, complete),
                    "stream_id": stream.id,
                    "bot_member": True,
                }
                for profile in rows
            ]
        },
    )


@safe_agent_endpoint
def approve_agent_pairing(request: HttpRequest, user_profile: UserProfile) -> HttpResponse:
    data = payload(request, r.PairingApproval)
    if not agents.AgentRealmSettings.objects.filter(
        realm=user_profile.realm, enabled=True
    ).exists():
        raise ValueError
    pairing = agents.AgentPairing.objects.get(id=data.pairing_id, realm__isnull=True)
    pairing = actions.approve_pairing(user_profile, pairing, data.user_code)
    return _success(request, {"pairing": {"id": str(pairing.id), "state": pairing.state}})


@safe_agent_endpoint
def create_agent_provider(request: HttpRequest, user_profile: UserProfile) -> HttpResponse:
    data = payload(request, r.ProviderCreate)
    runner = agents.AgentRunner.objects.get(
        id=data.runner_id, realm=user_profile.realm, owner=user_profile
    )
    provider = actions.register_provider(
        user_profile,
        runner,
        name=data.name,
        base_url=data.base_url,
        model_id=data.model_id,
        allowed_models=data.allowed_models,
        context_window_tokens=data.context_window_tokens,
        max_output_tokens=data.max_output_tokens,
        credential=data.credential,
        local_credential_ref=data.local_credential_ref,
        api_mode=data.api_mode,
        network=p.serialize_payload(data.network),
        data_scope=list(data.data_scope),
    )
    return _success(
        request,
        {
            "provider": {
                "id": str(provider.id),
                "name": provider.name,
                "model_id": provider.model_id,
                "revision": provider.config_version,
            }
        },
    )


@safe_agent_endpoint
def update_agent_provider(
    request: HttpRequest, user_profile: UserProfile, provider_id: UUID
) -> HttpResponse:
    data = payload(request, r.ProviderUpdate)
    provider = actions.update_provider(
        user_profile,
        agents.AgentProvider.objects.get(id=provider_id, realm=user_profile.realm),
        expected_config_version=data.expected_config_version,
        expected_metadata_revision=data.expected_metadata_revision,
        name=data.name,
        base_url=data.base_url,
        model_id=data.model_id,
        allowed_models=data.allowed_models,
        context_window_tokens=data.context_window_tokens,
        max_output_tokens=data.max_output_tokens,
        credential_replacement=data.credential_replacement,
        local_credential_ref=data.local_credential_ref,
        api_mode=data.api_mode,
        network_policy=p.serialize_payload(data.network),
        data_scope=list(data.data_scope),
    )
    return _success(request, {"provider": _provider_data(provider)})


@safe_agent_endpoint
def update_agent_profile(
    request: HttpRequest, user_profile: UserProfile, profile_id: UUID
) -> HttpResponse:
    data = payload(request, r.ProfileUpdate)
    provider = (
        agents.AgentProvider.objects.get(id=data.provider_id, realm=user_profile.realm)
        if data.provider_id
        else None
    )
    repository = (
        agents.AgentRepository.objects.get(id=data.repository_id, realm=user_profile.realm)
        if data.repository_id
        else None
    )
    profile = actions.update_profile(
        user_profile,
        agents.AgentProfile.objects.get(id=profile_id, realm=user_profile.realm),
        expected_metadata_revision=data.expected_metadata_revision,
        expected_revision=data.expected_revision,
        name=data.name,
        description=data.description,
        provider=provider,
        repository=repository,
        adapter_id=data.adapter_id,
        adapter_version=data.adapter_version,
        mode=data.mode,
        default_mode=data.default_mode,
        sandbox_alias=data.sandbox_alias,
        actions=data.actions,
        network=p.serialize_payload(data.network) if data.network is not None else None,
        retain_network=data.retain_network,
        provider_network_version=data.provider_network_version,
        hard_cost_cap=data.hard_cost_cap,
        budget=p.serialize_payload(data.budget),
    )
    return _success(request, {"profile": _profile_data(profile, user_profile)})


@safe_agent_endpoint
def create_agent_repository(request: HttpRequest, user_profile: UserProfile) -> HttpResponse:
    data = payload(request, r.RepositoryCreate)
    runner = agents.AgentRunner.objects.get(
        id=data.runner_id, realm=user_profile.realm, owner=user_profile
    )
    repository = actions.register_repository(
        user_profile,
        runner,
        workspace_alias=data.workspace_alias,
        canonical_origin=data.canonical_origin,
        allowed_refs=data.allowed_refs,
        required_checks=[p.serialize_payload(item) for item in data.required_checks],
    )
    return _success(
        request,
        {
            "repository": {
                "id": str(repository.id),
                "workspace_alias": repository.workspace_alias,
                "revision": repository.policy_version,
            }
        },
    )


@safe_agent_endpoint
def create_agent_profile(request: HttpRequest, user_profile: UserProfile) -> HttpResponse:
    data = payload(request, r.ProfileCreate)
    runner = agents.AgentRunner.objects.get(id=data.runner_id, realm=user_profile.realm)
    require_agent_resource_access(user_profile, runner, target_kind="runner", action="runner.use")
    provider = (
        agents.AgentProvider.objects.get(id=data.provider_id, realm=user_profile.realm)
        if data.provider_id
        else None
    )
    repository = (
        agents.AgentRepository.objects.get(id=data.repository_id, realm=user_profile.realm)
        if data.repository_id
        else None
    )
    catalog = p.RunnerCatalog.model_validate(runner.catalog_report)
    sandbox = next((item for item in catalog.sandboxes if item.alias == data.sandbox_alias), None)
    if sandbox is None:
        raise ValueError
    # Ordinary profile owners select a catalog alias, never an image or host command.
    if data.network != p.NetworkPolicy() and (
        provider is None or data.network != p.NetworkPolicy.model_validate(provider.network_policy)
    ):
        raise ValueError
    policy = {
        "version": 1,
        "actions": data.actions,
        "scope": {"kind": "direct", "participant_user_ids": [user_profile.id]},
        "sandbox": p.serialize_payload(sandbox),
        "network": p.serialize_payload(data.network),
        "hard_cost_cap": data.hard_cost_cap,
    }
    profile = actions.create_profile(
        user_profile,
        name=data.name,
        description=data.description,
        runner=runner,
        adapter_id=data.adapter_id,
        adapter_version=data.adapter_version,
        mode=data.mode,
        default_mode=data.default_mode,
        provider=provider,
        repository=repository,
        policy=policy,
        budget=p.serialize_payload(data.budget),
        provider_network_version=data.provider_network_version,
        idempotency_key=data.idempotency_key,
    )
    setup = agents.AgentSetupOperation.objects.get(
        realm=user_profile.realm, owner=user_profile, retry_key=data.idempotency_key
    )
    return _success(
        request, {"profile": _profile_data(profile, user_profile), "setup_id": str(setup.id)}
    )


@safe_agent_endpoint
def create_provider_probe_view(
    request: HttpRequest, user_profile: UserProfile, provider_id: UUID
) -> HttpResponse:
    data = payload(request, r.SetupRequest)
    provider = agents.AgentProvider.objects.get(id=provider_id, realm=user_profile.realm)
    setup = actions.create_provider_probe(
        user_profile, provider, retry_key=data.retry_key, expected_revision=data.expected_revision
    )
    return _success(request, {"setup_id": str(setup.id), "phase": setup.phase})


@safe_agent_endpoint
def retry_profile_setup(
    request: HttpRequest, user_profile: UserProfile, profile_id: UUID
) -> HttpResponse:
    data = payload(request, r.SetupRequest)
    setup = actions.retry_profile_setup(
        user_profile,
        agents.AgentProfile.objects.get(id=profile_id, realm=user_profile.realm),
        expected_revision=data.expected_revision,
        retry_key=data.retry_key,
    )
    return _success(request, {"setup_id": str(setup.id), "phase": setup.phase})


@safe_agent_endpoint
def get_agent_setup(
    request: HttpRequest, user_profile: UserProfile, setup_id: UUID
) -> HttpResponse:
    setup = agents.AgentSetupOperation.objects.get(
        id=setup_id, realm=user_profile.realm, owner=user_profile
    )
    return _success(
        request,
        {
            "setup": _setup_data(setup),
        },
    )


@safe_agent_endpoint
def pause_agent_profile(
    request: HttpRequest, user_profile: UserProfile, profile_id: UUID
) -> HttpResponse:
    data = payload(request, r.RevisionRequest)
    profile = actions.pause_profile(
        user_profile,
        agents.AgentProfile.objects.get(id=profile_id, realm=user_profile.realm),
        expected_revision=data.expected_revision,
    )
    return _success(request, {"profile": _profile_data(profile, user_profile)})


@safe_agent_endpoint
def archive_agent_profile(
    request: HttpRequest, user_profile: UserProfile, profile_id: UUID
) -> HttpResponse:
    data = payload(request, r.ArchiveProfile)
    profile = actions.archive_profile(
        user_profile,
        agents.AgentProfile.objects.get(id=profile_id, realm=user_profile.realm),
        expected_revision=data.expected_revision,
    )
    return _success(request, {"profile": _profile_data(profile, user_profile)})


@safe_agent_endpoint
def enable_agent_profile(
    request: HttpRequest, user_profile: UserProfile, profile_id: UUID
) -> HttpResponse:
    data = payload(request, r.RevisionRequest)
    profile = actions.enable_profile(
        user_profile,
        agents.AgentProfile.objects.get(id=profile_id, realm=user_profile.realm),
        expected_revision=data.expected_revision,
    )
    return _success(request, {"profile": _profile_data(profile, user_profile)})


@safe_agent_endpoint
@transaction.atomic
def revoke_agent_runner(
    request: HttpRequest, user_profile: UserProfile, runner_id: UUID
) -> HttpResponse:
    data = payload(request, r.RevisionRequest)
    runner = agents.AgentRunner.objects.select_for_update().get(
        id=runner_id, realm=user_profile.realm, owner=user_profile
    )
    if runner.policy_version != data.expected_revision:
        raise ValueError
    actions.revoke_runner(runner)
    return _success(request)


@safe_agent_endpoint
def update_agent_runner_metadata(
    request: HttpRequest, user_profile: UserProfile, runner_id: UUID
) -> HttpResponse:
    data = payload(request, r.RunnerMetadataUpdate)
    runner = actions.update_runner_metadata(
        user_profile,
        agents.AgentRunner.objects.get(id=runner_id, realm=user_profile.realm),
        expected_metadata_revision=data.expected_metadata_revision,
        name=data.name,
        host_kind=data.host_kind,
    )
    return _success(request, {"runner": _runner_data(runner)})


@safe_agent_endpoint
def get_team_default(request: HttpRequest, user_profile: UserProfile) -> HttpResponse:
    return _success(request, {"default": _default_data(user_profile)})


@safe_agent_endpoint
def update_team_default(request: HttpRequest, user_profile: UserProfile) -> HttpResponse:
    data = payload(request, r.DefaultSelectionUpdate)
    profile = (
        agents.AgentProfile.objects.get(id=data.profile_id, realm=user_profile.realm)
        if data.profile_id is not None
        else None
    )
    settings = actions.update_team_default(
        user_profile,
        profile=profile,
        expected_selection_revision=data.expected_selection_revision,
    )
    return _success(
        request, {"default": _default_data(user_profile), "revision": settings.revision}
    )


@safe_agent_endpoint
def resolve_team_agent_selection(request: HttpRequest, user_profile: UserProfile) -> HttpResponse:
    data = payload(request, r.SelectionResolve)
    result = resolve_agent_selection(
        user_profile,
        destination=data.destination,
        source_message_id=data.source_message_id,
        job_kind=data.job_kind,
        repository_id=data.repository_id,
        explicit_profile_id=data.explicit_profile_id,
        selection_state=data.selection_state,
    )
    return _success(request, {"selection": result.data()})


@safe_agent_endpoint
@transaction.atomic
def create_agent_grant_view(request: HttpRequest, user_profile: UserProfile) -> HttpResponse:
    data = payload(request, r.GrantCreate)
    target: agents.AgentRunner | agents.AgentProvider | agents.AgentRepository | agents.AgentProfile
    if data.target_kind == "runner":
        target = agents.AgentRunner.objects.select_for_update().get(
            id=data.target_id, realm=user_profile.realm, owner=user_profile
        )
        revision = target.policy_version
    elif data.target_kind == "provider":
        target = agents.AgentProvider.objects.select_for_update().get(
            id=data.target_id, realm=user_profile.realm, owner=user_profile
        )
        revision = target.config_version
    elif data.target_kind == "repository":
        target = agents.AgentRepository.objects.select_for_update().get(
            id=data.target_id, realm=user_profile.realm, owner=user_profile
        )
        revision = target.policy_version
    else:
        target = agents.AgentProfile.objects.select_for_update().get(
            id=data.target_id, realm=user_profile.realm, owner=user_profile
        )
        revision = target.revision
    if revision != data.expected_revision:
        raise ValueError
    principal = (
        UserProfile.objects.get(id=data.principal_user_id, realm=user_profile.realm)
        if data.principal_user_id is not None
        else None
    )
    repository = (
        agents.AgentRepository.objects.get(id=data.repository_id, realm=user_profile.realm)
        if data.repository_id
        else None
    )
    grant = actions.create_agent_grant(
        user_profile,
        principal_user=principal,
        principal_group_id=data.principal_group_id,
        target_kind=data.target_kind,
        target=target,
        actions=list(data.actions),
        expires_at=data.expires_at,
        scope=p.serialize_payload(data.scope) if data.scope else None,
        repository=repository,
    )
    return _success(
        request,
        {
            "grant": {
                "id": str(grant.id),
                "target_kind": grant.target_kind,
                "revision": grant.policy_version,
            }
        },
    )


@safe_agent_endpoint
@transaction.atomic
def revoke_agent_grant(
    request: HttpRequest, user_profile: UserProfile, grant_id: UUID
) -> HttpResponse:
    data = payload(request, r.RevisionRequest)
    grant = agents.AgentGrant.objects.select_for_update().get(
        id=grant_id, realm=user_profile.realm, owner=user_profile
    )
    if grant.policy_version != data.expected_revision:
        raise ValueError
    grant.revoked_at = now()
    grant.policy_version += 1
    grant.save(update_fields=["revoked_at", "policy_version", "updated_at"])
    return _success(request)


@safe_agent_endpoint
def attach_agent_profile_stream(
    request: HttpRequest, user_profile: UserProfile, profile_id: UUID
) -> HttpResponse:
    data = payload(request, r.ChannelRequest)
    profile = agents.AgentProfile.objects.get(id=profile_id, realm=user_profile.realm)
    stream = Stream.objects.get(id=data.stream_id, realm=user_profile.realm)
    actions.attach_profile_to_stream(
        user_profile, profile, stream, expected_revision=data.expected_revision
    )
    return _success(request)


def _share_principal(user_profile: UserProfile, data: r.ProfilePrincipal) -> UserProfile | None:
    if data.principal_user_id is None:
        return None
    return UserProfile.objects.get(id=data.principal_user_id, realm=user_profile.realm)


@safe_agent_endpoint
@transaction.atomic
def share_agent_profile_view(
    request: HttpRequest, user_profile: UserProfile, profile_id: UUID
) -> HttpResponse:
    data = payload(request, r.ProfileShare)
    profile = agents.AgentProfile.objects.get(id=profile_id, realm=user_profile.realm)
    grants, skipped = actions.share_agent_profile(
        user_profile,
        profile,
        principal_user=_share_principal(user_profile, data),
        principal_group_id=data.principal_group_id,
        allow_job_control=data.allow_job_control,
        allow_job_review=data.allow_job_review,
    )
    return _success(
        request,
        {
            "grants": [
                {
                    "id": str(grant.id),
                    "target_kind": grant.target_kind,
                    "revision": grant.policy_version,
                }
                for grant in grants
            ],
            "skipped": skipped,
        },
    )


@safe_agent_endpoint
@transaction.atomic
def unshare_agent_profile_view(
    request: HttpRequest, user_profile: UserProfile, profile_id: UUID
) -> HttpResponse:
    data = payload(request, r.ProfileUnshare)
    profile = agents.AgentProfile.objects.get(id=profile_id, realm=user_profile.realm)
    actions.unshare_agent_profile(
        user_profile,
        profile,
        principal_user=_share_principal(user_profile, data),
        principal_group_id=data.principal_group_id,
    )
    return _success(request)
