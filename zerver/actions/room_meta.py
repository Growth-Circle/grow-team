"""Room metadata actions: effective owner, edit permissions, the meta and
archive mutations, type folders, and the quiet-room nudge DM (spec 01, 04;
PLAN.md WP14)."""

from collections import defaultdict
from collections.abc import Iterable
from datetime import date

from django.db import transaction
from django.utils.timezone import now as timezone_now
from django.utils.translation import gettext as _
from django.utils.translation import override as override_language

from zerver.actions.channel_folders import check_add_channel_folder
from zerver.actions.message_send import internal_send_private_message, internal_send_stream_message
from zerver.actions.streams import bulk_add_subscriptions, do_deactivate_stream
from zerver.lib.agent_events import send_room_meta_event
from zerver.lib.exceptions import JsonableError
from zerver.lib.quiet_rooms import QuietRoomNotice, rooms_needing_notice
from zerver.lib.role_permissions import has_role_permission
from zerver.lib.streams import (
    access_stream_for_send_message, can_administer_accessible_channel, channel_events_topic_name,
)
from zerver.models import AgentProfile, ChannelFolder, Realm, RealmAuditLog, RoomMeta, Stream, UserProfile
from zerver.models.realm_audit_logs import AuditLogEventType

ROOM_TYPE_FOLDER_NAMES = ("Proyek", "Klien", "Tim")


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


def _quiet_room_notice_text(notices: list[QuietRoomNotice]) -> str:
    lines = []
    for notice in notices:
        if notice.due_date_passed:
            lines.append(
                _("* #**{stream_name}**: past its due date. Consider archiving it.").format(
                    stream_name=notice.stream.name
                )
            )
        else:
            lines.append(
                _("* #**{stream_name}**: no activity for 30+ days. Consider archiving it.").format(
                    stream_name=notice.stream.name
                )
            )
    header = _("These channels may be ready to archive:")
    return header + "\n" + "\n".join(lines)


def send_quiet_room_notices(realm: Realm) -> int:
    """Kaki DMs each owner once for the rooms that just went quiet or
    passed their project due date (PLAN.md WP14 step 5, P-31/Q-02: Kaki
    only ever suggests, never archives). Returns how many owners were
    actually DMed (WP14 review defect 15: a failed send must not be
    counted, or mark the room as notified)."""
    sender = _find_builtin_agent_bot(realm, "Kaki")
    if sender is None:
        return 0
    notices = rooms_needing_notice(realm)
    if not notices:
        return 0
    by_owner: dict[int, list[QuietRoomNotice]] = defaultdict(list)
    for notice in notices:
        owner = get_room_owner(notice.stream)
        if owner is not None and owner.is_active:
            by_owner[owner.id].append(notice)
    if not by_owner:
        return 0
    owners = {user.id: user for user in UserProfile.objects.filter(id__in=by_owner)}
    notified_at = timezone_now()
    notified_owners = 0
    with transaction.atomic(savepoint=False):
        for owner_id, owner_notices in by_owner.items():
            owner = owners[owner_id]
            with override_language(owner.default_language):
                content = _quiet_room_notice_text(owner_notices)
            message_id = internal_send_private_message(sender, owner, content)
            if message_id is None:
                continue
            notified_owners += 1
            for notice in owner_notices:
                notice.room_meta.quiet_notified_at = notified_at
                notice.room_meta.save(update_fields=["quiet_notified_at"])
    return notified_owners


def check_can_archive_room(user_profile: UserProfile, stream: Stream) -> bool:
    """Caller must already have verified access to `stream`."""
    if can_administer_accessible_channel(stream, user_profile):
        return True
    return has_role_permission(user_profile, "room_archive")


@transaction.atomic(savepoint=False)
def do_bulk_archive_rooms(streams: Iterable[Stream], *, acting_user: UserProfile) -> None:
    event_time = timezone_now()
    for stream in streams:
        do_deactivate_stream(stream, acting_user=acting_user)
        RealmAuditLog.objects.create(
            realm=stream.realm,
            acting_user=acting_user,
            modified_stream=stream,
            event_type=AuditLogEventType.ROOM_ARCHIVED_BULK,
            event_time=event_time,
        )


class NoRealmOwnerError(Exception):
    """Raised by ensure_room_type_folders when the realm has no active,
    human Owner to act as the folders' creator (WP14 review defect 14: a
    silent no-op here would leave the management command reporting
    success with nothing created)."""


def ensure_room_type_folders(
    realm: Realm, *, acting_user: UserProfile | None = None
) -> list[ChannelFolder]:
    """Create the Proyek/Klien/Tim folders for `realm` if missing.
    Idempotent: never touches a folder or channel that already exists.
    Matches an existing folder name case-insensitively (WP14 review
    defect 8), so a folder made by hand still counts."""
    existing_names = {
        name.lower()
        for name in ChannelFolder.objects.filter(realm=realm, is_archived=False).values_list(
            "name", flat=True
        )
    }
    creator = acting_user or get_default_realm_owner(realm)
    if creator is None:
        raise NoRealmOwnerError(_("This organization has no owner to create the folders."))
    created = []
    for name in ROOM_TYPE_FOLDER_NAMES:
        if name.lower() in existing_names:
            continue
        created.append(check_add_channel_folder(realm, name, "", acting_user=creator))
    return created
