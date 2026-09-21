"""Anonymous pairing and runner-bearer endpoints.

These endpoints never use session or Zulip user API authentication.
"""

import json
from typing import cast
from uuid import UUID

from django.http import HttpRequest, HttpResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from zerver.actions.agents import (
    authenticate_runner_refresh,
    authenticate_runner_token,
    exchange_pairing,
    rotate_runner_credential,
    start_pairing,
)
from zerver.lib.rate_limiter import rate_limit_request_by_ip
from zerver.lib.response import json_response, json_success, json_unauthorized
from zerver.models import agents


def _payload(request: HttpRequest, *, fields: dict[str, type[object]]) -> dict[str, object]:
    try:
        value = json.loads(request.body)
    except (TypeError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("Invalid device request.") from error
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
@require_POST
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
    except ValueError as error:
        return _device_error(error)
    return json_success(
        request, {"schema_version": 1, "pairing_id": str(pairing.id), "state": pairing.state}
    )


@csrf_exempt
@require_POST
def exchange_pairing_device(request: HttpRequest, pairing_id: UUID) -> HttpResponse:
    rate_limit_request_by_ip(request, "agent_device_exchange_by_ip")
    try:
        data = _payload(request, fields={"polling_secret": str})
        pairing = agents.AgentPairing.objects.get(id=pairing_id)
        token, refresh_token = exchange_pairing(pairing, cast(str, data["polling_secret"]))
    except (agents.AgentPairing.DoesNotExist, ValueError):
        return _device_error(ValueError("Pairing is unavailable."))
    return json_success(
        request, {"schema_version": 1, "token": token, "refresh_token": refresh_token}
    )


@csrf_exempt
@require_POST
def rotate_device_credential(request: HttpRequest) -> HttpResponse:
    rate_limit_request_by_ip(request, "agent_device_exchange_by_ip")
    if request.user.is_authenticated:
        return json_unauthorized("Runner bearer authentication is required.")
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
@require_POST
def update_runner_catalog(request: HttpRequest) -> HttpResponse:
    token = _device_token(request)
    if token is None:
        return json_unauthorized("Runner bearer authentication is required.")
    try:
        credential = authenticate_runner_token(token)
        data = _payload(request, fields={"catalog": dict})
        catalog = data.get("catalog")
        if not isinstance(catalog, dict):
            raise ValueError
        # Validate here. Django stores only a typed runner catalog.
        from zerver.lib import agent_protocol as protocol

        parsed = protocol.RunnerCatalog.model_validate(catalog)
        runner = credential.runner
        if parsed.revision <= runner.catalog_revision:
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
