"""Workspace settings for `zerver/views/workspace_settings.py`: the
`AgentRealmSettings` fields that Owner and Admin change from the settings
page (`update_realm_settings`).
"""

import re
from uuid import UUID

from django.conf import settings
from django.db import transaction
from django.utils.timezone import now as timezone_now
from django.utils.translation import gettext as _

from zerver.lib.agent_events import send_agent_realm_settings_event
from zerver.lib.agent_presence import observed_runner_status
from zerver.lib.exceptions import JsonableError, OrganizationOwnerRequiredError
from zerver.lib.typed_endpoint_validators import check_timezone
from zerver.models import (
    AgentProvider,
    AgentRealmSettings,
    AgentRunner,
    Realm,
    RealmAuditLog,
    UserProfile,
)
from zerver.models.realm_audit_logs import AuditLogEventType
from zproject.config import get_secret

# The four accent colors that a workspace can pick for its initial badge.
BRAND_COLORS: tuple[str, ...] = ("#FFD84D", "#16C784", "#8B74FF", "#FF6A3D")

# capabilities["fathom"] is True when the meeting-notes bot is an active
# user of the workspace.
FATHOM_BOT_FULL_NAME = "Catatan Rapat"

_INVITE_EXPIRY_CHOICES = (7, 30)
_AGENT_LANGUAGES = ("id", "en")
_MCP_MODES = ("research_first", "direct")
# The task_id_prefix column holds at most 10 characters.
_TASK_ID_PREFIX = re.compile(r"[A-Z0-9]{0,10}")
# The largest value of the bigint column that holds the budget.
_MAX_BUDGET_MICROUNITS = 2**63 - 1


# -- GET/PATCH /json/agent/realm-settings --------------------------------


def _runner_summary(runner: AgentRunner | None) -> dict[str, object] | None:
    if runner is None:
        return None
    # A runner can serve many models, so its summary names no model.
    return {
        "id": str(runner.id),
        "name": runner.name,
        "status": observed_runner_status(runner),
        "model": None,
    }


def _provider_summary(provider: AgentProvider | None) -> dict[str, object] | None:
    if provider is None:
        return None
    return {
        "id": str(provider.id),
        "name": provider.name,
        "status": "disabled" if provider.disabled_at is not None else "active",
        "model": provider.model_id,
    }


def can_create_workspace(user_profile: UserProfile) -> bool:
    return user_profile.is_realm_admin and bool(settings.WORKSPACE_CREATION_ENABLED)


def capabilities(realm: Realm) -> dict[str, object]:
    return {
        "drive": bool(settings.GOOGLE_INTEGRATIONS_CLIENT_ID),
        "calendar": bool(settings.GOOGLE_INTEGRATIONS_CLIENT_ID),
        "github": bool(settings.GITHUB_APP_ID),
        "whatsapp": bool(settings.WHATSAPP_PHONE_NUMBER_ID),
        "fathom": UserProfile.objects.filter(
            realm=realm, full_name=FATHOM_BOT_FULL_NAME, is_bot=True, is_active=True
        ).exists(),
        "fathom_join": bool(settings.FATHOM_JOIN_CONTROL),
        "openrouter": bool(get_secret("openrouter_management_key")),
        "web_push": bool(settings.VAPID_PUBLIC_KEY),
        "cloud_runner": bool(settings.CLOUD_RUNNER_PROVIDER),
        "docker_runner_image": bool(settings.RUNNER_IMAGE_REF),
        "vapid_public_key": settings.VAPID_PUBLIC_KEY or "",
    }


def _realm_settings_fields(row: AgentRealmSettings) -> dict[str, object]:
    """The settings fields, keyed by the names that GET reports. The GET
    payload and the settings event both read this, so they always agree."""
    return {
        "brand_color": row.brand_color,
        "timezone": row.timezone,
        "summary_default": row.summary_default,
        "monthly_budget_microunits": row.monthly_budget_microunits,
        "approval_ttl_minutes": row.approval_ttl_minutes,
        "invite_expiry_days": row.invite_expiry_days,
        "task_status_notices": row.task_status_notices,
        "require_2fa": row.require_2fa,
        "task_id_prefix": row.task_id_prefix,
        "agent_language": row.agent_language,
        "mcp_default_mode": row.mcp_default_mode,
        # Read-only here.
        "model_source": row.model_source,
        "model_presets": row.model_presets,
        "model_guardrails": row.model_guardrails,
        "work_runner": _runner_summary(row.work_runner),
        "work_provider": _provider_summary(row.work_provider),
    }


def realm_settings_payload(user_profile: UserProfile) -> dict[str, object]:
    # A workspace that never changed a setting has no row. An unsaved row
    # holds every default, so it reads the same as a saved one.
    realm = user_profile.realm
    row = (
        AgentRealmSettings.objects.select_related("work_runner", "work_provider")
        .filter(realm=realm)
        .first()
    )
    if row is None:
        row = AgentRealmSettings(realm=realm)
    return {
        **_realm_settings_fields(row),
        "can_create_workspace": can_create_workspace(user_profile),
        "capabilities": capabilities(realm),
    }


def _parse_invite_expiry_days(value: int | str) -> int | None:
    if value == "unlimited":
        return None
    if value in _INVITE_EXPIRY_CHOICES:
        assert isinstance(value, int)
        return value
    raise JsonableError(_("Choose 7 days, 30 days, or no limit."))


def _parse_budget(value: int | str) -> int | None:
    if value == "unlimited":
        return None
    if isinstance(value, int) and 0 <= value <= _MAX_BUDGET_MICROUNITS:
        return value
    raise JsonableError(_("That budget is not possible. Choose another amount or no limit."))


def _own_runner(user_profile: UserProfile, runner_id: str) -> AgentRunner:
    try:
        runner = AgentRunner.objects.get(id=UUID(runner_id), realm=user_profile.realm)
    except (AgentRunner.DoesNotExist, ValueError):
        raise JsonableError(_("That runner is not part of this workspace."))
    # Every member's work agents run on this runner, so it must be the
    # requester's own runner, never the machine of another person.
    if runner.owner_id != user_profile.id or runner.revoked_at is not None:
        raise JsonableError(_("Choose one of your own runners that still has access."))
    return runner


def _own_provider(user_profile: UserProfile, provider_id: str) -> AgentProvider:
    try:
        provider = AgentProvider.objects.get(id=UUID(provider_id), realm=user_profile.realm)
    except (AgentProvider.DoesNotExist, ValueError):
        raise JsonableError(_("That model provider is not part of this workspace."))
    # The same applies to the model provider and its API key.
    if provider.owner_id != user_profile.id or provider.disabled_at is not None:
        raise JsonableError(_("Choose one of your own model providers that is turned on."))
    return provider


def update_realm_settings(
    user_profile: UserProfile,
    *,
    brand_color: str | None = None,
    timezone: str | None = None,
    summary_default: bool | None = None,
    monthly_budget_microunits: int | str | None = None,
    approval_ttl_minutes: int | None = None,
    invite_expiry_days: int | str | None = None,
    task_status_notices: bool | None = None,
    require_2fa: bool | None = None,
    task_id_prefix: str | None = None,
    work_runner_id: str | None = None,
    work_provider_id: str | None = None,
    agent_language: str | None = None,
    mcp_default_mode: str | None = None,
) -> dict[str, object]:
    """A parameter left as None keeps its current value. An empty
    work_runner_id or work_provider_id clears that default, and a
    monthly_budget_microunits of "unlimited" removes the limit."""
    if monthly_budget_microunits is not None and not user_profile.is_realm_owner:
        raise OrganizationOwnerRequiredError
    updates: dict[str, object] = {}

    if brand_color is not None:
        if brand_color not in BRAND_COLORS:
            raise JsonableError(_("That color is not available."))
        updates["brand_color"] = brand_color
    if timezone is not None:
        try:
            updates["timezone"] = check_timezone(timezone)
        except ValueError:
            # check_timezone is a pydantic validator, so it raises a plain
            # ValueError. Convert it here, or the request fails with a 500.
            raise JsonableError(_("Not a recognized time zone"))
    if summary_default is not None:
        updates["summary_default"] = summary_default
    if monthly_budget_microunits is not None:
        updates["monthly_budget_microunits"] = _parse_budget(monthly_budget_microunits)
    if approval_ttl_minutes is not None:
        updates["approval_ttl_minutes"] = approval_ttl_minutes
    if invite_expiry_days is not None:
        updates["invite_expiry_days"] = _parse_invite_expiry_days(invite_expiry_days)
    if task_status_notices is not None:
        updates["task_status_notices"] = task_status_notices
    if require_2fa is not None:
        updates["require_2fa"] = require_2fa
    if task_id_prefix is not None:
        if _TASK_ID_PREFIX.fullmatch(task_id_prefix) is None:
            raise JsonableError(_("Use up to 10 capital letters or digits."))
        updates["task_id_prefix"] = task_id_prefix
    if work_runner_id is not None:
        updates["work_runner"] = (
            _own_runner(user_profile, work_runner_id) if work_runner_id else None
        )
    if work_provider_id is not None:
        updates["work_provider"] = (
            _own_provider(user_profile, work_provider_id) if work_provider_id else None
        )
    if agent_language is not None:
        if agent_language not in _AGENT_LANGUAGES:
            raise JsonableError(_("Choose Indonesian or English."))
        updates["agent_language"] = agent_language
    if mcp_default_mode is not None:
        if mcp_default_mode not in _MCP_MODES:
            raise JsonableError(_("That mode is not available."))
        updates["mcp_default_mode"] = mcp_default_mode

    if updates:
        _save_realm_settings(user_profile, updates)
    return realm_settings_payload(user_profile)


def _audit_value(value: object) -> object:
    """A setting's value as the audit log keeps it, and as the check for a
    real change compares it: a runner or a model provider by its ID."""
    if isinstance(value, AgentRunner | AgentProvider):
        return str(value.id)
    return value


@transaction.atomic
def _save_realm_settings(acting_user: UserProfile, updates: dict[str, object]) -> None:
    realm = acting_user.realm
    row, _created = AgentRealmSettings.objects.select_for_update().get_or_create(realm=realm)
    old_values = {field: _audit_value(getattr(row, field)) for field in updates}
    changed = {
        field: value for field, value in updates.items() if _audit_value(value) != old_values[field]
    }
    if not changed:
        return
    for field, value in changed.items():
        setattr(row, field, value)
    if (
        ("work_runner" in changed or "work_provider" in changed)
        and row.work_provider is not None
        and row.work_provider.runner_id != row.work_runner_id
    ):
        raise JsonableError(_("Choose a model provider that runs on the chosen runner."))
    row.updated_at = timezone_now()
    row.save(update_fields=[*changed, "updated_at"])
    RealmAuditLog.objects.create(
        realm=realm,
        acting_user=acting_user,
        event_type=AuditLogEventType.WORKSPACE_SETTINGS_CHANGED,
        event_time=row.updated_at,
        extra_data={
            "changed": [
                {
                    "property": field,
                    "old_value": old_values[field],
                    "new_value": _audit_value(value),
                }
                for field, value in sorted(changed.items())
            ]
        },
    )
    fresh = _realm_settings_fields(row)
    send_agent_realm_settings_event(realm, {field: fresh[field] for field in changed})
