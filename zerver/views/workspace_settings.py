"""Views for the settings page: workspace settings. The logic lives in
zerver/lib/workspace_settings.py. This module parses each request and
checks who may call each endpoint."""

from typing import Annotated

from django.http import HttpRequest, HttpResponse
from django.utils.translation import gettext as _
from pydantic import Field, Json

from zerver.lib.exceptions import JsonableError
from zerver.lib.response import json_success
from zerver.lib.role_permissions import has_role_permission
from zerver.lib.typed_endpoint import typed_endpoint, typed_endpoint_without_parameters
from zerver.lib.workspace_settings import realm_settings_payload, update_realm_settings
from zerver.models import UserProfile

# At most one week.
ApprovalTtlMinutes = Annotated[int, Field(ge=1, lt=10081)]


def _require_permission(user_profile: UserProfile, key: str) -> None:
    if not has_role_permission(user_profile, key):
        raise JsonableError(_("You do not have permission to do this."))


@typed_endpoint_without_parameters
def get_realm_settings(request: HttpRequest, user_profile: UserProfile) -> HttpResponse:
    return json_success(request, data=realm_settings_payload(user_profile))


@typed_endpoint
def patch_realm_settings(
    request: HttpRequest,
    user_profile: UserProfile,
    *,
    brand_color: str | None = None,
    timezone: str | None = None,
    summary_default: Json[bool] | None = None,
    monthly_budget_microunits: Json[int | str] | None = None,
    approval_ttl_minutes: Json[ApprovalTtlMinutes] | None = None,
    invite_expiry_days: Json[int | str] | None = None,
    task_status_notices: Json[bool] | None = None,
    require_2fa: Json[bool] | None = None,
    task_id_prefix: str | None = None,
    work_runner_id: str | None = None,
    work_provider_id: str | None = None,
    agent_language: str | None = None,
    mcp_default_mode: str | None = None,
) -> HttpResponse:
    _require_permission(user_profile, "ws_settings")
    payload = update_realm_settings(
        user_profile,
        brand_color=brand_color,
        timezone=timezone,
        summary_default=summary_default,
        monthly_budget_microunits=monthly_budget_microunits,
        approval_ttl_minutes=approval_ttl_minutes,
        invite_expiry_days=invite_expiry_days,
        task_status_notices=task_status_notices,
        require_2fa=require_2fa,
        task_id_prefix=task_id_prefix,
        work_runner_id=work_runner_id,
        work_provider_id=work_provider_id,
        agent_language=agent_language,
        mcp_default_mode=mcp_default_mode,
    )
    return json_success(request, data=payload)
