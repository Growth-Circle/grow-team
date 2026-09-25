"""Room metadata actions: effective owner, edit permissions, and the meta
mutation (spec 01, 04; PLAN.md WP14)."""

from datetime import date

from django.db import transaction
from django.utils.timezone import now as timezone_now
from django.utils.translation import gettext as _
from django.utils.translation import override as override_language

from zerver.actions.message_send import internal_send_stream_message
from zerver.actions.streams import bulk_add_subscriptions
from zerver.lib.agent_events import send_room_meta_event
from zerver.lib.exceptions import JsonableError
from zerver.lib.role_permissions import has_role_permission
from zerver.lib.streams import access_stream_for_send_message, channel_events_topic_name
from zerver.models import AgentProfile, Realm, RealmAuditLog, RoomMeta, Stream, UserProfile
from zerver.models.realm_audit_logs import AuditLogEventType


def format_due_date(due_date: date) -> str:
    """`due_date` as a human-readable "30 Oct", never the raw ISO string a
    machine-readable field would use (map-mockup-a.md 11.3 step 4: "Selesai
    30 Okt"; WP14 review defect 16c). Each month name is translated on its
    own so this never depends on the server's own locale data."""
    month_names = (
        _("Jan"), _("Feb"), _("Mar"), _("Apr"), _("May"), _("Jun"),
        _("Jul"), _("Aug"), _("Sep"), _("Oct"), _("Nov"), _("Dec"),
    )
    return f"{due_date.day} {month_names[due_date.month - 1]}"


def get_default_realm_owner(realm: Realm) -> UserProfile | None:
    """The realm's longest-standing active, human Owner, used wherever a
    room has no individual owner to fall back on."""
    return (
        UserProfile.objects.filter(
            realm=realm, role=UserProfile.ROLE_REALM_OWNER, is_active=True, is_bot=False
        )
        .order_by("id")
        .first()
    )


def get_room_owner(stream: Stream) -> UserProfile | None:
    """Stream.creator, or the realm's fallback Owner when the room has no
    creator, or its creator is deactivated or a bot (PLAN.md WP14 step 1;
    WP14 review defect 13: a deactivated or bot creator must not silently
    swallow the quiet-room DM, or be shown as the room's owner)."""
    creator = stream.creator
    if creator is not None and creator.is_active and not creator.is_bot:
        return creator
    return get_default_realm_owner(stream.realm)


def can_edit_room_meta(user_profile: UserProfile, stream: Stream) -> bool:
    """The room's owner, or an organization Admin/Owner, per the brief's
    "pemilik ruang atau Admin"."""
    owner = get_room_owner(stream)
    if owner is not None and owner.id == user_profile.id:
        return True
    return user_profile.role in (UserProfile.ROLE_REALM_OWNER, UserProfile.ROLE_REALM_ADMINISTRATOR)


def can_toggle_room_summary(user_profile: UserProfile, stream: Stream) -> bool:
    """Stricter than can_edit_room_meta: only the room's own owner, and
    only with the room_summary permission (never just being an Admin)."""
    owner = get_room_owner(stream)
    if owner is None or owner.id != user_profile.id:
        return False
    return has_role_permission(user_profile, "room_summary")


def get_or_create_room_meta(stream: Stream) -> RoomMeta:
    room_meta, _created = RoomMeta.objects.get_or_create(stream=stream)
    return room_meta


def get_room_meta_or_unsaved(stream: Stream) -> RoomMeta:
    """Like get_or_create_room_meta, but never writes to the database: a
    plain GET must not create a permanent row just because someone looked
    (WP14 review defect 9b). An unsaved RoomMeta carries the same field
    defaults (due_date None, summary_enabled False) a freshly created one
    would."""
    return RoomMeta.objects.filter(stream=stream).first() or RoomMeta(stream=stream)


def _find_builtin_agent_bot(realm: Realm, name: str) -> UserProfile | None:
    """The bot user behind a built-in agent such as Kaki: the
    oldest non-archived profile with an active bot user, or None when
    this realm has not had its built-in agents seeded yet, or they were
    archived (WP14 review defect 12)."""
    profile = (
        AgentProfile.objects.filter(
            realm=realm, is_builtin=True, name=name,
            archived_at__isnull=True, bot_user__is_active=True,
        )
        .select_related("bot_user")
        .order_by("id")
        .first()
    )
    return None if profile is None else profile.bot_user


def _room_announce_text(
    *, stream_name: str, owner_name: str, due_date: date | None, is_proyek_folder: bool
) -> str:
    # Text from map-mockup-a.md 11.3 step 4, with the archive clause
    # replaced per PLAN.md 3.5 (Kaki only ever suggests archiving). The
    # reminder promise only ever applies to a Proyek-type room (WP14
    # review defect 11c); the due date itself reads as "30 Oct", never an
    # ISO string (defect 16c).
    if due_date is None:
        return _(
            "Channel #**{stream_name}** created. Owner: {owner_name}. Mention me to"
            " start splitting tasks."
        ).format(stream_name=stream_name, owner_name=owner_name)
    due_date_text = format_due_date(due_date)
    if not is_proyek_folder:
        return _(
            "Channel #**{stream_name}** created. Owner: {owner_name}. Due {due_date}."
            " Mention me to start splitting tasks."
        ).format(stream_name=stream_name, owner_name=owner_name, due_date=due_date_text)
    return _(
        "Channel #**{stream_name}** created. Owner: {owner_name}. Due {due_date},"
        " and Kaki will remind the owner to archive this channel after {due_date}."
        " Mention me to start splitting tasks."
    ).format(stream_name=stream_name, owner_name=owner_name, due_date=due_date_text)


def _send_room_announce(stream: Stream, room_meta: RoomMeta, *, acting_user: UserProfile) -> None:
    sender = _find_builtin_agent_bot(stream.realm, "Kaki")
    if sender is None:
        # Kaki is seeded per realm by a later work package; skip quietly
        # rather than fail the request until that lands.
        return
    try:
        access_stream_for_send_message(sender, stream, forwarder_user_profile=None)
    except JsonableError:
        # A private room, or one with a restricted send policy: Kaki is
        # not yet a subscriber. Subscribe it and retry once, instead of
        # dropping the room's first message (WP14 review defect 2).
        bulk_add_subscriptions(stream.realm, [stream], [sender], acting_user=acting_user)
        access_stream_for_send_message(sender, stream, forwarder_user_profile=None)
    owner = get_room_owner(stream)
    is_proyek_folder = stream.folder is not None and stream.folder.name.lower() == "proyek"
    with override_language(stream.realm.default_language):
        owner_name = owner.full_name if owner is not None else _("nobody")
        content = _room_announce_text(
            stream_name=stream.name, owner_name=owner_name,
            due_date=room_meta.due_date, is_proyek_folder=is_proyek_folder,
        )
        internal_send_stream_message(sender, stream, channel_events_topic_name(stream), content)


class DueDateUnset:
    """Sentinel: the PATCH request left `due_date` out entirely, as
    opposed to explicitly clearing it with `null`."""


DUE_DATE_UNSET = DueDateUnset()


@transaction.atomic(savepoint=False)
def do_update_room_meta(
    stream: Stream, *, due_date: date | None | DueDateUnset = DUE_DATE_UNSET,
    summary_enabled: bool | None = None, announce: bool = False, acting_user: UserProfile,
) -> RoomMeta:
    room_meta = get_or_create_room_meta(stream)
    changed: dict[str, object] = {}
    if not isinstance(due_date, DueDateUnset) and due_date != room_meta.due_date:
        changed["due_date"] = None if due_date is None else due_date.isoformat()
        room_meta.due_date = due_date
    if summary_enabled is not None and summary_enabled != room_meta.summary_enabled:
        changed["summary_enabled"] = summary_enabled
        room_meta.summary_enabled = summary_enabled
        room_meta.summary_enabled_by = acting_user
    if changed:
        update_fields = [*changed.keys()]
        if "summary_enabled" in changed:
            update_fields.append("summary_enabled_by")
        room_meta.save(update_fields=update_fields)
        RealmAuditLog.objects.create(
            realm=stream.realm, acting_user=acting_user, modified_stream=stream,
            event_type=AuditLogEventType.ROOM_META_CHANGED, event_time=timezone_now(),
            extra_data={"changed": changed},
        )
        send_room_meta_event(room_meta)
    if announce:
        _send_room_announce(stream, room_meta, acting_user=acting_user)
    return room_meta
