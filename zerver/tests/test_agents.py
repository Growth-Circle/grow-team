"""Tests for WP13's additions to the agent profile API: appearance and
work fields, the P-19 work-runner fast path, and P-35 admin pause."""

import json
from typing import Any
from uuid import uuid4

from typing_extensions import override

from zerver.actions.agent_jobs import create_job
from zerver.actions.agents import (
    claim_setup,
    endpoint_adapter,
    record_readiness,
    share_agent_profile,
    unshare_agent_profile,
    validate_runtime,
)
from zerver.lib import agent_protocol as protocol
from zerver.lib.agent_policy import AgentAccessDenied, check_agent_access
from zerver.lib.test_classes import ZulipTestCase
from zerver.models import agents
from zerver.models.external_accounts import DriveFolderLink, ExternalAccount
from zerver.models.mcp import McpAgentGrant, McpConnection, McpServer
from zerver.models.messages import Message
from zerver.models.realm_audit_logs import AuditLogEventType, RealmAuditLog


def catalog_report() -> dict[str, object]:
    return {
        "revision": 1,
        "adapters": [
            # The ACP harness comes first on purpose: the work-runner fast
            # path must still pick the endpoint adapter (version 0.1.0).
            {
                "id": "codex-acp",
                "version": "1.12.0",
                "auth_state": "ready",
                "capabilities": {"config_version": 1},
            },
            {
                "id": "endpoint-default",
                "version": "0.1.0",
                "auth_state": "ready",
                "capabilities": {"config_version": 1},
            },
        ],
        "sandboxes": [
            {
                "alias": "default",
                "image_digest": "sha256:" + "a" * 64,
                "toolchain_digest": "b" * 64,
                "catalog_revision": 1,
                "cpu_millicores": 100,
                "memory_bytes": 67108864,
                "pids_limit": 16,
                "temporary_bytes": 1048576,
            }
        ],
    }


class AgentDirectoryTestCase(ZulipTestCase):
    @override
    def setUp(self) -> None:
        super().setUp()
        self.owner = self.example_user("hamlet")
        self.member = self.example_user("cordelia")
        self.admin = self.example_user("iago")
        self.guest = self.example_user("polonius")
        self.settings_row, _created = agents.AgentRealmSettings.objects.update_or_create(
            realm=self.owner.realm, defaults={"enabled": True}
        )
        self.runner = agents.AgentRunner.objects.create(
            realm=self.owner.realm,
            owner=self.owner,
            name="work-runner",
            fingerprint="a" * 64,
            catalog_report=catalog_report(),
        )
        self.provider = self.make_provider("Work provider")

    def make_provider(self, name: str) -> agents.AgentProvider:
        return agents.AgentProvider.objects.create(
            realm=self.owner.realm,
            owner=self.owner,
            runner=self.runner,
            name=name,
            base_url="https://example.com",
            model_id="model",
            allowed_models=["model"],
            context_window_tokens=1000,
            max_output_tokens=100,
            local_credential_ref="local-secret",
            data_scope=["synthetic"],
        )

    def agent_request(self, method: str, path: str, payload: dict[str, object]) -> Any:
        request = getattr(self, f"client_{method}")
        return request(
            "/json/agent/" + path, {"payload": json.dumps({"schema_version": 1, **payload})}
        )

    def post_agent(self, path: str, payload: dict[str, object]) -> dict[str, Any]:
        response = self.agent_request("post", path, payload)
        if response.status_code != 200:
            self.fail(f"{response.status_code}: {response.content!r}")
        return self.assert_json_success(response)

    def profile_payload(self, **overrides: object) -> dict[str, object]:
        data: dict[str, object] = {
            "runner_id": str(self.runner.id),
            "name": "Custom agent",
            "adapter_id": "endpoint-default",
            "adapter_version": "0.1.0",
            "idempotency_key": str(uuid4()),
            "default_mode": "answer",
            "provider_id": str(self.provider.id),
            "mode": "endpoint",
            "sandbox_alias": "default",
        }
        data.update(overrides)
        return data

    def patch_payload(self, profile: agents.AgentProfile, **overrides: object) -> dict[str, object]:
        data: dict[str, object] = {
            "expected_metadata_revision": profile.metadata_revision,
            "expected_revision": profile.revision,
            "name": profile.name,
            "adapter_id": profile.adapter_id,
            "adapter_version": profile.adapter_version,
            "mode": profile.mode,
            "default_mode": profile.default_mode,
            "provider_id": str(profile.provider_id) if profile.provider_id else None,
            "sandbox_alias": "default",
        }
        data.update(overrides)
        return data

    def make_ready(self, profile: agents.AgentProfile) -> agents.AgentProfile:
        """Answer the profile's pending probe the way its runner does."""
        setup = agents.AgentSetupOperation.objects.filter(profile=profile).latest("created_at")
        setup = claim_setup(self.runner, setup.id, uuid4())
        record_readiness(
            self.runner,
            setup,
            {
                "schema_version": 1,
                "profile_id": str(profile.id),
                "profile_revision": profile.revision,
                "runner_id": str(self.runner.id),
                "descriptor_digest": setup.descriptor_digest,
                "configuration_digest": setup.configuration_digest,
                "state": "ready",
                "capabilities": {"chat_ready": True, "config_version": 1},
            },
        )
        profile.refresh_from_db()
        return profile


class WorkRunnerFastPathTest(AgentDirectoryTestCase):
    @override
    def setUp(self) -> None:
        super().setUp()
        self.settings_row.work_runner = self.runner
        self.settings_row.work_provider = self.provider
        self.settings_row.save(update_fields=["work_runner", "work_provider"])

    def create_member_agent(self) -> agents.AgentProfile:
        self.login_user(self.member)
        result = self.post_agent(
            "profiles",
            self.profile_payload(
                name="Member's work agent",
                # A member picking the fast path never chooses a provider
                # or adapter; the server fills both in from work_runner,
                # ignoring whatever this client happened to send.
                provider_id=None,
                mode="acp",
                adapter_id="bogus-adapter",
                adapter_version="0",
            ),
        )
        return agents.AgentProfile.objects.get(id=result["profile"]["id"])

    def test_member_creates_answer_agent_without_a_grant(self) -> None:
        profile = self.create_member_agent()
        self.assertEqual(profile.owner_id, self.member.id)
        self.assertEqual(profile.runner_id, self.runner.id)
        self.assertEqual(profile.provider_id, self.provider.id)
        self.assertEqual(profile.mode, "endpoint")
        self.assertEqual(profile.adapter_id, "endpoint-default")
        self.assertEqual(profile.adapter_version, "0.1.0")

    def test_member_work_agent_lifecycle(self) -> None:
        profile = self.create_member_agent()
        # The runner probes the new agent; the setup names the member.
        profile = self.make_ready(profile)
        self.assertEqual(profile.readiness_state, "ready")

        result = self.post_agent(
            f"profiles/{profile.id}/enable", {"expected_revision": profile.revision}
        )
        self.assertEqual(result["profile"]["desired_state"], "enabled")
        self.assertIn("edit", result["profile"]["allowed_actions"])

        # The member cannot see the work provider, so the client sends none.
        response = self.agent_request(
            "patch",
            f"profiles/{profile.id}",
            self.patch_payload(profile, name="Renamed agent", provider_id=None),
        )
        self.assert_json_success(response)
        profile.refresh_from_db()
        self.assertEqual(profile.name, "Renamed agent")
        self.assertEqual(profile.provider_id, self.provider.id)
        self.assertEqual(profile.desired_state, "enabled")

        source = Message.objects.get(
            id=self.send_stream_message(self.member, "Verona", "Plan the launch")
        )
        job = create_job(
            self.member,
            profile=profile,
            source=source,
            request="Plan the launch",
            idempotency_key=uuid4(),
            job_kind="answer",
            delivery_target="answer",
        )
        self.assertEqual(job.requester_id, self.member.id)

    def test_member_may_not_move_a_work_agent_to_code_mode(self) -> None:
        profile = self.make_ready(self.create_member_agent())
        response = self.agent_request(
            "patch", f"profiles/{profile.id}", self.patch_payload(profile, default_mode="code")
        )
        self.assert_json_error(response, "Agent request rejected.")

    def test_member_requesting_code_mode_is_rejected(self) -> None:
        self.login_user(self.member)
        response = self.agent_request(
            "post",
            "profiles",
            self.profile_payload(
                name="Member's coding agent",
                default_mode="code",
                mode="acp",
                adapter_id="codex-acp",
                adapter_version="1.12.0",
                provider_id=None,
            ),
        )
        self.assert_json_error(response, "Agent request rejected.")
        self.assertFalse(
            agents.AgentProfile.objects.filter(owner=self.member, default_mode="code").exists()
        )

    def test_guest_is_rejected(self) -> None:
        self.login_user(self.guest)
        response = self.agent_request(
            "post", "profiles", self.profile_payload(name="Guest's agent", provider_id=None)
        )
        self.assert_json_error(response, "Agent request rejected.")
        self.assertFalse(agents.AgentProfile.objects.filter(owner=self.guest).exists())

    def test_runner_owner_keeps_the_chosen_provider(self) -> None:
        other_provider = self.make_provider("Other provider")
        self.login_user(self.owner)
        result = self.post_agent(
            "profiles",
            self.profile_payload(
                provider_id=str(other_provider.id),
                mode="acp",
                adapter_id="codex-acp",
                adapter_version="1.12.0",
            ),
        )
        profile = agents.AgentProfile.objects.get(id=result["profile"]["id"])
        self.assertEqual(profile.provider_id, other_provider.id)
        self.assertEqual(profile.mode, "acp")
        self.assertEqual(profile.adapter_id, "codex-acp")

    def test_changed_work_provider_closes_the_path(self) -> None:
        profile = self.create_member_agent()
        validate_runtime(profile)
        check_agent_access(self.member, profile, None, None, "profile.manage")

        self.settings_row.work_provider = self.make_provider("New work provider")
        self.settings_row.save(update_fields=["work_provider"])
        profile = agents.AgentProfile.objects.get(id=profile.id)
        with self.assertRaises(AgentAccessDenied):
            validate_runtime(profile)
        with self.assertRaises(AgentAccessDenied):
            check_agent_access(self.member, profile, None, None, "profile.manage")

    def test_endpoint_adapter_needs_version_0_1_0(self) -> None:
        catalog = protocol.RunnerCatalog.model_validate(catalog_report())
        self.assertEqual(endpoint_adapter(catalog).id, "endpoint-default")
        report = catalog_report()
        report["adapters"] = [
            {
                "id": "codex-acp",
                "version": "1.12.0",
                "auth_state": "ready",
                "capabilities": {"config_version": 1},
            }
        ]
        with self.assertRaises(ValueError):
            endpoint_adapter(protocol.RunnerCatalog.model_validate(report))


class ProfileAppearanceTest(AgentDirectoryTestCase):
    def create_owner_agent(self, **overrides: object) -> agents.AgentProfile:
        self.login_user(self.owner)
        result = self.post_agent("profiles", self.profile_payload(**overrides))
        return agents.AgentProfile.objects.get(id=result["profile"]["id"])

    def test_appearance_and_work_fields_round_trip(self) -> None:
        self.login_user(self.owner)
        result = self.post_agent(
            "profiles",
            self.profile_payload(
                agent_role="builder",
                avatar_shape="ring",
                avatar_color="#8B74FF",
                model_preset="fast",
                monthly_budget_microunits=5_000_000,
                work_skills=["deck"],
                work_tools=["drive"],
            ),
        )
        data = result["profile"]
        self.assertEqual(data["agent_role"], "builder")
        self.assertEqual(data["avatar_shape"], "ring")
        self.assertEqual(data["avatar_color"], "#8B74FF")
        self.assertEqual(data["model_preset"], "fast")
        self.assertEqual(data["monthly_budget_microunits"], 5_000_000)
        self.assertEqual(data["work_skills"], ["deck"])
        self.assertEqual(data["work_tools"], ["drive"])
        self.assertEqual(data["kind"], "work")
        self.assertEqual(data["model_label"], "Fast · model")
        self.assertEqual(data["access_grants"], [])
        self.assertFalse(data["is_builtin"])

        list_result = self.assert_json_success(self.client_get("/json/agent/profiles"))
        listed = next(item for item in list_result["profiles"] if item["id"] == data["id"])
        self.assertEqual(listed["work_skills"], ["deck"])
        self.assertEqual(listed["work_tools"], ["drive"])

    def test_patch_with_only_a_name_keeps_the_other_fields(self) -> None:
        profile = self.create_owner_agent(
            agent_role="planner",
            avatar_shape="box",
            avatar_color="#FF6A3D",
            model_preset="best",
            monthly_budget_microunits=7,
            work_skills=["brief"],
            work_tools=["drive"],
        )
        response = self.agent_request(
            "patch", f"profiles/{profile.id}", self.patch_payload(profile, name="Renamed")
        )
        self.assert_json_success(response)
        profile.refresh_from_db()
        self.assertEqual(profile.name, "Renamed")
        self.assertEqual(profile.agent_role, "planner")
        self.assertEqual(profile.avatar_shape, "box")
        self.assertEqual(profile.avatar_color, "#FF6A3D")
        self.assertEqual(profile.model_preset, "best")
        self.assertEqual(profile.monthly_budget_microunits, 7)
        self.assertEqual(profile.work_skills, ["brief"])
        self.assertEqual(profile.work_tools, ["drive"])

        response = self.agent_request(
            "patch",
            f"profiles/{profile.id}",
            self.patch_payload(profile, monthly_budget_microunits=None, agent_role="custom"),
        )
        self.assert_json_success(response)
        profile.refresh_from_db()
        self.assertIsNone(profile.monthly_budget_microunits)
        self.assertEqual(profile.agent_role, "custom")
        self.assertEqual(profile.avatar_shape, "box")

    def test_unsafe_color_and_oversized_budget_are_rejected(self) -> None:
        self.login_user(self.owner)
        for overrides in (
            {"avatar_color": "url(//x.io/a)"},
            {"avatar_color": "#FF6A3"},
            {"monthly_budget_microunits": 2**63},
        ):
            response = self.agent_request("post", "profiles", self.profile_payload(**overrides))
            self.assert_json_error(response, "Agent request rejected.")
        profile = self.create_owner_agent()
        response = self.agent_request(
            "patch",
            f"profiles/{profile.id}",
            self.patch_payload(profile, avatar_color="red;background:url(//x.io)"),
        )
        self.assert_json_error(response, "Agent request rejected.")

    def test_coding_profile_model_label_uses_harness(self) -> None:
        self.login_user(self.owner)
        result = self.post_agent(
            "profiles",
            self.profile_payload(
                default_mode="code",
                mode="acp",
                adapter_id="codex-acp",
                adapter_version="1.12.0",
                provider_id=None,
            ),
        )
        self.assertEqual(result["profile"]["model_label"], "Codex")

    def test_access_grants_lists_rooms_folders_and_live_mcp_connections(self) -> None:
        profile = self.create_owner_agent()
        stream = self.make_stream("agent-room")
        self.subscribe(self.owner, "agent-room")
        self.subscribe(profile.bot_user, "agent-room")
        account = ExternalAccount.objects.create(
            realm=self.owner.realm, user=self.owner, provider="google", purpose="drive"
        )
        folder = DriveFolderLink.objects.create(
            realm=self.owner.realm,
            stream=stream,
            account=account,
            folder_id="folder-1",
            folder_name="Briefs",
            linked_by=self.owner,
        )
        server = McpServer.objects.create(
            realm=self.owner.realm,
            slug="linear",
            name="Linear",
            auth_mode="none",
            added_by=self.owner,
        )
        live = McpConnection.objects.create(
            realm=self.owner.realm, server=server, created_by=self.owner
        )
        removed = McpConnection.objects.create(
            realm=self.owner.realm, server=server, created_by=self.owner
        )
        McpAgentGrant.objects.create(connection=live, agent_profile=profile)
        McpAgentGrant.objects.create(connection=removed, agent_profile=profile)
        removed.removed_at = removed.created_at
        removed.save(update_fields=["removed_at"])

        data = self.assert_json_success(self.client_get(f"/json/agent/profiles/{profile.id}"))
        self.assertEqual(
            data["profile"]["access_grants"],
            [
                {"kind": "room", "id": stream.id, "label": "# agent-room"},
                {"kind": "drive_folder", "id": folder.id, "label": "Briefs"},
                {"kind": "mcp", "id": live.id, "label": "Linear"},
            ],
        )

        # A viewer who cannot read the room does not see it, nor its folders.
        self.make_stream("private-agent-room", invite_only=True)
        self.subscribe(self.owner, "private-agent-room")
        self.subscribe(profile.bot_user, "private-agent-room")
        share_agent_profile(self.owner, profile, principal_user=self.member)
        self.login_user(self.member)
        data = self.assert_json_success(self.client_get(f"/json/agent/profiles/{profile.id}"))
        labels = [chip["label"] for chip in data["profile"]["access_grants"]]
        self.assertNotIn("# private-agent-room", labels)
        self.assertIn("# agent-room", labels)


class AgentProfileAuditTest(AgentDirectoryTestCase):
    def audit_rows(self, profile: agents.AgentProfile, event_type: int) -> list[RealmAuditLog]:
        return list(
            RealmAuditLog.objects.filter(
                realm=self.owner.realm,
                event_type=event_type,
                extra_data__profile_id=str(profile.id),
            ).order_by("id")
        )

    def enable_share_and_unshare(self) -> agents.AgentProfile:
        self.login_user(self.owner)
        result = self.post_agent("profiles", self.profile_payload())
        profile = self.make_ready(agents.AgentProfile.objects.get(id=result["profile"]["id"]))
        self.post_agent(f"profiles/{profile.id}/enable", {"expected_revision": profile.revision})
        share_agent_profile(self.owner, profile, principal_user=self.member)
        # Sharing again changes no grant, so it adds no row.
        share_agent_profile(self.owner, profile, principal_user=self.member)
        unshare_agent_profile(self.owner, profile, principal_user=self.member)
        return profile

    def test_enable_share_and_unshare_are_audited(self) -> None:
        profile = self.enable_share_and_unshare()
        [created] = self.audit_rows(profile, AuditLogEventType.AGENT_PROFILE_CREATED)
        self.assertEqual(created.modified_user_id, profile.bot_user_id)
        [enabled] = self.audit_rows(profile, AuditLogEventType.AGENT_PROFILE_ENABLED)
        self.assertEqual(enabled.acting_user_id, self.owner.id)
        rows = self.audit_rows(profile, AuditLogEventType.AGENT_PROFILE_SHARED)
        self.assertEqual([row.extra_data["shared"] for row in rows], [True, False])
        self.assertEqual({row.modified_user_id for row in rows}, {self.member.id})
