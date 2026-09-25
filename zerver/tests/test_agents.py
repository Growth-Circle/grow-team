"""Tests for WP13's additions to the agent profile API: appearance and
work fields, the P-19 work-runner fast path, and P-35 admin pause."""

import json
from typing import Any
from uuid import uuid4

from typing_extensions import override

from zerver.actions.agents import claim_setup, record_readiness
from zerver.lib.test_classes import ZulipTestCase
from zerver.models import agents


def catalog_report() -> dict[str, object]:
    return {
        "revision": 1,
        "adapters": [
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
