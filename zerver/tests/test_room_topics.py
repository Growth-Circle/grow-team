from zerver.lib.test_classes import ZulipTestCase
from zerver.lib.test_helpers import queries_captured
from zerver.models.streams import get_stream


class RoomTopicsTest(ZulipTestCase):
    def test_returns_name_max_id_and_message_count(self) -> None:
        user = self.example_user("hamlet")
        stream = self.subscribe(user, "wp14-topics")

        self.send_stream_message(user, "wp14-topics", topic_name="alpha")
        self.send_stream_message(user, "wp14-topics", topic_name="alpha")
        alpha_last_id = self.send_stream_message(user, "wp14-topics", topic_name="alpha")
        beta_last_id = self.send_stream_message(user, "wp14-topics", topic_name="beta")

        self.login_user(user)
        result = self.client_get(f"/json/streams/{stream.id}/topics")
        data = self.assert_json_success(result)
        topics_by_name = {topic["name"]: topic for topic in data["topics"]}

        self.assertEqual(topics_by_name["alpha"]["message_count"], 3)
        self.assertEqual(topics_by_name["alpha"]["max_id"], alpha_last_id)
        self.assertEqual(topics_by_name["beta"]["message_count"], 1)
        self.assertEqual(topics_by_name["beta"]["max_id"], beta_last_id)
        # Most recently active topic first.
        self.assertEqual(data["topics"][0]["name"], "beta")

    def test_query_count_does_not_grow_with_topic_count(self) -> None:
        user = self.example_user("hamlet")
        small_stream = self.subscribe(user, "wp14-topics-small")
        for i in range(2):
            self.send_stream_message(user, "wp14-topics-small", topic_name=f"topic-{i}")

        large_stream = self.subscribe(user, "wp14-topics-large")
        for i in range(8):
            self.send_stream_message(user, "wp14-topics-large", topic_name=f"topic-{i}")
            self.send_stream_message(user, "wp14-topics-large", topic_name=f"topic-{i}")

        self.login_user(user)
        with queries_captured() as small_queries:
            result = self.client_get(f"/json/streams/{small_stream.id}/topics")
        self.assert_json_success(result)

        with queries_captured() as large_queries:
            result = self.client_get(f"/json/streams/{large_stream.id}/topics")
        self.assert_json_success(result)

        self.assert_length(large_queries, len(small_queries))

    def test_protected_history_counts_only_the_callers_own_usermessage(self) -> None:
        owner = self.example_user("hamlet")
        self.make_stream(
            "wp14-topics-protected", invite_only=True, history_public_to_subscribers=False
        )
        self.subscribe(owner, "wp14-topics-protected")
        self.send_stream_message(owner, "wp14-topics-protected", topic_name="old")

        joiner = self.example_user("cordelia")
        self.subscribe(joiner, "wp14-topics-protected")
        joiner_message_id = self.send_stream_message(
            joiner, "wp14-topics-protected", topic_name="old"
        )

        stream = get_stream("wp14-topics-protected", owner.realm)
        self.login_user(joiner)
        result = self.client_get(f"/json/streams/{stream.id}/topics")
        data = self.assert_json_success(result)
        topics_by_name = {topic["name"]: topic for topic in data["topics"]}

        # joiner has no UserMessage for the message sent before they
        # subscribed, so it does not count toward message_count.
        self.assertEqual(topics_by_name["old"]["message_count"], 1)
        self.assertEqual(topics_by_name["old"]["max_id"], joiner_message_id)

    def test_no_access_returns_invalid_channel_id(self) -> None:
        owner = self.example_user("hamlet")
        stream = self.make_stream("wp14-topics-private", invite_only=True)
        self.subscribe(owner, stream.name)

        outsider = self.example_user("othello")
        self.login_user(outsider)
        result = self.client_get(f"/json/streams/{stream.id}/topics")
        self.assert_json_error(result, "Invalid channel ID")
