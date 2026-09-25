from datetime import date
from io import StringIO

import orjson
from django.core.management import call_command
from django.core.management.base import CommandError

from zerver.actions.channel_folders import check_add_channel_folder
from zerver.actions.room_meta import (
    NoRealmOwnerError, do_bulk_archive_rooms, ensure_room_type_folders, format_due_date,
    get_room_owner,
)
from zerver.actions.streams import do_change_stream_group_based_setting
from zerver.actions.users import do_change_user_role, do_deactivate_user
from zerver.lib.test_classes import ZulipTestCase
from zerver.lib.types import UserGroupMembersData
from zerver.models import (
    AgentProfile, AgentRunner, ChannelFolder, Message, RealmAuditLog, UserProfile,
)
from zerver.models.realm_audit_logs import AuditLogEventType


def create_kaki(owner: UserProfile, bot_user: UserProfile) -> AgentProfile:
    runner = AgentRunner.objects.create(
        realm=owner.realm, owner=owner, name="Kaki runner", fingerprint="k" * 64
    )
    return AgentProfile.objects.create(
        realm=owner.realm,
        owner=owner,
        runner=runner,
        bot_user=bot_user,
        name="Kaki",
        adapter_id="kaki-builtin",
        adapter_version="1.0.0",
        is_builtin=True,
    )


class RoomMetaTest(ZulipTestCase):
    def test_get_and_patch_require_stream_access(self) -> None:
        owner = self.example_user("hamlet")
        stream = self.make_stream("wp14-private", invite_only=True)
        stream.creator = owner
        stream.save(update_fields=["creator"])

        outsider = self.example_user("othello")
        self.login_user(outsider)
        result = self.client_get(f"/json/streams/{stream.id}/meta")
        self.assert_json_error(result, "Invalid channel ID")

    def test_get_meta_reports_owner_and_counts(self) -> None:
        owner = self.example_user("hamlet")
        kaki = create_kaki(owner, self.example_user("default_bot"))
        stream = self.subscribe(owner, "wp14-meta")
        stream.creator = owner
        stream.save(update_fields=["creator"])
        self.subscribe(self.example_user("cordelia"), "wp14-meta")
        self.subscribe(kaki.bot_user, "wp14-meta")

        self.login_user(owner)
        result = self.client_get(f"/json/streams/{stream.id}/meta")
        data = self.assert_json_success(result)
        self.assertEqual(data["owner"], {"id": owner.id, "full_name": owner.full_name})
        self.assertEqual(data["people_count"], 2)
        self.assertEqual(data["agent_count"], 1)
        self.assertIsNone(data["due_date"])
        self.assertFalse(data["summary_enabled"])
        self.assertTrue(data["can_edit_meta"])

    def test_agent_count_excludes_a_plain_bot(self) -> None:
        # A bot with no AgentProfile, such as a webhook integration, is
        # not an agent and must not count toward agent_count, even
        # though it counts toward neither total (WP14 review defect 11).
        owner = self.example_user("hamlet")
        plain_bot = self.create_test_bot("wp14-plain", self.example_user("desdemona"))
        stream = self.subscribe(owner, "wp14-meta-plain-bot")
        stream.creator = owner
        stream.save(update_fields=["creator"])
        self.subscribe(plain_bot, "wp14-meta-plain-bot")

        self.login_user(owner)
        result = self.client_get(f"/json/streams/{stream.id}/meta")
        data = self.assert_json_success(result)
        self.assertEqual(data["people_count"], 1)
        self.assertEqual(data["agent_count"], 0)

    def test_get_meta_works_for_guest_and_moderator_non_owner(self) -> None:
        owner = self.example_user("hamlet")
        guest = self.example_user("polonius")
        moderator = self.example_user("shiva")
        self.set_user_role(moderator, UserProfile.ROLE_MODERATOR)
        stream = self.subscribe(owner, "wp14-meta-roles")
        stream.creator = owner
        stream.save(update_fields=["creator"])
        self.subscribe(guest, "wp14-meta-roles")
        self.subscribe(moderator, "wp14-meta-roles")

        for viewer in (guest, moderator):
            self.login_user(viewer)
            result = self.client_get(f"/json/streams/{stream.id}/meta")
            data = self.assert_json_success(result)
            self.assertFalse(data["can_edit_meta"])

            result = self.client_patch(
                f"/json/streams/{stream.id}/meta",
                {"due_date": orjson.dumps("2026-10-30").decode()},
            )
            self.assert_json_error(
                result, "You do not have permission to change this channel."
            )

    def test_fallback_owner_is_lowest_id_realm_owner(self) -> None:
        realm = self.example_user("hamlet").realm
        stream = self.make_stream("wp14-no-creator", realm=realm)
        self.assertIsNone(stream.creator)

        first_owner = self.example_user("desdemona")
        second_owner = self.example_user("othello")
        self.set_user_role(second_owner, UserProfile.ROLE_REALM_OWNER)
        lowest_id_owner = min(first_owner, second_owner, key=lambda user: user.id)

        resolved = get_room_owner(stream)
        assert resolved is not None
        self.assertEqual(resolved.id, lowest_id_owner.id)

    def test_deactivated_creator_falls_back_to_realm_owner(self) -> None:
        # A deactivated creator must not swallow the quiet-room DM, or be
        # shown as the channel's owner (WP14 review defect 5).
        realm = self.example_user("hamlet").realm
        creator = self.example_user("cordelia")
        stream = self.make_stream("wp14-deactivated-creator", realm=realm)
        stream.creator = creator
        stream.save(update_fields=["creator"])
        do_deactivate_user(creator, acting_user=None)

        fallback_owner = self.example_user("desdemona")

        resolved = get_room_owner(stream)
        assert resolved is not None
        self.assertEqual(resolved.id, fallback_owner.id)

    def test_patch_due_date_permission(self) -> None:
        owner = self.example_user("hamlet")
        member = self.example_user("cordelia")
        admin = self.example_user("iago")
        stream = self.subscribe(owner, "wp14-due")
        stream.creator = owner
        stream.save(update_fields=["creator"])
        self.subscribe(member, "wp14-due")
        self.subscribe(admin, "wp14-due")

        self.login_user(member)
        result = self.client_patch(
            f"/json/streams/{stream.id}/meta", {"due_date": orjson.dumps("2026-10-30").decode()}
        )
        self.assert_json_error(result, "You do not have permission to change this channel.")

        self.login_user(admin)
        result = self.client_patch(
            f"/json/streams/{stream.id}/meta", {"due_date": orjson.dumps("2026-10-30").decode()}
        )
        data = self.assert_json_success(result)
        self.assertEqual(data["due_date"], "2026-10-30")

        self.login_user(owner)
        result = self.client_patch(
            f"/json/streams/{stream.id}/meta", {"due_date": orjson.dumps(None).decode()}
        )
        data = self.assert_json_success(result)
        self.assertIsNone(data["due_date"])

    def test_patch_rejects_an_invalid_due_date(self) -> None:
        owner = self.example_user("hamlet")
        stream = self.subscribe(owner, "wp14-due-invalid")
        stream.creator = owner
        stream.save(update_fields=["creator"])

        self.login_user(owner)
        result = self.client_patch(
            f"/json/streams/{stream.id}/meta", {"due_date": orjson.dumps("30-10-2026").decode()}
        )
        self.assert_json_error(result, "Enter the date as YYYY-MM-DD.")

    def test_patch_summary_enabled_requires_owner_and_permission(self) -> None:
        owner = self.example_user("hamlet")
        admin = self.example_user("iago")
        guest = self.example_user("polonius")
        stream = self.subscribe(owner, "wp14-summary")
        stream.creator = owner
        stream.save(update_fields=["creator"])
        self.subscribe(admin, "wp14-summary")
        self.subscribe(guest, "wp14-summary")

        # Admin is not the room's owner, so summary_enabled stays out of reach
        # even though the same Admin can edit due_date.
        self.login_user(admin)
        result = self.client_patch(
            f"/json/streams/{stream.id}/meta", {"summary_enabled": orjson.dumps(True).decode()}
        )
        self.assert_json_error(result, "You do not have permission to change this channel.")

        # A guest owner (edge case) still lacks the room_summary permission.
        stream.creator = guest
        stream.save(update_fields=["creator"])
        self.login_user(guest)
        result = self.client_patch(
            f"/json/streams/{stream.id}/meta", {"summary_enabled": orjson.dumps(True).decode()}
        )
        self.assert_json_error(result, "You do not have permission to change this channel.")

        self.login_user(owner)
        stream.creator = owner
        stream.save(update_fields=["creator"])
        result = self.client_patch(
            f"/json/streams/{stream.id}/meta", {"summary_enabled": orjson.dumps(True).decode()}
        )
        data = self.assert_json_success(result)
        self.assertTrue(data["summary_enabled"])

    def test_patch_sends_event_and_audit_log(self) -> None:
        owner = self.example_user("hamlet")
        stream = self.subscribe(owner, "wp14-audit")
        stream.creator = owner
        stream.save(update_fields=["creator"])

        self.login_user(owner)
        with self.capture_send_event_calls(expected_num_events=1) as events:
            result = self.client_patch(
                f"/json/streams/{stream.id}/meta",
                {"due_date": orjson.dumps("2026-11-01").decode()},
            )
        self.assert_json_success(result)
        self.assertEqual(events[0]["event"]["type"], "room_meta")
        self.assertEqual(events[0]["event"]["stream_id"], stream.id)

        audit_log = RealmAuditLog.objects.filter(
            realm=owner.realm,
            modified_stream=stream,
            event_type=AuditLogEventType.ROOM_META_CHANGED,
        ).last()
        assert audit_log is not None
        self.assertEqual(audit_log.acting_user_id, owner.id)

    def test_announce_is_a_noop_without_kaki(self) -> None:
        owner = self.example_user("hamlet")
        stream = self.subscribe(owner, "wp14-announce-missing")
        stream.creator = owner
        stream.save(update_fields=["creator"])

        self.login_user(owner)
        with self.capture_send_event_calls(expected_num_events=0):
            result = self.client_patch(
                f"/json/streams/{stream.id}/meta", {"announce": orjson.dumps(True).decode()}
            )
        self.assert_json_success(result)
        assert stream.recipient_id is not None
        self.assertFalse(Message.objects.filter(recipient_id=stream.recipient_id).exists())

    def test_announce_sends_kakis_first_message(self) -> None:
        owner = self.example_user("hamlet")
        create_kaki(self.example_user("desdemona"), self.example_user("default_bot"))
        stream = self.subscribe(owner, "wp14-announce")
        stream.creator = owner
        stream.save(update_fields=["creator"])

        self.login_user(owner)
        result = self.client_patch(
            f"/json/streams/{stream.id}/meta",
            {
                "due_date": orjson.dumps("2026-12-25").decode(),
                "announce": orjson.dumps(True).decode(),
            },
        )
        self.assert_json_success(result)
        message = self.get_last_message()
        self.assertIn(f"#**{stream.name}**", message.content)
        # A human-readable date, never the raw ISO string (WP14 review
        # defect 17).
        self.assertIn(format_due_date(date(2026, 12, 25)), message.content)
        self.assertNotIn("2026-12-25", message.content)
        self.assertIn(owner.full_name, message.content)

    def test_announce_names_the_archive_reminder_for_a_proyek_room(self) -> None:
        # A Proyek-folder room with a due date gets the combined,
        # comma-joined sentence that also promises the archive reminder
        # (WP14 review defect 17); a non-Proyek room never gets that
        # promise (WP14 review defect 11c, covered by
        # test_announce_sends_kakis_first_message above).
        owner = self.example_user("hamlet")
        create_kaki(self.example_user("desdemona"), self.example_user("default_bot"))
        folder = check_add_channel_folder(
            owner.realm, "Proyek", "", acting_user=self.example_user("desdemona")
        )
        stream = self.subscribe(owner, "wp14-announce-proyek")
        stream.creator = owner
        stream.folder = folder
        stream.save(update_fields=["creator", "folder"])

        self.login_user(owner)
        result = self.client_patch(
            f"/json/streams/{stream.id}/meta",
            {
                "due_date": orjson.dumps("2026-12-25").decode(),
                "announce": orjson.dumps(True).decode(),
            },
        )
        self.assert_json_success(result)
        message = self.get_last_message()
        # One comma-joined sentence, not "Due <date>." followed by a
        # separate "Kaki will remind..." sentence.
        due_date_text = format_due_date(date(2026, 12, 25))
        self.assertIn(
            f"Due {due_date_text}, and Kaki will remind the owner to archive this channel",
            message.content,
        )
        self.assertNotIn(f"Due {due_date_text}.", message.content)

    def test_announce_subscribes_kaki_to_a_private_room(self) -> None:
        # Kaki's first message must still arrive when the room is
        # private and Kaki is not already a subscriber (WP14 review
        # defect 2).
        owner = self.example_user("hamlet")
        create_kaki(self.example_user("desdemona"), self.example_user("default_bot"))
        stream = self.make_stream("wp14-announce-private", invite_only=True)
        self.subscribe(owner, stream.name)
        stream.creator = owner
        stream.save(update_fields=["creator"])

        self.login_user(owner)
        result = self.client_patch(
            f"/json/streams/{stream.id}/meta", {"announce": orjson.dumps(True).decode()}
        )
        self.assert_json_success(result)
        message = self.get_last_message()
        self.assertIn(f"#**{stream.name}**", message.content)


class RoomArchiveTest(ZulipTestCase):
    def test_bulk_archive_requires_permission_per_stream(self) -> None:
        member = self.example_user("cordelia")
        admin = self.example_user("iago")
        stream_a = self.make_stream("wp14-archive-a")
        stream_b = self.make_stream("wp14-archive-b")
        self.subscribe(member, "wp14-archive-a")
        self.subscribe(member, "wp14-archive-b")

        self.login_user(member)
        result = self.client_post(
            "/json/channels/archive",
            {"stream_ids": orjson.dumps([stream_a.id, stream_b.id]).decode()},
        )
        self.assert_json_error(result, "You do not have permission to archive this channel.")
        stream_a.refresh_from_db()
        self.assertFalse(stream_a.deactivated)

        self.login_user(admin)
        result = self.client_post(
            "/json/channels/archive",
            {"stream_ids": orjson.dumps([stream_a.id, stream_b.id]).decode()},
        )
        self.assert_json_success(result)
        stream_a.refresh_from_db()
        stream_b.refresh_from_db()
        self.assertTrue(stream_a.deactivated)
        self.assertTrue(stream_b.deactivated)

        self.assertEqual(
            RealmAuditLog.objects.filter(
                realm=admin.realm,
                event_type=AuditLogEventType.ROOM_ARCHIVED_BULK,
                modified_stream_id__in=[stream_a.id, stream_b.id],
            ).count(),
            2,
        )

    def test_moderator_can_archive_via_room_archive_permission(self) -> None:
        # A Moderator gets the room_archive permission by default, with
        # no channel-administrator group membership needed (WP14 review
        # defect 14).
        moderator = self.example_user("shiva")
        self.set_user_role(moderator, UserProfile.ROLE_MODERATOR)
        stream = self.make_stream("wp14-archive-moderator")

        self.login_user(moderator)
        result = self.client_post(
            "/json/channels/archive", {"stream_ids": orjson.dumps([stream.id]).decode()}
        )
        self.assert_json_success(result)
        stream.refresh_from_db()
        self.assertTrue(stream.deactivated)

    def test_member_can_archive_as_a_channel_administrator(self) -> None:
        # A plain Member with no room_archive permission can still
        # archive a channel it was made an administrator of (WP14
        # review defect 14).
        member = self.example_user("cordelia")
        stream = self.subscribe(member, "wp14-archive-channel-admin")
        do_change_stream_group_based_setting(
            stream,
            "can_administer_channel_group",
            UserGroupMembersData(direct_members=[member.id], direct_subgroups=[]),
            acting_user=self.example_user("iago"),
        )

        self.login_user(member)
        result = self.client_post(
            "/json/channels/archive", {"stream_ids": orjson.dumps([stream.id]).decode()}
        )
        self.assert_json_success(result)
        stream.refresh_from_db()
        self.assertTrue(stream.deactivated)

    def test_guest_cannot_archive(self) -> None:
        guest = self.example_user("polonius")
        stream = self.subscribe(guest, "wp14-archive-guest")

        self.login_user(guest)
        result = self.client_post(
            "/json/channels/archive", {"stream_ids": orjson.dumps([stream.id]).decode()}
        )
        self.assert_json_error(result, "You do not have permission to archive this channel.")
        stream.refresh_from_db()
        self.assertFalse(stream.deactivated)

    def test_duplicate_stream_ids_archive_once(self) -> None:
        # Repeating a stream ID in stream_ids must not archive it, or
        # write its audit log entry, twice (WP14 review defect 10).
        admin = self.example_user("iago")
        stream = self.make_stream("wp14-archive-dup")

        self.login_user(admin)
        result = self.client_post(
            "/json/channels/archive",
            {"stream_ids": orjson.dumps([stream.id, stream.id]).decode()},
        )
        self.assert_json_success(result)
        stream.refresh_from_db()
        self.assertTrue(stream.deactivated)
        self.assertEqual(
            RealmAuditLog.objects.filter(
                realm=admin.realm,
                event_type=AuditLogEventType.ROOM_ARCHIVED_BULK,
                modified_stream_id=stream.id,
            ).count(),
            1,
        )

    def test_do_bulk_archive_rooms_action(self) -> None:
        admin = self.example_user("iago")
        stream = self.subscribe(self.example_user("cordelia"), "wp14-archive-direct")
        do_bulk_archive_rooms([stream], acting_user=admin)
        stream.refresh_from_db()
        self.assertTrue(stream.deactivated)


class EnsureRoomTypeFoldersTest(ZulipTestCase):
    def test_creates_three_folders_idempotently(self) -> None:
        realm = self.example_user("hamlet").realm
        created = ensure_room_type_folders(realm)
        self.assertEqual({folder.name for folder in created}, {"Proyek", "Klien", "Tim"})

        created_again = ensure_room_type_folders(realm)
        self.assertEqual(created_again, [])
        self.assertEqual(ChannelFolder.objects.filter(realm=realm, is_archived=False).count(), 3)

    def test_leaves_an_existing_matching_folder_alone(self) -> None:
        # A folder made by hand still counts, matched case-insensitively
        # (WP14 review defect 8).
        realm = self.example_user("hamlet").realm
        existing = check_add_channel_folder(
            realm, "proyek", "Sudah ada", acting_user=self.example_user("iago")
        )
        ensure_room_type_folders(realm)
        existing.refresh_from_db()
        self.assertEqual(existing.description, "Sudah ada")
        self.assertEqual(ChannelFolder.objects.filter(realm=realm, is_archived=False).count(), 3)

    def test_raises_without_a_realm_owner(self) -> None:
        # No silent no-op: a realm that has lost its last Owner must
        # surface an error, not report success with nothing created
        # (WP14 review defect 14).
        realm = self.example_user("hamlet").realm
        desdemona = self.example_user("desdemona")
        do_change_user_role(desdemona, UserProfile.ROLE_MEMBER, acting_user=None, notify=False)

        with self.assertRaises(NoRealmOwnerError):
            ensure_room_type_folders(realm)
        self.assertEqual(ChannelFolder.objects.filter(realm=realm, is_archived=False).count(), 0)

    def test_call_command_creates_folders(self) -> None:
        realm = self.example_user("hamlet").realm
        call_command("ensure_room_type_folders", f"--realm={realm.string_id}")
        self.assertEqual(
            set(
                ChannelFolder.objects.filter(realm=realm, is_archived=False).values_list(
                    "name", flat=True
                )
            ),
            {"Proyek", "Klien", "Tim"},
        )

    def test_call_command_reports_missing_owner(self) -> None:
        realm = self.example_user("hamlet").realm
        do_change_user_role(
            self.example_user("desdemona"), UserProfile.ROLE_MEMBER, acting_user=None, notify=False
        )

        with self.assertRaises(CommandError):
            call_command(
                "ensure_room_type_folders", f"--realm={realm.string_id}", stdout=StringIO()
            )
