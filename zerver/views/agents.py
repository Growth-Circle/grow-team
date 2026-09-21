"""Human agent connection endpoints authenticated by Zulip REST dispatch."""

from uuid import UUID

from django.http import HttpRequest, HttpResponse

from zerver.actions.agents import (
    approve_pairing,
    archive_profile,
    attach_profile_to_stream,
    create_agent_grant,
    create_profile,
    create_provider_probe,
    pause_profile,
    register_provider,
    register_repository,
)
from zerver.lib.agent_policy import (
    AgentAccessDenied,
    accessible_profiles,
    require_agent_resource_access,
)
from zerver.lib.exceptions import JsonableError
from zerver.lib.response import json_success
from zerver.lib.typed_endpoint import typed_endpoint
from zerver.models import UserProfile, agents
from zerver.models.streams import Stream


def _runner_for_owner(user_profile: UserProfile, runner_id: UUID) -> agents.AgentRunner:
    try:
        runner = agents.AgentRunner.objects.get(id=runner_id, realm=user_profile.realm)
        require_agent_resource_access(
            user_profile, runner, target_kind="runner", action="runner.use"
        )
        return runner
    except (agents.AgentRunner.DoesNotExist, AgentAccessDenied):
        raise JsonableError("Runner is unavailable.") from None


def _profile_data(profile: agents.AgentProfile) -> dict[str, object]:
    return {
        "id": str(profile.id),
        "name": profile.name,
        "runner_id": str(profile.runner_id),
        "provider_id": str(profile.provider_id) if profile.provider_id else None,
        "state": profile.desired_state,
        "readiness_state": profile.readiness_state,
        "revision": profile.revision,
        "bot_user_id": profile.bot_user_id,
    }


def list_agent_profiles(request: HttpRequest, user_profile: UserProfile) -> HttpResponse:
    profiles = accessible_profiles(user_profile).order_by("id")
    return json_success(
        request, {"schema_version": 1, "profiles": [_profile_data(profile) for profile in profiles]}
    )


@typed_endpoint
def approve_agent_pairing(
    request: HttpRequest, user_profile: UserProfile, *, pairing_id: UUID, user_code: str
) -> HttpResponse:
    if not agents.AgentRealmSettings.objects.filter(
        realm=user_profile.realm, enabled=True
    ).exists():
        raise JsonableError("Agent connections are disabled.")
    pairing = agents.AgentPairing.objects.filter(id=pairing_id, realm__isnull=True).first()
    if pairing is None:
        raise JsonableError("Pairing is unavailable.")
    try:
        pairing = approve_pairing(user_profile, pairing, user_code)
    except ValueError:
        raise JsonableError("Agent request rejected.") from None
    return json_success(
        request, {"schema_version": 1, "pairing": {"id": str(pairing.id), "state": pairing.state}}
    )


@typed_endpoint
def create_agent_provider(
    request: HttpRequest,
    user_profile: UserProfile,
    *,
    runner_id: UUID,
    name: str,
    base_url: str,
    model_id: str,
    allowed_models: list[str],
    context_window_tokens: int,
    max_output_tokens: int,
    credential: str | None = None,
    local_credential_ref: str = "",
) -> HttpResponse:
    try:
        provider = register_provider(
            user_profile,
            _runner_for_owner(user_profile, runner_id),
            name=name,
            base_url=base_url,
            model_id=model_id,
            allowed_models=allowed_models,
            context_window_tokens=context_window_tokens,
            max_output_tokens=max_output_tokens,
            credential=credential,
            local_credential_ref=local_credential_ref,
        )
    except ValueError:
        raise JsonableError("Agent request rejected.") from None
    # Do not serialize credential input, ciphertext, wrapped key, or local reference.
    return json_success(
        request,
        {
            "schema_version": 1,
            "provider": {
                "id": str(provider.id),
                "name": provider.name,
                "model_id": provider.model_id,
            },
        },
    )


@typed_endpoint
def create_agent_repository(
    request: HttpRequest,
    user_profile: UserProfile,
    *,
    runner_id: UUID,
    workspace_alias: str,
    canonical_origin: str,
    allowed_refs: list[str],
    required_checks: list[dict[str, object]] | None = None,
) -> HttpResponse:
    try:
        repository = register_repository(
            user_profile,
            _runner_for_owner(user_profile, runner_id),
            workspace_alias=workspace_alias,
            canonical_origin=canonical_origin,
            allowed_refs=allowed_refs,
            required_checks=required_checks,
        )
    except ValueError:
        raise JsonableError("Agent request rejected.") from None
    return json_success(
        request,
        {
            "schema_version": 1,
            "repository": {"id": str(repository.id), "workspace_alias": repository.workspace_alias},
        },
    )


@typed_endpoint
def create_provider_probe_view(
    request: HttpRequest, user_profile: UserProfile, *, provider_id: UUID, retry_key: UUID
) -> HttpResponse:
    provider = agents.AgentProvider.objects.filter(id=provider_id, realm=user_profile.realm).first()
    if provider is None:
        raise JsonableError("Provider is unavailable.")
    try:
        setup = create_provider_probe(user_profile, provider, retry_key=retry_key)
    except ValueError:
        raise JsonableError("Agent request rejected.") from None
    return json_success(
        request, {"schema_version": 1, "setup_id": str(setup.id), "phase": setup.phase}
    )


@typed_endpoint
def create_agent_profile(
    request: HttpRequest,
    user_profile: UserProfile,
    *,
    runner_id: UUID,
    name: str,
    adapter_id: str,
    adapter_version: str,
    idempotency_key: UUID,
    mode: str = "acp",
    description: str = "",
    provider_id: UUID | None = None,
    repository_id: UUID | None = None,
) -> HttpResponse:
    runner = _runner_for_owner(user_profile, runner_id)
    provider = None
    repository = None
    if provider_id is not None:
        provider = agents.AgentProvider.objects.filter(
            id=provider_id, realm=user_profile.realm
        ).first()
        if provider is None:
            raise JsonableError("Provider is unavailable.")
    if repository_id is not None:
        repository = agents.AgentRepository.objects.filter(
            id=repository_id, realm=user_profile.realm
        ).first()
        if repository is None:
            raise JsonableError("Repository is unavailable.")
    try:
        profile = create_profile(
            user_profile,
            name=name,
            runner=runner,
            adapter_id=adapter_id,
            adapter_version=adapter_version,
            mode=mode,
            description=description,
            provider=provider,
            repository=repository,
            idempotency_key=idempotency_key,
        )
    except ValueError:
        raise JsonableError("Agent request rejected.") from None
    return json_success(request, {"schema_version": 1, "profile": _profile_data(profile)})


@typed_endpoint
def pause_agent_profile(
    request: HttpRequest, user_profile: UserProfile, *, profile_id: UUID
) -> HttpResponse:
    profile = agents.AgentProfile.objects.filter(id=profile_id, realm=user_profile.realm).first()
    if profile is None:
        raise JsonableError("Profile is unavailable.")
    try:
        profile = pause_profile(user_profile, profile)
    except ValueError:
        raise JsonableError("Agent request rejected.") from None
    return json_success(request, {"schema_version": 1, "profile": _profile_data(profile)})


@typed_endpoint
def archive_agent_profile(
    request: HttpRequest, user_profile: UserProfile, *, profile_id: UUID
) -> HttpResponse:
    profile = agents.AgentProfile.objects.filter(id=profile_id, realm=user_profile.realm).first()
    if profile is None:
        raise JsonableError("Profile is unavailable.")
    try:
        profile = archive_profile(user_profile, profile)
    except ValueError:
        raise JsonableError("Agent request rejected.") from None
    return json_success(request, {"schema_version": 1, "profile": _profile_data(profile)})


@typed_endpoint
def create_agent_grant_view(
    request: HttpRequest,
    user_profile: UserProfile,
    *,
    target_kind: str,
    target_id: UUID,
    actions: list[str],
    principal_user_id: int | None = None,
    principal_group_id: int | None = None,
) -> HttpResponse:
    target: (
        agents.AgentRunner
        | agents.AgentProvider
        | agents.AgentRepository
        | agents.AgentProfile
        | None
    )
    if target_kind == "runner":
        target = agents.AgentRunner.objects.filter(
            id=target_id, realm=user_profile.realm, owner=user_profile
        ).first()
    elif target_kind == "provider":
        target = agents.AgentProvider.objects.filter(
            id=target_id, realm=user_profile.realm, owner=user_profile
        ).first()
    elif target_kind == "repository":
        target = agents.AgentRepository.objects.filter(
            id=target_id, realm=user_profile.realm, owner=user_profile
        ).first()
    elif target_kind == "profile":
        target = agents.AgentProfile.objects.filter(
            id=target_id, realm=user_profile.realm, owner=user_profile
        ).first()
    else:
        raise JsonableError("Invalid grant.")
    principal = None
    if principal_user_id is not None:
        principal = UserProfile.objects.filter(
            id=principal_user_id, realm=user_profile.realm
        ).first()
    if target is None or (principal_user_id is not None and principal is None):
        raise JsonableError("Grant target is unavailable.")
    try:
        grant = create_agent_grant(
            user_profile,
            principal_user=principal,
            principal_group_id=principal_group_id,
            target_kind=target_kind,
            target=target,
            actions=actions,
        )
    except ValueError:
        raise JsonableError("Agent request rejected.") from None
    return json_success(
        request,
        {"schema_version": 1, "grant": {"id": str(grant.id), "target_kind": grant.target_kind}},
    )


@typed_endpoint
def attach_agent_profile_stream(
    request: HttpRequest, user_profile: UserProfile, *, profile_id: UUID, stream_id: int
) -> HttpResponse:
    profile = agents.AgentProfile.objects.filter(id=profile_id, realm=user_profile.realm).first()
    stream = Stream.objects.filter(id=stream_id, realm=user_profile.realm).first()
    if profile is None or stream is None:
        raise JsonableError("Channel is unavailable.")
    try:
        attach_profile_to_stream(user_profile, profile, stream)
    except ValueError:
        raise JsonableError("Agent request rejected.") from None
    return json_success(request, {"schema_version": 1})
