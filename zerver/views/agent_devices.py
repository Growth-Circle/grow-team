"""Anonymous pairing and runner-bearer endpoints.

These endpoints never use session or Zulip user API authentication.
"""

import json
from collections.abc import Callable
from functools import wraps
from typing import cast
from uuid import UUID

from django.db import IntegrityError, transaction
from django.http import HttpRequest, HttpResponse
from django.views.decorators.csrf import csrf_exempt

from zerver.actions.agents import (
    authenticate_runner_refresh,
    authenticate_runner_token,
    claim_setup,
    exchange_pairing,
    record_setup_result,
    rotate_runner_credential,
    start_pairing,
)
from zerver.lib import agent_requests as requests
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
        try:
            return view(request)
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
        data = _payload(
            request,
            fields={
                "device_name": str,
                "fingerprint": str,
                "user_code": str,
                "polling_secret": str,
            },
        )
        pairing = start_pairing(
            cast(str, data["device_name"]),
            cast(str, data["fingerprint"]),
            cast(str, data["user_code"]),
            cast(str, data["polling_secret"]),
        )
    except (ValueError, IntegrityError):
        return _device_error(ValueError("Invalid device request."))
    return json_success(
        request, {"schema_version": 1, "pairing_id": str(pairing.id), "state": pairing.state}
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
            "runner_id": str(authenticate_runner_token(token).runner_id),
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
    except ValueError:
        return _device_error(ValueError("Runner credential is unavailable."))
    return json_success(
        request, {"schema_version": 1, "token": new_token, "refresh_token": refresh_token}
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
