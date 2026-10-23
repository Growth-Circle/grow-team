"""Tests for what the realm export and import do with the tables that Sanji
added to Zulip: zerver/lib/export.py (SANJI_EXPORTED_TABLES,
EXCLUDED_SANJI_TABLES) and zerver/lib/import_realm.py (import_sanji_tables)."""

import os
import re
import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Any

import orjson
from django.apps import apps
from django.db import models
from django.utils.timezone import now as timezone_now

from zerver.actions.playbooks import do_create_playbook
from zerver.actions.tasks import do_add_task_note, do_create_task
from zerver.lib.export import (
    ALL_ZULIP_TABLES,
    EXCLUDED_SANJI_TABLES,
    NON_EXPORTED_TABLES,
    SANJI_EXPORTED_TABLES,
    do_export_realm,
)
from zerver.lib.import_realm import do_import_realm
from zerver.lib.tasks import get_or_create_default_board
from zerver.models import (
    AgentRealmSettings,
    ChannelPin,
    Message,
    NeedResolution,
    Playbook,
    PlaybookRevision,
    Realm,
    RealmAuditLog,
    RealmExport,
    RolePermission,
    RoomMeta,
    Task,
    TaskBoard,
    TaskHistory,
    TaskNote,
    TaskNotification,
    TaskPlan,
    UserProfile,
)
from zerver.models.agents import AgentRunnerRegistrationToken, AgentSecret
from zerver.models.realm_audit_logs import AuditLogEventType
from zerver.models.streams import get_stream
from zerver.models.users import get_user_by_delivery_email
from zerver.tests.test_import_export import (
    ExportFile,
    get_output_dir,
    make_export_output_dir,
    read_json,
)

SECRET_MARKER = b"never-leaves-the-server"


def zerver_table_names() -> set[str]:
    return {
        model._meta.db_table
        for model in apps.get_app_config("zerver").get_models(include_auto_created=True)
    }


class SanjiExportDecisionTest(ExportFile):
    def test_every_table_of_the_zerver_app_has_an_export_decision(self) -> None:
        # A new model fails this test, and the export itself, until it is in
        # SANJI_EXPORTED_TABLES or in EXCLUDED_SANJI_TABLES with a reason.
        self.assertEqual(zerver_table_names() - ALL_ZULIP_TABLES, set())

    def test_a_sanji_table_is_exported_or_excluded_with_a_reason(self) -> None:
        self.assertEqual(SANJI_EXPORTED_TABLES & set(EXCLUDED_SANJI_TABLES), set())
        self.assertEqual(SANJI_EXPORTED_TABLES & NON_EXPORTED_TABLES, set())
        self.assertLessEqual(set(EXCLUDED_SANJI_TABLES), NON_EXPORTED_TABLES)
        self.assertLessEqual(SANJI_EXPORTED_TABLES | set(EXCLUDED_SANJI_TABLES), ALL_ZULIP_TABLES)
        for table, reason in EXCLUDED_SANJI_TABLES.items():
            self.assertGreater(len(reason), 20, table)

    def test_tables_with_secrets_are_excluded(self) -> None:
        secret_tables = {
            "zerver_agentpairing",
            "zerver_agentprobegrant",
            "zerver_agentrunnercredential",
            "zerver_agentrunnerregistrationtoken",
            "zerver_agentsecret",
            "zerver_externalaccount",
            "zerver_mcpaccesstoken",
            "zerver_mcpauthorizationcode",
            "zerver_mcpconnection",
        }
        self.assertLessEqual(secret_tables, set(EXCLUDED_SANJI_TABLES))

    def test_an_exported_table_has_no_secret_column_and_no_link_to_an_excluded_table(self) -> None:
        secret_column = re.compile(
            r"token|secret|ciphertext|wrapped_key|password|credential|_hash$|digest$"
        )
        # These point at tables that are not exported. The export writes None
        # for them (custom_fetch_tasks, clear_agent_references_of_realm_settings).
        cleared_links = {
            ("zerver_task", "agent_profile"),
            ("zerver_task", "agent_job"),
            ("zerver_agentrealmsettings", "default_profile"),
            ("zerver_agentrealmsettings", "work_runner"),
            ("zerver_agentrealmsettings", "work_provider"),
        }
        for model in apps.get_app_config("zerver").get_models():
            table = model._meta.db_table
            if table not in SANJI_EXPORTED_TABLES:
                continue
            for field in model._meta.concrete_fields:
                self.assertIsNone(secret_column.search(field.name), (table, field.name))
                if isinstance(field, models.ForeignKey):
                    related_model = field.related_model
                    assert not isinstance(related_model, str)
                    if related_model._meta.db_table not in EXCLUDED_SANJI_TABLES:
                        continue
                    self.assertIn((table, field.name), cleared_links)
                    self.assertTrue(field.null, (table, field.name))


class SanjiExportImportTestCase(ExportFile):
    def export_realm(
        self,
        realm: Realm,
        export_type: int = RealmExport.EXPORT_FULL_WITHOUT_CONSENT,
        exportable_user_ids: set[int] | None = None,
    ) -> None:
        RealmAuditLog.objects.create(
            realm=realm, event_type=AuditLogEventType.REALM_EXPORTED, event_time=timezone_now()
        )
        output_dir = make_export_output_dir()
        with self.assertLogs(level="INFO"):
            do_export_realm(
                realm=realm,
                output_dir=output_dir,
                processes=1,
                export_type=export_type,
                exportable_user_ids=exportable_user_ids,
            )
            # The realm uuid is unique, and the import goes into the same server.
            realm.uuid = uuid.uuid4()
            realm.save()

    def import_realm(self) -> Realm:
        with (
            self.settings(BILLING_ENABLED=False),
            self.assertLogs(level="INFO"),
            self.captureOnCommitCallbacks(execute=True),
        ):
            return do_import_realm(get_output_dir(), "test-zulip")

    def task_titles_in_export(self) -> set[str]:
        return {row["title"] for row in read_json("realm.json")["zerver_task"]}


class SanjiExportImportTest(SanjiExportImportTestCase):
    def test_tasks_with_checklist_and_notes_round_trip(self) -> None:
        realm = self.example_user("hamlet").realm
        hamlet = self.example_user("hamlet")
        cordelia = self.example_user("cordelia")
        othello = self.example_user("othello")
        stream = get_stream("Denmark", realm)
        board = get_or_create_default_board(realm)
        columns = list(board.columns.all())
        due_at = datetime(2026, 11, 3, 16, 59, 59, tzinfo=timezone.utc)
        checklist = [{"text": "Ask product", "done": True}, {"text": "Write it up", "done": False}]
        origin_message_id = self.send_stream_message(hamlet, "Denmark", topic_name="plan")

        task = do_create_task(
            user_profile=hamlet,
            board=board,
            column=columns[1],
            title="Lock the October feature list",
            body="Decide the list before the cut-off.",
            stream_id=stream.id,
            topic="plan",
            origin_message_id=origin_message_id,
            assignee=cordelia,
            reviewer=othello,
            labels=["ops", "q4"],
            checklist=checklist,
            due_at=due_at,
            priority=3,
        )
        do_create_task(user_profile=hamlet, board=board, column=columns[0], title="No deadline")
        note = do_add_task_note(
            user_profile=hamlet, task=task, content="@**Cordelia, Lear's daughter** please check"
        ).note
        note_id = note.id
        TaskPlan.objects.create(user=cordelia, task=task, date=date(2026, 10, 30), position=1000)

        AgentRealmSettings.objects.create(
            realm=realm,
            task_id_prefix="ACME",
            timezone="Asia/Makassar",
            team_instructions="Answer in Indonesian.",
            default_selected_by=hamlet,
        )
        RolePermission.objects.create(
            realm=realm, permission_key="agents.create", role=UserProfile.ROLE_MEMBER, allowed=True
        )
        RoomMeta.objects.create(
            stream=stream,
            due_date=date(2026, 12, 1),
            summary_enabled=True,
            summary_enabled_by=hamlet,
        )
        pin_message_id = self.send_stream_message(hamlet, "Denmark", topic_name="pins")
        ChannelPin.objects.create(
            realm=realm, stream=stream, message_id=pin_message_id, pinned_by=cordelia
        )
        NeedResolution.objects.create(realm=realm, user=cordelia, message_id=pin_message_id)
        shared = do_create_playbook(
            user_profile=hamlet, title="Release", content="1. Tag it\n2. Ship it", scope="shared"
        )
        personal = do_create_playbook(
            user_profile=cordelia, title="Mine", content="Private steps", scope="personal"
        )

        self.export_realm(realm)
        exported = read_json("realm.json")
        self.assertEqual(
            {row["title"] for row in exported["zerver_task"]},
            {"Lock the October feature list", "No deadline"},
        )
        exported_task = next(row for row in exported["zerver_task"] if row["title"] == task.title)
        self.assertIsNone(exported_task["agent_profile"])
        self.assertIsNone(exported_task["agent_job"])

        imported_realm = self.import_realm()

        imported_task = Task.objects.get(realm=imported_realm, title=task.title)
        self.assertNotEqual(imported_task.id, task.id)
        self.assertEqual(imported_task.body, "Decide the list before the cut-off.")
        self.assertEqual(imported_task.checklist, checklist)
        self.assertEqual(imported_task.labels, ["ops", "q4"])
        self.assertEqual(imported_task.priority, 3)
        self.assertEqual(imported_task.due_at, due_at)
        self.assertEqual(imported_task.counter, task.counter)
        self.assertEqual(imported_task.topic, "plan")
        self.assertEqual(imported_task.board.realm_id, imported_realm.id)
        self.assertEqual(imported_task.column.name, columns[1].name)
        assert imported_task.stream is not None
        assert imported_task.origin_message is not None
        assert imported_task.assignee is not None
        assert imported_task.reviewer is not None
        self.assertEqual(imported_task.stream.realm_id, imported_realm.id)
        self.assertEqual(imported_task.stream.name, "Denmark")
        self.assertEqual(
            imported_task.origin_message.content, Message.objects.get(id=origin_message_id).content
        )
        self.assertEqual(imported_task.origin_message.realm_id, imported_realm.id)
        self.assertEqual(imported_task.creator.delivery_email, hamlet.delivery_email)
        self.assertEqual(imported_task.creator.realm_id, imported_realm.id)
        self.assertEqual(imported_task.assignee.delivery_email, cordelia.delivery_email)
        self.assertEqual(imported_task.reviewer.delivery_email, othello.delivery_email)
        self.assertIsNone(imported_task.agent_profile_id)
        self.assertIsNone(Task.objects.get(realm=imported_realm, title="No deadline").due_at)
        self.assertEqual(TaskBoard.objects.filter(realm=imported_realm).count(), 1)

        imported_note = TaskNote.objects.get(task=imported_task)
        self.assertEqual(imported_note.content, note.content)
        self.assertEqual(imported_note.author.delivery_email, hamlet.delivery_email)
        imported_cordelia = get_user_by_delivery_email(cordelia.delivery_email, imported_realm)
        # The mention in the rendered note names the member of the new server.
        self.assertIn(f'data-user-id="{imported_cordelia.id}"', imported_note.rendered_content)
        self.assertNotEqual(imported_cordelia.id, cordelia.id)

        history = TaskHistory.objects.filter(task=imported_task)
        self.assertEqual(
            {entry.kind for entry in history},
            {entry.kind for entry in TaskHistory.objects.filter(task=task)},
        )
        note_added = history.get(kind=TaskHistory.NOTE_ADDED)
        self.assertEqual(note_added.extra_data["note_id"], imported_note.id)
        self.assertNotEqual(imported_note.id, note_id)

        plan = TaskPlan.objects.get(task=imported_task)
        self.assertEqual(plan.user_id, imported_cordelia.id)
        self.assertEqual(plan.date, date(2026, 10, 30))

        def notifications_of(a_task: Task) -> set[tuple[str, str, str, bool]]:
            return {
                (n.user.delivery_email, n.kind, n.actor.delivery_email, n.note_id is not None)
                for n in TaskNotification.objects.filter(task=a_task)
            }

        # Writing the note made a notification for the member it mentions.
        self.assertIn(
            (cordelia.delivery_email, "mention", hamlet.delivery_email, True),
            notifications_of(task),
        )
        self.assertEqual(notifications_of(imported_task), notifications_of(task))
        self.assertEqual(
            TaskNotification.objects.get(task=imported_task, note__isnull=False).note_id,
            imported_note.id,
        )

        settings_row = AgentRealmSettings.objects.get(realm=imported_realm)
        self.assertEqual(settings_row.task_id_prefix, "ACME")
        self.assertEqual(settings_row.timezone, "Asia/Makassar")
        self.assertEqual(settings_row.team_instructions, "Answer in Indonesian.")
        assert settings_row.default_selected_by is not None
        self.assertEqual(settings_row.default_selected_by.realm_id, imported_realm.id)
        self.assertIsNone(settings_row.default_profile_id)
        self.assertEqual(
            RolePermission.objects.get(realm=imported_realm).permission_key, "agents.create"
        )
        imported_stream = get_stream("Denmark", imported_realm)
        imported_meta = RoomMeta.objects.get(stream=imported_stream)
        self.assertEqual(imported_meta.due_date, date(2026, 12, 1))
        self.assertTrue(imported_meta.summary_enabled)
        imported_pin = ChannelPin.objects.get(realm=imported_realm)
        self.assertEqual(imported_pin.stream_id, imported_stream.id)
        self.assertEqual(imported_pin.message.realm_id, imported_realm.id)
        self.assertEqual(imported_pin.pinned_by_id, imported_cordelia.id)
        resolution = NeedResolution.objects.get(realm=imported_realm)
        self.assertEqual(resolution.message_id, imported_pin.message_id)
        self.assertEqual(resolution.user_id, imported_cordelia.id)

        imported_shared = Playbook.objects.get(realm=imported_realm, title="Release")
        imported_personal = Playbook.objects.get(realm=imported_realm, title="Mine")
        self.assertNotEqual(imported_shared.id, shared.id)
        assert imported_shared.latest_revision is not None
        self.assertEqual(imported_shared.latest_revision.playbook_id, imported_shared.id)
        self.assertEqual(imported_shared.latest_revision.content, "1. Tag it\n2. Ship it")
        assert imported_personal.latest_revision is not None
        self.assertEqual(imported_personal.latest_revision.content, "Private steps")
        self.assertEqual(imported_personal.owner_id, imported_cordelia.id)
        self.assertEqual(
            PlaybookRevision.objects.filter(playbook__realm=imported_realm).count(),
            PlaybookRevision.objects.filter(playbook__in=[shared, personal]).count(),
        )

        # The original realm is untouched.
        self.assertEqual(Task.objects.filter(realm=realm).count(), 2)
        self.assertEqual(TaskNote.objects.filter(task__realm=realm).count(), 1)
        self.assertEqual(Playbook.objects.filter(realm=realm).count(), 2)

    def test_import_of_an_export_without_sanji_tables(self) -> None:
        realm = self.example_user("hamlet").realm
        self.export_realm(realm)
        realm_data = read_json("realm.json")
        for table in SANJI_EXPORTED_TABLES:
            del realm_data[table]
        with open(os.path.join(get_output_dir(), "realm.json"), "wb") as f:
            f.write(orjson.dumps(realm_data))

        imported_realm = self.import_realm()

        self.assertEqual(Task.objects.filter(realm=imported_realm).count(), 0)

    def test_secrets_never_leave_the_server(self) -> None:
        hamlet = self.example_user("hamlet")
        realm = hamlet.realm
        AgentSecret.objects.create(
            realm=realm,
            owner=hamlet,
            ciphertext=SECRET_MARKER,
            wrapped_key=SECRET_MARKER,
            key_id="key-1",
        )
        AgentRunnerRegistrationToken.objects.create(
            realm=realm,
            created_by=hamlet,
            token_hash=SECRET_MARKER.decode(),
            expires_at=timezone_now() + timedelta(days=1),
        )

        self.export_realm(realm)

        exported = read_json("realm.json")
        for table in EXCLUDED_SANJI_TABLES:
            self.assertNotIn(table, exported)
        for name in os.listdir(get_output_dir()):
            path = os.path.join(get_output_dir(), name)
            if os.path.isfile(path):
                with open(path, "rb") as f:
                    self.assertNotIn(SECRET_MARKER, f.read(), name)
                self.assertNotIn(SECRET_MARKER.hex().encode(), name.encode())


class SanjiExportScopeTest(SanjiExportImportTestCase):
    def make_data(self) -> dict[str, Any]:
        hamlet = self.example_user("hamlet")
        cordelia = self.example_user("cordelia")
        realm = hamlet.realm
        board = get_or_create_default_board(realm)
        column = board.columns.order_by("order").first()
        assert column is not None
        private_stream = self.make_stream("secret-plan", invite_only=True)
        self.subscribe(hamlet, "secret-plan")
        public_stream = get_stream("Denmark", realm)
        dm_message_id = self.send_personal_message(hamlet, cordelia)
        public_message_id = self.send_stream_message(hamlet, "Denmark")
        private_message_id = self.send_stream_message(hamlet, "secret-plan")

        def card(title: str, **kwargs: Any) -> Task:
            return do_create_task(
                user_profile=hamlet, board=board, column=column, title=title, **kwargs
            )

        public_task = card(
            "Public card", stream_id=public_stream.id, origin_message_id=public_message_id
        )
        private_task = card(
            "Private card", stream_id=private_stream.id, origin_message_id=private_message_id
        )
        card("Workspace card", origin_message_id=dm_message_id)
        do_add_task_note(user_profile=hamlet, task=public_task, content="Public note")
        do_add_task_note(user_profile=hamlet, task=private_task, content="Private note")
        for task in (public_task, private_task):
            TaskPlan.objects.create(user=hamlet, task=task, date=date(2026, 10, 30), position=1000)
            TaskNotification.objects.create(user=hamlet, task=task, actor=cordelia, kind="assigned")
        ChannelPin.objects.create(
            realm=realm, stream=public_stream, message_id=public_message_id, pinned_by=hamlet
        )
        ChannelPin.objects.create(
            realm=realm, stream=private_stream, message_id=private_message_id, pinned_by=hamlet
        )
        NeedResolution.objects.create(realm=realm, user=hamlet, message_id=public_message_id)
        NeedResolution.objects.create(realm=realm, user=hamlet, message_id=dm_message_id)
        do_create_playbook(user_profile=hamlet, title="Shared", content="x", scope="shared")
        do_create_playbook(user_profile=hamlet, title="Personal", content="y", scope="personal")
        return {
            "realm": realm,
            "public_message_id": public_message_id,
            "dm_message_id": dm_message_id,
        }

    def test_a_public_export_has_no_private_channel_or_member_data(self) -> None:
        context = self.make_data()

        self.export_realm(context["realm"], RealmExport.EXPORT_PUBLIC)

        exported = read_json("realm.json")
        self.assertEqual(self.task_titles_in_export(), {"Public card", "Workspace card"})
        self.assertEqual({row["content"] for row in exported["zerver_tasknote"]}, {"Public note"})
        # A card whose message is not in a public export loses only that link.
        origin_messages = {row["title"]: row["origin_message"] for row in exported["zerver_task"]}
        self.assertEqual(origin_messages["Public card"], context["public_message_id"])
        self.assertIsNone(origin_messages["Workspace card"])
        self.assertEqual(
            [row["message"] for row in exported["zerver_channelpin"]],
            [context["public_message_id"]],
        )
        self.assertEqual(exported["zerver_taskplan"], [])
        self.assertEqual(exported["zerver_tasknotification"], [])
        self.assertEqual(exported["zerver_needresolution"], [])
        self.assertEqual({row["title"] for row in exported["zerver_playbook"]}, {"Shared"})

    def test_a_consent_export_has_the_private_data_of_consenting_members_only(self) -> None:
        context = self.make_data()
        hamlet = self.example_user("hamlet")

        self.export_realm(
            context["realm"],
            RealmExport.EXPORT_FULL_WITH_CONSENT,
            exportable_user_ids={self.example_user("cordelia").id},
        )

        exported = read_json("realm.json")
        self.assertEqual(exported["zerver_taskplan"], [])
        self.assertEqual(exported["zerver_needresolution"], [])
        self.assertEqual({row["title"] for row in exported["zerver_playbook"]}, {"Shared"})
        # Cordelia is not subscribed to the private channel, and Hamlet does
        # not consent, so the card of the private channel stays out.
        self.assertEqual(self.task_titles_in_export(), {"Public card", "Workspace card"})

        self.export_realm(
            context["realm"],
            RealmExport.EXPORT_FULL_WITH_CONSENT,
            exportable_user_ids={hamlet.id},
        )

        exported = read_json("realm.json")
        self.assertEqual(
            {row["task"] for row in exported["zerver_taskplan"]},
            {row["id"] for row in exported["zerver_task"] if row["title"] != "Workspace card"},
        )
        self.assertEqual(
            {row["title"] for row in exported["zerver_playbook"]}, {"Shared", "Personal"}
        )
        self.assertEqual(
            {row["message"] for row in exported["zerver_needresolution"]},
            {context["public_message_id"], context["dm_message_id"]},
        )

    def test_a_full_export_has_everything(self) -> None:
        context = self.make_data()

        self.export_realm(context["realm"])

        exported = read_json("realm.json")
        self.assertEqual(
            self.task_titles_in_export(), {"Public card", "Private card", "Workspace card"}
        )
        self.assertEqual(len(exported["zerver_channelpin"]), 2)
        self.assertEqual(len(exported["zerver_taskplan"]), 2)
        self.assertEqual(len(exported["zerver_needresolution"]), 2)
        self.assertEqual(len(exported["zerver_playbook"]), 2)
