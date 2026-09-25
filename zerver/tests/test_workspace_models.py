"""Tests for the Sanji workspace schema: the additive tables and columns
added in migrations 0820-0835."""

from uuid import uuid4

from django.db import IntegrityError, connection
from django.utils.timezone import now as timezone_now
from typing_extensions import override

from zerver.lib.test_classes import ZulipTestCase
from zerver.models import (
    AgentPairing,
    AgentProfile,
    AgentRealmSettings,
    AgentRunner,
    AgentRunnerRegistrationToken,
    CloudRunnerInstance,
    DriveFolderLink,
    ExternalAccount,
    McpAgentGrant,
    McpConnection,
    McpJobPlan,
    McpServer,
    McpToolPolicy,
    RolePermission,
    RoomChannelLink,
    RoomDigest,
    RoomMeta,
    Task,
    WebPushSubscription,
    agents,
)
from zerver.models.tasks import TASK_SOURCE_MANUAL, TaskBoard, TaskBoardColumn


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

    # -- 0821 AgentProfile appearance/budget ---------------------------

    def test_agent_profile_new_field_defaults(self) -> None:
        self.assertEqual(self.profile.agent_role, "custom")
        self.assertEqual(self.profile.avatar_shape, "circle")
        self.assertEqual(self.profile.avatar_color, "")
        self.assertEqual(self.profile.model_preset, "")
        self.assertIsNone(self.profile.monthly_budget_microunits)
        self.assertFalse(self.profile.is_builtin)
        self.assertEqual(self.profile.work_skills, [])
        self.assertEqual(self.profile.work_tools, [])

    def test_agent_profile_rejects_bad_role(self) -> None:
        with self.assertRaises(IntegrityError):
            AgentProfile.objects.create(
                realm=self.realm,
                owner=self.owner,
                runner=self.runner,
                bot_user=self.example_user("webhook_bot"),
                name="Bad",
                adapter_id="codex-acp",
                adapter_version="1.0",
                agent_role="villain",
            )

    def test_agent_profile_rejects_bad_avatar_shape(self) -> None:
        with self.assertRaises(IntegrityError):
            AgentProfile.objects.create(
                realm=self.realm,
                owner=self.owner,
                runner=self.runner,
                bot_user=self.example_user("webhook_bot"),
                name="Bad",
                adapter_id="codex-acp",
                adapter_version="1.0",
                avatar_shape="triangle",
            )

    def test_agent_profile_rejects_bad_model_preset(self) -> None:
        with self.assertRaises(IntegrityError):
            AgentProfile.objects.create(
                realm=self.realm,
                owner=self.owner,
                runner=self.runner,
                bot_user=self.example_user("webhook_bot"),
                name="Bad",
                adapter_id="codex-acp",
                adapter_version="1.0",
                model_preset="ultra",
            )

    # -- 0822 AgentRealmSettings workspace fields -----------------------

    def test_agent_realm_settings_new_field_defaults(self) -> None:
        settings_row = AgentRealmSettings.objects.create(realm=self.realm)
        self.assertEqual(settings_row.timezone, "Asia/Jakarta")
        self.assertEqual(settings_row.approval_ttl_minutes, 120)
        self.assertEqual(settings_row.invite_expiry_days, 7)
        self.assertEqual(settings_row.agent_language, "id")
        self.assertEqual(settings_row.mcp_default_mode, "research_first")
        self.assertEqual(settings_row.model_source, {})

    def test_agent_realm_settings_rejects_bad_language(self) -> None:
        with self.assertRaises(IntegrityError):
            AgentRealmSettings.objects.create(realm=self.realm, agent_language="fr")

    def test_agent_realm_settings_rejects_bad_mcp_mode(self) -> None:
        with self.assertRaises(IntegrityError):
            AgentRealmSettings.objects.create(realm=self.realm, mcp_default_mode="freeform")

    # -- 0823 Task.source ------------------------------------------------

    def test_task_source_default_is_manual(self) -> None:
        board = TaskBoard.objects.create(realm=self.realm, name="Board")
        column = TaskBoardColumn.objects.create(board=board, name="Todo", order=1)
        task = Task.objects.create(
            realm=self.realm,
            board=board,
            column=column,
            counter=1,
            title="Card",
            creator=self.owner,
        )
        self.assertEqual(task.source, TASK_SOURCE_MANUAL)

    def test_task_rejects_bad_source(self) -> None:
        board = TaskBoard.objects.create(realm=self.realm, name="Board2")
        column = TaskBoardColumn.objects.create(board=board, name="Todo", order=1)
        with self.assertRaises(IntegrityError):
            Task.objects.create(
                realm=self.realm,
                board=board,
                column=column,
                counter=1,
                title="Card",
                creator=self.owner,
                source="carrier_pigeon",
            )

    # -- 0824 AgentRunner inventory + registration tokens ----------------

    def test_agent_runner_new_field_defaults(self) -> None:
        self.assertEqual(self.runner.runner_kind, "")
        self.assertEqual(self.runner.labels, [])
        self.assertIsNone(self.runner.hidden_at)

    def test_agent_runner_rejects_bad_kind(self) -> None:
        with self.assertRaises(IntegrityError):
            AgentRunner.objects.create(
                realm=self.realm,
                owner=self.owner,
                name="Bad",
                fingerprint="b" * 64,
                runner_kind="mainframe",
            )

    def test_registration_token_defaults_and_kind_constraint(self) -> None:
        token = AgentRunnerRegistrationToken.objects.create(
            realm=self.realm,
            created_by=self.owner,
            token_hash="c" * 40,
            expires_at=timezone_now(),
        )
        self.assertEqual(token.runner_kind, "vps")
        self.assertEqual(token.name, "")
        self.assertFalse(token.sets_work_runner)
        with self.assertRaises(IntegrityError):
            AgentRunnerRegistrationToken.objects.create(
                realm=self.realm,
                created_by=self.owner,
                token_hash="d" * 40,
                runner_kind="local",
                expires_at=timezone_now(),
            )

    # -- 0825 AgentPairing meta -------------------------------------------

    def test_agent_pairing_requested_meta_defaults_to_empty_dict(self) -> None:
        pairing = AgentPairing.objects.create(
            realm=self.realm,
            owner=self.owner,
            device_name="Laptop",
            fingerprint="e" * 64,
            polling_secret_hash="f" * 40,
            user_code_hash="g" * 40,
            expires_at=timezone_now(),
            approved_at=timezone_now(),
        )
        self.assertEqual(pairing.requested_meta, {})
        self.assertIsNone(pairing.denied_at)

    # -- 0826 WebPushSubscription ------------------------------------------

    def test_web_push_subscription_unique_per_user_and_endpoint(self) -> None:
        fields = dict(
            user=self.owner,
            realm=self.realm,
            endpoint="https://push.example/abc",
            p256dh="key",
            auth="secret",
        )
        WebPushSubscription.objects.create(**fields)
        with self.assertRaises(IntegrityError):
            WebPushSubscription.objects.create(**fields)

    # -- 0827 ExternalAccount / DriveFolderLink -----------------------------

    def test_external_account_rejects_bad_provider(self) -> None:
        with self.assertRaises(IntegrityError):
            ExternalAccount.objects.create(realm=self.realm, provider="dropbox", purpose="drive")

    def test_external_account_rejects_bad_purpose(self) -> None:
        with self.assertRaises(IntegrityError):
            ExternalAccount.objects.create(realm=self.realm, provider="google", purpose="mail")

    def test_external_account_rejects_bad_status(self) -> None:
        with self.assertRaises(IntegrityError):
            ExternalAccount.objects.create(
                realm=self.realm, provider="google", purpose="drive", status="expired"
            )

    def test_external_account_accepts_needs_reconnect_status(self) -> None:
        account = ExternalAccount.objects.create(
            realm=self.realm, provider="google", purpose="drive", status="needs_reconnect"
        )
        self.assertEqual(account.status, "needs_reconnect")

    def test_drive_folder_link_rejects_bad_mode(self) -> None:
        account = ExternalAccount.objects.create(
            realm=self.realm, provider="google", purpose="drive"
        )
        with self.assertRaises(IntegrityError):
            DriveFolderLink.objects.create(
                realm=self.realm,
                stream=self.stream,
                account=account,
                folder_id="f1",
                mode="write_only",
                linked_by=self.owner,
            )

    # -- 0828 MCP catalog / connections / policy / grants / plan ------------

    def test_mcp_server_rejects_bad_auth_mode(self) -> None:
        with self.assertRaises(IntegrityError):
            McpServer.objects.create(
                realm=self.realm,
                slug="linear",
                name="Linear",
                added_by=self.owner,
                auth_mode="basic",
            )

    def test_mcp_server_new_field_defaults(self) -> None:
        server = McpServer.objects.create(
            realm=self.realm, slug="linear", name="Linear", added_by=self.owner
        )
        self.assertFalse(server.verified)
        self.assertEqual(server.version_pin, "")
        self.assertEqual(server.supported_scopes, [])

    def test_mcp_connection_rejects_bad_scope(self) -> None:
        server = McpServer.objects.create(
            realm=self.realm, slug="linear", name="Linear", added_by=self.owner
        )
        with self.assertRaises(IntegrityError):
            McpConnection.objects.create(
                realm=self.realm, server=server, scope="global", created_by=self.owner
            )

    def test_mcp_connection_scope_must_match_its_target(self) -> None:
        server = McpServer.objects.create(
            realm=self.realm, slug="linear", name="Linear", added_by=self.owner
        )
        with self.assertRaises(IntegrityError):
            McpConnection.objects.create(
                realm=self.realm,
                server=server,
                scope="room",
                stream=None,
                created_by=self.owner,
            )

    def test_mcp_connection_status_default_and_constraint(self) -> None:
        server = McpServer.objects.create(
            realm=self.realm, slug="linear", name="Linear", added_by=self.owner
        )
        connection_row = McpConnection.objects.create(
            realm=self.realm, server=server, scope="workspace", created_by=self.owner
        )
        self.assertEqual(connection_row.status, "pending")
        with self.assertRaises(IntegrityError):
            McpConnection.objects.create(
                realm=self.realm,
                server=server,
                scope="workspace",
                created_by=self.owner,
                status="disabled",
            )

    def test_mcp_tool_policy_unique_per_connection_and_tool(self) -> None:
        server = McpServer.objects.create(
            realm=self.realm, slug="linear", name="Linear", added_by=self.owner
        )
        connection_row = McpConnection.objects.create(
            realm=self.realm, server=server, scope="workspace", created_by=self.owner
        )
        policy = McpToolPolicy.objects.create(connection=connection_row, tool_name="search_issues")
        self.assertEqual(policy.hints, {})
        self.assertIsNone(policy.reviewed_at)
        with self.assertRaises(IntegrityError):
            McpToolPolicy.objects.create(connection=connection_row, tool_name="search_issues")

    def test_mcp_tool_policy_rejects_bad_policy(self) -> None:
        server = McpServer.objects.create(
            realm=self.realm, slug="linear", name="Linear", added_by=self.owner
        )
        connection_row = McpConnection.objects.create(
            realm=self.realm, server=server, scope="workspace", created_by=self.owner
        )
        with self.assertRaises(IntegrityError):
            McpToolPolicy.objects.create(
                connection=connection_row, tool_name="search_issues", policy="warn"
            )

    def test_mcp_agent_grant_and_job_plan_defaults(self) -> None:
        server = McpServer.objects.create(
            realm=self.realm, slug="linear", name="Linear", added_by=self.owner
        )
        connection_row = McpConnection.objects.create(
            realm=self.realm, server=server, scope="workspace", created_by=self.owner
        )
        McpAgentGrant.objects.create(connection=connection_row, agent_profile=self.profile)
        job = self._make_agent_job()
        plan = McpJobPlan.objects.create(job=job, tools={"search_issues": 3})
        self.assertEqual(plan.status, "proposed")

    def test_mcp_job_plan_rejects_bad_status(self) -> None:
        job = self._make_agent_job()
        with self.assertRaises(IntegrityError):
            McpJobPlan.objects.create(job=job, tools={}, status="pending")

    # -- 0829 RoomChannelLink -----------------------------------------------

    def test_room_channel_link_rejects_bad_direction(self) -> None:
        with self.assertRaises(IntegrityError):
            RoomChannelLink.objects.create(
                realm=self.realm,
                stream=self.stream,
                external_id="62800000000",
                direction="sideways",
                created_by=self.owner,
            )

    def test_room_channel_link_rejects_bad_provider(self) -> None:
        with self.assertRaises(IntegrityError):
            RoomChannelLink.objects.create(
                realm=self.realm,
                stream=self.stream,
                provider="telegram",
                external_id="62800000000",
                created_by=self.owner,
            )

    # -- 0830 CloudRunnerInstance ---------------------------------------------

    def test_cloud_runner_instance_rejects_bad_state(self) -> None:
        with self.assertRaises(IntegrityError):
            CloudRunnerInstance.objects.create(
                realm=self.realm, created_by=self.owner, state="teleporting"
            )

    def test_cloud_runner_instance_defaults_to_cloudflare(self) -> None:
        instance = CloudRunnerInstance.objects.create(realm=self.realm, created_by=self.owner)
        self.assertEqual(instance.provider, "cloudflare")
        self.assertEqual(instance.egress, "https_only")

    def test_cloud_runner_instance_rejects_bad_provider(self) -> None:
        with self.assertRaises(IntegrityError):
            CloudRunnerInstance.objects.create(
                realm=self.realm, created_by=self.owner, provider="gcp"
            )

    # -- 0832 RolePermission model-level constraint (see test_role_permissions.py
    # for has_role_permission/permission_matrix behaviour) -------------------

    def test_role_permission_rejects_unknown_role(self) -> None:
        with self.assertRaises(IntegrityError):
            RolePermission.objects.create(
                realm=self.realm, permission_key="ws_settings", role=999, allowed=True
            )

    # -- helpers --------------------------------------------------------------

    def _make_agent_job(self) -> agents.AgentJob:
        conversation = agents.AgentConversation.objects.create(
            realm=self.realm, profile=self.profile, anchor_message_id=self.message_id
        )
        return agents.AgentJob.objects.create(
            realm=self.realm,
            requester=self.owner,
            conversation=conversation,
            profile=self.profile,
            runner=self.runner,
            request="Explain",
            idempotency_key=uuid4(),
            payload_digest="a" * 64,
            admission_revision=1,
        )
