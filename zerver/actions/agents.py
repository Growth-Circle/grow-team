"""Transactional connection actions. Runner execution belongs to Task 3."""

import hashlib
import secrets
import uuid
from datetime import datetime, timedelta
from urllib.parse import urlsplit

from django.db import transaction
from django.utils.timezone import now

from zerver.actions.create_user import do_create_user
from zerver.actions.streams import bulk_add_subscriptions
from zerver.lib import agent_protocol as protocol
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


@transaction.atomic(savepoint=False)
def start_pairing(
    device_name: str,
    fingerprint: str,
    user_code: str,
    polling_secret: str,
    *,
    expires_at: datetime | None = None,
) -> agents.AgentPairing:
    if not device_name or not fingerprint or not user_code or not polling_secret:
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
    with transaction.atomic(savepoint=False):
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
    with transaction.atomic(savepoint=False):
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
    with transaction.atomic(savepoint=False):
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


@transaction.atomic(savepoint=False)
def revoke_runner(runner: agents.AgentRunner) -> None:
    runner = agents.AgentRunner.objects.select_for_update().get(id=runner.id)
    if runner.revoked_at is None:
        runner.revoked_at = now()
        runner.status = "revoked"
        runner.save(update_fields=["revoked_at", "status", "updated_at"])
    agents.AgentRunnerCredential.objects.filter(runner=runner, revoked_at__isnull=True).update(
        revoked_at=now()
    )
    # Task 3 consumes this durable stopping state and records runner stop evidence.
    active_attempts = agents.AgentAttempt.objects.filter(runner=runner, active=True)
    active_attempts.update(process_state="stopping")
    agents.AgentJob.objects.filter(agentattempt__runner=runner, agentattempt__active=True).update(
        status="cancel_requested"
    )


def authenticate_runner_token(token: str) -> agents.AgentRunnerCredential:
    """Return an active credential for a device bearer token."""
    digest = hash_agent_credential(token)
    credential = (
        agents.AgentRunnerCredential.objects.select_related("runner")
        .filter(token_hash=digest, revoked_at__isnull=True)
        .first()
    )
    if (
        credential is None
        or credential.runner.revoked_at is not None
        or credential.expires_at <= now()
        or not credential_matches(token, credential.token_hash)
    ):
        raise ValueError("Runner credential is unavailable.")
    return credential


def authenticate_runner_refresh(refresh_token: str) -> agents.AgentRunnerCredential:
    digest = hash_agent_credential(refresh_token)
    credential = (
        agents.AgentRunnerCredential.objects.select_related("runner")
        .filter(refresh_hash=digest, revoked_at__isnull=True)
        .first()
    )
    if (
        credential is None
        or credential.runner.revoked_at is not None
        or credential.refresh_expires_at <= now()
        or not credential_matches(refresh_token, credential.refresh_hash)
    ):
        raise ValueError("Runner credential is unavailable.")
    return credential


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


@transaction.atomic(savepoint=False)
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
) -> agents.AgentProvider:
    if runner.realm_id != owner.realm_id or runner.owner_id != owner.id:
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
        data_scope=["synthetic"],
        network_policy=protocol.serialize_payload(protocol.NetworkPolicy()),
        capability_report={"config_version": 1},
    )
    try:
        provider.clean()
    except Exception:
        raise ValueError("Invalid provider configuration.") from None
    return provider


@transaction.atomic(savepoint=False)
def register_repository(
    owner: UserProfile,
    runner: agents.AgentRunner,
    *,
    workspace_alias: str,
    canonical_origin: str,
    allowed_refs: list[str],
    required_checks: list[dict[str, object]] | None = None,
) -> agents.AgentRepository:
    if runner.realm_id != owner.realm_id or runner.owner_id != owner.id:
        raise ValueError("Runner is unavailable.")
    if canonical_origin:
        _safe_origin(canonical_origin)
    if not workspace_alias or not allowed_refs or any(not item for item in allowed_refs):
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
        canonical_origin=canonical_origin,
        allowed_refs=allowed_refs,
        required_checks=normalized_checks,
    )
    return repository


def _default_sandbox(runner: agents.AgentRunner) -> dict[str, object]:
    catalog = protocol.RunnerCatalog.model_validate(runner.catalog_report)
    if not catalog.sandboxes:
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
    provider_payload = None
    if provider is not None:
        credential_ref = None
        if provider.secret_id is not None:
            assert provider.secret is not None
            credential_ref = {
                "kind": "server",
                "id": str(provider.secret_id),
                "version": provider.secret.version,
            }
        elif provider.local_credential_ref:
            credential_ref = {"kind": "local", "id": provider.local_credential_ref, "version": 1}
        provider_payload = {
            "id": str(provider.id),
            "owner_user_id": provider.owner_id,
            "runner_id": str(provider.runner_id),
            "name": provider.name,
            "base_url": provider.base_url,
            "api_mode": provider.api_mode,
            "model_id": provider.model_id,
            "allowed_models": provider.allowed_models,
            "credential_ref": credential_ref,
            "context_window_tokens": provider.context_window_tokens,
            "max_output_tokens": provider.max_output_tokens,
            "config_version": provider.config_version,
            "data_scope": provider.data_scope,
            "network": provider.network_policy,
            "capability_report": provider.capability_report,
        }
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
                "canonical_origin": profile.default_repository.canonical_origin,
                "repository_id": str(profile.default_repository_id),
                "workspace_alias": profile.default_repository.workspace_alias,
                "policy_version": profile.default_repository.policy_version,
                "allowed_refs": profile.default_repository.allowed_refs,
                "checks_digest": hashlib.sha256(
                    protocol.canonical_json(profile.default_repository.required_checks)
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


@transaction.atomic(savepoint=False)
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
    if runner.realm_id != owner.realm_id or runner.owner_id != owner.id:
        raise ValueError("Runner is unavailable.")
    if provider is not None and (
        provider.realm_id != owner.realm_id or provider.runner_id != runner.id
    ):
        raise ValueError("Provider is unavailable.")
    if repository is not None and (
        repository.realm_id != owner.realm_id or repository.runner_id != runner.id
    ):
        raise ValueError("Repository is unavailable.")
    if (
        mode not in {"acp", "endpoint"}
        or default_mode not in {"answer", "code"}
        or (mode == "endpoint" and provider is None)
        or (default_mode == "code" and repository is None)
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
    payload_digest = hashlib.sha256(
        protocol.canonical_json(
            {
                "name": name,
                "description": description,
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


def record_readiness(
    runner: agents.AgentRunner, setup: agents.AgentSetupOperation, report: dict[str, object]
) -> agents.AgentProfile:
    parsed = protocol.ReadinessReport.model_validate(report)
    stale = False
    with transaction.atomic(savepoint=False):
        setup = agents.AgentSetupOperation.objects.select_for_update().get(id=setup.id)
        current_runner = agents.AgentRunner.objects.select_for_update().get(id=setup.runner_id)
        probe_grant = agents.AgentProbeGrant.objects.filter(setup_operation=setup).first()
        if (
            setup.profile is None
            or setup.runner_id != runner.id
            or current_runner.revoked_at is not None
            or probe_grant is None
            or probe_grant.revoked_at is not None
            or probe_grant.expires_at <= now()
        ):
            stale = True
            profile = None
        else:
            assert setup.profile_id is not None
            profile = agents.AgentProfile.objects.select_for_update().get(id=setup.profile_id)
            stale = (
                setup.phase not in {"pending", "probing"}
                or profile.revision != setup.profile_revision
                or parsed.profile_id != profile.id
                or parsed.profile_revision != profile.revision
                or parsed.runner_id != runner.id
                or parsed.descriptor_digest != setup.descriptor_digest
                or parsed.configuration_digest != setup.configuration_digest
            )
            if not stale:
                current_descriptor = build_probe_descriptor(profile, setup)
                stale = current_descriptor["descriptor_digest"] != setup.descriptor_digest
        if stale:
            setup.phase = "stale"
            setup.save(update_fields=["phase", "updated_at"])
        else:
            assert profile is not None
            probe = protocol.ProbeDescriptor.model_validate(setup.descriptor)
            configuration = protocol.serialize_payload(protocol.execution_configuration(probe))
            profile.readiness_state = parsed.state
            profile.readiness_revision = profile.revision
            profile.readiness_digest = setup.descriptor_digest
            profile.readiness_configuration_digest = setup.configuration_digest
            profile.readiness_configuration = configuration
            profile.capability_report = protocol.serialize_payload(parsed.capabilities)
            ready_for_mode = parsed.capabilities.chat_ready and (
                profile.default_mode != "code" or parsed.capabilities.code_ready
            )
            if parsed.state == "ready" and ready_for_mode and profile.desired_state == "draft":
                profile.desired_state = "enabled"
                profile.enabled_revision = profile.revision
            profile.save()
            setup.phase = "ready" if parsed.state == "ready" else "needs_action"
            setup.finished_at = now()
            setup.save(update_fields=["phase", "finished_at", "updated_at"])
    if stale:
        raise ValueError("Readiness report is stale.")
    assert profile is not None
    return profile


@transaction.atomic(savepoint=False)
def pause_profile(owner: UserProfile, profile: agents.AgentProfile) -> agents.AgentProfile:
    profile = agents.AgentProfile.objects.select_for_update().get(id=profile.id)
    if profile.realm_id != owner.realm_id or profile.owner_id != owner.id:
        raise ValueError("Profile is unavailable.")
    profile.desired_state = "paused"
    profile.save(update_fields=["desired_state", "updated_at"])
    return profile


@transaction.atomic(savepoint=False)
def archive_profile(owner: UserProfile, profile: agents.AgentProfile) -> agents.AgentProfile:
    profile = agents.AgentProfile.objects.select_for_update().get(id=profile.id)
    has_active_attempt = agents.AgentAttempt.objects.filter(
        job__profile=profile, active=True
    ).exists()
    if profile.realm_id != owner.realm_id or profile.owner_id != owner.id or has_active_attempt:
        raise ValueError("Profile cannot be archived.")
    profile.desired_state = "archived"
    profile.archived_at = now()
    profile.save(update_fields=["desired_state", "archived_at", "updated_at"])
    return profile


@transaction.atomic(savepoint=False)
def attach_profile_to_stream(
    owner: UserProfile, profile: agents.AgentProfile, stream: Stream
) -> None:
    if profile.realm_id != owner.realm_id or stream.realm_id != owner.realm_id:
        raise ValueError("Channel is unavailable.")
    from zerver.lib.agent_policy import check_agent_access

    check_agent_access(owner, profile, None, None, "profile.manage")
    allowed = filter_stream_authorization_for_adding_subscribers(owner, [stream], True)
    if allowed.authorized_streams != [stream]:
        raise ValueError("Channel is unavailable.")
    agents.AgentGrant.objects.get_or_create(
        realm=owner.realm,
        owner=profile.owner,
        principal_user=profile.bot_user,
        target_kind="profile",
        profile=profile,
        scope={"kind": "stream", "stream_id": stream.id},
        actions=["profile.use"],
    )
    bulk_add_subscriptions(owner.realm, [stream], [profile.bot_user], acting_user=owner)


@transaction.atomic(savepoint=False)
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
    expires_at: object | None = None,
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
    }
    fields[target_kind] = target
    grant = agents.AgentGrant(**fields)
    try:
        grant.clean()
    except Exception as error:
        raise ValueError("Invalid grant.") from error
    grant.save()
    return grant
