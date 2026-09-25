"""The audit log behind `GET /json/realm/audit` and
`GET /json/realm/audit.csv`: `RealmAuditLog` (actions of people and of the
server) merged with `AgentAuditEvent` (actions in agent jobs), newest
first.

The log shows only the event types and the details that this module
lists. It never shows message text, the text of a job, or a link to a
data export."""

import csv
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import datetime, timezone, tzinfo
from heapq import merge
from itertools import islice
from typing import Literal, TypedDict
from uuid import UUID
from zoneinfo import ZoneInfo

from django.db.models import Q, QuerySet
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy
from django.utils.translation import override as override_language
from django_stubs_ext import StrPromise

from zerver.lib.exceptions import JsonableError
from zerver.lib.role_permissions import GROUP_SETTING_MAP
from zerver.lib.timezone import canonicalize_timezone
from zerver.models import AgentAuditEvent, AgentRealmSettings, Realm, RealmAuditLog, UserProfile
from zerver.models.realm_audit_logs import AuditLogEventType

Source = Literal["realm", "agent"]


class AuditActor(TypedDict):
    kind: Literal["human", "agent"]
    id: int
    name: str


class AuditEntry(TypedDict):
    # "<source>:<ID of the row>". The next page starts after it.
    id: str
    event_type: str
    time: float
    actor: AuditActor | None
    parameter: dict[str, object]
    # The table of the row: RealmAuditLog ("realm") or AgentAuditEvent
    # ("agent"). Only this module reads it.
    source: Source


@dataclass(frozen=True)
class AuditCursor:
    """The last event of the page before. The log sorts events newest
    first. At the same instant, a realm event comes before an agent event,
    and in each table a higher ID comes first."""

    time: datetime
    source: Source | None
    row_id: int | UUID | None


# The RealmAuditLog event types that the log shows, and the text that the
# CSV file shows for each one.
_EVENT_LABELS: dict[AuditLogEventType, StrPromise] = {
    AuditLogEventType.WORKSPACE_SETTINGS_CHANGED: gettext_lazy("Changed workspace settings."),
    AuditLogEventType.PERMISSION_MATRIX_CHANGED: gettext_lazy(
        "Changed the role permission matrix."
    ),
    AuditLogEventType.REALM_PROPERTY_CHANGED: gettext_lazy("Changed a permission setting."),
    AuditLogEventType.ROOM_META_CHANGED: gettext_lazy("Changed a room's details."),
    AuditLogEventType.ROOM_ARCHIVED_BULK: gettext_lazy("Archived quiet rooms."),
    AuditLogEventType.AGENT_PROFILE_CREATED: gettext_lazy("Created an agent."),
    AuditLogEventType.AGENT_PROFILE_PAUSED: gettext_lazy("Paused an agent."),
    AuditLogEventType.AGENT_PROFILE_ENABLED: gettext_lazy("Enabled an agent."),
    AuditLogEventType.AGENT_PROFILE_SHARED: gettext_lazy("Shared an agent."),
    AuditLogEventType.RUNNER_PAIRED: gettext_lazy("Paired a runner."),
    AuditLogEventType.RUNNER_REVOKED: gettext_lazy("Revoked runner access."),
    AuditLogEventType.RUNNER_TOKEN_CREATED: gettext_lazy("Created a runner setup token."),
    AuditLogEventType.INTEGRATION_CONNECTED: gettext_lazy("Connected an integration."),
    AuditLogEventType.INTEGRATION_DISCONNECTED: gettext_lazy("Disconnected an integration."),
    AuditLogEventType.DRIVE_FOLDER_LINKED: gettext_lazy("Linked a Drive folder."),
    AuditLogEventType.DRIVE_FOLDER_UNLINKED: gettext_lazy("Unlinked a Drive folder."),
    AuditLogEventType.MCP_CONNECTION_CHANGED: gettext_lazy("Changed an MCP connection."),
    AuditLogEventType.MCP_TOOL_POLICY_CHANGED: gettext_lazy("Changed an MCP tool policy."),
    AuditLogEventType.MODEL_SOURCE_CHANGED: gettext_lazy("Changed the model source."),
    AuditLogEventType.RUNNER_ROTATED: gettext_lazy("Rotated runner credentials."),
    AuditLogEventType.RUNNER_HIDDEN: gettext_lazy("Hid a runner."),
    AuditLogEventType.RUNNER_PAIRING_DENIED: gettext_lazy("Denied a runner pairing request."),
    AuditLogEventType.CLOUD_RUNNER_CHANGED: gettext_lazy("Changed a cloud runner."),
    AuditLogEventType.WHATSAPP_LINK_CHANGED: gettext_lazy("Changed a WhatsApp room link."),
    AuditLogEventType.MODEL_KEY_ROTATED: gettext_lazy("Rotated a model API key."),
    AuditLogEventType.USER_CREATED: gettext_lazy("Added a member."),
    AuditLogEventType.USER_DEACTIVATED: gettext_lazy("Deactivated a member."),
    AuditLogEventType.USER_REACTIVATED: gettext_lazy("Reactivated a member."),
    AuditLogEventType.USER_ROLE_CHANGED: gettext_lazy("Changed a member's role."),
    AuditLogEventType.INVITATION_REVOKED: gettext_lazy("Revoked an invitation."),
}

_OLD_AND_NEW = {RealmAuditLog.OLD_VALUE: "old_value", RealmAuditLog.NEW_VALUE: "new_value"}
# For each event type whose details the log shows: extra_data key -> the
# name of that detail in the log. Other event types show no details.
_PARAMETER_KEYS: dict[AuditLogEventType, dict[str, str]] = {
    AuditLogEventType.WORKSPACE_SETTINGS_CHANGED: {"changed": "changed"},
    AuditLogEventType.PERMISSION_MATRIX_CHANGED: {"changed": "changed"},
    AuditLogEventType.USER_ROLE_CHANGED: _OLD_AND_NEW,
    AuditLogEventType.REALM_PROPERTY_CHANGED: {"property": "property", **_OLD_AND_NEW},
}

# REALM_PROPERTY_CHANGED appears only for the group settings behind the
# role permission matrix.
_MATRIX_SETTINGS = sorted({name for names in GROUP_SETTING_MAP.values() for name in names})
_OTHER_SHOWN_EVENT_TYPES = [
    event_type
    for event_type in _EVENT_LABELS
    if event_type != AuditLogEventType.REALM_PROPERTY_CHANGED
]
_MATRIX_SETTING_CHANGES = Q(
    event_type=AuditLogEventType.REALM_PROPERTY_CHANGED, extra_data__property__in=_MATRIX_SETTINGS
)
_SHOWN_REALM_EVENTS = Q(event_type__in=_OTHER_SHOWN_EVENT_TYPES) | _MATRIX_SETTING_CHANGES

# The only AgentAuditEvent payload keys that the log shows. The text of a
# job, such as its summary or its question, never appears.
_AGENT_PARAMETER_KEYS = ("status", "reason", "tool_class", "exit_code")

_SOURCE_RANK: dict[Source, int] = {"realm": 1, "agent": 0}


def parse_audit_cursor(before: float, before_id: str | None) -> AuditCursor:
    time = datetime.fromtimestamp(before, tz=timezone.utc)
    if before_id is None:
        return AuditCursor(time, None, None)
    source, _separator, row_id = before_id.partition(":")
    try:
        if source == "realm":
            return AuditCursor(time, "realm", int(row_id))
        if source == "agent":
            return AuditCursor(time, "agent", UUID(row_id))
    except ValueError:
        pass
    raise JsonableError(_("Invalid audit log position."))


def _older_than(cursor: AuditCursor | None, source: Source, time_field: str) -> Q:
    """The rows of `source` that come after `cursor` in the log order."""
    if cursor is None:
        return Q()
    older = Q(**{f"{time_field}__lt": cursor.time})
    if cursor.source == source:
        return older | Q(**{time_field: cursor.time, "id__lt": cursor.row_id})
    if cursor.source is not None and _SOURCE_RANK[source] < _SOURCE_RANK[cursor.source]:
        # At the cursor's instant, this table comes after the table of the
        # cursor, so none of its rows at that instant was shown.
        return older | Q(**{time_field: cursor.time})
    return older


def _realm_rows(realm: Realm, cursor: AuditCursor | None) -> QuerySet[RealmAuditLog]:
    return (
        RealmAuditLog.objects.filter(
            _SHOWN_REALM_EVENTS, _older_than(cursor, "realm", "event_time"), realm=realm
        )
        .select_related("acting_user", "modified_user")
        .order_by("-event_time", "-id")
    )


def _agent_rows(realm: Realm, cursor: AuditCursor | None) -> QuerySet[AgentAuditEvent]:
    return (
        AgentAuditEvent.objects.filter(_older_than(cursor, "agent", "occurred_at"), realm=realm)
        .select_related("actor", "job__profile__bot_user")
        .order_by("-occurred_at", "-id")
    )


def _actor(user: UserProfile | None) -> AuditActor | None:
    if user is None:
        return None
    return {"kind": "agent" if user.is_bot else "human", "id": user.id, "name": user.full_name}


def _realm_entry(row: RealmAuditLog) -> AuditEntry:
    event_type = AuditLogEventType(row.event_type)
    extra_data = row.extra_data or {}
    parameter: dict[str, object] = {
        name: extra_data[key]
        for key, name in _PARAMETER_KEYS.get(event_type, {}).items()
        if key in extra_data
    }
    modified_user = row.modified_user
    if modified_user is not None:
        parameter["modified_user_name"] = modified_user.full_name
    return {
        "id": f"realm:{row.id}",
        "event_type": event_type.name,
        "time": row.event_time.timestamp(),
        "actor": _actor(row.acting_user),
        "parameter": parameter,
        "source": "realm",
    }


def _agent_entry(row: AgentAuditEvent) -> AuditEntry:
    payload = row.payload or {}
    actor = row.actor
    if actor is None and row.authority != "server":
        # The runner, the verifier, or the publisher acted for the agent of
        # the job.
        actor = row.job.profile.bot_user
    return {
        "id": f"agent:{row.id}",
        "event_type": row.type,
        "time": row.occurred_at.timestamp(),
        "actor": _actor(actor),
        "parameter": {key: payload[key] for key in _AGENT_PARAMETER_KEYS if key in payload},
        "source": "agent",
    }


def _sort_key(entry: AuditEntry) -> tuple[float, int]:
    return entry["time"], _SOURCE_RANK[entry["source"]]


def _merged(
    realm_entries: Iterable[AuditEntry], agent_entries: Iterable[AuditEntry]
) -> Iterator[AuditEntry]:
    # Both inputs are newest first. The two tables never tie on the sort
    # key, and merge() keeps the database order in each table.
    return merge(realm_entries, agent_entries, key=_sort_key, reverse=True)


def list_realm_audit_events(
    realm: Realm, *, cursor: AuditCursor | None = None, limit: int = 50
) -> list[AuditEntry]:
    """Newest first. Each table gives at most `limit` rows, which is
    enough for a merged page of `limit` events."""
    realm_entries = map(_realm_entry, _realm_rows(realm, cursor)[:limit])
    agent_entries = map(_agent_entry, _agent_rows(realm, cursor)[:limit])
    return list(islice(_merged(realm_entries, agent_entries), limit))


# -- CSV: text in the workspace's own language ---------------------------


def csv_cell(value: str) -> str:
    """Put ' before a value that a spreadsheet program reads as a formula."""
    if value.startswith(("=", "+", "-", "@", "\t", "\r")):
        return "'" + value
    return value


class _Echo:
    """A file object whose write() returns its text, so that csv.writer
    makes one line at a time for a streaming response."""

    def write(self, value: str) -> str:
        return value


def _describe(entry: AuditEntry) -> str:
    if entry["source"] == "agent":
        # The event type of a job is an action name from the job protocol.
        action = entry["event_type"].replace(".", " ").replace("_", " ")
        return _("Agent activity: {action}.").format(action=action)
    name = entry["parameter"].get("modified_user_name")
    if entry["event_type"] == "USER_ROLE_CHANGED" and isinstance(name, str):
        return _("Changed the role for {name}.").format(name=name)
    return str(_EVENT_LABELS[AuditLogEventType[entry["event_type"]]])


def export_realm_audit_csv(realm: Realm) -> Iterator[str]:
    """Every event, newest first, as lines of a CSV file: the time in the
    workspace's time zone, who acted, and what happened, in the
    workspace's language."""
    settings_row = AgentRealmSettings.objects.filter(realm=realm).first()
    zone: tzinfo = timezone.utc
    if settings_row is not None:
        zone = ZoneInfo(canonicalize_timezone(settings_row.timezone))
    language = realm.default_language
    writer = csv.writer(_Echo())
    # A byte order mark tells a spreadsheet program that the file is UTF-8.
    yield "﻿"
    with override_language(language):
        header = [_("Time"), _("Who"), _("What")]
    yield writer.writerow(header)
    entries = _merged(
        map(_realm_entry, _realm_rows(realm, None).iterator()),
        map(_agent_entry, _agent_rows(realm, None).iterator()),
    )
    for entry in entries:
        when = datetime.fromtimestamp(entry["time"], tz=zone).strftime("%Y-%m-%d %H:%M:%S %z")
        with override_language(language):
            who = entry["actor"]["name"] if entry["actor"] is not None else _("System")
            what = _describe(entry)
        yield writer.writerow([csv_cell(when), csv_cell(who), csv_cell(what)])
