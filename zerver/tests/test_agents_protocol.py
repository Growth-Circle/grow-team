"""Protocol tests also run with Python unittest without Django services."""

import importlib
import importlib.util
import json
from copy import deepcopy
from pathlib import Path
from types import ModuleType
from typing import Any
from unittest import TestCase

from pydantic import ValidationError

FIXTURE_PATH = Path(__file__).parent / "fixtures/agents/protocol-v1.json"


class AgentProtocolTest(TestCase):
    def protocol(self) -> ModuleType:
        self.assertIsNotNone(
            importlib.util.find_spec("zerver.lib.agent_protocol"),
            "The versioned agent protocol must be implemented",
        )
        return importlib.import_module("zerver.lib.agent_protocol")

    def test_shared_fixtures(self) -> None:
        protocol = self.protocol()
        fixtures = json.loads(FIXTURE_PATH.read_text())
        for case in fixtures["valid"]:
            with self.subTest(case=case["name"]):
                parsed = protocol.parse_payload(case["schema"], case["payload"])
                self.assertEqual(protocol.serialize_payload(parsed), case["payload"])
        for case in fixtures["invalid"]:
            with self.subTest(case=case["name"]), self.assertRaises((ValidationError, ValueError)):
                protocol.parse_payload(case["schema"], case["payload"])

    def test_instructions_length_counts_utf16_code_units_not_code_points(self) -> None:
        """The runner's zod schema measures a JS string (UTF-16 code units);
        counting Python code points instead let an astral character pass the
        server's 8000-character limit and then fail the runner's own check."""
        protocol = self.protocol()
        # An astral emoji is one Python code point but two UTF-16 code units.
        at_the_limit = "\U0001f600" * 4000
        over_the_limit = "\U0001f600" * 4001
        self.assertEqual(len(over_the_limit), 4001)
        protocol.InstructionText(revision=1, text=at_the_limit)
        with self.assertRaises(ValidationError):
            protocol.InstructionText(revision=1, text=over_the_limit)

    def descriptor(self) -> dict[str, Any]:
        return deepcopy(json.loads(FIXTURE_PATH.read_text())["valid"][0]["payload"])

    def fixture(self, name: str) -> dict[str, Any]:
        return deepcopy(
            next(
                item["payload"]
                for item in json.loads(FIXTURE_PATH.read_text())["valid"]
                if item["name"] == name
            )
        )

    def test_nested_authority_is_closed(self) -> None:
        protocol = self.protocol()
        for nested in ["adapter", "policy", "budget", "repository", "provider"]:
            data = self.descriptor()
            data[nested]["allow_all"] = True
            with self.subTest(nested=nested), self.assertRaises(ValidationError):
                protocol.parse_payload("attempt_descriptor", data)

    def test_answer_has_no_mutation_authority(self) -> None:
        protocol = self.protocol()
        data = self.descriptor()
        data.update(job_kind="answer", delivery_target="answer")
        with self.assertRaises(ValidationError):
            protocol.parse_payload("attempt_descriptor", data)
        data["policy"]["actions"] = ["context.read", "repository.read"]
        protocol.parse_payload("attempt_descriptor", data)

    def test_runner_cannot_send_server_authority_events(self) -> None:
        protocol = self.protocol()
        fixture = json.loads(FIXTURE_PATH.read_text())["valid"][1]["payload"]
        for name in ["approval.resolved", "job.queued", "result.published", "job.completed"]:
            data = {**fixture, "type": name}
            with self.subTest(event=name), self.assertRaises((ValidationError, ValueError)):
                protocol.parse_runner_event(data)

    def test_event_size_is_utf8_bytes(self) -> None:
        protocol = self.protocol()
        data = deepcopy(json.loads(FIXTURE_PATH.read_text())["valid"][1]["payload"])
        data["payload"]["summary"] = "測" * 22000
        with self.assertRaises(ValueError):
            protocol.parse_runner_event(data)

    def test_descriptor_digest_is_canonical_and_covers_authority(self) -> None:
        protocol = self.protocol()
        data = self.descriptor()
        first = protocol.descriptor_digest(data)
        self.assertEqual(first, protocol.descriptor_digest(dict(reversed(list(data.items())))))
        data["policy"]["actions"].append("git.push")
        self.assertNotEqual(first, protocol.descriptor_digest(data))

    def test_probe_never_has_repository_authority(self) -> None:
        protocol = self.protocol()
        fixture = json.loads(FIXTURE_PATH.read_text())["valid"][2]["payload"]
        with self.assertRaises(ValidationError):
            protocol.parse_payload(
                "probe_descriptor", {**fixture, "repository": self.descriptor()["repository"]}
            )

    def test_digest_redacts_no_fields_except_self(self) -> None:
        protocol = self.protocol()
        data = self.descriptor()
        first = protocol.descriptor_digest(data)
        data["descriptor_digest"] = "f" * 64
        self.assertEqual(first, protocol.descriptor_digest(data))
        data["provider"]["config_version"] += 1
        self.assertNotEqual(first, protocol.descriptor_digest(data))

    def test_naive_timestamp_and_unknown_schema_rejected(self) -> None:
        protocol = self.protocol()
        data = self.descriptor()
        data["lease_expires_at"] = "2026-09-21T12:00:00"
        with self.assertRaises(ValidationError):
            protocol.parse_payload("attempt_descriptor", data)
        with self.assertRaises(ValueError):
            protocol.parse_payload("future", {})

    def test_configuration_digest_is_shared_between_probe_and_attempt(self) -> None:
        protocol = self.protocol()
        data = self.descriptor()
        probe = deepcopy(json.loads(FIXTURE_PATH.read_text())["valid"][2]["payload"])
        attempt = protocol.parse_payload("attempt_descriptor", data)
        probe_record = protocol.parse_payload("probe_descriptor", probe)
        self.assertEqual(
            protocol.configuration_digest(attempt), protocol.configuration_digest(probe_record)
        )
        data["lease_epoch"] += 1
        changed = protocol.parse_payload("attempt_descriptor", data)
        self.assertEqual(
            protocol.configuration_digest(attempt), protocol.configuration_digest(changed)
        )
        data["policy"]["version"] += 1
        data["tested_configuration"]["policy_version"] += 1
        changed = protocol.parse_payload("attempt_descriptor", data)
        self.assertNotEqual(
            protocol.configuration_digest(attempt), protocol.configuration_digest(changed)
        )

    def test_instruction_fields_are_omitted_when_absent(self) -> None:
        """A profile without instructions must serialize exactly like release 24.

        A wrong serializer here makes every existing profile's readiness stale
        the moment this change deploys, so every non-instructed fixture's
        round trip must stay byte for byte the same.
        """
        protocol = self.protocol()
        instructed = {"instructed_answer_descriptor", "instructed_provider_probe"}
        fixtures = json.loads(FIXTURE_PATH.read_text())
        for case in fixtures["valid"]:
            if case["name"] in instructed or case["schema"] not in (
                "attempt_descriptor",
                "probe_descriptor",
                "configuration",
            ):
                continue
            with self.subTest(case=case["name"]):
                parsed = protocol.parse_payload(case["schema"], case["payload"])
                serialized = protocol.serialize_payload(parsed)
                self.assertEqual(serialized, case["payload"])
                self.assertNotIn("instructions_digest", serialized)
                self.assertNotIn("instructions", serialized)
                if case["schema"] == "attempt_descriptor":
                    self.assertNotIn("instructions_digest", serialized["tested_configuration"])

    def test_attempt_instructions_bind_the_profile_digest(self) -> None:
        protocol = self.protocol()
        data = self.fixture("instructed_answer_descriptor")
        attempt = protocol.parse_payload("attempt_descriptor", data)
        expected = protocol.instructions_digest(data["instructions"]["profile"]["text"])
        self.assertEqual(attempt.tested_configuration.instructions_digest, expected)
        probe = protocol.parse_payload(
            "probe_descriptor", self.fixture("instructed_provider_probe")
        )
        self.assertEqual(
            protocol.configuration_digest(attempt), protocol.configuration_digest(probe)
        )
        data["instructions"]["profile"]["text"] = "A different instruction body entirely."
        data["instructions"]["profile"]["revision"] = data["profile_revision"]
        changed = protocol.parse_payload("attempt_descriptor", data)
        self.assertNotEqual(
            protocol.configuration_digest(attempt), protocol.configuration_digest(changed)
        )

    def test_team_instructions_do_not_change_the_configuration_digest(self) -> None:
        protocol = self.protocol()
        data = self.fixture("instructed_answer_descriptor")
        attempt = protocol.parse_payload("attempt_descriptor", data)
        data["instructions"]["team"]["text"] = "A completely different team instruction body."
        changed_team = protocol.parse_payload("attempt_descriptor", data)
        self.assertEqual(
            protocol.configuration_digest(attempt), protocol.configuration_digest(changed_team)
        )
        first = protocol.descriptor_digest(
            {k: v for k, v in protocol.serialize_payload(attempt).items() if k != "descriptor_digest"}
        )
        second = protocol.descriptor_digest(
            {
                k: v
                for k, v in protocol.serialize_payload(changed_team).items()
                if k != "descriptor_digest"
            }
        )
        self.assertNotEqual(first, second)

    def test_instructions_profile_revision_must_match(self) -> None:
        protocol = self.protocol()
        data = self.fixture("instructed_answer_descriptor")
        data["instructions"]["profile"]["revision"] = data["profile_revision"] + 1
        with self.assertRaises(ValidationError):
            protocol.parse_payload("attempt_descriptor", data)

    def test_boolean_authority_does_not_coerce_strings(self) -> None:
        protocol = self.protocol()
        data = self.descriptor()
        data["policy"]["network"]["project_network"] = "true"
        with self.assertRaises(ValidationError):
            protocol.parse_payload("attempt_descriptor", data)

    def test_panel_events_preserve_authority(self) -> None:
        protocol = self.protocol()
        data = {
            "schema_version": 1,
            "job_id": "00000000-0000-4000-8000-000000000001",
            "attempt_id": None,
            "lease_epoch": None,
            "event_id": "00000000-0000-4000-8000-000000000002",
            "sequence": 1,
            "type": "job.queued",
            "occurred_at": "2026-09-21T12:00:00Z",
            "payload": {"status": "queued", "reason": "runner_offline"},
        }
        protocol.parse_authority_event("server", data)
        with self.assertRaises((ValidationError, ValueError)):
            protocol.parse_authority_event("runner", data)
        with self.assertRaises((ValidationError, ValueError)):
            protocol.parse_authority_event("publisher", data)

    def test_resource_grants_work_before_profile_creation(self) -> None:
        protocol = self.protocol()
        grant = {
            "id": "00000000-0000-4000-8000-000000000001",
            "principal_user_id": 1,
            "target_kind": "runner",
            "runner_id": "00000000-0000-4000-8000-000000000002",
            "actions": ["runner.use"],
            "policy_version": 1,
        }
        protocol.parse_payload("grant", grant)
        with self.assertRaises(ValidationError):
            protocol.parse_payload("grant", {**grant, "provider_id": grant["runner_id"]})
        with self.assertRaises(ValidationError):
            protocol.parse_payload("grant", {**grant, "actions": ["repository.edit"]})

    def test_management_grants_are_not_execution_tools(self) -> None:
        protocol = self.protocol()
        data = self.descriptor()
        data["policy"]["actions"] = ["profile.manage"]
        with self.assertRaises(ValidationError):
            protocol.parse_payload("attempt_descriptor", data)

    def test_sandbox_identity_changes_configuration_digest(self) -> None:
        protocol = self.protocol()
        data = self.descriptor()
        before = protocol.configuration_digest(protocol.parse_payload("attempt_descriptor", data))
        data["policy"]["sandbox"]["image_digest"] = "sha256:" + "f" * 64
        data["tested_configuration"]["sandbox"]["image_digest"] = "sha256:" + "f" * 64
        after = protocol.configuration_digest(protocol.parse_payload("attempt_descriptor", data))
        self.assertNotEqual(before, after)

    def test_runner_catalog_rejects_host_commands(self) -> None:
        protocol = self.protocol()
        catalog: dict[str, Any] = {
            "revision": 1,
            "adapters": [
                {
                    "id": "codex-acp",
                    "version": "1.12.0",
                    "auth_state": "unchecked",
                    "capabilities": {},
                }
            ],
            "sandboxes": [],
        }
        protocol.parse_payload("runner_catalog", catalog)
        catalog["adapters"][0]["command"] = "/bin/bash"
        with self.assertRaises(ValidationError):
            protocol.parse_payload("runner_catalog", catalog)

    def test_provider_url_cannot_contain_credentials(self) -> None:
        protocol = self.protocol()
        data = self.descriptor()["provider"]
        for url in [
            "https://secret:password@example.test/v1",
            "https://example.test/v1?api_key=secret",
            "https://example.test/v1#secret",
        ]:
            with self.subTest(url=url), self.assertRaises(ValidationError):
                protocol.parse_payload("provider", {**data, "base_url": url})

    def test_workspace_preparation_pins_commit_before_execution(self) -> None:
        protocol = self.protocol()
        data = self.descriptor()
        data["repository"]["base_commit"] = None
        protocol.parse_payload("attempt_descriptor", data)
        event = deepcopy(json.loads(FIXTURE_PATH.read_text())["valid"][1]["payload"])
        event["type"] = "workspace.prepared"
        event["payload"] = {
            "repository_id": data["repository"]["id"],
            "workspace_reference": "fixture-attempt",
            "base_ref": "main",
            "base_commit": "a" * 40,
            "tree_hash": "b" * 40,
            "user_worktree_dirty": True,
        }
        protocol.parse_runner_event(event)
        event["payload"]["base_commit"] = None
        with self.assertRaises(ValidationError):
            protocol.parse_runner_event(event)

    def test_private_http_requires_explicit_private_permission(self) -> None:
        protocol = self.protocol()
        data = self.descriptor()
        data["provider"]["network"]["targets"][0]["allow_http_private"] = True
        with self.assertRaises(ValidationError):
            protocol.parse_payload("attempt_descriptor", data)
        data["provider"]["network"]["targets"][0]["allow_private"] = True
        data["tested_configuration"]["provider"]["network"] = deepcopy(data["provider"]["network"])
        protocol.parse_payload("attempt_descriptor", data)
        data["policy"]["network"]["project_network"] = True
        with self.assertRaises(ValidationError):
            protocol.parse_payload("attempt_descriptor", data)

    def test_json_schema_export_stays_current(self) -> None:
        protocol = self.protocol()
        exported = json.loads(FIXTURE_PATH.with_name("protocol-v1.schema.json").read_text())
        self.assertEqual(exported, protocol.protocol_json_schemas())

    def test_enabled_profile_can_wait_for_edited_revision(self) -> None:
        protocol = self.protocol()
        profile = deepcopy(
            next(
                item["payload"]
                for item in json.loads(FIXTURE_PATH.read_text())["valid"]
                if item["name"] == "profile"
            )
        )
        profile.update(
            desired_state="enabled",
            revision=2,
            enabled_revision=1,
            readiness_revision=1,
            readiness_state="checking",
        )
        protocol.parse_payload("profile", profile)
        with self.assertRaises(ValidationError):
            protocol.parse_payload("profile", {**profile, "enabled_revision": 3})

    def test_schema_version_is_an_integer_not_boolean(self) -> None:
        protocol = self.protocol()
        for version in [True, 1.0, "1"]:
            with self.subTest(version=version), self.assertRaises(ValidationError):
                protocol.parse_payload(
                    "attempt_descriptor", {**self.descriptor(), "schema_version": version}
                )

    def test_effective_lease_can_narrow_tested_configuration(self) -> None:
        protocol = self.protocol()
        data = self.descriptor()
        self.assertIn("tested_configuration", data)
        baseline = protocol.configuration_digest(protocol.parse_payload("attempt_descriptor", data))
        data.update(job_kind="answer", delivery_target="answer", repository=None)
        data["policy"]["actions"] = ["context.read"]
        data["budget"]["output_tokens"] = 100
        narrowed = protocol.parse_payload("attempt_descriptor", data)
        self.assertEqual(protocol.configuration_digest(narrowed), baseline)

    def test_effective_lease_cannot_widen_tested_configuration(self) -> None:
        protocol = self.protocol()
        for change in [
            lambda data: data["policy"]["actions"].append("git.push"),
            lambda data: data["budget"].update(output_tokens=2048),
            lambda data: data["provider"].update(base_url="https://other.example.test/v1"),
            lambda data: data["policy"]["sandbox"].update(image_digest="sha256:" + "f" * 64),
            lambda data: data["policy"]["network"]["targets"].append(
                {"hostname": "other.test", "port": 443}
            ),
            lambda data: data["repository"].update(workspace_alias="other"),
            lambda data: data["repository"].update(canonical_origin="example/other"),
            lambda data: data["policy"].update(version=2),
        ]:
            data = self.descriptor()
            change(data)
            with self.subTest(change=change), self.assertRaises(ValidationError):
                protocol.parse_payload("attempt_descriptor", data)

    def test_manage_job_can_narrow_tested_workspace_binding(self) -> None:
        """A manage-mode profile may keep a repository binding (the form
        allows it, and manage readiness does not need code_ready), but a
        manage job's own attempt never has a workspace (contract 2.5: "no
        repository and no workspace"). That must not count as differing from
        its tested configuration, the same as the existing "answer" case."""
        protocol = self.protocol()
        data = self.descriptor()
        data.update(job_kind="manage", delivery_target="answer", repository=None)
        data["policy"]["actions"] = ["context.read"]
        protocol.parse_payload("attempt_descriptor", data)

    def test_effective_lease_preserves_finite_cost_ceiling(self) -> None:
        protocol = self.protocol()
        data = self.descriptor()
        self.assertIn("tested_configuration", data)
        data["tested_configuration"]["budget"]["cost_limit_microunits"] = 1000
        data["budget"]["cost_limit_microunits"] = 500
        protocol.parse_payload("attempt_descriptor", data)
        data["budget"]["cost_limit_microunits"] = None
        with self.assertRaises(ValidationError):
            protocol.parse_payload("attempt_descriptor", data)
        data["budget"]["cost_limit_microunits"] = 500
        data["tested_configuration"]["hard_cost_cap"] = True
        with self.assertRaises(ValidationError):
            protocol.parse_payload("attempt_descriptor", data)

    def test_event_type_rejects_nonstring_values(self) -> None:
        protocol = self.protocol()
        data = deepcopy(json.loads(FIXTURE_PATH.read_text())["valid"][1]["payload"])
        values: list[Any] = [None, [], {}]
        for value in values:
            data["type"] = value
            with self.subTest(value=value), self.assertRaises(ValidationError):
                protocol.parse_runner_event(data)

    def test_all_execution_actions_have_typed_operation_arguments(self) -> None:
        protocol = self.protocol()
        repository_id = self.descriptor()["repository"]["id"]
        cases = [
            {"action": "context.read", "context_ids": [repository_id]},
            {"action": "repository.read", "repository_id": repository_id, "paths": ["src/app.py"]},
            {
                "action": "repository.edit",
                "repository_id": repository_id,
                "patch_artifact_id": repository_id,
                "patch_checksum": "a" * 64,
                "expected_tree": "b" * 40,
            },
            {
                "action": "checks.run",
                "repository_id": repository_id,
                "check_ids": ["unit"],
                "tree_hash": "a" * 40,
            },
            {
                "action": "git.commit",
                "repository_id": repository_id,
                "message": "Fix fixture",
                "tree_hash": "a" * 40,
                "expected_parent": "b" * 40,
            },
        ]
        for case in cases:
            with self.subTest(action=case["action"]):
                protocol.parse_payload("operation_arguments", case)
                with self.assertRaises((ValueError, ValidationError)):
                    protocol.parse_payload("approval_arguments", case)

    def test_event_type_requires_matching_semantic_state(self) -> None:
        protocol = self.protocol()
        runner = deepcopy(json.loads(FIXTURE_PATH.read_text())["valid"][1]["payload"])
        identifier = runner["event_id"]
        cases = [
            (
                "runner",
                "input.applied",
                {"input_id": identifier, "input_sequence": 1, "delivery_state": "pending"},
            ),
            (
                "runner",
                "tool.started",
                {
                    "operation_id": identifier,
                    "tool_class": "git.commit",
                    "argument_digest": "a" * 64,
                    "status": "succeeded",
                },
            ),
            (
                "runner",
                "tool.finished",
                {
                    "operation_id": identifier,
                    "tool_class": "git.commit",
                    "argument_digest": "a" * 64,
                    "status": "started",
                },
            ),
            ("server", "job.queued", {"status": "completed"}),
            (
                "server",
                "input.received",
                {"input_id": identifier, "input_sequence": 1, "delivery_state": "applied"},
            ),
            (
                "server",
                "approval.requested",
                {
                    "approval_id": identifier,
                    "operation_hash": "a" * 64,
                    "version": 1,
                    "decision": "approved",
                },
            ),
            (
                "server",
                "approval.resolved",
                {
                    "approval_id": identifier,
                    "operation_hash": "a" * 64,
                    "version": 1,
                    "decision": "pending",
                },
            ),
            ("publisher", "result.published", {"result_message_id": None}),
            ("publisher", "publication.blocked", {"result_message_id": 12, "reason": "revoked"}),
        ]
        for authority, kind, payload in cases:
            with self.subTest(kind=kind), self.assertRaises(ValidationError):
                protocol.parse_authority_event(
                    authority, {**runner, "type": kind, "payload": payload}
                )

    def test_endpoint_probe_requires_provider(self) -> None:
        protocol = self.protocol()
        probe = deepcopy(json.loads(FIXTURE_PATH.read_text())["valid"][2]["payload"])
        probe["provider"] = None
        probe["grant"].update(provider_id=None, provider_config_version=None)
        with self.assertRaises(ValidationError):
            protocol.parse_payload("probe_descriptor", probe)

    def test_verification_exit_code_is_strict(self) -> None:
        protocol = self.protocol()
        verification = next(
            item["payload"]
            for item in json.loads(FIXTURE_PATH.read_text())["valid"]
            if item["name"] == "verification"
        )
        for value in [False, "0", 0.0]:
            with self.subTest(value=value), self.assertRaises(ValidationError):
                protocol.parse_payload("verification", {**verification, "exit_code": value})

    def manage_descriptor(self) -> dict[str, Any]:
        adapter = {"id": "grow-team-manage", "version": "1", "mode": "acp"}
        sandbox = {
            "alias": "manage",
            "image_digest": "sha256:" + "0" * 64,
            "toolchain_digest": "0" * 64,
            "catalog_revision": 1,
            "cpu_millicores": 100,
            "memory_bytes": 67108864,
            "pids_limit": 16,
            "temporary_bytes": 1048576,
        }
        network = {"targets": []}
        budget = {"input_tokens": 1024, "output_tokens": 512}
        return {
            "job_id": "00000000-0000-4000-8000-000000000101",
            "attempt_id": "00000000-0000-4000-8000-000000000102",
            "lease_epoch": 1,
            "audience": {
                "conversation_id": "00000000-0000-4000-8000-000000000103",
                "epoch": 1,
                "realm_id": 1,
                "profile_id": "00000000-0000-4000-8000-000000000104",
                "requester_user_id": 1,
                "bot_user_id": 2,
                "anchor_message_id": 5,
                "recipient_id": 9,
                "kind": "stream",
                "stream_id": 1,
                "invite_only": False,
                "is_web_public": False,
                "history_public_to_subscribers": True,
                "audience_user_ids": [1, 2],
            },
            "tested_configuration": {
                "runner_id": "00000000-0000-4000-8000-000000000105",
                "profile_revision": 1,
                "adapter": adapter,
                "provider": None,
                "workspace_binding": None,
                "policy_version": 1,
                "actions": ["context.read"],
                "sandbox": sandbox,
                "network": network,
                "hard_cost_cap": False,
                "budget": budget,
            },
            "profile_id": "00000000-0000-4000-8000-000000000104",
            "profile_revision": 1,
            "descriptor_digest": "0" * 64,
            "configuration_digest": "0" * 64,
            "lease_expires_at": "2026-09-21T12:00:00Z",
            "job_kind": "manage",
            "delivery_target": "answer",
            "request": "Give Budi access to #launch.",
            "runner_id": "00000000-0000-4000-8000-000000000105",
            "adapter": adapter,
            "provider": None,
            "repository": None,
            "policy": {
                "version": 1,
                "actions": ["context.read"],
                "grant_ids": [],
                "scope": {
                    "kind": "stream",
                    "stream_id": 1,
                    "topic": "general chat",
                    "participant_user_ids": [],
                    "anchor_message_id": None,
                },
                "sandbox": sandbox,
                "network": network,
                "hard_cost_cap": False,
            },
            "budget": budget,
        }

    def test_manage_descriptor_is_valid(self) -> None:
        protocol = self.protocol()
        attempt = protocol.parse_payload("attempt_descriptor", self.manage_descriptor())
        self.assertEqual(attempt.job_kind, "manage")
        self.assertIsNone(attempt.repository)

    def test_manage_descriptor_rejects_repository(self) -> None:
        protocol = self.protocol()
        data = self.manage_descriptor()
        data["repository"] = self.descriptor()["repository"]
        with self.assertRaises(ValidationError):
            protocol.parse_payload("attempt_descriptor", data)

    def test_manage_descriptor_rejects_code_delivery_target(self) -> None:
        protocol = self.protocol()
        data = self.manage_descriptor()
        data["delivery_target"] = "patch"
        with self.assertRaises(ValidationError):
            protocol.parse_payload("attempt_descriptor", data)

    def test_team_manage_input_rejects_extra_fields(self) -> None:
        protocol = self.protocol()
        arguments = {
            "action": "team.manage",
            "input": {
                "tool": "channel.subscribe",
                "channel_id": 7,
                "user_ids": [1, 2],
                "unexpected": True,
            },
        }
        with self.assertRaises(ValidationError):
            protocol.parse_payload("operation_arguments", arguments)

    def test_team_manage_input_rejects_wrong_tool_pairing(self) -> None:
        protocol = self.protocol()
        arguments = {
            "action": "team.manage",
            "input": {
                "tool": "channel.subscribe",
                "name": "launch",
                "description": "",
                "is_private": False,
                "subscriber_user_ids": [],
            },
        }
        with self.assertRaises(ValidationError):
            protocol.parse_payload("operation_arguments", arguments)

    def test_team_manage_input_is_typed_per_tool(self) -> None:
        protocol = self.protocol()
        cases = [
            {"tool": "team.find", "query": "budi", "kinds": ["person"]},
            {
                "tool": "channel.create",
                "name": "launch",
                "description": "",
                "is_private": False,
                "subscriber_user_ids": [],
            },
            {"tool": "channel.subscribe", "channel_id": 1, "user_ids": [1]},
            {"tool": "channel.unsubscribe", "channel_id": 1, "user_ids": [1]},
            {
                "tool": "group.create",
                "name": "launch-team",
                "description": "",
                "member_user_ids": [],
            },
            {"tool": "group.add_members", "group_id": 1, "user_ids": [1]},
            {"tool": "group.remove_members", "group_id": 1, "user_ids": [1]},
            {"tool": "topic.post", "channel_id": 1, "topic": "plan", "content": "Kickoff at 9am."},
            {"tool": "topic.add_person", "channel_id": 1, "topic": "plan", "user_ids": [1]},
            {"tool": "topic.resolve", "channel_id": 1, "topic": "plan", "resolved": True},
            {
                "tool": "topic.move",
                "channel_id": 1,
                "topic": "plan",
                "new_topic": "launch plan",
                "new_channel_id": None,
            },
        ]
        for input_value in cases:
            with self.subTest(tool=input_value["tool"]):
                parsed = protocol.parse_payload(
                    "operation_arguments", {"action": "team.manage", "input": input_value}
                )
                self.assertEqual(parsed.input.tool, input_value["tool"])

    def test_approval_tree_hash_is_optional_for_team_manage(self) -> None:
        protocol = self.protocol()
        approval = {
            "job_id": "00000000-0000-4000-8000-000000000101",
            "attempt_id": "00000000-0000-4000-8000-000000000102",
            "lease_epoch": 1,
            "id": "00000000-0000-4000-8000-000000000106",
            "operation_id": "00000000-0000-4000-8000-000000000107",
            "operation_hash": "a" * 64,
            "policy_version": 1,
            "version": 1,
            "arguments": {
                "action": "team.manage",
                "input": {"tool": "team.find", "query": "budi", "kinds": ["person"]},
            },
            "tree_hash": None,
            "approver_user_id": None,
            "decision": "pending",
            "expires_at": "2026-09-21T12:00:00Z",
            "nonce": "00000000-0000-4000-8000-000000000108",
        }
        parsed = protocol.parse_payload("approval", approval)
        self.assertIsNone(parsed.tree_hash)

    def test_team_executed_is_a_server_authority_event(self) -> None:
        protocol = self.protocol()
        event = {
            "job_id": "00000000-0000-4000-8000-000000000101",
            "attempt_id": "00000000-0000-4000-8000-000000000102",
            "lease_epoch": 1,
            "event_id": "00000000-0000-4000-8000-000000000109",
            "sequence": 1,
            "type": "team.executed",
            "occurred_at": "2026-09-21T12:00:00Z",
            "payload": {
                "tool": "channel.subscribe",
                "outcome": "succeeded",
                "summary": "Budi is now subscribed to #launch.",
                "objects": {"channel_id": 3, "user_ids": [1]},
                "error": None,
                "operation_id": "00000000-0000-4000-8000-000000000107",
            },
        }
        parsed = protocol.parse_authority_event("server", event)
        self.assertEqual(parsed.type, "team.executed")
        with self.assertRaises((ValidationError, ValueError)):
            protocol.parse_authority_event("runner", event)
        with self.assertRaises((ValidationError, ValueError)):
            protocol.parse_authority_event("publisher", event)
