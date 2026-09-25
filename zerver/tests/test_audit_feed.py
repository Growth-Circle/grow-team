"""Tests for zerver/lib/audit_feed.py and the two views built on it:
GET /json/realm/audit and GET /json/realm/audit.csv."""

import csv
import io
from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import call, patch
from uuid import uuid4

import orjson
from django.utils.timezone import now as timezone_now
from django.utils.translation import override as override_language
from typing_extensions import override

from zerver.lib.audit_feed import (
    csv_cell,
    export_realm_audit_csv,
    list_realm_audit_events,
    parse_audit_cursor,
)
from zerver.lib.test_classes import ZulipTestCase
from zerver.models import (
    AgentAuditEvent,
    AgentConversation,
    AgentJob,
    AgentProfile,
    AgentRealmSettings,
    AgentRunner,
    RealmAuditLog,
    UserProfile,
)
from zerver.models.realm_audit_logs import AuditLogEventType
from zerver.models.realms import get_realm


class AuditFeedTests(ZulipTestCase):
    @override
    def setUp(self) -> None:
        super().setUp()
        self.realm = get_realm("zulip")
        self.owner = self.example_user("desdemona")
        self.job: AgentJob | None = None

    def _realm_log(
        self,
        *,
        event_type: AuditLogEventType,
        when: datetime,
        acting_user: UserProfile | None = None,
        modified_user: UserProfile | None = None,
        extra_data: dict[str, object] | None = None,
    ) -> RealmAuditLog:
        return RealmAuditLog.objects.create(
            realm=self.realm,
            acting_user=acting_user,
            modified_user=modified_user,
            event_type=event_type,
            event_time=when,
            extra_data=extra_data or {},
        )

    def _agent_job(self) -> AgentJob:
        """One job for the whole test: a runner, a bare profile, a bare
        conversation, and a bare job. It only needs to exist, so that an
        AgentAuditEvent row can point at it."""
        if self.job is None:
            runner = AgentRunner.objects.create(
                realm=self.realm, owner=self.owner, name="Fixture", fingerprint=uuid4().hex
            )
            profile = AgentProfile.objects.create(
                realm=self.realm,
                owner=self.owner,
                runner=runner,
                bot_user=self.example_user("default_bot"),
                name="Fixture",
                adapter_id="codex-acp",
                adapter_version="1.12.0",
            )
            conversation = AgentConversation.objects.create(realm=self.realm, profile=profile)
            self.job = AgentJob.objects.create(
                realm=self.realm,
                requester=self.owner,
                conversation=conversation,
                profile=profile,
                runner=runner,
                request="hi",
                idempotency_key=uuid4(),
                payload_digest="a" * 64,
            )
        return self.job

    def _agent_event(
        self,
        *,
        when: datetime,
        actor: UserProfile | None = None,
        event_type: str = "tool.finished",
        authority: str = "server",
        payload: dict[str, object] | None = None,
    ) -> AgentAuditEvent:
        job = self._agent_job()
        return AgentAuditEvent.objects.create(
            realm=self.realm,
            job=job,
            event_id=uuid4(),
            sequence=AgentAuditEvent.objects.filter(job=job).count() + 1,
            actor=actor,
            authority=authority,
            type=event_type,
            occurred_at=when,
            payload=payload or {},
        )

    def _page(self, limit: int, after: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        params = {"limit": orjson.dumps(limit).decode()}
        if after is not None:
            params["before"] = orjson.dumps(after["time"]).decode()
            params["before_id"] = after["id"]
        return self.assert_json_success(self.client_get("/json/realm/audit", params))["events"]

    def _csv_rows(self) -> list[list[str]]:
        csv_text = "".join(export_realm_audit_csv(self.realm))
        self.assertTrue(csv_text.startswith("﻿"))
        return list(csv.reader(io.StringIO(csv_text.removeprefix("﻿"))))

    def test_lists_newest_first(self) -> None:
        now = timezone_now()
        older = self._realm_log(
            event_type=AuditLogEventType.WORKSPACE_SETTINGS_CHANGED, when=now - timedelta(hours=2)
        )
        newer = self._realm_log(
            event_type=AuditLogEventType.PERMISSION_MATRIX_CHANGED, when=now - timedelta(hours=1)
        )
        events = list_realm_audit_events(self.realm)
        ids = [event["id"] for event in events]
        self.assertEqual(ids[0], f"realm:{newer.id}")
        self.assertEqual(ids[1], f"realm:{older.id}")
        self.assertEqual(events[0]["time"], newer.event_time.timestamp())

    def test_actor_is_reported_or_null_for_a_system_event(self) -> None:
        self._realm_log(
            event_type=AuditLogEventType.WORKSPACE_SETTINGS_CHANGED,
            when=timezone_now(),
            acting_user=self.owner,
        )
        self._realm_log(
            event_type=AuditLogEventType.PERMISSION_MATRIX_CHANGED,
            when=timezone_now(),
            acting_user=None,
        )
        events = {event["event_type"]: event for event in list_realm_audit_events(self.realm)}
        self.assertEqual(
            events["WORKSPACE_SETTINGS_CHANGED"]["actor"],
            {"kind": "human", "id": self.owner.id, "name": self.owner.full_name},
        )
        self.assertIsNone(events["PERMISSION_MATRIX_CHANGED"]["actor"])

    def test_filters_to_this_realm(self) -> None:
        RealmAuditLog.objects.create(
            realm=get_realm("lear"),
            event_type=AuditLogEventType.WHATSAPP_LINK_CHANGED,
            event_time=timezone_now(),
            extra_data={},
        )
        self._realm_log(event_type=AuditLogEventType.PERMISSION_MATRIX_CHANGED, when=timezone_now())
        types = {event["event_type"] for event in list_realm_audit_events(self.realm)}
        self.assertIn("PERMISSION_MATRIX_CHANGED", types)
        self.assertNotIn("WHATSAPP_LINK_CHANGED", types)

    def test_before_cursor_excludes_newer_events(self) -> None:
        now = timezone_now()
        self._realm_log(
            event_type=AuditLogEventType.RUNNER_PAIRING_DENIED, when=now - timedelta(days=1)
        )
        cutoff = now - timedelta(hours=1)
        self._realm_log(event_type=AuditLogEventType.PERMISSION_MATRIX_CHANGED, when=now)
        # The shared test realm has its own older events, so read all of
        # them.
        cursor = parse_audit_cursor(cutoff.timestamp(), None)
        types = {
            event["event_type"]
            for event in list_realm_audit_events(self.realm, cursor=cursor, limit=1000)
        }
        self.assertIn("RUNNER_PAIRING_DENIED", types)
        self.assertNotIn("PERMISSION_MATRIX_CHANGED", types)

    def test_limit_caps_the_merged_result(self) -> None:
        now = timezone_now()
        for offset in range(5):
            self._realm_log(
                event_type=AuditLogEventType.WORKSPACE_SETTINGS_CHANGED,
                when=now - timedelta(minutes=offset),
            )
        self.assert_length(list_realm_audit_events(self.realm, limit=2), 2)

    def test_merges_an_agent_job_event_with_a_human_actor(self) -> None:
        """The actor of a job event is often the person who asked for the
        job, not the bot. The row still reads as an agent event."""
        self._agent_event(when=timezone_now(), actor=self.owner, event_type="drive.read")
        events = list_realm_audit_events(self.realm)
        self.assertEqual(events[0]["event_type"], "drive.read")
        self.assertEqual(
            events[0]["actor"], {"kind": "human", "id": self.owner.id, "name": self.owner.full_name}
        )

    def test_an_agent_is_the_actor_of_a_runner_event(self) -> None:
        self._agent_event(when=timezone_now(), authority="runner")
        bot = self.example_user("default_bot")
        events = list_realm_audit_events(self.realm)
        self.assertEqual(events[0]["actor"], {"kind": "agent", "id": bot.id, "name": bot.full_name})

    def test_shows_only_listed_event_types_and_details(self) -> None:
        now = timezone_now()
        hamlet = self.example_user("hamlet")
        export = self._realm_log(
            event_type=AuditLogEventType.REALM_EXPORTED,
            when=now,
            extra_data={"export_path": "https://files.example.com/export.tar.gz"},
        )
        subscription = self._realm_log(
            event_type=AuditLogEventType.SUBSCRIPTION_CREATED, when=now, modified_user=hamlet
        )
        name_change = self._realm_log(
            event_type=AuditLogEventType.REALM_PROPERTY_CHANGED,
            when=now,
            extra_data={
                RealmAuditLog.OLD_VALUE: "Zulip Dev",
                RealmAuditLog.NEW_VALUE: "Kopi Senja",
                "property": "name",
            },
        )
        group_change = self._realm_log(
            event_type=AuditLogEventType.REALM_PROPERTY_CHANGED,
            when=now,
            extra_data={
                RealmAuditLog.OLD_VALUE: 11,
                RealmAuditLog.NEW_VALUE: 12,
                "property": "can_create_bots_group",
            },
        )
        role_change = self._realm_log(
            event_type=AuditLogEventType.USER_ROLE_CHANGED,
            when=now,
            modified_user=hamlet,
            extra_data={
                RealmAuditLog.OLD_VALUE: UserProfile.ROLE_MEMBER,
                RealmAuditLog.NEW_VALUE: UserProfile.ROLE_MODERATOR,
                RealmAuditLog.ROLE_COUNT: {"human": {}},
            },
        )
        events = {event["id"]: event for event in list_realm_audit_events(self.realm, limit=1000)}
        self.assertNotIn(f"realm:{export.id}", events)
        self.assertNotIn(f"realm:{subscription.id}", events)
        self.assertNotIn(f"realm:{name_change.id}", events)
        self.assertEqual(
            events[f"realm:{group_change.id}"]["parameter"],
            {"property": "can_create_bots_group", "old_value": 11, "new_value": 12},
        )
        self.assertEqual(
            events[f"realm:{role_change.id}"]["parameter"],
            {
                "old_value": UserProfile.ROLE_MEMBER,
                "new_value": UserProfile.ROLE_MODERATOR,
                "modified_user_name": hamlet.full_name,
            },
        )

    def test_agent_event_shows_only_status_details(self) -> None:
        self._agent_event(
            when=timezone_now(),
            payload={
                "summary": "The text of the job.",
                "question": "Which file?",
                "status": "failed",
                "reason": "timeout",
                "tool_class": "shell",
                "exit_code": 2,
            },
        )
        events = list_realm_audit_events(self.realm)
        self.assertEqual(
            events[0]["parameter"],
            {"status": "failed", "reason": "timeout", "tool_class": "shell", "exit_code": 2},
        )

    def test_get_audit_hides_job_text_and_export_links(self) -> None:
        now = timezone_now()
        self._realm_log(
            event_type=AuditLogEventType.REALM_EXPORTED,
            when=now,
            extra_data={"export_path": "https://files.example.com/export.tar.gz"},
        )
        self._agent_event(
            when=now, payload={"summary": "Text from a direct message.", "status": "succeeded"}
        )
        self.login("iago")
        result = self.client_get("/json/realm/audit", {"limit": "200"})
        self.assert_json_success(result)
        text = result.content.decode()
        self.assertNotIn("files.example.com", text)
        self.assertNotIn("Text from a direct message", text)

    def test_pages_through_events_at_one_instant(self) -> None:
        now = timezone_now()
        first = self._realm_log(event_type=AuditLogEventType.WORKSPACE_SETTINGS_CHANGED, when=now)
        second = self._realm_log(event_type=AuditLogEventType.PERMISSION_MATRIX_CHANGED, when=now)
        agent_event = self._agent_event(when=now)
        self.login("iago")
        page = self._page(2)
        self.assertEqual(
            [event["id"] for event in page], [f"realm:{second.id}", f"realm:{first.id}"]
        )
        page = self._page(2, after=page[-1])
        self.assertEqual(page[0]["id"], f"agent:{agent_event.id}")
        page = self._page(2, after=page[0])
        self.assertTrue(all(event["time"] < now.timestamp() for event in page))

    def test_bad_audit_request_is_rejected(self) -> None:
        self.login("iago")
        for before_id in ["nope:1", "realm:abc", "agent:not-a-uuid"]:
            result = self.client_get("/json/realm/audit", {"before": "1", "before_id": before_id})
            self.assert_json_error(result, "Invalid audit log position.")
        for limit, message in [
            ("0", "limit is too small"),
            ("-1", "limit is too small"),
            ("201", "limit is too large"),
        ]:
            result = self.client_get("/json/realm/audit", {"limit": limit})
            self.assert_json_error(result, message)
        result = self.client_get("/json/realm/audit", {"before": "1e20"})
        self.assert_json_error(result, "before is too large")

    def test_csv_has_a_header_and_a_row_per_event(self) -> None:
        self._realm_log(
            event_type=AuditLogEventType.WORKSPACE_SETTINGS_CHANGED,
            when=timezone_now(),
            acting_user=self.owner,
        )
        rows = self._csv_rows()
        self.assertEqual(rows[0], ["Time", "Who", "What"])
        self.assertEqual(rows[1][1:], [self.owner.full_name, "Changed workspace settings."])

    def test_csv_exports_every_event(self) -> None:
        now = timezone_now()
        RealmAuditLog.objects.bulk_create(
            RealmAuditLog(
                realm=self.realm,
                event_type=AuditLogEventType.WORKSPACE_SETTINGS_CHANGED,
                event_time=now - timedelta(seconds=offset),
                extra_data={},
            )
            for offset in range(201)
        )
        all_events = list_realm_audit_events(self.realm, limit=100000)
        self.assertGreater(len(all_events), 200)
        self.assert_length(self._csv_rows(), len(all_events) + 1)

    def test_csv_describes_agent_and_system_events(self) -> None:
        now = timezone_now()
        self._agent_event(
            when=now - timedelta(seconds=1), event_type="drive.read", authority="server"
        )
        rows = self._csv_rows()
        self.assertEqual(rows[1][1:], ["System", "Agent activity: drive read."])

        self._agent_event(when=now, event_type="tool.finished", authority="runner")
        rows = self._csv_rows()
        bot = self.example_user("default_bot")
        self.assertEqual(rows[1][1:], [bot.full_name, "Agent activity: tool finished."])

    def test_csv_names_the_member_whose_role_changed(self) -> None:
        now = timezone_now()
        self._realm_log(
            event_type=AuditLogEventType.USER_ROLE_CHANGED,
            when=now,
            modified_user=self.example_user("hamlet"),
        )
        self._realm_log(
            event_type=AuditLogEventType.USER_ROLE_CHANGED, when=now - timedelta(seconds=1)
        )
        rows = self._csv_rows()
        self.assertEqual(rows[1][2], "Changed the role for King Hamlet.")
        self.assertEqual(rows[2][2], "Changed a member's role.")

    def test_csv_times_use_the_workspace_time_zone(self) -> None:
        when = datetime(2026, 9, 20, 10, 30, tzinfo=timezone.utc)
        self._realm_log(event_type=AuditLogEventType.WORKSPACE_SETTINGS_CHANGED, when=when)
        AgentRealmSettings.objects.filter(realm=self.realm).delete()
        times = [row[0] for row in self._csv_rows()]
        self.assertIn("2026-09-20 10:30:00 +0000", times)

        AgentRealmSettings.objects.create(realm=self.realm, timezone="Asia/Makassar")
        times = [row[0] for row in self._csv_rows()]
        self.assertIn("2026-09-20 18:30:00 +0800", times)

    def test_csv_cells_never_start_a_formula(self) -> None:
        self.owner.full_name = "=cmd|' /C calc'!A0"
        self.owner.save(update_fields=["full_name"])
        self._realm_log(
            event_type=AuditLogEventType.WORKSPACE_SETTINGS_CHANGED,
            when=timezone_now(),
            acting_user=self.owner,
        )
        rows = self._csv_rows()
        self.assertEqual(rows[1][1], "'=cmd|' /C calc'!A0")
        for prefix in ["=", "+", "-", "@", "\t", "\r"]:
            self.assertEqual(csv_cell(prefix + "1"), "'" + prefix + "1")
        self.assertEqual(csv_cell("Dita"), "Dita")

    def test_csv_export_renders_inside_the_workspace_language(self) -> None:
        """Check the mechanism: each text renders while the workspace's own
        language is active. The Indonesian text itself is not in the
        compiled translations of this test run."""
        self._realm_log(
            event_type=AuditLogEventType.WORKSPACE_SETTINGS_CHANGED, when=timezone_now()
        )
        self.realm.default_language = "id"
        self.realm.save(update_fields=["default_language"])
        with patch(
            "zerver.lib.audit_feed.override_language", wraps=override_language
        ) as mock_override:
            rows = self._csv_rows()
        self.assertGreater(len(rows), 1)
        self.assertEqual(mock_override.call_count, len(rows))
        self.assertTrue(all(each == call("id") for each in mock_override.call_args_list))

    def test_get_audit_requires_the_audit_permission(self) -> None:
        self.login("hamlet")  # Member: audit defaults to False.
        result = self.client_get("/json/realm/audit")
        self.assert_json_error(result, "You do not have permission to do this.")

    def test_get_audit_as_admin_returns_events(self) -> None:
        self._realm_log(
            event_type=AuditLogEventType.WORKSPACE_SETTINGS_CHANGED,
            when=timezone_now(),
            extra_data={"changed": [{"property": "timezone"}]},
        )
        self.login("iago")
        payload = self.assert_json_success(self.client_get("/json/realm/audit"))
        self.assertEqual(payload["events"][0]["event_type"], "WORKSPACE_SETTINGS_CHANGED")
        self.assertEqual(payload["events"][0]["parameter"], {"changed": [{"property": "timezone"}]})
        self.assertEqual(
            set(payload["events"][0]), {"id", "event_type", "time", "actor", "parameter"}
        )

    def test_get_audit_csv_requires_the_audit_permission(self) -> None:
        self.login("hamlet")
        result = self.client_get("/json/realm/audit.csv")
        self.assert_json_error(result, "You do not have permission to do this.")

    def test_get_audit_csv_downloads_a_csv_file(self) -> None:
        self.login("iago")
        result = self.client_get("/json/realm/audit.csv")
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result["Content-Type"], "text/csv; charset=utf-8")
        self.assertEqual(result["Content-Disposition"], 'attachment; filename="audit-zulip.csv"')
        body = result.getvalue().decode()
        self.assertTrue(body.startswith("﻿Time,Who,What\r\n"))
