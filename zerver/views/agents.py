"""Human connection APIs use Zulip authentication and form-encoded payload JSON."""

from collections.abc import Callable
from functools import wraps
from typing import ParamSpec, TypeVar
from uuid import UUID

from django.core.exceptions import ObjectDoesNotExist, ValidationError
from django.db import transaction
from django.http import HttpRequest, HttpResponse
from django.utils.timezone import now

from zerver.actions import agents as actions
from zerver.lib import agent_protocol as p
from zerver.lib import agent_requests as r
from zerver.lib.agent_policy import accessible_profiles, require_agent_resource_access
from zerver.lib.exceptions import JsonableError
from zerver.lib.response import json_response, json_success
from zerver.models import UserProfile, agents
from zerver.models.streams import Stream

P = ParamSpec("P")
T = TypeVar("T", bound=p.Versioned)


def safe_agent_endpoint(view: Callable[P, HttpResponse]) -> Callable[P, HttpResponse]:
    @wraps(view)
    def wrapped(*args: P.args, **kwargs: P.kwargs) -> HttpResponse:
        try:
            return view(*args, **kwargs)
        except (ValueError, ValidationError, ObjectDoesNotExist, JsonableError):
            return json_response(
                "error", "Agent request rejected.", {"schema_version": 1}, status=400
            )

    return wrapped


def payload(request: HttpRequest, schema: type[T]) -> T:
    if set(request.POST) != {"payload"} or len(request.POST.getlist("payload")) != 1:
        raise ValueError("Invalid request.")
    return schema.model_validate_json(request.POST["payload"])


def _profile_data(profile: agents.AgentProfile) -> dict[str, object]:
    return {
        "id": str(profile.id),
        "name": profile.name,
        "description": profile.description,
        "runner_id": str(profile.runner_id),
        "provider_id": str(profile.provider_id) if profile.provider_id else None,
        "repository_id": (
            str(profile.default_repository_id) if profile.default_repository_id else None
        ),
        "state": profile.desired_state,
        "readiness_state": profile.readiness_state,
        "revision": profile.revision,
        "bot_user_id": profile.bot_user_id,
        "default_mode": profile.default_mode,
        "capabilities": profile.capability_report,
    }


def _success(request: HttpRequest, data: dict[str, object] | None = None) -> HttpResponse:
    return json_success(request, {"schema_version": 1, **(data or {})})


@safe_agent_endpoint
def list_agent_profiles(request: HttpRequest, user_profile: UserProfile) -> HttpResponse:
    profiles = accessible_profiles(user_profile).order_by("id")
    offset, limit = int(request.GET.get("offset", "0")), int(request.GET.get("limit", "50"))
    if offset < 0 or not 1 <= limit <= 100:
        raise ValueError
    return _success(
        request,
        {
            "count": profiles.count(),
            "profiles": [_profile_data(item) for item in profiles[offset : offset + limit]],
        },
    )


@safe_agent_endpoint
def get_agent_profile(
    request: HttpRequest, user_profile: UserProfile, profile_id: UUID
) -> HttpResponse:
    return _success(
        request, {"profile": _profile_data(accessible_profiles(user_profile).get(id=profile_id))}
    )


@safe_agent_endpoint
def list_agent_runners(request: HttpRequest, user_profile: UserProfile) -> HttpResponse:
    visible = []
    for runner in agents.AgentRunner.objects.filter(realm=user_profile.realm):
        try:
            require_agent_resource_access(
                user_profile, runner, target_kind="runner", action="runner.use"
            )
        except JsonableError:
            if runner.owner_id != user_profile.id:
                continue
        visible.append(
            {
                "id": str(runner.id),
                "name": runner.name,
                "status": runner.status,
                "revision": runner.policy_version,
                "catalog_revision": runner.catalog_revision,
                "catalog": runner.catalog_report,
            }
        )
    return _success(request, {"count": len(visible), "runners": visible})


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
        idempotency_key=data.idempotency_key,
    )
    setup = agents.AgentSetupOperation.objects.get(
        realm=user_profile.realm, owner=user_profile, retry_key=data.idempotency_key
    )
    return _success(request, {"profile": _profile_data(profile), "setup_id": str(setup.id)})


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
            "setup_id": str(setup.id),
            "phase": setup.phase,
            "requirements": setup.requirements,
            "profile_revision": setup.profile_revision,
            "provider_config_version": setup.provider_config_version,
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
    return _success(request, {"profile": _profile_data(profile)})


@safe_agent_endpoint
def archive_agent_profile(
    request: HttpRequest, user_profile: UserProfile, profile_id: UUID
) -> HttpResponse:
    data = payload(request, r.RevisionRequest)
    profile = actions.archive_profile(
        user_profile,
        agents.AgentProfile.objects.get(id=profile_id, realm=user_profile.realm),
        expected_revision=data.expected_revision,
    )
    return _success(request, {"profile": _profile_data(profile)})


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
    return _success(request, {"profile": _profile_data(profile)})


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
