"""Views for the settings page: workspace settings, the role permission
matrix, the audit log, and the workspace switcher list. The logic lives in
zerver/lib/workspace_settings.py and zerver/lib/audit_feed.py. This module
parses each request and checks who may call each endpoint."""

from typing import Annotated

from django.http import HttpRequest, HttpResponse, HttpResponseBase, StreamingHttpResponse
from django.utils.translation import gettext as _
from pydantic import Field, Json

from zerver.lib.audit_feed import (
    export_realm_audit_csv,
    list_realm_audit_events,
    parse_audit_cursor,
)
from zerver.lib.exceptions import JsonableError
from zerver.lib.response import json_success
from zerver.lib.role_permissions import has_role_permission
from zerver.lib.typed_endpoint import typed_endpoint, typed_endpoint_without_parameters
from zerver.lib.workspace_settings import (
    PermissionChange,
    apply_permission_changes,
    list_my_workspaces,
    permission_matrix_payload,
    realm_settings_payload,
    update_realm_settings,
)
from zerver.models import UserProfile

DEFAULT_AUDIT_LIMIT = 50
MAX_AUDIT_LIMIT = 200

# At most one week.
ApprovalTtlMinutes = Annotated[int, Field(ge=1, lt=10081)]
AuditLimit = Annotated[int, Field(ge=1, lt=MAX_AUDIT_LIMIT + 1)]
# A Unix timestamp before the year 10000.
AuditTime = Annotated[float, Field(ge=0, lt=253402300800)]


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


@typed_endpoint_without_parameters
def get_permission_matrix(request: HttpRequest, user_profile: UserProfile) -> HttpResponse:
    return json_success(request, data=permission_matrix_payload(user_profile))


@typed_endpoint
def put_permission_matrix(
    request: HttpRequest,
    user_profile: UserProfile,
    *,
    changes: Json[list[PermissionChange]],
) -> HttpResponse:
    # Every cell is locked for a person who is not an Owner or an Admin.
    if not user_profile.is_realm_admin:
        raise JsonableError(_("You do not have permission to do this."))
    apply_permission_changes(user_profile, changes)
    return json_success(request)


@typed_endpoint
def get_realm_audit(
    request: HttpRequest,
    user_profile: UserProfile,
    *,
    before: Json[AuditTime] | None = None,
    before_id: str | None = None,
    limit: Json[AuditLimit] = DEFAULT_AUDIT_LIMIT,
) -> HttpResponse:
    _require_permission(user_profile, "audit")
    cursor = parse_audit_cursor(before, before_id) if before is not None else None
    events = list_realm_audit_events(user_profile.realm, cursor=cursor, limit=limit)
    # "source" is internal to audit_feed and not part of the response.
    public_events = [
        {
            "id": event["id"],
            "event_type": event["event_type"],
            "time": event["time"],
            "actor": event["actor"],
            "parameter": event["parameter"],
        }
        for event in events
    ]
    return json_success(request, data={"events": public_events})


@typed_endpoint_without_parameters
def get_realm_audit_csv(request: HttpRequest, user_profile: UserProfile) -> HttpResponseBase:
    _require_permission(user_profile, "audit")
    response = StreamingHttpResponse(
        export_realm_audit_csv(user_profile.realm), content_type="text/csv; charset=utf-8"
    )
    filename = f"audit-{user_profile.realm.string_id or 'workspace'}.csv"
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


@typed_endpoint_without_parameters
def list_workspaces(request: HttpRequest, user_profile: UserProfile) -> HttpResponse:
    return json_success(request, data={"workspaces": list_my_workspaces(user_profile)})
