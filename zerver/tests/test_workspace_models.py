"""Tests for the Sanji workspace schema: the additive tables and columns
added in migrations 0820-0835."""

from django.db import IntegrityError, connection
from django.utils.timezone import now as timezone_now
from typing_extensions import override

from zerver.lib.test_classes import ZulipTestCase
from zerver.models import AgentProfile, AgentRunner, RoomDigest, RoomMeta


class WorkspaceSchemaTests(ZulipTestCase):
    @override
    def setUp(self) -> None:
        super().setUp()
        self.owner = self.example_user("hamlet")
        self.realm = self.owner.realm
        self.runner = AgentRunner.objects.create(
            realm=self.realm, owner=self.owner, name="Fixture", fingerprint="a" * 64
        )
        self.profile = AgentProfile.objects.create(
            realm=self.realm,
            owner=self.owner,
            runner=self.runner,
            bot_user=self.example_user("default_bot"),
            name="Fixture",
            adapter_id="codex-acp",
            adapter_version="1.12.0",
        )
        self.stream = self.make_stream("wp03-schema", realm=self.realm)
        self.subscribe(self.owner, self.stream.name)
        self.message_id = self.send_stream_message(self.owner, self.stream.name, "hi")

    # -- 0820 RoomMeta / RoomDigest -----------------------------------

    def test_room_meta_defaults_and_unique(self) -> None:
        meta = RoomMeta.objects.create(stream=self.stream)
        self.assertIsNone(meta.due_date)
        self.assertFalse(meta.summary_enabled)
        with self.assertRaises(IntegrityError):
            RoomMeta.objects.create(stream=self.stream)

    def test_room_digest_unique_per_stream_and_day(self) -> None:
        RoomDigest.objects.create(
            stream=self.stream, date=timezone_now().date(), source_message_id=self.message_id
        )
        with self.assertRaises(IntegrityError):
            RoomDigest.objects.create(stream=self.stream, date=timezone_now().date())

    def test_room_digest_source_message_deletable_without_error(self) -> None:
        digest = RoomDigest.objects.create(
            stream=self.stream, date=timezone_now().date(), source_message_id=self.message_id
        )
        # Retention can delete a message without knowing this table exists;
        # the reference must carry no database-level constraint. Delete the
        # UserMessage row the way retention itself does first, so only the
        # new table's constraint (not the pre-existing one) is under test.
        with connection.cursor() as cursor:
            cursor.execute(
                "DELETE FROM zerver_usermessage WHERE message_id = %s", [self.message_id]
            )
            cursor.execute("DELETE FROM zerver_message WHERE id = %s", [self.message_id])
            # Force any constraint check to run now, inside this test,
            # instead of silently at commit time.
            cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")
            cursor.execute("SELECT 1 FROM zerver_message WHERE id = %s", [self.message_id])
            self.assertIsNone(cursor.fetchone())
        # A raw SQL delete runs no Python on_delete, so the column keeps the
        # old id; read code must treat that id as a message that is gone.
        digest.refresh_from_db()
        self.assertEqual(digest.source_message_id, self.message_id)
