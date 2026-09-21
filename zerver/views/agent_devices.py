"""Anonymous pairing and runner-bearer endpoints.

These endpoints never use session or Zulip user API authentication.
"""

import json
import secrets
from collections.abc import Callable
from functools import wraps
from typing import cast
from uuid import UUID

from django.db import IntegrityError, transaction
from django.http import HttpRequest, HttpResponse
from django.utils.timezone import now
from django.views.decorators.csrf import csrf_exempt

from zerver.actions.agents import (
    RunnerCredentialError,
    _safe_origin,
    authenticate_runner_refresh,
    authenticate_runner_token,
    claim_setup,
    exchange_pairing,
    record_setup_result,
    register_repository,
    rotate_runner_credential,
    start_pairing,
)
from zerver.lib import agent_requests as requests
from zerver.lib.agent_secrets import credential_matches
from zerver.lib.exceptions import JsonableError
from zerver.lib.rate_limiter import rate_limit_request_by_ip
from zerver.lib.response import json_response, json_success
from zerver.models import agents
from zerver.views.agents import safe_agent_endpoint


def device_endpoint(
    view: Callable[[HttpRequest], HttpResponse],
) -> Callable[[HttpRequest], HttpResponse]:
    @wraps(view)
    def wrapped(request: HttpRequest) -> HttpResponse:
        if request.method != "POST":
            return json_response("error", "POST is required.", {"schema_version": 1}, status=405)
        if request.user.is_authenticated:
            return json_response(
                "error",
                "Runner bearer authentication is required.",
                {"schema_version": 1, "code": "credential_invalid"},
                status=401,
            )
        try:
            return view(request)
        except RunnerCredentialError as error:
            return json_response(
                "error",
                "Runner credential rejected.",
                {"schema_version": 1, "code": error.code},
                status=401,
            )
        except JsonableError as error:
            response = json_response(
                "error",
                "Device request rejected.",
                {"schema_version": 1},
                status=error.http_status_code,
            )
            for key, value in error.extra_headers.items():
                response[key] = value
            return response

    return wrapped


def _payload(request: HttpRequest, *, fields: dict[str, type[object]]) -> dict[str, object]:
    if len(request.body) > 65536:
        raise ValueError("Device payload exceeds its limit.")
    try:
        value = json.loads(request.body)
    except (TypeError, UnicodeDecodeError, json.JSONDecodeError):
        raise ValueError("Invalid device request.") from None
    if (
        not isinstance(value, dict)
        or set(value) != {"schema_version", *fields}
        or type(value.get("schema_version")) is not int
        or value.get("schema_version") != 1
        or any(not isinstance(value[name], field_type) for name, field_type in fields.items())
        or any(field_type is str and not value[name] for name, field_type in fields.items())
    ):
        raise ValueError("Invalid device request.")
    return value


def _device_token(request: HttpRequest) -> str | None:
    if request.user.is_authenticated:
        return None
    value = request.headers.get("Authorization", "")
    prefix = "Bearer "
    if not value.startswith(prefix) or not value[len(prefix) :]:
        return None
    return value[len(prefix) :]


def _device_error(_error: ValueError) -> HttpResponse:
    return json_response("error", "Invalid device request.", {"schema_version": 1}, status=400)


@csrf_exempt
@device_endpoint
def start_pairing_device(request: HttpRequest) -> HttpResponse:
    rate_limit_request_by_ip(request, "agent_pairing_by_ip")
    try:
        raw = json.loads(request.body)
        legacy = isinstance(raw, dict) and "user_code" in raw
        data = _payload(
            request,
            fields={
                "device_name": str,
                "fingerprint": str,
                **({"user_code": str} if legacy else {}),
                "polling_secret": str,
            },
        )
        user_code = cast(str, data["user_code"]) if legacy else secrets.token_hex(4).upper()
        pairing = start_pairing(
            cast(str, data["device_name"]),
            cast(str, data["fingerprint"]),
            user_code,
            cast(str, data["polling_secret"]),
        )
    except (ValueError, IntegrityError):
        return _device_error(ValueError("Invalid device request."))
    return json_success(
        request,
        {
            "schema_version": 1,
            "pairing_id": str(pairing.id),
            "state": pairing.state,
            "user_code": user_code,
            "expires_at": pairing.expires_at.isoformat(),
        },
    )


@csrf_exempt
@device_endpoint
def exchange_pairing_device(request: HttpRequest) -> HttpResponse:
    rate_limit_request_by_ip(request, "agent_device_exchange_by_ip")
    try:
        data = _payload(request, fields={"pairing_id": str, "polling_secret": str})
        pairing = agents.AgentPairing.objects.get(id=UUID(cast(str, data["pairing_id"])))
        token, refresh_token = exchange_pairing(pairing, cast(str, data["polling_secret"]))
    except (agents.AgentPairing.DoesNotExist, ValueError):
        return _device_error(ValueError("Pairing is unavailable."))
    return json_success(
        request,
        {
            "schema_version": 1,
            "token": token,
            "refresh_token": refresh_token,
            **_credential_result(authenticate_runner_token(token)),
        },
    )


@csrf_exempt
@device_endpoint
def rotate_device_credential(request: HttpRequest) -> HttpResponse:
    rate_limit_request_by_ip(request, "agent_device_exchange_by_ip")
    if request.user.is_authenticated:
        return json_response(
            "error", "Runner bearer authentication is required.", {"schema_version": 1}, status=401
        )
    try:
        data = _payload(request, fields={"refresh_token": str})
        refresh_token = cast(str, data["refresh_token"])
        credential = authenticate_runner_refresh(refresh_token)
        _replacement, new_token, refresh_token = rotate_runner_credential(credential, refresh_token)
    except RunnerCredentialError:
        raise
    except ValueError:
        return _device_error(ValueError("Runner credential is unavailable."))
    return json_success(
        request,
        {
            "schema_version": 1,
            "token": new_token,
            "refresh_token": refresh_token,
            **_credential_result(_replacement),
        },
    )


@csrf_exempt
@device_endpoint
@transaction.atomic
def update_runner_catalog(request: HttpRequest) -> HttpResponse:
    token = _device_token(request)
    if token is None:
        return json_response(
            "error", "Runner bearer authentication is required.", {"schema_version": 1}, status=401
        )
    try:
        credential = authenticate_runner_token(token)
        data = _payload(request, fields={"catalog": dict})
        catalog = data.get("catalog")
        if not isinstance(catalog, dict):
            raise ValueError
        # Validate here. Django stores only a typed runner catalog.
        from zerver.lib import agent_protocol as protocol

        parsed = protocol.RunnerCatalog.model_validate(catalog)
        runner = agents.AgentRunner.objects.select_for_update().get(id=credential.runner_id)
        authenticate_runner_token(token)
        normalized = protocol.serialize_payload(parsed)
        if parsed.revision == runner.catalog_revision and normalized == runner.catalog_report:
            return json_success(request, {"schema_version": 1, "catalog_revision": parsed.revision})
        if parsed.revision < runner.catalog_revision or (
            parsed.revision == runner.catalog_revision and runner.catalog_report
        ):
            raise ValueError
        if any(item.catalog_revision != parsed.revision for item in parsed.sandboxes):
            raise ValueError
        runner.catalog_revision = parsed.revision
        runner.catalog_report = protocol.serialize_payload(parsed)
        runner.save(update_fields=["catalog_revision", "catalog_report", "updated_at"])
        agents.AgentProfile.objects.filter(runner=runner, readiness_state="ready").update(
            readiness_state="unchecked",
            readiness_revision=None,
            readiness_digest="",
            readiness_configuration_digest="",
            readiness_configuration=None,
            enabled_revision=None,
        )
    except RunnerCredentialError:
        raise
    except ValueError:
        return _device_error(ValueError("Invalid runner catalog."))
    return json_success(request, {"schema_version": 1, "catalog_revision": runner.catalog_revision})


@csrf_exempt
@device_endpoint
@safe_agent_endpoint
@transaction.atomic
def claim_setup_device(request: HttpRequest) -> HttpResponse:
    token = _device_token(request)
    if token is None:
        return json_response(
            "error", "Runner authentication is required.", {"schema_version": 1}, status=401
        )
    data = requests.SetupClaim.model_validate_json(request.body)
    credential = authenticate_runner_token(token)
    runner = agents.AgentRunner.objects.select_for_update().get(id=credential.runner_id)
    authenticate_runner_token(token)
    setup = claim_setup(runner, data.setup_id, data.claim_key)
    return json_success(
        request,
        {
            "schema_version": 1,
            "setup_id": str(setup.id),
            "claim_key": str(setup.claim_key),
            "lease_epoch": setup.lease_epoch,
            "lease_expires_at": (
                setup.lease_expires_at.isoformat() if setup.lease_expires_at else None
            ),
            "descriptor": setup.descriptor,
        },
    )


@csrf_exempt
@device_endpoint
@safe_agent_endpoint
@transaction.atomic
def setup_result_device(request: HttpRequest) -> HttpResponse:
    token = _device_token(request)
    if token is None:
        return json_response(
            "error", "Runner authentication is required.", {"schema_version": 1}, status=401
        )
    data = requests.SetupResult.model_validate_json(request.body)
    credential = authenticate_runner_token(token)
    runner = agents.AgentRunner.objects.select_for_update().get(id=credential.runner_id)
    authenticate_runner_token(token)
    setup = record_setup_result(runner, data)
    return json_success(
        request, {"schema_version": 1, "setup_id": str(setup.id), "phase": setup.phase}
    )


@csrf_exempt
@safe_agent_endpoint
def list_setups_device(request: HttpRequest) -> HttpResponse:
    """Expose only this runner's unfinished probe work, without credential material."""
    if request.method != "GET":
        return json_response("error", "GET is required.", {"schema_version": 1}, status=405)
    token = _device_token(request)
    if token is None:
        return json_response(
            "error", "Runner authentication is required.", {"schema_version": 1}, status=401
        )
    credential = authenticate_runner_token(token)
    offset = int(request.GET.get("offset", "0"))
    if offset < 0:
        raise ValueError
    setups = agents.AgentSetupOperation.objects.filter(
        runner=credential.runner,
        realm=credential.realm,
        phase__in=["pending", "claimed", "probing"],
    ).order_by("created_at", "id")
    return json_success(
        request,
        {
            "schema_version": 1,
            "setups": [
                {
                    "setup_id": str(setup.id),
                    "phase": setup.phase,
                    "profile_revision": setup.profile_revision,
                    "provider_config_version": setup.provider_config_version,
                }
                for setup in setups[offset : offset + 100]
            ],
        },
    )


def _credential_result(credential: agents.AgentRunnerCredential) -> dict[str, object]:
    return {
        "runner_id": str(credential.runner_id),
        "expires_at": credential.expires_at.isoformat(),
        "refresh_expires_at": credential.refresh_expires_at.isoformat(),
    }


@csrf_exempt
@device_endpoint
def pairing_status_device(request: HttpRequest) -> HttpResponse:
    """Return one pairing state. Never return a credential after exchange."""
    rate_limit_request_by_ip(request, "agent_device_exchange_by_ip")
    try:
        data = _payload(request, fields={"pairing_id": str, "polling_secret": str})
        pairing = agents.AgentPairing.objects.get(id=UUID(cast(str, data["pairing_id"])))
        if not credential_matches(cast(str, data["polling_secret"]), pairing.polling_secret_hash):
            raise ValueError
    except (ValueError, agents.AgentPairing.DoesNotExist):
        return _device_error(ValueError("Pairing is unavailable."))
    state = pairing.state
    if state in {"pending", "approved"} and pairing.expires_at <= now():
        state = "expired"
    return json_success(
        request,
        {
            "schema_version": 1,
            "pairing_id": str(pairing.id),
            "state": state,
            "expires_at": pairing.expires_at.isoformat(),
            "runner_id": str(pairing.runner_id) if state == "exchanged" else None,
            "recovery": "re_pair_and_revoke_orphan"
            if state == "exchanged"
            else "re_pair"
            if state in {"expired", "rejected"}
            else "poll",
        },
    )


@csrf_exempt
@device_endpoint
@safe_agent_endpoint
@transaction.atomic
def register_workspace_device(request: HttpRequest) -> HttpResponse:
    """Register owner-approved metadata. Local paths are never accepted."""
    token = _device_token(request)
    if token is None:
        raise RunnerCredentialError("credential_invalid")
    credential = authenticate_runner_token(token)
    runner = agents.AgentRunner.objects.select_for_update().get(id=credential.runner_id)
    authenticate_runner_token(token)
    data = requests.WorkspaceReport.model_validate_json(request.body)
    if data.canonical_origin:
        _safe_origin(data.canonical_origin)
    checks = [item.model_dump(mode="json") for item in data.required_checks]
    metadata = {
        "canonical_origin": data.canonical_origin or "",
        "allowed_refs": data.allowed_refs,
        "required_checks": checks,
    }
    repository = (
        agents.AgentRepository.objects.select_for_update()
        .filter(
            runner=runner,
            workspace_alias=data.workspace_alias,
        )
        .first()
    )
    if repository is None:
        if data.revision != 1:
            raise ValueError("Initial revision must be one.")
        repository = register_repository(
            runner.owner,
            runner,
            workspace_alias=data.workspace_alias,
            canonical_origin=data.canonical_origin,
            allowed_refs=data.allowed_refs,
            required_checks=checks,
        )
    else:
        if repository.owner_id != runner.owner_id or repository.disabled_at is not None:
            raise ValueError("Workspace is unavailable.")
        same = all(getattr(repository, key) == value for key, value in metadata.items())
        if data.revision == repository.policy_version and same:
            pass
        elif data.revision == repository.policy_version + 1:
            for key, value in metadata.items():
                setattr(repository, key, value)
            repository.policy_version = data.revision
            repository.save(update_fields=[*metadata, "policy_version", "updated_at"])
            agents.AgentProfile.objects.filter(default_repository=repository).update(
                readiness_state="unchecked",
                readiness_revision=None,
                readiness_digest="",
                readiness_configuration_digest="",
                readiness_configuration=None,
                enabled_revision=None,
            )
        else:
            raise ValueError("Workspace revision conflicts.")
    return json_success(
        request,
        {
            "schema_version": 1,
            "repository": {
                "id": str(repository.id),
                "workspace_alias": repository.workspace_alias,
                "revision": repository.policy_version,
                "policy_version": repository.policy_version,
            },
        },
    )
