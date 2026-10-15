from typing import Any

from typing_extensions import override

from zerver.actions.message_delete import do_delete_messages
from zerver.actions.pins import do_pin_message
from zerver.actions.streams import do_change_stream_group_based_setting
from zerver.lib.event_schema import check_channel_pin_add, check_channel_pin_remove
from zerver.lib.test_classes import ZulipTestCase
from zerver.models import ChannelPin, Message, NamedUserGroup, Stream
from zerver.models.groups import SystemGroups


class ChannelPinTestCase(ZulipTestCase):
    @override
    def setUp(self) -> None:
        super().setUp()
        self.hamlet = self.example_user("hamlet")
        self.iago = self.example_user("iago")
        self.realm = self.hamlet.realm
        self.stream = self.make_stream("pinned channel", realm=self.realm)
        for user in (self.hamlet, self.iago, self.example_user("cordelia")):
            self.subscribe(user, self.stream.name)

    def send_to_channel(self, content: str = "Deploy checklist", topic_name: str = "ops") -> int:
        return self.send_stream_message(self.hamlet, self.stream.name, content, topic_name)

    def pin_url(self, message_id: int) -> str:
        return f"/json/messages/{message_id}/pin"

    def pins_url(self, stream: Stream | None = None) -> str:
        return f"/json/streams/{(stream or self.stream).id}/pins"

    def pinned_ids(self, user_name: str, stream: Stream | None = None) -> list[int]:
        self.login(user_name)
        result = self.client_get(self.pins_url(stream))
        return [pin["message_id"] for pin in self.assert_json_success(result)["pins"]]


class ChannelPinTest(ChannelPinTestCase):
    def test_member_pins_and_unpins_and_everyone_reads_the_list(self) -> None:
        message_id = self.send_to_channel()

        self.login_user(self.iago)
        result = self.client_post(self.pin_url(message_id))
        self.assert_json_success(result)

        for user_name in ("hamlet", "cordelia"):
            self.assertEqual(self.pinned_ids(user_name), [message_id])

        self.login_user(self.hamlet)
        result = self.client_get(self.pins_url())
        [pin] = self.assert_json_success(result)["pins"]
        self.assertEqual(pin["pinned_by_id"], self.iago.id)
        self.assertEqual(pin["stream_id"], self.stream.id)
        self.assertEqual(pin["sender_id"], self.hamlet.id)
        self.assertEqual(pin["topic"], "ops")
        self.assertIn("Deploy checklist", pin["rendered_content"])

        result = self.client_delete(self.pin_url(message_id))
        self.assert_json_success(result)
        self.assertEqual(self.pinned_ids("hamlet"), [])

    def test_pins_are_listed_newest_first(self) -> None:
        first, second = self.send_to_channel("one"), self.send_to_channel("two")
        self.login_user(self.hamlet)
        self.assert_json_success(self.client_post(self.pin_url(first)))
        self.assert_json_success(self.client_post(self.pin_url(second)))

        self.assertEqual(self.pinned_ids("hamlet"), [second, first])

    def test_pinning_twice_or_unpinning_an_unpinned_message_is_rejected(self) -> None:
        message_id = self.send_to_channel()
        self.login_user(self.hamlet)

        result = self.client_delete(self.pin_url(message_id))
        self.assert_json_error(result, "Message is not pinned.")

        self.assert_json_success(self.client_post(self.pin_url(message_id)))
        result = self.client_post(self.pin_url(message_id))
        self.assert_json_error(result, "Message is already pinned.")
        self.assertEqual(ChannelPin.objects.count(), 1)

    def test_direct_messages_cannot_be_pinned(self) -> None:
        message_id = self.send_personal_message(self.hamlet, self.iago)
        self.login_user(self.hamlet)

        result = self.client_post(self.pin_url(message_id))
        self.assert_json_error(result, "Only channel messages can be pinned.")

    def test_user_who_cannot_post_cannot_pin_but_can_read(self) -> None:
        message_id = self.send_to_channel()
        self.login_user(self.hamlet)
        self.assert_json_success(self.client_post(self.pin_url(message_id)))

        admins_group = NamedUserGroup.objects.get(
            name=SystemGroups.ADMINISTRATORS, realm_for_sharding=self.realm, is_system_group=True
        )
        do_change_stream_group_based_setting(
            self.stream, "can_send_message_group", admins_group, acting_user=self.iago
        )

        self.login_user(self.hamlet)
        result = self.client_delete(self.pin_url(message_id))
        self.assert_json_error(result, "You do not have permission to post in this channel.")
        other_id = self.send_stream_message(self.iago, self.stream.name, "later", "ops")
        result = self.client_post(self.pin_url(other_id))
        self.assert_json_error(result, "You do not have permission to post in this channel.")
        self.assertEqual(self.pinned_ids("hamlet"), [message_id])

        self.login_user(self.iago)
        self.assert_json_success(self.client_post(self.pin_url(other_id)))

    def test_user_who_cannot_read_the_channel_is_rejected(self) -> None:
        private = self.make_stream("private pins", realm=self.realm, invite_only=True)
        self.subscribe(self.hamlet, private.name)
        message_id = self.send_stream_message(self.hamlet, private.name, "secret", "ops")
        self.login_user(self.hamlet)
        self.assert_json_success(self.client_post(self.pin_url(message_id)))

        cordelia = self.example_user("cordelia")
        self.login_user(cordelia)
        self.assert_json_error(self.client_get(self.pins_url(private)), "Invalid channel ID")
        self.assert_json_error(self.client_post(self.pin_url(message_id)), "Invalid message(s)")
        self.assert_json_error(self.client_delete(self.pin_url(message_id)), "Invalid message(s)")
        self.assertEqual(ChannelPin.objects.count(), 1)

    def test_guest_reads_pins_only_of_channels_they_are_in(self) -> None:
        polonius = self.example_user("polonius")
        message_id = self.send_to_channel()
        self.login_user(self.hamlet)
        self.assert_json_success(self.client_post(self.pin_url(message_id)))

        self.login_user(polonius)
        self.assert_json_error(self.client_get(self.pins_url()), "Invalid channel ID")

        self.subscribe(polonius, self.stream.name)
        self.assertEqual(self.pinned_ids("polonius"), [message_id])

    def test_older_private_history_stays_hidden_from_a_later_subscriber(self) -> None:
        private = self.make_stream(
            "hidden history",
            realm=self.realm,
            invite_only=True,
            history_public_to_subscribers=False,
        )
        self.subscribe(self.hamlet, private.name)
        old_id = self.send_stream_message(self.hamlet, private.name, "before", "ops")
        self.subscribe(self.iago, private.name)
        new_id = self.send_stream_message(self.hamlet, private.name, "after", "ops")
        for message_id in (old_id, new_id):
            do_pin_message(
                user_profile=self.hamlet,
                stream=private,
                message=Message.objects.get(id=message_id),
            )

        self.assertEqual(self.pinned_ids("hamlet", private), [new_id, old_id])
        self.assertEqual(self.pinned_ids("iago", private), [new_id])

    def test_pin_move_to_another_channel_leaves_the_old_list(self) -> None:
        message_id = self.send_to_channel()
        self.login_user(self.hamlet)
        self.assert_json_success(self.client_post(self.pin_url(message_id)))

        other = self.make_stream("elsewhere", realm=self.realm)
        Message.objects.filter(id=message_id).update(recipient=other.recipient)

        self.assertEqual(self.pinned_ids("hamlet"), [])

    def test_deleting_a_message_or_a_channel_removes_its_pins(self) -> None:
        kept_id, doomed_id = self.send_to_channel("keep"), self.send_to_channel("doomed")
        self.login_user(self.hamlet)
        for message_id in (kept_id, doomed_id):
            self.assert_json_success(self.client_post(self.pin_url(message_id)))

        do_delete_messages(self.realm, [Message.objects.get(id=doomed_id)], acting_user=self.iago)
        self.assertEqual(list(ChannelPin.objects.values_list("message_id", flat=True)), [kept_id])

        Stream.objects.get(id=self.stream.id).delete()
        self.assertEqual(ChannelPin.objects.count(), 0)

    def test_pinner_leaving_keeps_the_pin(self) -> None:
        message_id = self.send_to_channel()
        pin = do_pin_message(
            user_profile=self.iago, stream=self.stream, message=Message.objects.get(id=message_id)
        )

        self.iago.delete()
        pin.refresh_from_db()
        self.assertIsNone(pin.pinned_by_id)

    def test_pin_and_unpin_events_reach_the_readers_of_the_channel(self) -> None:
        message_id = self.send_to_channel()
        self.login_user(self.hamlet)

        with self.capture_send_event_calls(expected_num_events=1) as events:
            self.assert_json_success(self.client_post(self.pin_url(message_id)))
        add_event: dict[str, Any] = events[0]["event"]
        check_channel_pin_add("event", {**add_event, "id": 0})
        self.assertEqual(add_event["stream_id"], self.stream.id)
        self.assertEqual(add_event["message_id"], message_id)
        self.assertEqual(add_event["pinned_by_id"], self.hamlet.id)
        # A public channel reaches every non-guest member; a guest who is
        # not subscribed does not see it.
        users = set(events[0]["users"])
        self.assertLessEqual({self.hamlet.id, self.iago.id}, users)
        self.assertNotIn(self.example_user("polonius").id, users)

        with self.capture_send_event_calls(expected_num_events=1) as events:
            self.assert_json_success(self.client_delete(self.pin_url(message_id)))
        check_channel_pin_remove("event", {**events[0]["event"], "id": 0})

    def test_events_of_a_private_channel_skip_non_subscribers(self) -> None:
        private = self.make_stream("private events", realm=self.realm, invite_only=True)
        self.subscribe(self.hamlet, private.name)
        message_id = self.send_stream_message(self.hamlet, private.name, "secret", "ops")
        self.login_user(self.hamlet)

        with self.capture_send_event_calls(expected_num_events=1) as events:
            self.assert_json_success(self.client_post(self.pin_url(message_id)))
        self.assertEqual(events[0]["users"], [self.hamlet.id])

    def test_archived_channel_still_lists_pins_but_cannot_change_them(self) -> None:
        message_id = self.send_to_channel()
        self.login_user(self.hamlet)
        self.assert_json_success(self.client_post(self.pin_url(message_id)))

        self.login_user(self.iago)
        self.assert_json_success(self.client_delete(f"/json/streams/{self.stream.id}"))

        self.assertEqual(self.pinned_ids("hamlet"), [message_id])
        self.login_user(self.hamlet)
        result = self.client_delete(self.pin_url(message_id))
        self.assert_json_error(result, f"Not authorized to send to channel '{self.stream.name}'")
