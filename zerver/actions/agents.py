"""Transactional connection actions. Runner execution belongs to Task 3."""

import hashlib
import re
import secrets
import uuid
from datetime import datetime, timedelta
from urllib.parse import urlsplit

from django.db import transaction
from django.db.models import F
from django.utils.timezone import now

from zerver.actions.create_user import do_create_user
from zerver.actions.streams import bulk_add_subscriptions
from zerver.lib import agent_protocol as protocol
from zerver.lib.agent_policy import check_agent_access, require_agent_resource_access
from zerver.lib.agent_requests import SetupResult
from zerver.lib.agent_secrets import credential_matches, encrypt_agent_secret, hash_agent_credential
from zerver.lib.streams import filter_stream_authorization_for_adding_subscribers
from zerver.lib.users import (
    check_can_create_bot,
    check_full_name,
    validate_short_name_and_construct_bot_email,
)
from zerver.models import UserProfile, agents
from zerver.models.groups import UserGroup
from zerver.models.streams import Stream

PAIRING_MAX_FAILURES = 5
PAIRING_TTL = timedelta(minutes=10)
ACCESS_TOKEN_TTL = timedelta(hours=24)
REFRESH_TOKEN_TTL = timedelta(days=30)


@transaction.atomic()
def start_pairing(
    device_name: str,
    fingerprint: str,
    user_code: str,
    polling_secret: str,
    *,
    expires_at: datetime | None = None,
) -> agents.AgentPairing:
    if (
        not 1 <= len(device_name) <= 200
        or not 1 <= len(fingerprint) <= 128
        or not 4 <= len(user_code) <= 64
        or not 32 <= len(polling_secret) <= 512
    ):
        raise ValueError("Invalid pairing request.")
    return agents.AgentPairing.objects.create(
        realm=None,
        device_name=device_name,
        fingerprint=fingerprint,
        user_code_hash=hash_agent_credential(user_code),
        polling_secret_hash=hash_agent_credential(polling_secret),
        expires_at=expires_at or now() + PAIRING_TTL,
    )


def _pairing_code_matches(pairing: agents.AgentPairing, user_code: str) -> bool:
    return credential_matches(user_code, pairing.user_code_hash)


def approve_pairing(
    owner: UserProfile, pairing: agents.AgentPairing, user_code: str
) -> agents.AgentPairing:
    unavailable = False
    with transaction.atomic():
        pairing = agents.AgentPairing.objects.select_for_update().get(id=pairing.id)
        if pairing.state != "pending" or pairing.expires_at <= now():
            pairing.state = "expired" if pairing.expires_at <= now() else pairing.state
            pairing.save(update_fields=["state", "updated_at"])
            unavailable = True
        elif not _pairing_code_matches(pairing, user_code):
            pairing.failed_attempts += 1
            if pairing.failed_attempts >= PAIRING_MAX_FAILURES:
                pairing.state = "rejected"
            pairing.save(update_fields=["failed_attempts", "state", "updated_at"])
            unavailable = True
        else:
            pairing.realm = owner.realm
            pairing.owner = owner
            pairing.approved_at = now()
            pairing.state = "approved"
            pairing.save(update_fields=["realm", "owner", "approved_at", "state", "updated_at"])
    if unavailable:
        raise ValueError("Pairing is unavailable.")
    return pairing


def exchange_pairing(pairing: agents.AgentPairing, polling_secret: str) -> tuple[str, str]:
    unavailable = False
    with transaction.atomic():
        pairing = agents.AgentPairing.objects.select_for_update().get(id=pairing.id)
        if pairing.state != "approved" or pairing.owner is None or pairing.expires_at <= now():
            unavailable = True
        elif not credential_matches(polling_secret, pairing.polling_secret_hash):
            pairing.failed_attempts += 1
            if pairing.failed_attempts >= PAIRING_MAX_FAILURES:
                pairing.state = "rejected"
            pairing.save(update_fields=["failed_attempts", "state", "updated_at"])
            unavailable = True
        else:
            runner = agents.AgentRunner.objects.create(
                realm=pairing.owner.realm,
                owner=pairing.owner,
                name=pairing.device_name,
                fingerprint=pairing.fingerprint,
            )
            token = secrets.token_urlsafe(32)
            refresh = secrets.token_urlsafe(32)
            agents.AgentRunnerCredential.objects.create(
                realm=runner.realm,
                runner=runner,
                token_hash=hash_agent_credential(token),
                refresh_hash=hash_agent_credential(refresh),
                expires_at=now() + ACCESS_TOKEN_TTL,
                refresh_expires_at=now() + REFRESH_TOKEN_TTL,
            )
            pairing.runner = runner
            pairing.exchanged_at = now()
            pairing.state = "exchanged"
            pairing.save(update_fields=["runner", "exchanged_at", "state", "updated_at"])
    if unavailable:
        raise ValueError("Pairing is unavailable.")
    return token, refresh


def rotate_runner_credential(
    credential: agents.AgentRunnerCredential, refresh_token: str
) -> tuple[agents.AgentRunnerCredential, str, str]:
    unavailable = False
    with transaction.atomic():
        agents.AgentRunner.objects.select_for_update().get(id=credential.runner_id)
        credential = agents.AgentRunnerCredential.objects.select_for_update().get(id=credential.id)
        if (
            credential.revoked_at is not None
            or credential.runner.revoked_at is not None
            or credential.refresh_expires_at <= now()
            or not credential_matches(refresh_token, credential.refresh_hash)
        ):
            unavailable = True
        else:
            token = secrets.token_urlsafe(32)
            refresh = secrets.token_urlsafe(32)
            replacement = agents.AgentRunnerCredential.objects.create(
                realm=credential.realm,
                runner=credential.runner,
                token_hash=hash_agent_credential(token),
                refresh_hash=hash_agent_credential(refresh),
                expires_at=now() + ACCESS_TOKEN_TTL,
                refresh_expires_at=now() + REFRESH_TOKEN_TTL,
                parent=credential,
            )
            credential.revoked_at = now()
            credential.rotated_at = now()
            credential.save(update_fields=["revoked_at", "rotated_at", "updated_at"])
    if unavailable:
        raise ValueError("Runner credential is unavailable.")
    return replacement, token, refresh


@transaction.atomic()
def revoke_runner(runner: agents.AgentRunner) -> None:
    runner = agents.AgentRunner.objects.select_for_update().get(id=runner.id)
    if runner.revoked_at is not None:
        return
    runner.revoked_at = now()
    runner.status = "revoked"
    runner.save(update_fields=["revoked_at", "status", "updated_at"])
    agents.AgentRunnerCredential.objects.filter(runner=runner, revoked_at__isnull=True).update(
        revoked_at=runner.revoked_at
    )
    # Task 3 consumes this durable stopping state and records runner stop evidence.
    active_attempts = agents.AgentAttempt.objects.filter(runner=runner, active=True)
    active_attempts.update(process_state="stopping")
    agents.AgentJob.objects.filter(agentattempt__runner=runner, agentattempt__active=True).update(
        status="cancel_requested", version=F("version") + 1
    )


class RunnerCredentialError(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__("Runner credential is unavailable.")
        self.code = code


def _authenticate_runner(value: str, *, refresh: bool) -> agents.AgentRunnerCredential:
    field = "refresh_hash" if refresh else "token_hash"
    credential = (
        agents.AgentRunnerCredential.objects.select_related("runner")
        .filter(**{field: hash_agent_credential(value)})
        .first()
    )
    if credential is None or not credential_matches(value, getattr(credential, field)):
        raise RunnerCredentialError("credential_invalid")
    if credential.revoked_at is not None or credential.runner.revoked_at is not None:
        raise RunnerCredentialError("credential_revoked")
    expiry = credential.refresh_expires_at if refresh else credential.expires_at
    if expiry <= now():
        raise RunnerCredentialError("credential_expired")
    return credential


def authenticate_runner_token(token: str) -> agents.AgentRunnerCredential:
    return _authenticate_runner(token, refresh=False)


def authenticate_runner_refresh(refresh_token: str) -> agents.AgentRunnerCredential:
    return _authenticate_runner(refresh_token, refresh=True)


def _safe_provider_url(value: str) -> str:
    parsed = urlsplit(value)
    if (
        parsed.scheme not in {"https", "http"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("Invalid provider configuration.")
    return value


def _safe_origin(value: str) -> str:
    parsed = urlsplit(value)
    if (
        parsed.scheme not in {"https", "ssh"}
        or not parsed.hostname
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("Invalid repository configuration.")
    if parsed.username or parsed.password or value.startswith(("/", "file:", "~")):
        raise ValueError("Invalid repository configuration.")
    return value


@transaction.atomic()
def register_provider(
    owner: UserProfile,
    runner: agents.AgentRunner,
    *,
    name: str,
    base_url: str,
    model_id: str,
    allowed_models: list[str],
    context_window_tokens: int,
    max_output_tokens: int,
    credential: str | None = None,
    local_credential_ref: str = "",
    api_mode: str = "chat_completions",
    network: dict[str, object] | None = None,
    data_scope: list[str] | None = None,
) -> agents.AgentProvider:
    if (
        runner.realm_id != owner.realm_id
        or runner.owner_id != owner.id
        or runner.revoked_at is not None
    ):
        raise ValueError("Runner is unavailable.")
    if (credential is None) == (local_credential_ref == ""):
        raise ValueError("Provider credential is required.")
    _safe_provider_url(base_url)
    if not name or not model_id or model_id not in allowed_models:
        raise ValueError("Invalid provider configuration.")
    secret = None
    if credential is not None:
        ciphertext, wrapped_key, key_id = encrypt_agent_secret(
            credential, realm_id=owner.realm_id, owner_id=owner.id, version=1
        )
        secret = agents.AgentSecret.objects.create(
            realm=owner.realm,
            owner=owner,
            ciphertext=ciphertext,
            wrapped_key=wrapped_key,
            key_id=key_id,
        )
    provider = agents.AgentProvider.objects.create(
        realm=owner.realm,
        owner=owner,
        runner=runner,
        name=name,
        base_url=base_url,
        model_id=model_id,
        allowed_models=allowed_models,
        secret=secret,
        local_credential_ref=local_credential_ref,
        context_window_tokens=context_window_tokens,
        max_output_tokens=max_output_tokens,
        api_mode=api_mode,
        data_scope=data_scope or ["synthetic"],
        network_policy=protocol.serialize_payload(
            protocol.NetworkPolicy.model_validate(network or {})
        ),
        capability_report={"config_version": 1},
    )
    try:
        provider.clean()
        provider_config(provider)
    except Exception:
        raise ValueError("Invalid provider configuration.") from None
    return provider


@transaction.atomic()
def register_repository(
    owner: UserProfile,
    runner: agents.AgentRunner,
    *,
    workspace_alias: str,
    canonical_origin: str | None,
    allowed_refs: list[str],
    required_checks: list[dict[str, object]] | None = None,
) -> agents.AgentRepository:
    if (
        runner.realm_id != owner.realm_id
        or runner.owner_id != owner.id
        or runner.revoked_at is not None
    ):
        raise ValueError("Runner is unavailable.")
    if canonical_origin:
        _safe_origin(canonical_origin)
    if (
        not re.fullmatch(r"[a-zA-Z0-9_-]{1,80}", workspace_alias)
        or not allowed_refs
        or any(not item for item in allowed_refs)
    ):
        raise ValueError("Invalid repository configuration.")
    try:
        normalized_checks = [
            protocol.serialize_payload(protocol.RequiredCheck.model_validate(item))
            for item in required_checks or []
        ]
    except Exception:
        raise ValueError("Invalid repository configuration.") from None
    repository = agents.AgentRepository.objects.create(
        realm=owner.realm,
        owner=owner,
        runner=runner,
        workspace_alias=workspace_alias,
        canonical_origin=canonical_origin or "",
        allowed_refs=allowed_refs,
        required_checks=normalized_checks,
    )
    return repository


def _default_sandbox(runner: agents.AgentRunner) -> dict[str, object]:
    catalog = protocol.RunnerCatalog.model_validate(runner.catalog_report)
    if (
        catalog.revision != runner.catalog_revision
        or any(item.catalog_revision != catalog.revision for item in catalog.sandboxes)
        or not catalog.sandboxes
    ):
        raise ValueError("Runner catalog is unavailable.")
    return protocol.serialize_payload(catalog.sandboxes[0])


def _default_policy(owner: UserProfile, runner: agents.AgentRunner) -> dict[str, object]:
    return {
        "version": 1,
        "actions": ["context.read"],
        # Setup scope carries no message authority. Admission derives the live scope.
        "scope": {"kind": "direct", "participant_user_ids": [owner.id]},
        "sandbox": _default_sandbox(runner),
        "network": {},
    }


def _default_budget() -> dict[str, int]:
    return {"input_tokens": 1024, "output_tokens": 512}


def build_probe_descriptor(
    profile: agents.AgentProfile, setup: agents.AgentSetupOperation
) -> dict[str, object]:
    """Build the immutable readiness descriptor from current server records."""
    profile = agents.AgentProfile.objects.select_related("provider", "runner").get(id=profile.id)
    provider = profile.provider
    grant = agents.AgentProbeGrant.objects.get(setup_operation=setup)
    validate_runtime(profile)
    provider_payload = provider_config(provider) if provider is not None else None
    descriptor = {
        "schema_version": 1,
        "setup_operation_id": str(setup.id),
        "profile_id": str(profile.id),
        "profile_revision": profile.revision,
        "runner_id": str(profile.runner_id),
        "descriptor_digest": "0" * 64,
        "configuration_digest": "0" * 64,
        "adapter": {
            "id": profile.adapter_id,
            "version": profile.adapter_version,
            "mode": profile.mode,
        },
        "provider": provider_payload,
        "workspace_binding": (
            {
                "canonical_origin": profile.default_repository.canonical_origin or None,
                "repository_id": str(profile.default_repository_id),
                "workspace_alias": profile.default_repository.workspace_alias,
                "policy_version": profile.default_repository.policy_version,
                "allowed_refs": profile.default_repository.allowed_refs,
                "checks_digest": hashlib.sha256(
                    protocol.canonical_json(
                        [
                            protocol.serialize_payload(protocol.RequiredCheck.model_validate(item))
                            for item in profile.default_repository.required_checks
                        ]
                    )
                ).hexdigest(),
            }
            if profile.default_repository_id is not None and profile.default_repository is not None
            else None
        ),
        "grant": {
            "id": str(grant.id),
            "runner_id": str(profile.runner_id),
            "provider_id": str(provider.id) if provider else None,
            "provider_config_version": provider.config_version if provider else None,
            "profile_revision": profile.revision,
            "expires_at": grant.expires_at.isoformat(),
            "actions": ["probe"],
        },
        "policy": profile.policy,
        "budget": profile.budget,
    }
    probe = protocol.ProbeDescriptor.model_validate(descriptor)
    serialized = protocol.serialize_payload(probe)
    serialized["configuration_digest"] = protocol.configuration_digest(probe)
    serialized["descriptor_digest"] = protocol.descriptor_digest(serialized)
    return serialized


def provider_config(provider: agents.AgentProvider) -> dict[str, object]:
    if provider.disabled_at is not None or (
        provider.secret is not None and provider.secret.revoked_at is not None
    ):
        raise ValueError("Provider is unavailable.")
    secret = provider.secret
    reference = (
        {"kind": "server", "id": str(secret.id), "version": secret.version}
        if secret is not None
        else {
            "kind": "local",
            "id": provider.local_credential_ref,
            "version": provider.config_version,
        }
    )
    return protocol.serialize_payload(
        protocol.ProviderConfig.model_validate(
            {
                "id": str(provider.id),
                "owner_user_id": provider.owner_id,
                "runner_id": str(provider.runner_id),
                "name": provider.name,
                "base_url": provider.base_url,
                "api_mode": provider.api_mode,
                "model_id": provider.model_id,
                "allowed_models": provider.allowed_models,
                "credential_ref": reference,
                "context_window_tokens": provider.context_window_tokens,
                "max_output_tokens": provider.max_output_tokens,
                "config_version": provider.config_version,
                "data_scope": provider.data_scope,
                "network": provider.network_policy,
                "capability_report": provider.capability_report,
            }
        )
    )


def validate_runtime(profile: agents.AgentProfile) -> None:
    require_agent_resource_access(
        profile.owner, profile.runner, target_kind="runner", action="runner.use"
    )
    catalog = protocol.RunnerCatalog.model_validate(profile.runner.catalog_report)
    policy = protocol.Policy.model_validate(profile.policy)
    if (
        catalog.revision != profile.runner.catalog_revision
        or not any(
            entry.id == profile.adapter_id and entry.version == profile.adapter_version
            for entry in catalog.adapters
        )
        or policy.sandbox not in catalog.sandboxes
        or policy.sandbox.catalog_revision != catalog.revision
    ):
        raise ValueError("Runtime configuration is unavailable.")
    if profile.provider is not None:
        if profile.provider.runner_id != profile.runner_id:
            raise ValueError("Provider is unavailable.")
        require_agent_resource_access(
            profile.owner, profile.provider, target_kind="provider", action="provider.use"
        )
        provider_config(profile.provider)
    if profile.default_repository is not None:
        if profile.default_repository.runner_id != profile.runner_id:
            raise ValueError("Repository is unavailable.")
        require_agent_resource_access(
            profile.owner,
            profile.default_repository,
            target_kind="repository",
            action="repository.read",
        )
        for action in policy.actions:
            if action != "context.read":
                require_agent_resource_access(
                    profile.owner,
                    profile.default_repository,
                    target_kind="repository",
                    action=action,
                )


def current_execution_configuration(
    profile: agents.AgentProfile,
) -> protocol.ExecutionConfiguration:
    """Compare live dependencies without a stored or unexpired setup grant."""
    policy = protocol.Policy.model_validate(profile.policy)
    provider = None
    if profile.provider is not None:
        configured = protocol.ProviderConfig.model_validate(provider_config(profile.provider))
        provider = protocol.EffectiveProvider.model_validate(
            configured.model_dump(include=set(protocol.EffectiveProvider.model_fields))
        )
    repository = profile.default_repository
    binding = None
    if repository is not None:
        binding = protocol.WorkspaceBinding(
            canonical_origin=repository.canonical_origin or None,
            repository_id=repository.id,
            workspace_alias=repository.workspace_alias,
            policy_version=repository.policy_version,
            allowed_refs=repository.allowed_refs,
            checks_digest=hashlib.sha256(
                protocol.canonical_json(
                    [
                        protocol.serialize_payload(protocol.RequiredCheck.model_validate(item))
                        for item in repository.required_checks
                    ]
                )
            ).hexdigest(),
        )
    return protocol.ExecutionConfiguration(
        runner_id=profile.runner_id,
        profile_revision=profile.revision,
        adapter=protocol.AdapterConfig.model_validate(
            {"id": profile.adapter_id, "version": profile.adapter_version, "mode": profile.mode}
        ),
        provider=provider,
        workspace_binding=binding,
        policy_version=policy.version,
        actions=sorted(set(policy.actions)),
        sandbox=policy.sandbox,
        network=policy.network,
        hard_cost_cap=policy.hard_cost_cap,
        budget=protocol.Budget.model_validate(profile.budget),
    )


def _save_descriptor(setup: agents.AgentSetupOperation, descriptor: dict[str, object]) -> None:
    setup.descriptor = descriptor
    setup.configuration_digest = str(descriptor["configuration_digest"])
    setup.descriptor_digest = str(descriptor["descriptor_digest"])
    setup.save(
        update_fields=["descriptor", "configuration_digest", "descriptor_digest", "updated_at"]
    )


def build_provider_probe(setup: agents.AgentSetupOperation) -> dict[str, object]:
    provider = setup.provider
    assert provider is not None
    require_agent_resource_access(
        setup.owner, provider, target_kind="provider", action="provider.use"
    )
    require_agent_resource_access(
        setup.owner, provider.runner, target_kind="runner", action="runner.use"
    )
    grant = agents.AgentProbeGrant.objects.get(setup_operation=setup)
    catalog = protocol.RunnerCatalog.model_validate(provider.runner.catalog_report)
    if not catalog.adapters:
        raise ValueError("Runner catalog is unavailable.")
    adapter_data = setup.descriptor.get("adapter")
    adapter = (
        protocol.AdapterConfig.model_validate(adapter_data)
        if adapter_data
        else protocol.AdapterConfig(
            id=catalog.adapters[0].id, version=catalog.adapters[0].version, mode="endpoint"
        )
    )
    if not any(
        item.id == adapter.id and item.version == adapter.version for item in catalog.adapters
    ):
        raise ValueError("Adapter is unavailable.")
    descriptor = protocol.ProbeDescriptor.model_validate(
        {
            "schema_version": 1,
            "setup_operation_id": str(setup.id),
            "profile_id": None,
            "profile_revision": setup.profile_revision,
            "runner_id": str(provider.runner_id),
            "descriptor_digest": "0" * 64,
            "configuration_digest": "0" * 64,
            "adapter": protocol.serialize_payload(adapter),
            "provider": provider_config(provider),
            "grant": {
                "id": str(grant.id),
                "runner_id": str(provider.runner_id),
                "provider_id": str(provider.id),
                "provider_config_version": provider.config_version,
                "profile_revision": setup.profile_revision,
                "expires_at": grant.expires_at.isoformat(),
                "actions": ["probe"],
            },
            "policy": _default_policy(setup.owner, provider.runner),
            "budget": _default_budget(),
        }
    )
    serialized = protocol.serialize_payload(descriptor)
    serialized["configuration_digest"] = protocol.configuration_digest(descriptor)
    serialized["descriptor_digest"] = protocol.descriptor_digest(serialized)
    return serialized


def _create_probe_grant(setup: agents.AgentSetupOperation) -> None:
    agents.AgentProbeGrant.objects.create(
        realm=setup.realm,
        setup_operation=setup,
        runner=setup.runner,
        provider=setup.provider,
        profile_revision=setup.profile_revision,
        provider_config_version=setup.provider_config_version,
        token_hash=hash_agent_credential(secrets.token_urlsafe(32)),
        expires_at=now() + PAIRING_TTL,
    )


@transaction.atomic()
def create_provider_probe(
    owner: UserProfile,
    provider: agents.AgentProvider,
    *,
    retry_key: uuid.UUID,
    expected_revision: int | None = None,
) -> agents.AgentSetupOperation:
    # Lock order: optional retry-key owner, then runner, then setup and resource rows.
    # The runner guard serializes setup creation, retry, claim, result, and catalog changes.
    UserProfile.objects.select_for_update().get(id=owner.id)
    runner = agents.AgentRunner.objects.select_for_update().get(id=provider.runner_id)
    provider = agents.AgentProvider.objects.select_for_update().get(id=provider.id)
    if provider.runner_id != runner.id:
        raise ValueError("Runner binding changed.")
    require_agent_resource_access(owner, provider, target_kind="provider", action="provider.use")
    require_agent_resource_access(owner, provider.runner, target_kind="runner", action="runner.use")
    if expected_revision is not None and expected_revision != provider.config_version:
        raise ValueError("Provider revision is stale.")
    digest = hashlib.sha256(
        protocol.canonical_json(
            {"provider_id": str(provider.id), "version": provider.config_version}
        )
    ).hexdigest()
    existing = agents.AgentSetupOperation.objects.filter(
        realm=owner.realm, owner=owner, retry_key=retry_key
    ).first()
    if existing is not None:
        if existing.profile_id is not None or existing.payload_digest != digest:
            raise ValueError("Probe retry does not match.")
        return existing
    previous = agents.AgentSetupOperation.objects.filter(
        provider=provider, profile__isnull=True, phase__in=["pending", "claimed", "probing"]
    )
    agents.AgentProbeGrant.objects.filter(setup_operation__in=previous).update(revoked_at=now())
    previous.update(phase="cancelled")
    setup = agents.AgentSetupOperation.objects.create(
        realm=owner.realm,
        owner=owner,
        provider=provider,
        runner=provider.runner,
        provider_config_version=provider.config_version,
        retry_key=retry_key,
        payload_digest=digest,
    )
    _create_probe_grant(setup)
    _save_descriptor(setup, build_provider_probe(setup))
    return setup


@transaction.atomic()
def create_profile(
    owner: UserProfile,
    *,
    name: str,
    runner: agents.AgentRunner,
    adapter_id: str,
    adapter_version: str,
    mode: str = "acp",
    default_mode: str = "answer",
    provider: agents.AgentProvider | None = None,
    repository: agents.AgentRepository | None = None,
    idempotency_key: uuid.UUID,
    description: str = "",
    policy: dict[str, object] | None = None,
    budget: dict[str, object] | None = None,
) -> agents.AgentProfile:
    from zerver.lib.agent_policy import AgentAccessDenied

    UserProfile.objects.select_for_update().get(id=owner.id)
    runner = agents.AgentRunner.objects.select_for_update().get(id=runner.id)
    try:
        require_agent_resource_access(owner, runner, target_kind="runner", action="runner.use")
    except AgentAccessDenied:
        raise ValueError("Runner is unavailable.") from None
    if provider is not None:
        if provider.realm_id != owner.realm_id or provider.runner_id != runner.id:
            raise ValueError("Provider is unavailable.")
        try:
            require_agent_resource_access(
                owner, provider, target_kind="provider", action="provider.use"
            )
        except AgentAccessDenied:
            raise ValueError("Provider is unavailable.") from None
    if repository is not None:
        if repository.realm_id != owner.realm_id or repository.runner_id != runner.id:
            raise ValueError("Repository is unavailable.")
        try:
            require_agent_resource_access(
                owner, repository, target_kind="repository", action="repository.read"
            )
        except AgentAccessDenied:
            raise ValueError("Repository is unavailable.") from None
    if (
        mode not in {"acp", "endpoint"}
        or default_mode not in {"answer", "code"}
        or (mode == "endpoint" and provider is None)
    ):
        raise ValueError("Invalid profile configuration.")
    try:
        catalog = protocol.RunnerCatalog.model_validate(runner.catalog_report)
    except Exception:
        raise ValueError("Runner catalog is unavailable.") from None
    if not any(
        item.id == adapter_id and item.version == adapter_version for item in catalog.adapters
    ):
        raise ValueError("Adapter is unavailable.")
    policy_data: dict[str, object] = _default_policy(owner, runner) if policy is None else policy
    budget_data: dict[str, object] = dict(_default_budget()) if budget is None else budget
    try:
        policy_data = protocol.serialize_payload(protocol.Policy.model_validate(policy_data))
        budget_data = protocol.serialize_payload(protocol.Budget.model_validate(budget_data))
    except Exception:
        raise ValueError("Invalid profile configuration.") from None
    name = check_full_name(name, user_profile=None, realm=None)
    payload_digest = hashlib.sha256(
        protocol.canonical_json(
            {
                "name": name,
                "description": description,
                "default_mode": default_mode,
                "runner": str(runner.id),
                "adapter": [adapter_id, adapter_version, mode],
                "provider": str(provider.id) if provider else None,
                "repository": str(repository.id) if repository else None,
                "policy": policy_data,
                "budget": budget_data,
            }
        )
    ).hexdigest()
    existing = (
        agents.AgentSetupOperation.objects.filter(
            realm=owner.realm, owner=owner, retry_key=idempotency_key
        )
        .select_related("profile")
        .first()
    )
    if existing is not None:
        if existing.payload_digest != payload_digest or existing.profile is None:
            raise ValueError("Profile retry does not match the original request.")
        return existing.profile
    check_can_create_bot(owner, UserProfile.DEFAULT_BOT)
    name = check_full_name(name, user_profile=None, realm=None)
    _short_name, email = validate_short_name_and_construct_bot_email(
        f"agent-{uuid.uuid4().hex}", owner.realm
    )
    bot = do_create_user(
        email,
        None,
        owner.realm,
        name,
        bot_type=UserProfile.DEFAULT_BOT,
        bot_owner=owner,
        acting_user=owner,
        add_initial_stream_subscriptions=False,
    )
    profile = agents.AgentProfile.objects.create(
        realm=owner.realm,
        owner=owner,
        bot_user=bot,
        runner=runner,
        name=name,
        description=description,
        mode=mode,
        default_mode=default_mode,
        adapter_id=adapter_id,
        adapter_version=adapter_version,
        provider=provider,
        default_repository=repository,
        policy=policy_data,
        budget=budget_data,
    )
    setup = agents.AgentSetupOperation.objects.create(
        realm=owner.realm,
        owner=owner,
        profile=profile,
        provider=provider,
        runner=runner,
        profile_revision=profile.revision,
        provider_config_version=provider.config_version if provider else None,
        retry_key=idempotency_key,
        payload_digest=payload_digest,
    )
    probe_token = secrets.token_urlsafe(32)
    agents.AgentProbeGrant.objects.create(
        realm=owner.realm,
        setup_operation=setup,
        runner=runner,
        provider=provider,
        profile_revision=profile.revision,
        provider_config_version=provider.config_version if provider else None,
        token_hash=hash_agent_credential(probe_token),
        expires_at=now() + timedelta(minutes=10),
    )
    descriptor = build_probe_descriptor(profile, setup)
    setup.descriptor = descriptor
    setup.descriptor_digest = str(descriptor["descriptor_digest"])
    setup.configuration_digest = str(descriptor["configuration_digest"])
    setup.save(
        update_fields=["descriptor", "descriptor_digest", "configuration_digest", "updated_at"]
    )
    return profile


def _current_setup_descriptor(setup: agents.AgentSetupOperation) -> dict[str, object]:
    return (
        build_probe_descriptor(setup.profile, setup)
        if setup.profile is not None
        else build_provider_probe(setup)
    )


def _validate_setup(setup: agents.AgentSetupOperation, runner: agents.AgentRunner) -> None:
    if (
        setup.runner_id != runner.id
        or setup.realm_id != runner.realm_id
        or runner.revoked_at is not None
    ):
        raise ValueError("Setup is unavailable.")
    if setup.profile_id is not None:
        setup.profile = agents.AgentProfile.objects.select_for_update().get(id=setup.profile_id)
        check_agent_access(setup.owner, setup.profile, None, None, "profile.manage")
        if setup.profile.default_repository_id is not None:
            agents.AgentRepository.objects.select_for_update().get(
                id=setup.profile.default_repository_id
            )
    if setup.provider_id is not None:
        setup.provider = agents.AgentProvider.objects.select_for_update().get(id=setup.provider_id)
        if setup.provider.secret_id is not None:
            agents.AgentSecret.objects.select_for_update().get(id=setup.provider.secret_id)
    grant = agents.AgentProbeGrant.objects.select_for_update().get(setup_operation=setup)
    if grant.revoked_at is not None or grant.expires_at <= now():
        raise ValueError("Probe authority is unavailable.")
    if setup.profile is not None and setup.profile.revision != setup.profile_revision:
        raise ValueError("Setup revision is stale.")
    current = _current_setup_descriptor(setup)
    if current["configuration_digest"] != setup.configuration_digest:
        raise ValueError("Setup configuration is stale.")


@transaction.atomic()
def claim_setup(
    runner: agents.AgentRunner, setup_id: uuid.UUID, claim_key: uuid.UUID
) -> agents.AgentSetupOperation:
    runner = agents.AgentRunner.objects.select_for_update().get(id=runner.id)
    if not agents.AgentRealmSettings.objects.filter(realm=runner.realm, enabled=True).exists():
        raise ValueError("Agent connections are disabled.")
    setup = agents.AgentSetupOperation.objects.select_for_update().get(
        id=setup_id, runner=runner, realm=runner.realm
    )
    _validate_setup(setup, runner)
    if (
        setup.claim_key == claim_key
        and setup.lease_expires_at is not None
        and setup.lease_expires_at > now()
    ):
        return setup
    if setup.phase not in {"pending", "claimed", "probing"} or (
        setup.lease_expires_at is not None and setup.lease_expires_at > now()
    ):
        raise ValueError("Setup cannot be claimed.")
    setup.claim_key = claim_key
    setup.lease_epoch += 1
    setup.lease_expires_at = min(
        now() + timedelta(minutes=5),
        agents.AgentProbeGrant.objects.get(setup_operation=setup).expires_at,
    )
    setup.phase = "probing"
    setup.save(
        update_fields=["claim_key", "lease_epoch", "lease_expires_at", "phase", "updated_at"]
    )
    return setup


@transaction.atomic()
def record_setup_result(
    runner: agents.AgentRunner, result: SetupResult
) -> agents.AgentSetupOperation:
    runner = agents.AgentRunner.objects.select_for_update().get(id=runner.id)
    setup = agents.AgentSetupOperation.objects.select_for_update().get(
        id=result.setup_id, runner=runner, realm=runner.realm
    )
    normalized = protocol.serialize_payload(result)
    # Exact receipt replay never changes readiness or restores expired authority.
    if setup.result is not None:
        if runner.revoked_at is not None or setup.result != normalized:
            raise ValueError("Setup result does not match.")
        return setup
    _validate_setup(setup, runner)
    if (
        setup.claim_key != result.claim_key
        or setup.lease_epoch != result.lease_epoch
        or setup.lease_expires_at is None
        or setup.lease_expires_at <= now()
        or setup.descriptor_digest != result.descriptor_digest
        or setup.configuration_digest != result.configuration_digest
    ):
        raise ValueError("Setup result is stale.")
    if setup.phase != "probing":
        raise ValueError("Setup result is stale.")
    expected_capability_version = setup.provider_config_version or setup.profile_revision
    if result.capabilities.config_version != expected_capability_version:
        raise ValueError("Capability version is stale.")
    if setup.profile is not None:
        profile = agents.AgentProfile.objects.select_for_update().get(id=setup.profile.id)
        probe = protocol.ProbeDescriptor.model_validate(setup.descriptor)
        ready = (
            result.state == "ready"
            and result.capabilities.chat_ready
            and (
                profile.default_mode != "code"
                or profile.default_repository_id is None
                or result.capabilities.code_ready
            )
        )
        profile.readiness_state = "ready" if ready else "needs_action"
        profile.readiness_revision = profile.revision
        profile.readiness_digest = setup.descriptor_digest
        profile.readiness_configuration_digest = setup.configuration_digest
        profile.readiness_configuration = (
            protocol.serialize_payload(protocol.execution_configuration(probe)) if ready else None
        )
        profile.capability_report = protocol.serialize_payload(result.capabilities)
        if ready and profile.desired_state == "draft":
            profile.desired_state = "enabled"
            profile.enabled_revision = profile.revision
        profile.save()
        setup.phase = "ready" if ready else "needs_action"
    else:
        assert setup.provider_id is not None
        provider = agents.AgentProvider.objects.select_for_update().get(id=setup.provider_id)
        provider.capability_report = protocol.serialize_payload(result.capabilities)
        provider.save(update_fields=["capability_report", "updated_at"])
        setup.phase = (
            "ready"
            if result.state == "ready" and result.capabilities.chat_ready
            else "needs_action"
        )
    if result.state == "failed":
        setup.phase = "failed"
    setup.result = normalized
    setup.requirements = [protocol.serialize_payload(item) for item in result.requirements]
    setup.finished_at = now()
    setup.save(update_fields=["result", "phase", "requirements", "finished_at", "updated_at"])
    return setup


def record_readiness(
    runner: agents.AgentRunner, setup: agents.AgentSetupOperation, report: dict[str, object]
) -> agents.AgentProfile:
    """Internal compatibility entry point. Device requests use the explicit lease API."""
    parsed = protocol.ReadinessReport.model_validate(report)
    if (
        parsed.runner_id != runner.id
        or parsed.profile_id != setup.profile_id
        or parsed.profile_revision != setup.profile_revision
    ):
        raise ValueError("Readiness report is stale.")
    setup.refresh_from_db()
    setup = claim_setup(runner, setup.id, setup.claim_key or uuid.uuid4())
    assert setup.claim_key is not None
    result = SetupResult(
        schema_version=1,
        setup_id=setup.id,
        claim_key=setup.claim_key,
        lease_epoch=setup.lease_epoch,
        descriptor_digest=parsed.descriptor_digest,
        configuration_digest=parsed.configuration_digest,
        state="ready" if parsed.state == "ready" else "needs_action",
        capabilities=parsed.capabilities,
        requirements=parsed.requirements,
    )
    completed = record_setup_result(runner, result)
    assert completed.profile is not None
    return completed.profile


@transaction.atomic()
def retry_profile_setup(
    owner: UserProfile,
    profile: agents.AgentProfile,
    *,
    expected_revision: int,
    retry_key: uuid.UUID,
) -> agents.AgentSetupOperation:
    UserProfile.objects.select_for_update().get(id=owner.id)
    runner = agents.AgentRunner.objects.select_for_update().get(id=profile.runner_id)
    profile = agents.AgentProfile.objects.select_for_update().get(id=profile.id)
    if profile.runner_id != runner.id:
        raise ValueError("Runner binding changed.")
    check_agent_access(owner, profile, None, None, "profile.manage")
    if profile.revision != expected_revision or profile.desired_state == "archived":
        raise ValueError("Profile revision is stale.")
    digest = hashlib.sha256(
        protocol.canonical_json({"retry_profile": str(profile.id), "revision": profile.revision})
    ).hexdigest()
    existing = agents.AgentSetupOperation.objects.filter(
        realm=owner.realm, owner=owner, retry_key=retry_key
    ).first()
    if existing is not None:
        if existing.payload_digest != digest:
            raise ValueError("Setup retry does not match.")
        return existing
    previous = agents.AgentSetupOperation.objects.filter(
        profile=profile, phase__in=["pending", "claimed", "probing"]
    )
    agents.AgentProbeGrant.objects.filter(setup_operation__in=previous).update(revoked_at=now())
    previous.update(phase="cancelled")
    setup = agents.AgentSetupOperation.objects.create(
        realm=owner.realm,
        owner=owner,
        profile=profile,
        runner=profile.runner,
        provider=profile.provider,
        profile_revision=profile.revision,
        provider_config_version=(
            profile.provider.config_version if profile.provider is not None else None
        ),
        retry_key=retry_key,
        payload_digest=digest,
    )
    _create_probe_grant(setup)
    _save_descriptor(setup, build_probe_descriptor(profile, setup))
    return setup


@transaction.atomic()
def enable_profile(
    owner: UserProfile, profile: agents.AgentProfile, *, expected_revision: int
) -> agents.AgentProfile:
    profile = agents.AgentProfile.objects.select_for_update().get(id=profile.id)
    check_agent_access(owner, profile, None, None, "profile.manage")
    if (
        profile.revision != expected_revision
        or profile.readiness_revision != profile.revision
        or profile.readiness_state != "ready"
        or profile.readiness_configuration is None
        or profile.desired_state == "archived"
    ):
        raise ValueError("Profile is not ready.")
    setup = agents.AgentSetupOperation.objects.filter(
        profile=profile, phase="ready", configuration_digest=profile.readiness_configuration_digest
    ).latest("finished_at")
    current = _current_setup_descriptor(setup)
    if current["configuration_digest"] != profile.readiness_configuration_digest:
        raise ValueError("Profile configuration is stale.")
    profile.desired_state = "enabled"
    profile.enabled_revision = profile.revision
    profile.save(update_fields=["desired_state", "enabled_revision", "updated_at"])
    return profile


@transaction.atomic()
def pause_profile(
    owner: UserProfile, profile: agents.AgentProfile, *, expected_revision: int | None = None
) -> agents.AgentProfile:
    profile = agents.AgentProfile.objects.select_for_update().get(id=profile.id)
    check_agent_access(owner, profile, None, None, "profile.manage")
    if profile.desired_state == "archived" or (
        expected_revision is not None and expected_revision != profile.revision
    ):
        raise ValueError("Profile is unavailable.")
    profile.desired_state = "paused"
    profile.revision += 1
    profile.enabled_revision = None
    profile.save(update_fields=["desired_state", "revision", "enabled_revision", "updated_at"])
    return profile


@transaction.atomic()
def archive_profile(
    owner: UserProfile, profile: agents.AgentProfile, *, expected_revision: int | None = None
) -> agents.AgentProfile:
    profile = agents.AgentProfile.objects.select_for_update().get(id=profile.id)
    has_active_attempt = agents.AgentAttempt.objects.filter(
        job__profile=profile, active=True
    ).exists()
    check_agent_access(owner, profile, None, None, "profile.manage")
    if (
        expected_revision is not None and expected_revision != profile.revision
    ) or has_active_attempt:
        raise ValueError("Profile cannot be archived.")
    profile.desired_state = "archived"
    profile.revision += 1
    profile.enabled_revision = None
    profile.archived_at = now()
    profile.save(
        update_fields=["desired_state", "revision", "enabled_revision", "archived_at", "updated_at"]
    )
    return profile


@transaction.atomic()
def attach_profile_to_stream(
    owner: UserProfile,
    profile: agents.AgentProfile,
    stream: Stream,
    *,
    expected_revision: int | None = None,
) -> None:
    profile = agents.AgentProfile.objects.select_for_update().get(id=profile.id)
    if (
        profile.realm_id != owner.realm_id
        or stream.realm_id != owner.realm_id
        or (expected_revision is not None and expected_revision != profile.revision)
    ):
        raise ValueError("Channel is unavailable.")
    from zerver.lib.agent_policy import check_agent_access

    check_agent_access(owner, profile, None, None, "profile.manage")
    allowed = filter_stream_authorization_for_adding_subscribers(owner, [stream], True)
    if allowed.authorized_streams != [stream]:
        raise ValueError("Channel is unavailable.")
    agents.AgentGrant.objects.filter(revoked_at__isnull=True).get_or_create(
        realm=owner.realm,
        owner=profile.owner,
        principal_user=owner,
        target_kind="profile",
        profile=profile,
        scope={"kind": "stream", "stream_id": stream.id},
        actions=["profile.use"],
    )
    bulk_add_subscriptions(owner.realm, [stream], [profile.bot_user], acting_user=owner)


@transaction.atomic()
def create_agent_grant(
    owner: UserProfile,
    *,
    principal_user: UserProfile | None = None,
    principal_group_id: int | None = None,
    target_kind: str,
    target: (
        agents.AgentRunner | agents.AgentProvider | agents.AgentRepository | agents.AgentProfile
    ),
    actions: list[str],
    expires_at: datetime | None = None,
    scope: dict[str, object] | None = None,
    repository: agents.AgentRepository | None = None,
) -> agents.AgentGrant:
    if target.realm_id != owner.realm_id or getattr(target, "owner_id", None) != owner.id:
        raise ValueError("Grant target is unavailable.")
    if principal_user is not None and principal_user.realm_id != owner.realm_id:
        raise ValueError("Grant principal is unavailable.")
    if (
        principal_group_id is not None
        and not UserGroup.objects.filter(id=principal_group_id, realm=owner.realm).exists()
    ):
        raise ValueError("Grant principal is unavailable.")
    if (principal_user is None) == (principal_group_id is None) or not actions:
        raise ValueError("Invalid grant.")
    if target_kind not in {"runner", "provider", "repository", "profile"}:
        raise ValueError("Invalid grant.")
    fields: dict[str, object] = {
        "realm": owner.realm,
        "owner": owner,
        "principal_user": principal_user,
        "principal_group_id": principal_group_id,
        "target_kind": target_kind,
        "actions": actions,
        "expires_at": expires_at,
        "scope": scope,
    }
    if repository is not None:
        if target_kind != "profile" or repository.realm_id != owner.realm_id:
            raise ValueError("Invalid repository grant.")
        fields["repository"] = repository
    fields[target_kind] = target
    grant = agents.AgentGrant(**fields)
    try:
        grant.clean()
    except Exception:
        raise ValueError("Invalid grant.") from None
    grant.save()
    return grant


def authenticate_runner_stop_token(
    token: str, *, job_id: uuid.UUID, attempt_id: uuid.UUID, lease_epoch: int, job_version: int
) -> agents.AgentAttempt:
    """Authorize only stop evidence for an existing revoked runner attempt.

    Task 3 must recheck these bindings under locks before recording process evidence.
    This function never marks a process stopped and never returns an execution principal.
    """
    credential = (
        agents.AgentRunnerCredential.objects.select_related("runner")
        .filter(token_hash=hash_agent_credential(token))
        .first()
    )
    if (
        credential is None
        or credential.runner.revoked_at is None
        or credential.rotated_at is not None
        or credential.revoked_at != credential.runner.revoked_at
        or credential.expires_at <= now()
        or not credential_matches(token, credential.token_hash)
    ):
        raise ValueError("Stop credential is unavailable.")
    attempt = agents.AgentAttempt.objects.filter(
        id=attempt_id,
        job_id=job_id,
        runner=credential.runner,
        realm=credential.realm,
        job__realm=credential.realm,
        lease_epoch=lease_epoch,
        job__version=job_version,
        active=True,
        process_state="stopping",
    ).first()
    if attempt is None:
        raise ValueError("Stop attempt is unavailable.")
    return attempt
