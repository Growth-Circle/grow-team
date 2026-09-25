from datetime import timedelta
from io import StringIO
from unittest import mock

from django.core.management import call_command
from django.utils.timezone import now as timezone_now

from zerver.actions.channel_folders import check_add_channel_folder
from zerver.actions.room_meta import send_quiet_room_notices
from zerver.actions.users import do_change_user_role
from zerver.lib.quiet_rooms import QUIET_THRESHOLD_DAYS, compute_quiet_streams, rooms_needing_notice
from zerver.lib.test_classes import ZulipTestCase
from zerver.lib.test_helpers import queries_captured
from zerver.models import Message, RoomMeta, Stream, UserProfile
from zerver.tests.test_room_meta import create_kaki


def backdate_last_message(stream: Stream, days: int) -> None:
    assert stream.recipient_id is not None
    message = Message.objects.filter(recipient_id=stream.recipient_id).latest("id")
    Message.objects.filter(id=message.id).update(date_sent=timezone_now() - timedelta(days=days))


def backdate_stream_creation(stream: Stream, days: int) -> None:
    Stream.objects.filter(id=stream.id).update(date_created=timezone_now() - timedelta(days=days))


class QuietChannelsTest(ZulipTestCase):
    def test_quiet_channels_lists_stale_and_never_active_rooms(self) -> None:
        user = self.example_user("hamlet")

        active_stream = self.subscribe(user, "wp14-quiet-active")
        self.send_stream_message(user, "wp14-quiet-active")

        stale_stream = self.subscribe(user, "wp14-quiet-stale")
        self.send_stream_message(user, "wp14-quiet-stale")
        backdate_last_message(stale_stream, QUIET_THRESHOLD_DAYS + 1)

        never_active_stream = self.subscribe(user, "wp14-quiet-never-active")
        backdate_stream_creation(never_active_stream, QUIET_THRESHOLD_DAYS + 1)

        brand_new_stream = self.subscribe(user, "wp14-quiet-new")

        self.login_user(user)
        result = self.client_get("/json/channels/quiet")
        data = self.assert_json_success(result)
        self.assertEqual(data["threshold_days"], QUIET_THRESHOLD_DAYS)
        # Compare with assertIn/assertNotIn, not exact set equality: other
        # default realm streams hamlet can see are outside this test's
        # control and may or may not be quiet depending on fixture data.
        quiet_ids = {row["stream_id"] for row in data["quiet_channels"]}
        self.assertIn(stale_stream.id, quiet_ids)
        self.assertIn(never_active_stream.id, quiet_ids)
        self.assertNotIn(active_stream.id, quiet_ids)
        self.assertNotIn(brand_new_stream.id, quiet_ids)

    def test_quiet_channels_guest_gets_empty_list(self) -> None:
        guest = self.example_user("polonius")
        sender = self.example_user("hamlet")
        stale_stream = self.subscribe(guest, "wp14-quiet-guest")
        self.subscribe(sender, "wp14-quiet-guest")
        self.send_stream_message(sender, "wp14-quiet-guest")
        backdate_last_message(stale_stream, QUIET_THRESHOLD_DAYS + 1)

        self.login_user(guest)
        result = self.client_get("/json/channels/quiet")
        data = self.assert_json_success(result)
        self.assertEqual(data["quiet_channels"], [])

    def test_compute_quiet_streams_is_one_query(self) -> None:
        user = self.example_user("hamlet")
        streams = []
        for i in range(4):
            stream = self.subscribe(user, f"wp14-quiet-query-{i}")
            self.send_stream_message(user, f"wp14-quiet-query-{i}")
            backdate_last_message(stream, QUIET_THRESHOLD_DAYS + 1)
            streams.append(stream)

        with queries_captured() as queries:
            quiet = compute_quiet_streams(streams)
        self.assert_length(queries, 1)
        self.assertEqual({stream.id for stream, _last_activity in quiet}, {s.id for s in streams})

    def test_compute_quiet_streams_empty_input_short_circuits(self) -> None:
        self.assertEqual(compute_quiet_streams([]), [])


class NotifyQuietRoomsTest(ZulipTestCase):
    def test_sends_one_dm_per_owner_and_marks_notified(self) -> None:
        # This realm's default fixture streams may themselves be quiet by
        # the time this test runs, and may bundle their own owners into
        # the same notify_quiet_rooms pass; assertions below only pin
        # down what this test itself set up, not the total DM count.
        owner = self.example_user("hamlet")
        realm = owner.realm
        kaki = create_kaki(self.example_user("desdemona"), self.example_user("default_bot"))

        room_a = self.subscribe(owner, "wp14-notify-a")
        self.send_stream_message(owner, "wp14-notify-a")
        backdate_last_message(room_a, QUIET_THRESHOLD_DAYS + 1)

        room_b = self.subscribe(owner, "wp14-notify-b")
        self.send_stream_message(owner, "wp14-notify-b")
        backdate_last_message(room_b, QUIET_THRESHOLD_DAYS + 1)

        notified = send_quiet_room_notices(realm)
        self.assertGreaterEqual(notified, 1)

        dm = Message.objects.filter(sender=kaki.bot_user, content__icontains=room_a.name).latest(
            "id"
        )
        self.assertIn(f"#**{room_a.name}**", dm.content)
        self.assertIn(f"#**{room_b.name}**", dm.content)

        for room in (room_a, room_b):
            room_meta = RoomMeta.objects.get(stream=room)
            self.assertIsNotNone(room_meta.quiet_notified_at)

        # A second run must not send any further DMs.
        dm_count_before = Message.objects.filter(sender=kaki.bot_user).count()
        send_quiet_room_notices(realm)
        dm_count_after = Message.objects.filter(sender=kaki.bot_user).count()
        self.assertEqual(dm_count_before, dm_count_after)

    def test_project_room_past_due_date_is_flagged_even_if_active(self) -> None:
        owner = self.example_user("hamlet")
        realm = owner.realm
        folder = check_add_channel_folder(
            realm, "Proyek", "", acting_user=self.example_user("iago")
        )
        room = self.subscribe(owner, "wp14-notify-due")
        room.folder = folder
        room.save(update_fields=["folder"])
        # Active room: a message sent moments ago, so it is not "quiet".
        self.send_stream_message(owner, "wp14-notify-due")

        room_meta, _created = RoomMeta.objects.get_or_create(stream=room)
        room_meta.due_date = (timezone_now() - timedelta(days=1)).date()
        room_meta.save(update_fields=["due_date"])

        notices = rooms_needing_notice(realm)
        matching = [notice for notice in notices if notice.stream.id == room.id]
        self.assertEqual(len(matching), 1)
        self.assertTrue(matching[0].due_date_passed)

    def test_overdue_project_room_dm_names_the_due_date_reason(self) -> None:
        owner = self.example_user("hamlet")
        realm = owner.realm
        kaki = create_kaki(self.example_user("desdemona"), self.example_user("default_bot"))
        folder = check_add_channel_folder(
            realm, "Proyek", "", acting_user=self.example_user("iago")
        )
        room = self.subscribe(owner, "wp14-notify-overdue-text")
        room.folder = folder
        room.save(update_fields=["folder"])
        self.send_stream_message(owner, "wp14-notify-overdue-text")

        room_meta, _created = RoomMeta.objects.get_or_create(stream=room)
        room_meta.due_date = (timezone_now() - timedelta(days=1)).date()
        room_meta.save(update_fields=["due_date"])

        notified = send_quiet_room_notices(realm)
        self.assertGreaterEqual(notified, 1)
        dm = Message.objects.filter(sender=kaki.bot_user, content__icontains=room.name).latest("id")
        # The DM must say *why* (WP14 review defect 14), not only flag the
        # room: "past its due date", not the quiet-room wording below.
        self.assertIn(f"#**{room.name}**: past its due date. Consider archiving it.", dm.content)

    def test_quiet_notified_at_resets_when_a_room_goes_active_again(self) -> None:
        # A room that stops being quiet, then goes quiet again, must be
        # notified about again rather than staying marked notified forever
        # (WP14 review defect 6).
        owner = self.example_user("hamlet")
        realm = owner.realm
        create_kaki(self.example_user("desdemona"), self.example_user("default_bot"))

        room = self.subscribe(owner, "wp14-requiet")
        self.send_stream_message(owner, "wp14-requiet")
        backdate_last_message(room, QUIET_THRESHOLD_DAYS + 1)

        notified = send_quiet_room_notices(realm)
        self.assertGreaterEqual(notified, 1)
        room_meta = RoomMeta.objects.get(stream=room)
        self.assertIsNotNone(room_meta.quiet_notified_at)

        # The room goes active again: no longer a notice candidate, so its
        # old notified mark must clear.
        self.send_stream_message(owner, "wp14-requiet")
        notices = rooms_needing_notice(realm)
        self.assertNotIn(room.id, {notice.stream.id for notice in notices})
        room_meta.refresh_from_db()
        self.assertIsNone(room_meta.quiet_notified_at)

        # It goes quiet again later: Kaki must be able to notify once more.
        backdate_last_message(room, QUIET_THRESHOLD_DAYS + 1)
        notified_again = send_quiet_room_notices(realm)
        self.assertGreaterEqual(notified_again, 1)
        room_meta.refresh_from_db()
        self.assertIsNotNone(room_meta.quiet_notified_at)

    def test_send_quiet_room_notices_skips_rooms_with_no_active_owner(self) -> None:
        # A quiet room whose owner cannot be resolved (no creator, and the
        # realm itself has no active Owner to fall back to) must be
        # skipped, not crash or count as notified.
        owner = self.example_user("hamlet")
        realm = owner.realm
        create_kaki(self.example_user("desdemona"), self.example_user("default_bot"))

        # make_stream (unlike subscribe) leaves creator unset, so the room
        # has no individual owner to start with.
        room = self.make_stream("wp14-notify-no-owner", realm=realm)
        self.assertIsNone(room.creator)
        self.subscribe(owner, room.name)
        self.send_stream_message(owner, room.name)
        backdate_last_message(room, QUIET_THRESHOLD_DAYS + 1)

        # desdemona is this realm's only default Owner; demoting her
        # leaves every room without a resolvable owner.
        do_change_user_role(
            self.example_user("desdemona"), UserProfile.ROLE_MEMBER, acting_user=None, notify=False
        )

        self.assertEqual(send_quiet_room_notices(realm), 0)

    def test_send_quiet_room_notices_does_not_count_a_failed_send(self) -> None:
        # A rejected send must not mark the room notified: only a real
        # send may set quiet_notified_at, per this function's own
        # contract ("Returns how many owners were actually DMed").
        owner = self.example_user("hamlet")
        realm = owner.realm
        create_kaki(self.example_user("desdemona"), self.example_user("default_bot"))

        room = self.subscribe(owner, "wp14-notify-send-fails")
        self.send_stream_message(owner, "wp14-notify-send-fails")
        backdate_last_message(room, QUIET_THRESHOLD_DAYS + 1)

        with mock.patch(
            "zerver.actions.room_meta.internal_send_private_message", return_value=None
        ):
            self.assertEqual(send_quiet_room_notices(realm), 0)

        room_meta = RoomMeta.objects.get(stream=room)
        self.assertIsNone(room_meta.quiet_notified_at)

    def test_send_quiet_room_notices_without_kaki_returns_zero(self) -> None:
        owner = self.example_user("hamlet")
        realm = owner.realm
        room = self.subscribe(owner, "wp14-notify-no-kaki")
        self.send_stream_message(owner, "wp14-notify-no-kaki")
        backdate_last_message(room, QUIET_THRESHOLD_DAYS + 1)

        self.assertEqual(send_quiet_room_notices(realm), 0)

    def test_call_command_runs_without_error(self) -> None:
        out = StringIO()
        call_command("notify_quiet_rooms", stdout=out)
        self.assertIn("Notified ", out.getvalue())
