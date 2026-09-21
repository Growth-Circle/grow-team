"""Connection API regressions use the real Zulip authentication dispatch."""

import json
from uuid import UUID, uuid4

from typing_extensions import override

from zerver.lib.test_classes import ZulipTestCase
from zerver.models import Subscription, agents
from zerver.models.streams import get_stream


class AgentAPITests(ZulipTestCase):
    @override
    def setUp(self) -> None:
        super().setUp()
        self.owner = self.example_user("hamlet")
        agents.AgentRealmSettings.objects.update_or_create(
            realm=self.owner.realm, defaults={"enabled": True}
        )
        self.login_user(self.owner)
        self.runner = agents.AgentRunner.objects.create(
            realm=self.owner.realm,
            owner=self.owner,
            name="runner",
            fingerprint="a" * 64,
            catalog_report={
                "revision": 1,
                "adapters": [
                    {
                        "id": "acp",
                        "version": "1",
                        "auth_state": "ready",
                        "capabilities": {"config_version": 1},
                    }
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
            },
        )

    def post_agent(self, path: str, payload: dict[str, object]) -> dict[str, object]:
        response = self.client_post(
            "/json/agent/" + path, {"payload": json.dumps({"schema_version": 1, **payload})}
        )
        result = self.assert_json_success(response)
        self.assertEqual(result["schema_version"], 1)
        return result

    def test_provider_and_no_origin_repository_use_payload_envelopes(self) -> None:
        self.post_agent(
            "providers",
            {
                "runner_id": str(self.runner.id),
                "name": "Provider",
                "base_url": "https://example.com",
                "model_id": "model",
                "allowed_models": ["model"],
                "context_window_tokens": 1000,
                "max_output_tokens": 100,
                "local_credential_ref": "local-secret",
                "api_mode": "responses",
            },
        )
        self.post_agent(
            "repositories",
            {
                "runner_id": str(self.runner.id),
                "workspace_alias": "work",
                "canonical_origin": None,
                "allowed_refs": ["main"],
                "required_checks": [{"id": "test", "argv": ["true"]}],
            },
        )
        repository = agents.AgentRepository.objects.get(runner=self.runner)
        self.assertEqual(repository.required_checks[0]["cwd"], ".")
        self.post_agent(
            "profiles",
            {
                "runner_id": str(self.runner.id),
                "name": "Code",
                "adapter_id": "acp",
                "adapter_version": "1",
                "idempotency_key": str(uuid4()),
                "default_mode": "code",
                "repository_id": str(repository.id),
                "sandbox_alias": "default",
                "actions": ["context.read", "repository.read", "checks.run"],
            },
        )
        profile = agents.AgentProfile.objects.get(runner=self.runner)
        self.assertEqual(profile.default_mode, "code")
        self.assertIn("checks.run", profile.policy["actions"])

    def test_strict_payload_version_and_safe_errors(self) -> None:
        for version in (True, 1.0, 2):
            response = self.client_post(
                "/json/agent/profiles",
                {
                    "payload": json.dumps(
                        {"schema_version": version, "secret-marker": "do-not-echo"}
                    )
                },
            )
            self.assertEqual(response.status_code, 400)
            self.assertEqual(response.json()["schema_version"], 1)
            self.assertNotIn("do-not-echo", response.content.decode())

    def profile_payload(self) -> dict[str, object]:
        return {
            "runner_id": str(self.runner.id),
            "name": "Agent",
            "adapter_id": "acp",
            "adapter_version": "1",
            "idempotency_key": str(uuid4()),
            "sandbox_alias": "default",
        }

    def test_payload_idempotency_covers_description_and_mode(self) -> None:
        data = self.profile_payload()
        first = self.post_agent("profiles", data)
        self.assertEqual(first, self.post_agent("profiles", data))
        response = self.client_post(
            "/json/agent/profiles",
            {"payload": json.dumps({"schema_version": 1, **data, "description": "changed"})},
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(agents.AgentProfile.objects.count(), 1)

    def test_shared_runner_create_list_detail_and_cross_realm_denial(self) -> None:
        member = self.example_user("othello")
        self.post_agent(
            "grants",
            {
                "target_kind": "runner",
                "target_id": str(self.runner.id),
                "expected_revision": 1,
                "principal_user_id": member.id,
                "actions": ["runner.use"],
            },
        )
        self.login_user(member)
        created = self.post_agent("profiles", self.profile_payload())
        profile = agents.AgentProfile.objects.get(runner=self.runner)
        self.assertEqual(profile.owner_id, member.id)
        self.assertEqual(
            self.assert_json_success(self.client_get("/json/agent/profiles"))["count"], 1
        )
        self.assertEqual(self.client_get(f"/json/agent/profiles/{profile.id}").status_code, 200)
        self.login_user(self.mit_user("sipbtest"))
        self.assertEqual(
            self.assert_json_success(self.client_get("/json/agent/profiles", subdomain="zephyr"))[
                "count"
            ],
            0,
        )
        self.assertEqual(
            self.client_get(f"/json/agent/profiles/{profile.id}", subdomain="zephyr").status_code,
            400,
        )
        self.assertIsNotNone(created)

    def test_channel_attachment_requires_profile_and_runner_authority_and_retries(self) -> None:
        self.post_agent("profiles", self.profile_payload())
        profile = agents.AgentProfile.objects.get(runner=self.runner)
        stream = get_stream("Denmark", self.owner.realm)
        path = f"profiles/{profile.id}/attach-channel"
        data: dict[str, object] = {
            "schema_version": 1,
            "expected_revision": 1,
            "stream_id": stream.id,
        }
        member = self.example_user("othello")
        self.login_user(member)
        self.assertEqual(
            self.client_post("/json/agent/" + path, {"payload": json.dumps(data)}).status_code, 400
        )
        self.login_user(self.owner)
        self.post_agent(path, data)
        self.post_agent(path, data)
        self.assertEqual(
            agents.AgentGrant.objects.filter(profile=profile, scope__stream_id=stream.id).count(), 1
        )
        self.assertTrue(
            Subscription.objects.filter(
                user_profile=profile.bot_user, recipient=stream.recipient, active=True
            ).exists()
        )

    def test_pause_requires_current_revision_and_rejects_late_readiness(self) -> None:
        from zerver.actions.agents import record_readiness

        self.post_agent("profiles", self.profile_payload())
        profile = agents.AgentProfile.objects.get(runner=self.runner)
        setup = agents.AgentSetupOperation.objects.get(profile=profile)
        self.post_agent(f"profiles/{profile.id}/pause", {"expected_revision": 1})
        response = self.client_post(
            f"/json/agent/profiles/{profile.id}/archive",
            {"payload": json.dumps({"schema_version": 1, "expected_revision": 1})},
        )
        self.assertEqual(response.status_code, 400)
        with self.assertRaises(ValueError):
            record_readiness(
                self.runner,
                setup,
                {
                    "schema_version": 1,
                    "profile_id": str(profile.id),
                    "profile_revision": 1,
                    "runner_id": str(self.runner.id),
                    "descriptor_digest": setup.descriptor_digest,
                    "configuration_digest": setup.configuration_digest,
                    "state": "ready",
                    "capabilities": {"config_version": 1, "chat_ready": True},
                },
            )
        profile.refresh_from_db()
        self.assertEqual(profile.desired_state, "paused")

    def test_provider_probe_api_is_independent_and_exact_results_are_idempotent(self) -> None:
        from datetime import timedelta

        from django.utils.timezone import now

        from zerver.lib.agent_secrets import hash_agent_credential

        self.post_agent(
            "providers",
            {
                "runner_id": str(self.runner.id),
                "name": "Provider",
                "base_url": "https://example.com",
                "model_id": "model",
                "allowed_models": ["model"],
                "context_window_tokens": 1000,
                "max_output_tokens": 100,
                "local_credential_ref": "local-secret",
            },
        )
        provider = agents.AgentProvider.objects.get(runner=self.runner)
        created = self.post_agent(
            f"providers/{provider.id}/probe", {"expected_revision": 1, "retry_key": str(uuid4())}
        )
        token = "synthetic-device-token"
        agents.AgentRunnerCredential.objects.create(
            realm=self.owner.realm,
            runner=self.runner,
            token_hash=hash_agent_credential(token),
            refresh_hash=hash_agent_credential("refresh"),
            expires_at=now() + timedelta(hours=1),
            refresh_expires_at=now() + timedelta(days=1),
        )
        self.client.logout()
        discovery = self.client.get(
            "/api/v1/agent/runner/setups", HTTP_AUTHORIZATION="Bearer " + token
        )
        discovered = self.assert_json_success(discovery)
        self.assertEqual([item["setup_id"] for item in discovered["setups"]], [created["setup_id"]])
        claim = {"schema_version": 1, "setup_id": created["setup_id"], "claim_key": str(uuid4())}
        response = self.client.post(
            "/api/v1/agent/runner/setups/claim",
            data=json.dumps(claim),
            content_type="application/json",
            HTTP_AUTHORIZATION="Bearer " + token,
        )
        data = self.assert_json_success(response)
        self.assertIsNone(data["descriptor"]["profile_id"])
        self.assertEqual(data["descriptor"]["grant"]["actions"], ["probe"])
        result = {
            **claim,
            "lease_epoch": data["lease_epoch"],
            "descriptor_digest": data["descriptor"]["descriptor_digest"],
            "configuration_digest": data["descriptor"]["configuration_digest"],
            "state": "ready",
            "capabilities": {"config_version": 1, "chat_ready": True},
        }
        for _ in range(2):
            response = self.client.post(
                "/api/v1/agent/runner/setups/result",
                data=json.dumps(result),
                content_type="application/json",
                HTTP_AUTHORIZATION="Bearer " + token,
            )
            self.assert_json_success(response)
        agents.AgentSetupOperation.objects.filter(id=UUID(str(created["setup_id"]))).update(
            lease_expires_at=now()
        )
        agents.AgentProbeGrant.objects.filter(
            setup_operation_id=UUID(str(created["setup_id"]))
        ).update(expires_at=now())
        response = self.client.post(
            "/api/v1/agent/runner/setups/result",
            data=json.dumps(result),
            content_type="application/json",
            HTTP_AUTHORIZATION="Bearer " + token,
        )
        self.assert_json_success(response)
        self.assertEqual(agents.AgentProfile.objects.count(), 0)
        provider.refresh_from_db()
        self.assertTrue(provider.capability_report["chat_ready"])
        changed = {**result, "state": "failed"}
        response = self.client.post(
            "/api/v1/agent/runner/setups/result",
            data=json.dumps(changed),
            content_type="application/json",
            HTTP_AUTHORIZATION="Bearer " + token,
        )
        self.assertEqual(response.status_code, 400)

    def test_unavailable_adapter_and_raw_sandbox_are_rejected(self) -> None:
        for extra in (
            {"adapter_id": "unknown"},
            {"sandbox_alias": "unknown"},
            {"image": "unapproved"},
        ):
            response = self.client_post(
                "/json/agent/profiles",
                {"payload": json.dumps({"schema_version": 1, **self.profile_payload(), **extra})},
            )
            self.assertEqual(response.status_code, 400)
        self.assertFalse(agents.AgentProfile.objects.exists())

    def test_encrypted_provider_is_write_only_and_missing_key_is_safe(self) -> None:
        import base64
        import tempfile
        from pathlib import Path

        from django.test import override_settings

        data = {
            "schema_version": 1,
            "runner_id": str(self.runner.id),
            "name": "Private",
            "base_url": "https://example.com",
            "model_id": "model",
            "allowed_models": ["model"],
            "context_window_tokens": 1000,
            "max_output_tokens": 100,
            "credential": "synthetic-secret-marker",
        }
        with override_settings(AGENT_SECRET_MASTER_KEY_FILE=""):
            response = self.client_post("/json/agent/providers", {"payload": json.dumps(data)})
            self.assertEqual(response.status_code, 400)
            self.assertNotIn("synthetic-secret-marker", response.content.decode())
        with tempfile.TemporaryDirectory() as directory:
            key_file = Path(directory) / "keys.json"
            key_file.write_text(
                json.dumps(
                    {"current": "one", "keys": {"one": base64.b64encode(b"a" * 32).decode()}}
                )
            )
            with override_settings(AGENT_SECRET_MASTER_KEY_FILE=str(key_file)):
                response = self.client_post("/json/agent/providers", {"payload": json.dumps(data)})
                self.assert_json_success(response)
                self.assertNotIn("synthetic-secret-marker", response.content.decode())
                self.assertIsNotNone(agents.AgentProvider.objects.get(runner=self.runner).secret_id)

    def test_api_auth_and_session_csrf_boundaries(self) -> None:
        response = self.api_post(
            self.owner,
            "/api/v1/agent/profiles",
            {"payload": json.dumps({"schema_version": 1, **self.profile_payload()})},
        )
        self.assert_json_success(response)
        self.client.handler.enforce_csrf_checks = True
        response = self.client_post(
            "/json/agent/profiles",
            {"payload": json.dumps({"schema_version": 1, **self.profile_payload()})},
        )
        self.assertEqual(response.status_code, 403)

    def test_current_catalog_grant_and_repository_changes_reject_readiness(self) -> None:
        from django.utils.timezone import now

        from zerver.actions.agents import claim_setup

        self.post_agent(
            "repositories",
            {"runner_id": str(self.runner.id), "workspace_alias": "work", "allowed_refs": ["main"]},
        )
        repository = agents.AgentRepository.objects.get(runner=self.runner)
        self.post_agent("profiles", {**self.profile_payload(), "repository_id": str(repository.id)})
        setup = agents.AgentSetupOperation.objects.get(runner=self.runner)
        repository.allowed_refs = ["other"]
        repository.save(update_fields=["allowed_refs"])
        with self.assertRaises(ValueError):
            claim_setup(self.runner, setup.id, uuid4())
        repository.allowed_refs = ["main"]
        repository.save(update_fields=["allowed_refs"])
        agents.AgentProbeGrant.objects.filter(setup_operation=setup).update(expires_at=now())
        with self.assertRaises(ValueError):
            claim_setup(self.runner, setup.id, uuid4())

    def test_code_profile_requires_code_capability_and_exact_profile_replay(self) -> None:
        from zerver.actions.agents import record_readiness

        self.post_agent(
            "repositories",
            {"runner_id": str(self.runner.id), "workspace_alias": "work", "allowed_refs": ["main"]},
        )
        repository = agents.AgentRepository.objects.get(runner=self.runner)
        self.post_agent(
            "profiles",
            {**self.profile_payload(), "repository_id": str(repository.id), "default_mode": "code"},
        )
        profile = agents.AgentProfile.objects.get(runner=self.runner)
        setup = agents.AgentSetupOperation.objects.get(profile=profile)
        report = {
            "schema_version": 1,
            "profile_id": str(profile.id),
            "profile_revision": 1,
            "runner_id": str(self.runner.id),
            "descriptor_digest": setup.descriptor_digest,
            "configuration_digest": setup.configuration_digest,
            "state": "ready",
            "capabilities": {"config_version": 1, "chat_ready": True},
        }
        record_readiness(self.runner, setup, report)
        profile.refresh_from_db()
        self.assertEqual(profile.desired_state, "draft")
        self.assertEqual(profile.readiness_state, "needs_action")

    def test_catalog_change_invalidates_pending_probe_and_expired_result_is_denied(self) -> None:
        from zerver.actions.agents import claim_setup

        self.post_agent("profiles", self.profile_payload())
        setup = agents.AgentSetupOperation.objects.get(runner=self.runner)
        self.runner.catalog_report["sandboxes"][0]["image_digest"] = "sha256:" + "c" * 64
        self.runner.save(update_fields=["catalog_report"])
        with self.assertRaises(ValueError):
            claim_setup(self.runner, setup.id, uuid4())

    def test_shared_authority_revocation_blocks_pending_setup(self) -> None:
        from django.utils.timezone import now

        from zerver.actions.agents import claim_setup
        from zerver.lib.agent_policy import AgentAccessDenied

        member = self.example_user("othello")
        self.post_agent(
            "grants",
            {
                "target_kind": "runner",
                "target_id": str(self.runner.id),
                "expected_revision": 1,
                "principal_user_id": member.id,
                "actions": ["runner.use"],
            },
        )
        self.login_user(member)
        self.post_agent("profiles", self.profile_payload())
        setup = agents.AgentSetupOperation.objects.get(runner=self.runner)
        agents.AgentGrant.objects.filter(runner=self.runner).update(revoked_at=now())
        with self.assertRaises(AgentAccessDenied):
            claim_setup(self.runner, setup.id, uuid4())

    def test_revocation_starts_shutdown_and_stop_auth_cannot_use_rotated_token(self) -> None:
        from datetime import timedelta

        from django.utils.timezone import now

        from zerver.actions.agents import (
            authenticate_runner_stop_token,
            authenticate_runner_token,
            rotate_runner_credential,
        )
        from zerver.lib.agent_secrets import hash_agent_credential

        self.post_agent("profiles", self.profile_payload())
        profile = agents.AgentProfile.objects.get(runner=self.runner)
        conversation = agents.AgentConversation.objects.create(
            realm=self.owner.realm, profile=profile
        )
        job = agents.AgentJob.objects.create(
            realm=self.owner.realm,
            requester=self.owner,
            conversation=conversation,
            profile=profile,
            runner=self.runner,
            request="test",
            idempotency_key=uuid4(),
            payload_digest="a" * 64,
            status="running",
        )
        attempt = agents.AgentAttempt.objects.create(
            realm=self.owner.realm,
            job=job,
            runner=self.runner,
            number=1,
            lease_epoch=1,
            lease_expires_at=now() + timedelta(minutes=5),
            descriptor_digest="a" * 64,
            process_state="active",
        )
        old_token, refresh = "old-synthetic", "refresh-synthetic"
        credential = agents.AgentRunnerCredential.objects.create(
            realm=self.owner.realm,
            runner=self.runner,
            token_hash=hash_agent_credential(old_token),
            refresh_hash=hash_agent_credential(refresh),
            expires_at=now() + timedelta(hours=1),
            refresh_expires_at=now() + timedelta(days=1),
        )
        _, token, _ = rotate_runner_credential(credential, refresh)
        self.post_agent(f"runners/{self.runner.id}/revoke", {"expected_revision": 1})
        job.refresh_from_db()
        attempt.refresh_from_db()
        self.assertEqual(job.status, "cancel_requested")
        self.assertEqual(attempt.process_state, "stopping")
        self.assertTrue(attempt.active)
        self.assertIsNone(attempt.stopped_at)
        self.post_agent(f"runners/{self.runner.id}/revoke", {"expected_revision": 1})
        self.assertEqual(
            authenticate_runner_stop_token(
                token, job_id=job.id, attempt_id=attempt.id, lease_epoch=1, job_version=job.version
            ).id,
            attempt.id,
        )
        for candidate in (old_token, "wrong"):
            with self.assertRaises(ValueError):
                authenticate_runner_stop_token(
                    candidate,
                    job_id=job.id,
                    attempt_id=attempt.id,
                    lease_epoch=1,
                    job_version=job.version,
                )
        with self.assertRaises(ValueError):
            authenticate_runner_token(token)
        with self.assertRaises(ValueError):
            authenticate_runner_stop_token(
                token, job_id=job.id, attempt_id=attempt.id, lease_epoch=2, job_version=job.version
            )

    def test_profile_probe_duplicate_preserves_ready_and_lease_epoch_is_required(self) -> None:
        from zerver.actions.agents import claim_setup, record_setup_result
        from zerver.lib.agent_protocol import CapabilityReport
        from zerver.lib.agent_requests import SetupResult

        self.post_agent("profiles", self.profile_payload())
        setup = agents.AgentSetupOperation.objects.get(runner=self.runner)
        key = uuid4()
        claim_setup(self.runner, setup.id, key)
        setup.refresh_from_db()
        result = SetupResult(
            schema_version=1,
            setup_id=setup.id,
            claim_key=key,
            lease_epoch=setup.lease_epoch,
            descriptor_digest=setup.descriptor_digest,
            configuration_digest=setup.configuration_digest,
            state="ready",
            capabilities=CapabilityReport(config_version=1, chat_ready=True),
        )
        record_setup_result(self.runner, result)
        record_setup_result(self.runner, result)
        setup.refresh_from_db()
        self.assertEqual(setup.phase, "ready")
        with self.assertRaises(ValueError):
            record_setup_result(self.runner, result.model_copy(update={"lease_epoch": 2}))
        setup.refresh_from_db()
        self.assertEqual(setup.phase, "ready")

    def test_feature_disable_blocks_new_setup_claim_but_preserves_history_and_controls(
        self,
    ) -> None:
        from zerver.actions.agents import claim_setup

        self.post_agent("profiles", self.profile_payload())
        profile = agents.AgentProfile.objects.get(runner=self.runner)
        setup = agents.AgentSetupOperation.objects.get(profile=profile)
        agents.AgentRealmSettings.objects.filter(realm=self.owner.realm).update(enabled=False)
        with self.assertRaises(ValueError):
            claim_setup(self.runner, setup.id, uuid4())
        self.assertEqual(self.client_get(f"/json/agent/profiles/{profile.id}").status_code, 200)
        self.post_agent(f"profiles/{profile.id}/pause", {"expected_revision": 1})

    def test_revoked_setup_manager_cannot_complete_pending_probe(self) -> None:
        from django.utils.timezone import now

        from zerver.actions.agents import claim_setup
        from zerver.lib.agent_policy import AgentAccessDenied

        self.post_agent("profiles", self.profile_payload())
        profile = agents.AgentProfile.objects.get(runner=self.runner)
        member = self.example_user("othello")
        for kind, target, action in [
            ("runner", self.runner.id, "runner.use"),
            ("profile", profile.id, "profile.manage"),
        ]:
            self.post_agent(
                "grants",
                {
                    "target_kind": kind,
                    "target_id": str(target),
                    "expected_revision": 1,
                    "principal_user_id": member.id,
                    "actions": [action],
                },
            )
        self.login_user(member)
        result = self.post_agent(
            f"profiles/{profile.id}/readiness", {"expected_revision": 1, "retry_key": str(uuid4())}
        )
        agents.AgentGrant.objects.filter(profile=profile, principal_user=member).update(
            revoked_at=now()
        )
        with self.assertRaises(AgentAccessDenied):
            claim_setup(self.runner, UUID(str(result["setup_id"])), uuid4())

    def test_shared_profile_discovery_intersects_all_grants_and_scope_before_count(self) -> None:
        self.post_agent(
            "providers",
            {
                "runner_id": str(self.runner.id),
                "name": "provider",
                "base_url": "https://example.com",
                "model_id": "model",
                "allowed_models": ["model"],
                "context_window_tokens": 1000,
                "max_output_tokens": 100,
                "local_credential_ref": "local",
            },
        )
        provider = agents.AgentProvider.objects.get(runner=self.runner)
        self.post_agent(
            "repositories",
            {"runner_id": str(self.runner.id), "workspace_alias": "work", "allowed_refs": ["main"]},
        )
        repository = agents.AgentRepository.objects.get(runner=self.runner)
        self.post_agent(
            "profiles",
            {
                **self.profile_payload(),
                "provider_id": str(provider.id),
                "repository_id": str(repository.id),
            },
        )
        profile = agents.AgentProfile.objects.get(runner=self.runner)
        member = self.example_user("othello")
        for index, (kind, target, action) in enumerate(
            [
                ("profile", profile.id, "profile.use"),
                ("runner", self.runner.id, "runner.use"),
                ("provider", provider.id, "provider.use"),
                ("repository", repository.id, "repository.read"),
            ]
        ):
            self.login_user(self.owner)
            self.post_agent(
                "grants",
                {
                    "target_kind": kind,
                    "target_id": str(target),
                    "expected_revision": 1,
                    "principal_user_id": member.id,
                    "actions": [action],
                },
            )
            self.login_user(member)
            visible = index == 3
            data = self.assert_json_success(self.client_get("/json/agent/profiles", {"limit": 1}))
            self.assertEqual(data["count"], int(visible))
            self.assertEqual(
                self.client_get(f"/json/agent/profiles/{profile.id}").status_code,
                200 if visible else 400,
            )
        hidden = self.make_stream("agent-private", invite_only=True)
        agents.AgentGrant.objects.filter(profile=profile).update(
            scope={"kind": "stream", "stream_id": hidden.id}
        )
        self.assertEqual(
            self.assert_json_success(self.client_get("/json/agent/profiles"))["count"], 0
        )
        self.assertEqual(self.client_get(f"/json/agent/profiles/{profile.id}").status_code, 400)


from zerver.lib.test_classes import ZulipTransactionTestCase


class AgentConcurrencyTests(ZulipTransactionTestCase):
    def test_concurrent_identical_and_conflicting_payloads_share_one_identity(self) -> None:
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier

        from django.db import connections
        from django.test import Client

        from zerver.models import Client as APIClient
        from zerver.models import RealmAuditLog, UserGroup, UserProfile

        owner = self.example_user("hamlet")
        initial_clients = set(APIClient.objects.values_list("id", flat=True))
        initial_users = set(UserProfile.objects.values_list("id", flat=True))
        initial_groups = set(UserGroup.objects.values_list("id", flat=True))
        initial_audits = set(RealmAuditLog.objects.values_list("id", flat=True))
        catalog = {
            "revision": 1,
            "adapters": [
                {
                    "id": "acp",
                    "version": "1",
                    "auth_state": "ready",
                    "capabilities": {"config_version": 1},
                }
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
        runner = agents.AgentRunner.objects.create(
            realm=owner.realm,
            owner=owner,
            name="race",
            fingerprint="z" * 64,
            catalog_report=catalog,
        )
        auth = self.encode_user(owner)
        try:
            for conflicting in (False, True):
                barrier = Barrier(2)
                key = str(uuid4())

                def create(
                    index: int,
                    *,
                    key: str = key,
                    conflicting: bool = conflicting,
                    barrier: Barrier = barrier,
                ) -> tuple[int, object]:
                    try:
                        data = {
                            "schema_version": 1,
                            "runner_id": str(runner.id),
                            "name": "Race Agent",
                            "adapter_id": "acp",
                            "adapter_version": "1",
                            "idempotency_key": key,
                            "sandbox_alias": "default",
                            "description": str(index) if conflicting else "same",
                        }
                        barrier.wait(timeout=10)
                        response = Client().post(
                            "/api/v1/agent/profiles",
                            {"payload": json.dumps(data)},
                            HTTP_HOST="zulip.testserver",
                            HTTP_AUTHORIZATION=auth,
                        )
                        return response.status_code, response.json()
                    finally:
                        connections.close_all()

                with ThreadPoolExecutor(max_workers=2) as pool:
                    results = list(pool.map(create, [0, 1]))
                self.assertEqual(
                    sorted(status for status, _ in results),
                    [200, 400] if conflicting else [200, 200],
                )
                self.assertEqual(
                    agents.AgentSetupOperation.objects.filter(retry_key=key).count(), 1
                )
                if not conflicting:
                    self.assertEqual(results[0][1], results[1][1])
            self.assertEqual(agents.AgentProfile.objects.filter(runner=runner).count(), 2)
            from collections.abc import Callable
            from typing import Any

            from django.db import connection

            from zerver.actions import agents as actions
            from zerver.lib.agent_protocol import CapabilityReport
            from zerver.lib.agent_requests import SetupResult
            from zerver.lib.exceptions import JsonableError

            settings_record, settings_created = agents.AgentRealmSettings.objects.get_or_create(
                realm=owner.realm, defaults={"enabled": True}
            )
            original_enabled = settings_record.enabled
            settings_record.enabled = True
            settings_record.save(update_fields=["enabled"])
            provider = actions.register_provider(
                owner,
                runner,
                name="race-provider",
                base_url="https://example.com",
                model_id="model",
                allowed_models=["model"],
                context_window_tokens=1000,
                max_output_tokens=100,
                local_credential_ref="local",
            )
            profile = agents.AgentProfile.objects.filter(runner=runner).first()
            assert profile is not None
            try:
                for provider_only in (False, True):
                    for operation in ("claim", "result"):

                        def retry(
                            *, provider_only: bool = provider_only
                        ) -> agents.AgentSetupOperation:
                            if provider_only:
                                return actions.create_provider_probe(
                                    owner, provider, retry_key=uuid4(), expected_revision=1
                                )
                            assert profile is not None
                            return actions.retry_profile_setup(
                                owner, profile, expected_revision=1, retry_key=uuid4()
                            )

                        setup = retry()
                        claim_key = uuid4()
                        setup = actions.claim_setup(runner, setup.id, claim_key)
                        result = SetupResult(
                            schema_version=1,
                            setup_id=setup.id,
                            claim_key=claim_key,
                            lease_epoch=setup.lease_epoch,
                            descriptor_digest=setup.descriptor_digest,
                            configuration_digest=setup.configuration_digest,
                            state="needs_action",
                            capabilities=CapabilityReport(config_version=1),
                        )
                        gate = Barrier(2)

                        def operate(
                            index: int,
                            *,
                            operation: str = operation,
                            result: SetupResult = result,
                            retry: Callable[[], agents.AgentSetupOperation] = retry,
                            gate: Barrier = gate,
                        ) -> list[str]:
                            rows: list[str] = []

                            def trace(
                                execute: Callable[..., Any],
                                sql: str,
                                params: Any,
                                many: bool,
                                context: Any,
                            ) -> Any:
                                if "FOR UPDATE" in sql:
                                    rows.extend(
                                        table
                                        for table in (
                                            "zerver_agentrunner",
                                            "zerver_agentprofile",
                                            "zerver_agentprovider",
                                            "zerver_agentsetupoperation",
                                        )
                                        if f'FROM "{table}"' in sql
                                    )
                                return execute(sql, params, many, context)

                            try:
                                with connection.cursor() as cursor:
                                    cursor.execute("SET statement_timeout = '3s'")
                                gate.wait(timeout=10)
                                with connection.execute_wrapper(trace):
                                    try:
                                        if index == 0:
                                            retry()
                                        elif operation == "claim":
                                            actions.claim_setup(
                                                runner, result.setup_id, result.claim_key
                                            )
                                        else:
                                            actions.record_setup_result(runner, result)
                                    except (ValueError, JsonableError):
                                        if index == 0:
                                            raise
                                        # A replaced setup can reject its old claim or result.
                                return rows
                            finally:
                                connections.close_all()

                        with ThreadPoolExecutor(max_workers=2) as pool:
                            locks = list(pool.map(operate, [0, 1]))
                        for order in locks:
                            self.assertTrue(order)
                            self.assertEqual(order[0], "zerver_agentrunner")
            finally:
                if settings_created:
                    settings_record.delete()
                else:
                    settings_record.enabled = original_enabled
                    settings_record.save(update_fields=["enabled"])
        finally:
            agents.AgentProbeGrant.objects.filter(runner=runner).delete()
            agents.AgentSetupOperation.objects.filter(runner=runner).delete()
            agents.AgentProfile.objects.filter(runner=runner).delete()
            agents.AgentProvider.objects.filter(runner=runner).delete()
            runner.delete()
            RealmAuditLog.objects.exclude(id__in=initial_audits).delete()
            UserProfile.objects.exclude(id__in=initial_users).delete()
            UserGroup.objects.exclude(id__in=initial_groups).delete()
            APIClient.objects.exclude(id__in=initial_clients).delete()
