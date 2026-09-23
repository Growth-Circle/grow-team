"""Connection API regressions use the real Zulip authentication dispatch."""

import json
from datetime import timedelta
from uuid import UUID, uuid4

from django.utils.timezone import now
from typing_extensions import override

from zerver.lib.test_classes import ZulipTestCase
from zerver.models import Subscription, UserProfile, agents
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
        profile_response = self.post_agent(
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
        response = self.client_get(f"/json/agent/profiles/{profile_response['profile']['id']}")
        detail = self.assert_json_success(response)
        self.assertEqual(detail["setup"]["profile_revision"], 1)

    def test_profile_budget_derives_from_provider_or_floors_without_one(self) -> None:
        def tokens(profile: agents.AgentProfile) -> tuple[int, int]:
            return profile.budget["input_tokens"], profile.budget["output_tokens"]

        self.post_agent("profiles", self.profile_payload())
        without_provider = agents.AgentProfile.objects.get(runner=self.runner)
        self.assertEqual(tokens(without_provider), (400000, 16000))
        self.post_agent(
            "providers",
            {
                "runner_id": str(self.runner.id),
                "name": "Provider",
                "base_url": "https://example.com",
                "model_id": "model",
                "allowed_models": ["model"],
                "context_window_tokens": 50000,
                "max_output_tokens": 10000,
                "local_credential_ref": "local-secret",
            },
        )
        provider = agents.AgentProvider.objects.get(runner=self.runner)
        self.post_agent("profiles", {**self.profile_payload(), "provider_id": str(provider.id)})
        with_provider = agents.AgentProfile.objects.get(provider=provider)
        # input_tokens = min(max(50000 * 10, 200000), 4000000); output_tokens =
        # min(max(10000 * 4, 16000), 256000).
        self.assertEqual(tokens(with_provider), (500000, 40000))
        # An explicit budget is never overridden by the provider.
        self.post_agent(
            "profiles",
            {
                **self.profile_payload(),
                "provider_id": str(provider.id),
                "budget": {"input_tokens": 1000, "output_tokens": 1000},
            },
        )
        explicit = agents.AgentProfile.objects.exclude(
            id__in=[without_provider.id, with_provider.id]
        ).get()
        self.assertEqual(tokens(explicit), (1000, 1000))

    def test_job_list_view_filters_mine_waiting_and_running(self) -> None:
        from zerver.actions import agent_jobs as job_actions
        from zerver.actions.agents import enable_profile, record_readiness, share_agent_profile
        from zerver.models import Message

        self.post_agent("profiles", self.profile_payload())
        profile = agents.AgentProfile.objects.get(runner=self.runner)
        setup = agents.AgentSetupOperation.objects.get(profile=profile)
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
                "capabilities": {"chat_ready": True, "config_version": 1},
            },
        )
        profile.refresh_from_db()
        enable_profile(self.owner, profile, expected_revision=profile.revision)
        other = self.example_user("othello")
        share_agent_profile(self.owner, profile, principal_user=other)

        def make_job(requester: UserProfile, status: str) -> agents.AgentJob:
            # Each job needs its own source message: create_job treats a repeat
            # (source, profile) trigger from a different requester as a conflict.
            source = Message.objects.get(
                id=self.send_stream_message(requester, "Verona", f"For the list filter {status}")
            )
            job = job_actions.create_job(
                requester,
                profile=profile,
                source=source,
                request="Task",
                idempotency_key=uuid4(),
                job_kind="answer",
                delivery_target="answer",
            )
            agents.AgentJob.objects.filter(id=job.id).update(status=status)
            return job

        mine_running = make_job(self.owner, "running")
        mine_waiting = make_job(self.owner, "waiting_for_input")
        theirs = make_job(other, "queued")

        def job_ids(view: str) -> set[str]:
            data = self.assert_json_success(self.client_get(f"/json/agent/jobs?view={view}"))
            return {item["id"] for item in data["jobs"]}

        self.assertEqual(job_ids("mine"), {str(mine_running.id), str(mine_waiting.id)})
        self.assertEqual(job_ids("waiting"), {str(mine_waiting.id)})
        self.assertEqual(job_ids("running"), {str(mine_running.id)})
        self.assertEqual(
            job_ids("all"), {str(mine_running.id), str(mine_waiting.id), str(theirs.id)}
        )
        self.assertEqual(self.client_get("/json/agent/jobs?view=bogus").status_code, 400)

    def test_runner_metadata_update_is_revision_checked(self) -> None:
        response = self.client_patch(
            f"/json/agent/runners/{self.runner.id}/metadata",
            {
                "payload": json.dumps(
                    {
                        "schema_version": 1,
                        "expected_metadata_revision": 1,
                        "name": "Build server",
                        "host_kind": "server",
                    }
                )
            },
        )
        result = self.assert_json_success(response)
        self.assertEqual(result["runner"]["host_kind"], "server")
        self.assertEqual(result["runner"]["metadata_revision"], 2)
        self.runner.refresh_from_db()
        self.assertEqual(self.runner.policy_version, 1)
        response = self.client_patch(
            f"/json/agent/runners/{self.runner.id}/metadata",
            {
                "payload": json.dumps(
                    {
                        "schema_version": 1,
                        "expected_metadata_revision": 1,
                        "name": "Stale",
                        "host_kind": "workstation",
                    }
                )
            },
        )
        self.assertEqual(response.status_code, 400)

    def test_device_runner_metadata_uses_current_credential_and_metadata_cas(self) -> None:
        from zerver.lib.agent_secrets import hash_agent_credential

        self.post_agent("profiles", self.profile_payload())
        setup = agents.AgentSetupOperation.objects.get(runner=self.runner)
        original_digest = setup.configuration_digest
        token = "metadata-device-token"
        credential = agents.AgentRunnerCredential.objects.create(
            realm=self.owner.realm,
            runner=self.runner,
            token_hash=hash_agent_credential(token),
            refresh_hash=hash_agent_credential("metadata-refresh"),
            expires_at=now() + timedelta(hours=1),
            refresh_expires_at=now() + timedelta(days=1),
        )
        path = "/api/v1/agent/runner/metadata"
        headers = {"HTTP_AUTHORIZATION": "Bearer " + token}
        body = {
            "schema_version": 1,
            "expected_metadata_revision": 1,
            "name": "Owner workstation",
            "host_kind": "workstation",
        }
        self.assertEqual(self.client.get(path, **headers).status_code, 401)
        self.assertEqual(
            self.client.post(
                path, data=json.dumps(body), content_type="application/json", **headers
            ).status_code,
            401,
        )
        self.client.logout()
        read = self.assert_json_success(self.client.get(path, **headers))
        self.assertEqual(
            read["metadata"],
            {"name": "runner", "host_kind": "unknown", "metadata_revision": 1},
        )
        self.assertNotIn(token, json.dumps(read))
        self.assertEqual(self.client.get(path).status_code, 401)
        for extra in ({"runner_id": str(uuid4())}, {"path": "/private/workspace"}):
            self.assertEqual(
                self.client.post(
                    path,
                    data=json.dumps({**body, **extra}),
                    content_type="application/json",
                    **headers,
                ).status_code,
                400,
            )
        self.assertEqual(
            self.client.post(
                path,
                data=json.dumps({**body, "name": "/private/workspace"}),
                content_type="application/json",
                **headers,
            ).status_code,
            400,
        )
        updated = self.assert_json_success(
            self.client.post(
                path, data=json.dumps(body), content_type="application/json", **headers
            )
        )
        self.assertEqual(updated["metadata"]["metadata_revision"], 2)
        self.assertEqual(updated["metadata"]["host_kind"], "workstation")
        self.assertEqual(
            self.client.post(
                path, data=json.dumps(body), content_type="application/json", **headers
            ).status_code,
            400,
        )
        self.runner.refresh_from_db()
        setup.refresh_from_db()
        self.assertEqual(self.runner.policy_version, 1)
        self.assertEqual(self.runner.catalog_revision, 1)
        self.assertEqual(setup.configuration_digest, original_digest)
        self.assertEqual(agents.AgentAttempt.objects.filter(runner=self.runner).count(), 0)
        credential.revoked_at = now()
        credential.save(update_fields=["revoked_at"])
        self.assertEqual(self.client.get(path, **headers).status_code, 401)
        credential.revoked_at = None
        credential.save(update_fields=["revoked_at"])
        self.owner.is_active = False
        self.owner.save(update_fields=["is_active"])
        self.assertEqual(self.client.get(path, **headers).status_code, 401)
        self.assertEqual(
            self.client.post(
                path,
                data=json.dumps({**body, "expected_metadata_revision": 2}),
                content_type="application/json",
                **headers,
            ).status_code,
            401,
        )

    def test_provider_update_replaces_write_only_credential_and_checks_revision(self) -> None:
        created = self.post_agent(
            "providers",
            {
                "runner_id": str(self.runner.id),
                "name": "Provider",
                "base_url": "https://example.com",
                "model_id": "model",
                "allowed_models": ["model"],
                "context_window_tokens": 1000,
                "max_output_tokens": 100,
                "local_credential_ref": "first",
            },
        )
        provider_id = created["provider"]["id"]
        body = {
            "schema_version": 1,
            "expected_config_version": 1,
            "expected_metadata_revision": 1,
            "name": "Changed",
            "base_url": "https://example.com/v1",
            "model_id": "model",
            "allowed_models": ["model"],
            "context_window_tokens": 2000,
            "max_output_tokens": 200,
            "local_credential_ref": "second",
        }
        response = self.client_patch(
            f"/json/agent/providers/{provider_id}", {"payload": json.dumps(body)}
        )
        self.assert_json_success(response)
        provider = agents.AgentProvider.objects.get(id=provider_id)
        self.assertEqual(provider.config_version, 2)
        self.assertEqual(provider.local_credential_ref, "second")
        body["expected_config_version"] = 1
        response = self.client_patch(
            f"/json/agent/providers/{provider_id}", {"payload": json.dumps(body)}
        )
        self.assertEqual(response.status_code, 400)
        provider.refresh_from_db()
        self.assertEqual(provider.name, "Changed")

    def test_provider_edit_rejects_storage_overflow_without_mutation(self) -> None:
        created = self.post_agent(
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
        provider = agents.AgentProvider.objects.get(id=created["provider"]["id"])
        self.post_agent("profiles", {**self.profile_payload(), "provider_id": str(provider.id)})
        profile = agents.AgentProfile.objects.get(provider=provider)
        agents.AgentProfile.objects.filter(id=profile.id).update(readiness_state="ready")
        profile.refresh_from_db()
        grant = agents.AgentProbeGrant.objects.get(setup_operation__profile=profile)
        secret_count = agents.AgentSecret.objects.count()
        before = (
            provider.name,
            provider.config_version,
            provider.metadata_revision,
            provider.capability_report,
            profile.revision,
            profile.readiness_state,
            profile.readiness_configuration_digest,
            grant.revoked_at,
        )
        base = {
            "schema_version": 1,
            "expected_config_version": provider.config_version,
            "expected_metadata_revision": provider.metadata_revision,
            "name": provider.name,
            "base_url": provider.base_url,
            "model_id": provider.model_id,
            "allowed_models": provider.allowed_models,
            "context_window_tokens": provider.context_window_tokens,
            "max_output_tokens": provider.max_output_tokens,
            "api_mode": provider.api_mode,
            "network": provider.network_policy,
            "data_scope": provider.data_scope,
            "credential_replacement": "replacement-secret",
        }
        for overflow in (
            {"name": "n" * 201},
            {"model_id": "m" * 201, "allowed_models": ["m" * 201]},
            {"base_url": "https://example.com/" + "x" * 2030},
            {"context_window_tokens": 2147483648},
            {"max_output_tokens": 2147483648},
        ):
            response = self.client_patch(
                f"/json/agent/providers/{provider.id}",
                {"payload": json.dumps({**base, **overflow})},
            )
            self.assertEqual(response.status_code, 400)
            provider.refresh_from_db()
            profile.refresh_from_db()
            grant.refresh_from_db()
            self.assertEqual(agents.AgentSecret.objects.count(), secret_count)
            self.assertEqual(
                (
                    provider.name,
                    provider.config_version,
                    provider.metadata_revision,
                    provider.capability_report,
                    profile.revision,
                    profile.readiness_state,
                    profile.readiness_configuration_digest,
                    grant.revoked_at,
                ),
                before,
            )

    def test_profile_metadata_update_preserves_readiness_and_renames_bot(self) -> None:
        created = self.post_agent("profiles", self.profile_payload())
        profile = agents.AgentProfile.objects.get(id=created["profile"]["id"])
        setup = agents.AgentSetupOperation.objects.get(profile=profile)
        agents.AgentProfile.objects.filter(id=profile.id).update(
            readiness_state="ready",
            readiness_revision=profile.revision,
            readiness_configuration_digest=setup.configuration_digest,
        )
        response = self.client_patch(
            f"/json/agent/profiles/{profile.id}",
            {
                "payload": json.dumps(
                    {
                        "schema_version": 1,
                        "expected_metadata_revision": 1,
                        "expected_revision": 1,
                        "name": "Renamed agent",
                        "description": "Changed display text",
                        "adapter_id": profile.adapter_id,
                        "adapter_version": profile.adapter_version,
                        "mode": profile.mode,
                        "default_mode": profile.default_mode,
                        "sandbox_alias": "default",
                        "actions": profile.policy["actions"],
                        "budget": profile.budget,
                        "network": profile.policy["network"],
                    }
                )
            },
        )
        result = self.assert_json_success(response)
        self.assertEqual(result["profile"]["metadata_revision"], 2)
        profile.refresh_from_db()
        self.assertEqual(profile.readiness_state, "ready")
        self.assertEqual(profile.readiness_configuration_digest, setup.configuration_digest)
        self.assertEqual(profile.bot_user.full_name, "Renamed agent")
        response = self.client_patch(
            f"/json/agent/profiles/{profile.id}",
            {
                "payload": json.dumps(
                    {
                        "schema_version": 1,
                        "expected_metadata_revision": 1,
                        "expected_revision": 1,
                        "name": "Stale",
                        "adapter_id": profile.adapter_id,
                        "adapter_version": profile.adapter_version,
                        "budget": profile.budget,
                    }
                )
            },
        )
        self.assertEqual(response.status_code, 400)

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

    def profile_edit_body(self, profile: agents.AgentProfile) -> dict[str, object]:
        return {
            "schema_version": 1,
            "expected_metadata_revision": profile.metadata_revision,
            "expected_revision": profile.revision,
            "name": profile.name,
            "description": profile.description,
            "adapter_id": profile.adapter_id,
            "adapter_version": profile.adapter_version,
            "mode": profile.mode,
            "default_mode": profile.default_mode,
            "provider_id": str(profile.provider_id) if profile.provider_id else None,
            "repository_id": str(profile.default_repository_id)
            if profile.default_repository_id
            else None,
            "sandbox_alias": "default",
            "actions": profile.policy["actions"],
            "network": profile.policy["network"],
            "hard_cost_cap": profile.policy["hard_cost_cap"],
            "budget": profile.budget,
        }

    def test_profile_edit_is_atomic_and_noop_preserves_revisions(self) -> None:
        created = self.post_agent("profiles", self.profile_payload())
        profile = agents.AgentProfile.objects.get(id=created["profile"]["id"])
        original = (profile.name, profile.metadata_revision, profile.revision)
        body = self.profile_edit_body(profile)
        self.assert_json_success(
            self.client_patch(f"/json/agent/profiles/{profile.id}", {"payload": json.dumps(body)})
        )
        for rejected in [
            {**body, "name": "Must not persist", "sandbox_alias": "missing"},
            {
                **body,
                "name": "Must not persist",
                "network": {"targets": [{"hostname": "example.com", "port": 443}]},
            },
        ]:
            self.assertEqual(
                self.client_patch(
                    f"/json/agent/profiles/{profile.id}", {"payload": json.dumps(rejected)}
                ).status_code,
                400,
            )
        profile.refresh_from_db()
        profile.bot_user.refresh_from_db()
        self.assertEqual((profile.name, profile.metadata_revision, profile.revision), original)
        self.assertEqual(profile.bot_user.full_name, original[0])

    def test_identical_provider_edit_preserves_versions_and_probe_authority(self) -> None:
        created = self.post_agent(
            "providers",
            {
                "runner_id": str(self.runner.id),
                "name": "Unchanged",
                "base_url": "https://example.com/v1",
                "model_id": "model",
                "allowed_models": ["model"],
                "context_window_tokens": 1000,
                "max_output_tokens": 100,
                "local_credential_ref": "alias",
            },
        )
        provider = agents.AgentProvider.objects.get(id=created["provider"]["id"])
        self.post_agent("profiles", {**self.profile_payload(), "provider_id": str(provider.id)})
        profile = agents.AgentProfile.objects.get(provider=provider)
        agents.AgentProfile.objects.filter(id=profile.id).update(readiness_state="ready")
        profile.refresh_from_db()
        grant = agents.AgentProbeGrant.objects.get(setup_operation__profile=profile)
        body = {
            "schema_version": 1,
            "expected_config_version": provider.config_version,
            "expected_metadata_revision": provider.metadata_revision,
            "name": provider.name,
            "base_url": provider.base_url,
            "model_id": provider.model_id,
            "allowed_models": provider.allowed_models,
            "context_window_tokens": provider.context_window_tokens,
            "max_output_tokens": provider.max_output_tokens,
            "api_mode": provider.api_mode,
            "network": provider.network_policy,
            "data_scope": provider.data_scope,
        }
        self.assert_json_success(
            self.client_patch(f"/json/agent/providers/{provider.id}", {"payload": json.dumps(body)})
        )
        provider.refresh_from_db()
        profile.refresh_from_db()
        grant.refresh_from_db()
        self.assertEqual((provider.config_version, provider.metadata_revision), (1, 1))
        self.assertEqual(profile.readiness_state, "ready")
        self.assertIsNone(grant.revoked_at)

    def test_invalid_provider_edit_preserves_profile_readiness_and_grants(self) -> None:
        provider_data = self.post_agent(
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
        )["provider"]
        provider = agents.AgentProvider.objects.get(id=provider_data["id"])
        self.post_agent("profiles", {**self.profile_payload(), "provider_id": str(provider.id)})
        profile = agents.AgentProfile.objects.get(provider=provider)
        agents.AgentProfile.objects.filter(id=profile.id).update(readiness_state="ready")
        grant = agents.AgentProbeGrant.objects.get(setup_operation__profile=profile)
        profile.refresh_from_db()
        original = (
            provider.config_version,
            provider.metadata_revision,
            provider.capability_report,
            profile.revision,
            profile.readiness_state,
            profile.readiness_configuration_digest,
            grant.revoked_at,
        )
        secret_count = agents.AgentSecret.objects.count()
        body = {
            "schema_version": 1,
            "expected_config_version": provider.config_version,
            "expected_metadata_revision": provider.metadata_revision,
            "name": "Changed name",
            "base_url": provider.base_url,
            "model_id": provider.model_id,
            "allowed_models": provider.allowed_models,
            "context_window_tokens": 100,
            "max_output_tokens": 101,
            "api_mode": provider.api_mode,
            "network": provider.network_policy,
            "data_scope": provider.data_scope,
            "credential_replacement": "replacement-secret",
        }
        response = self.client_patch(
            f"/json/agent/providers/{provider.id}", {"payload": json.dumps(body)}
        )
        self.assertEqual(response.status_code, 400)
        provider.refresh_from_db()
        profile.refresh_from_db()
        grant.refresh_from_db()
        self.assertEqual(provider.name, "Provider")
        self.assertEqual(provider.context_window_tokens, 1000)
        self.assertEqual(provider.max_output_tokens, 100)
        self.assertEqual(agents.AgentSecret.objects.count(), secret_count)
        self.assertEqual(
            (
                provider.config_version,
                provider.metadata_revision,
                provider.capability_report,
                profile.revision,
                profile.readiness_state,
                profile.readiness_configuration_digest,
                grant.revoked_at,
            ),
            original,
        )

    def test_valid_provider_edit_resets_capability_version_and_allows_new_probe(self) -> None:
        provider_data = self.post_agent(
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
        )["provider"]
        provider = agents.AgentProvider.objects.get(id=provider_data["id"])
        response = self.client_patch(
            f"/json/agent/providers/{provider.id}",
            {
                "payload": json.dumps(
                    {
                        "schema_version": 1,
                        "expected_config_version": 1,
                        "expected_metadata_revision": 1,
                        "name": provider.name,
                        "base_url": provider.base_url,
                        "model_id": provider.model_id,
                        "allowed_models": provider.allowed_models,
                        "context_window_tokens": 2000,
                        "max_output_tokens": 100,
                        "api_mode": provider.api_mode,
                        "network": provider.network_policy,
                        "data_scope": provider.data_scope,
                    }
                )
            },
        )
        self.assert_json_success(response)
        provider.refresh_from_db()
        self.assertEqual(provider.config_version, 2)
        self.assertEqual(provider.capability_report, {"config_version": 2})
        probe = self.post_agent(
            f"providers/{provider.id}/probe",
            {"expected_revision": 2, "retry_key": str(uuid4())},
        )
        setup = agents.AgentSetupOperation.objects.get(id=probe["setup_id"])
        self.assertEqual(setup.provider_config_version, 2)
        self.assertEqual(setup.descriptor["provider"]["capability_report"]["config_version"], 2)

    def test_resource_directory_partial_sharing_filters_and_safe_details(self) -> None:
        provider_data = self.post_agent(
            "providers",
            {
                "runner_id": str(self.runner.id),
                "name": "Model connection",
                "base_url": "https://private.example/v1",
                "model_id": "model",
                "allowed_models": ["model"],
                "context_window_tokens": 1000,
                "max_output_tokens": 100,
                "local_credential_ref": "private-local-alias",
            },
        )
        provider = agents.AgentProvider.objects.get(id=provider_data["provider"]["id"])
        created = self.post_agent(
            "profiles", {**self.profile_payload(), "provider_id": str(provider.id)}
        )
        profile = agents.AgentProfile.objects.get(id=created["profile"]["id"])
        member = self.example_user("othello")
        admin = self.example_user("iago")
        admin.role = UserProfile.ROLE_REALM_ADMINISTRATOR
        admin.save(update_fields=["role"])
        self.post_agent(
            "grants",
            {
                "target_kind": "profile",
                "target_id": str(profile.id),
                "expected_revision": 1,
                "principal_user_id": member.id,
                "actions": ["profile.use"],
            },
        )
        self.login_user(admin)
        self.assertEqual(
            self.assert_json_success(self.client_get("/json/agent/profiles"))["count"], 0
        )
        self.login_user(member)
        partial = self.assert_json_success(
            self.client_get(
                "/json/agent/profiles",
                {
                    "ownership": "shared",
                    "access": "partial",
                    "search": "Model",
                },
            )
        )
        self.assertEqual(partial["count"], 0)
        partial = self.assert_json_success(
            self.client_get(
                "/json/agent/profiles",
                {
                    "ownership": "shared",
                    "access": "partial",
                    "search": "Agent",
                },
            )
        )
        self.assertEqual(partial["count"], 1)
        self.assertFalse(partial["profiles"][0]["access"]["complete"])
        self.assertIsNone(partial["profiles"][0]["runner_id"])
        self.assertIsNone(partial["profiles"][0]["configuration"])
        self.assertEqual(
            self.assert_json_success(
                self.client_get("/json/agent/profiles", {"ownership": "mine"})
            )["count"],
            0,
        )
        self.assertEqual(
            self.client_get("/json/agent/profiles", {"search": "x" * 101}).status_code, 400
        )
        self.login_user(self.owner)
        self.post_agent(
            "grants",
            {
                "target_kind": "provider",
                "target_id": str(provider.id),
                "expected_revision": 1,
                "principal_user_id": member.id,
                "actions": ["provider.use"],
            },
        )
        self.login_user(member)
        shared_provider = self.assert_json_success(
            self.client_get(f"/json/agent/providers/{provider.id}")
        )["provider"]
        self.assertEqual(shared_provider["model_id"], "model")
        self.assertNotIn("base_url", shared_provider)
        self.assertNotIn("local_credential_ref", shared_provider)
        self.assertNotIn("private-local-alias", json.dumps(shared_provider))
        self.assertNotIn("private.example", json.dumps(shared_provider))
        self.owner.is_active = False
        self.owner.save(update_fields=["is_active"])
        self.assertEqual(
            self.assert_json_success(self.client_get("/json/agent/profiles"))["count"], 0
        )

    def test_scoped_grants_project_complete_and_conflicting_channel_access(self) -> None:
        provider = self.post_agent(
            "providers",
            {
                "runner_id": str(self.runner.id),
                "name": "Scoped model",
                "base_url": "https://example.com",
                "model_id": "model",
                "allowed_models": ["model"],
                "context_window_tokens": 1000,
                "max_output_tokens": 100,
                "local_credential_ref": "alias",
            },
        )
        repository = self.post_agent(
            "repositories",
            {
                "runner_id": str(self.runner.id),
                "workspace_alias": "scoped",
                "allowed_refs": ["main"],
            },
        )
        profile = self.post_agent(
            "profiles",
            {
                **self.profile_payload(),
                "provider_id": provider["provider"]["id"],
                "repository_id": repository["repository"]["id"],
            },
        )
        member = self.example_user("othello")
        bot = agents.AgentProfile.objects.get(id=profile["profile"]["id"]).bot_user
        first = get_stream("Denmark", self.owner.realm)
        second = self.make_stream("agent-second")
        for stream in (first, second):
            self.subscribe(member, stream.name)
            self.subscribe(bot, stream.name)
        targets = [
            ("profile", profile["profile"]["id"], "profile.use"),
            ("runner", str(self.runner.id), "runner.use"),
            ("provider", provider["provider"]["id"], "provider.use"),
            ("repository", repository["repository"]["id"], "repository.read"),
        ]
        for kind, target_id, action in targets:
            self.post_agent(
                "grants",
                {
                    "target_kind": kind,
                    "target_id": target_id,
                    "expected_revision": 1,
                    "principal_user_id": member.id,
                    "actions": [action],
                    "scope": {"kind": "stream", "stream_id": first.id},
                },
            )
        self.login_user(member)
        complete = self.assert_json_success(
            self.client_get("/json/agent/profiles", {"access": "complete"})
        )
        self.assertEqual(complete["count"], 1)
        self.assertTrue(complete["profiles"][0]["access"]["complete"])
        self.assertEqual(complete["profiles"][0]["runner_id"], str(self.runner.id))
        for kind in ("runners", "providers", "repositories"):
            self.assertEqual(
                self.assert_json_success(self.client_get(f"/json/agent/{kind}"))["count"], 1
            )
        self.login_user(self.owner)
        agents.AgentGrant.objects.filter(
            target_kind="repository", repository_id=repository["repository"]["id"]
        ).update(scope={"kind": "stream", "stream_id": second.id})
        self.login_user(member)
        self.assertEqual(
            self.assert_json_success(
                self.client_get("/json/agent/profiles", {"access": "complete"})
            )["count"],
            0,
        )
        partial = self.assert_json_success(
            self.client_get("/json/agent/profiles", {"access": "partial"})
        )
        self.assertEqual(partial["count"], 1)
        self.assertFalse(partial["profiles"][0]["access"]["complete"])

    def test_profile_owner_history_marks_lost_runner_access_incomplete(self) -> None:
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
        before = self.assert_json_success(
            self.client_get("/json/agent/profiles", {"access": "complete"})
        )
        self.assertEqual(before["count"], 1)
        agents.AgentGrant.objects.filter(runner=self.runner, principal_user=member).update(
            revoked_at=now()
        )
        self.assertEqual(
            self.assert_json_success(
                self.client_get("/json/agent/profiles", {"access": "complete"})
            )["count"],
            0,
        )
        partial = self.assert_json_success(
            self.client_get("/json/agent/profiles", {"access": "partial"})
        )
        self.assertEqual(partial["count"], 1)
        self.assertFalse(partial["profiles"][0]["access"]["complete"])
        self.assertIsNone(partial["profiles"][0]["runner_id"])

    def test_shared_profile_manager_can_edit_without_private_scope_disclosure(self) -> None:
        created = self.post_agent("profiles", self.profile_payload())
        profile = agents.AgentProfile.objects.get(id=created["profile"]["id"])
        member = self.example_user("othello")
        for kind, target_id, action in [
            ("profile", str(profile.id), "profile.manage"),
            ("runner", str(self.runner.id), "runner.use"),
        ]:
            self.post_agent(
                "grants",
                {
                    "target_kind": kind,
                    "target_id": target_id,
                    "expected_revision": 1,
                    "principal_user_id": member.id,
                    "actions": [action],
                },
            )
        self.login_user(member)
        detail = self.assert_json_success(self.client_get(f"/json/agent/profiles/{profile.id}"))
        self.assertIn("edit", detail["profile"]["allowed_actions"])
        configuration = detail["profile"]["configuration"]
        self.assertIsNotNone(configuration)
        self.assertIsNone(configuration["policy"]["scope"])
        self.assertTrue(configuration["scope_restricted"])
        original_scope = profile.policy["scope"]
        body = {**self.profile_edit_body(profile), "name": "Shared manager rename"}
        self.assert_json_success(
            self.client_patch(f"/json/agent/profiles/{profile.id}", {"payload": json.dumps(body)})
        )
        profile.refresh_from_db()
        self.assertEqual(profile.policy["scope"], original_scope)
        self.assertEqual(profile.name, "Shared manager rename")

    def test_shared_provider_profile_edit_retains_hidden_network_without_disclosure(self) -> None:
        private_host = "private-provider.example"
        network = {"targets": [{"hostname": private_host, "port": 443}]}
        provider = self.post_agent(
            "providers",
            {
                "runner_id": str(self.runner.id),
                "name": "Owner provider",
                "base_url": f"https://{private_host}",
                "model_id": "model",
                "allowed_models": ["model"],
                "context_window_tokens": 1000,
                "max_output_tokens": 100,
                "local_credential_ref": "private-alias",
                "network": network,
            },
        )["provider"]
        member = self.example_user("othello")
        for kind, target_id, action in [
            ("runner", str(self.runner.id), "runner.use"),
            ("provider", provider["id"], "provider.use"),
        ]:
            self.post_agent(
                "grants",
                {
                    "target_kind": kind,
                    "target_id": target_id,
                    "expected_revision": 1,
                    "principal_user_id": member.id,
                    "actions": [action],
                },
            )
        self.login_user(member)
        created = self.post_agent(
            "profiles",
            {
                **self.profile_payload(),
                "provider_id": provider["id"],
                "network": network,
            },
        )
        self.assertNotIn(private_host, json.dumps(created))
        profile = agents.AgentProfile.objects.get(id=created["profile"]["id"])
        detail = self.assert_json_success(self.client_get(f"/json/agent/profiles/{profile.id}"))
        self.assertNotIn(private_host, json.dumps(detail))
        configuration = detail["profile"]["configuration"]
        self.assertTrue(configuration["network_retained"])
        self.assertIsNone(configuration["policy"]["network"])
        self.assertEqual(configuration["policy"]["sandbox_alias"], "default")
        self.assertNotIn("sandbox", configuration["policy"])
        self.assertNotIn("grant_ids", configuration["policy"])
        before = (
            profile.revision,
            profile.policy,
            profile.budget,
            profile.readiness_configuration_digest,
        )
        body = {
            **self.profile_edit_body(profile),
            "name": "Member profile",
            "retain_network": True,
        }
        del body["network"]
        edited = self.assert_json_success(
            self.client_patch(f"/json/agent/profiles/{profile.id}", {"payload": json.dumps(body)})
        )
        self.assertNotIn(private_host, json.dumps(edited))
        profile.refresh_from_db()
        self.assertEqual(
            (
                profile.revision,
                profile.policy,
                profile.budget,
                profile.readiness_configuration_digest,
            ),
            before,
        )
        self.assertEqual(profile.name, "Member profile")
        for unsafe in (
            {**body, "retain_network": False},
            {**body, "network": network},
            {**body, "retain_network": False, "network": network},
        ):
            unsafe["expected_metadata_revision"] = profile.metadata_revision
            self.assertEqual(
                self.client_patch(
                    f"/json/agent/profiles/{profile.id}", {"payload": json.dumps(unsafe)}
                ).status_code,
                400,
            )

    def test_shared_provider_network_snapshot_create_replace_and_rejections(self) -> None:
        def post(path: str, body: dict[str, object]) -> dict[str, object]:
            response = self.client_post(
                "/json/agent/" + path,
                {"payload": json.dumps({"schema_version": 1, **body})},
            )
            return self.assert_json_success(response)

        private_host = "private-provider.example"
        network = {"targets": [{"hostname": private_host, "port": 443}]}
        provider_id = post(
            "providers",
            {
                "runner_id": str(self.runner.id),
                "name": "Private provider",
                "base_url": f"https://{private_host}",
                "model_id": "model",
                "allowed_models": ["model"],
                "context_window_tokens": 1000,
                "max_output_tokens": 100,
                "local_credential_ref": "private-alias",
                "network": network,
            },
        )["provider"]["id"]
        provider = agents.AgentProvider.objects.get(id=provider_id)
        member = self.example_user("othello")
        for kind, target_id, action in [
            ("runner", str(self.runner.id), "runner.use"),
            ("provider", provider_id, "provider.use"),
        ]:
            post(
                "grants",
                {
                    "target_kind": kind,
                    "target_id": target_id,
                    "expected_revision": 1,
                    "principal_user_id": member.id,
                    "actions": [action],
                },
            )
        self.login_user(member)
        browser = self.assert_json_success(self.client_get("/json/agent/providers"))
        shared = browser["providers"][0]
        self.assertEqual(shared["config_version"], provider.config_version)
        self.assertNotIn(private_host, json.dumps(browser))
        self.assertNotIn("private-alias", json.dumps(browser))
        self.assertNotIn("network", shared)
        self.assertNotIn("metadata_revision", shared)
        self.assertNotIn("credential", shared)
        self.assertEqual(
            self.assert_json_success(self.client_get(f"/json/agent/providers/{provider_id}"))[
                "provider"
            ],
            shared,
        )
        request = {
            **self.profile_payload(),
            "provider_id": provider_id,
            "provider_network_version": shared["config_version"],
        }

        def counts() -> tuple[int, int, int, int, int]:
            return (
                agents.AgentProfile.objects.count(),
                agents.AgentSetupOperation.objects.count(),
                agents.AgentProbeGrant.objects.count(),
                agents.AgentOutbox.objects.count(),
                UserProfile.objects.filter(is_bot=True).count(),
            )

        before = counts()
        for rejected in [
            {**request, "provider_network_version": 0},
            {**request, "provider_network_version": 2},
            {**request, "network": network},
            {**request, "retain_network": False},
            {**request, "provider_id": None},
        ]:
            response = self.client_post(
                "/json/agent/profiles", {"payload": json.dumps({"schema_version": 1, **rejected})}
            )
            self.assertEqual(response.status_code, 400)
            self.assertEqual(counts(), before)
        created = post("profiles", request)
        self.assertNotIn(private_host, json.dumps(created))
        self.assertNotIn("private-alias", json.dumps(created))
        profile = agents.AgentProfile.objects.get(id=created["profile"]["id"])
        self.assertNotIn(
            private_host,
            json.dumps(
                self.assert_json_success(self.client_get(f"/json/agent/profiles/{profile.id}"))
            ),
        )
        self.assertEqual(profile.policy["network"], provider.network_policy)
        setup = agents.AgentSetupOperation.objects.get(id=created["setup_id"])
        self.assertEqual(setup.descriptor["policy"]["network"], provider.network_policy)
        after = counts()
        self.assertEqual(post("profiles", request)["profile"]["id"], str(profile.id))
        self.assertEqual(counts(), after)
        recovered = self.assert_json_success(
            self.client_get(
                "/json/agent/profiles/recover", {"idempotency_key": request["idempotency_key"]}
            )
        )
        self.assertEqual(recovered["profile"]["id"], str(profile.id))
        self.assertNotIn(private_host, json.dumps(recovered))
        self.assertEqual(counts(), after)

        plain = post("profiles", self.profile_payload())
        replacement = agents.AgentProfile.objects.get(id=plain["profile"]["id"])
        prior_grant = agents.AgentProbeGrant.objects.get(setup_operation__profile=replacement)
        agents.AgentProfile.objects.filter(id=replacement.id).update(readiness_state="ready")
        edit = self.profile_edit_body(replacement)
        edit["provider_id"] = provider_id
        edit["provider_network_version"] = provider.config_version
        del edit["network"]
        before_replace = counts()
        for rejected in [
            {**edit, "provider_network_version": 2},
            {**edit, "network": network},
            {**edit, "retain_network": False},
        ]:
            response = self.client_patch(
                f"/json/agent/profiles/{replacement.id}", {"payload": json.dumps(rejected)}
            )
            self.assertEqual(response.status_code, 400)
            replacement.refresh_from_db()
            self.assertIsNone(replacement.provider_id)
            self.assertEqual(replacement.revision, 1)
            self.assertEqual(counts(), before_replace)
        updated = self.assert_json_success(
            self.client_patch(
                f"/json/agent/profiles/{replacement.id}",
                {"payload": json.dumps(edit)},
            )
        )
        self.assertNotIn(private_host, json.dumps(updated))
        replacement.refresh_from_db()
        self.assertEqual(replacement.policy["network"], provider.network_policy)
        self.assertEqual(replacement.provider_id, provider.id)
        self.assertEqual(replacement.readiness_state, "unchecked")
        prior_grant.refresh_from_db()
        self.assertIsNotNone(prior_grant.revoked_at)
        replacement_setup = post(
            f"profiles/{replacement.id}/readiness",
            {"expected_revision": replacement.revision, "retry_key": str(uuid4())},
        )
        self.assertEqual(
            agents.AgentSetupOperation.objects.get(id=replacement_setup["setup_id"]).descriptor[
                "policy"
            ]["network"],
            provider.network_policy,
        )

        self.login_user(self.owner)
        provider_edit = {
            "schema_version": 1,
            "expected_config_version": provider.config_version,
            "expected_metadata_revision": provider.metadata_revision,
            "name": provider.name,
            "base_url": provider.base_url,
            "model_id": provider.model_id,
            "allowed_models": provider.allowed_models,
            "context_window_tokens": provider.context_window_tokens,
            "max_output_tokens": provider.max_output_tokens + 1,
            "api_mode": provider.api_mode,
            "network": provider.network_policy,
            "data_scope": provider.data_scope,
        }
        self.assert_json_success(
            self.client_patch(
                f"/json/agent/providers/{provider.id}",
                {"payload": json.dumps(provider_edit)},
            )
        )
        provider.refresh_from_db()
        self.assertEqual(provider.config_version, 2)
        self.login_user(member)
        stale = {**request, "idempotency_key": str(uuid4())}
        unchanged = counts()
        self.assertEqual(
            self.client_post(
                "/json/agent/profiles",
                {"payload": json.dumps({"schema_version": 1, **stale})},
            ).status_code,
            400,
        )
        self.assertEqual(counts(), unchanged)

        foreign_owner = self.lear_user("king")
        foreign_runner = agents.AgentRunner.objects.create(
            realm=foreign_owner.realm,
            owner=foreign_owner,
            name="Other realm runner",
            fingerprint="b" * 64,
            catalog_report=self.runner.catalog_report,
        )
        foreign_provider = agents.AgentProvider.objects.create(
            realm=foreign_owner.realm,
            owner=foreign_owner,
            runner=foreign_runner,
            name="Other realm provider",
            base_url="https://elsewhere.example",
            model_id="model",
            allowed_models=["model"],
            context_window_tokens=1000,
            max_output_tokens=100,
            local_credential_ref="foreign-alias",
            data_scope=["synthetic"],
            network_policy=provider.network_policy,
            capability_report={"config_version": 1},
        )
        cross_realm = {
            **request,
            "idempotency_key": str(uuid4()),
            "provider_id": str(foreign_provider.id),
        }
        self.assertEqual(
            self.client_post(
                "/json/agent/profiles",
                {"payload": json.dumps({"schema_version": 1, **cross_realm})},
            ).status_code,
            400,
        )
        self.assertEqual(counts(), unchanged)
        stale_edit = {**self.profile_edit_body(replacement), "provider_network_version": 1}
        del stale_edit["network"]
        retained_policy = replacement.policy
        self.assertEqual(
            self.client_patch(
                f"/json/agent/profiles/{replacement.id}",
                {"payload": json.dumps(stale_edit)},
            ).status_code,
            400,
        )
        replacement.refresh_from_db()
        self.assertEqual(replacement.revision, 2)
        self.assertEqual(replacement.policy, retained_policy)
        self.assertEqual(counts(), unchanged)

        agents.AgentGrant.objects.filter(provider=provider, principal_user=member).update(
            revoked_at=now()
        )
        revoked = {**request, "idempotency_key": str(uuid4()), "provider_network_version": 2}
        self.assertEqual(
            self.client_post(
                "/json/agent/profiles",
                {"payload": json.dumps({"schema_version": 1, **revoked})},
            ).status_code,
            400,
        )
        self.assertEqual(counts(), unchanged)

        ungranted = self.example_user("iago")
        self.login_user(self.owner)
        post(
            "grants",
            {
                "target_kind": "runner",
                "target_id": str(self.runner.id),
                "expected_revision": 1,
                "principal_user_id": ungranted.id,
                "actions": ["runner.use"],
            },
        )
        self.login_user(ungranted)
        missing_grant = {
            **request,
            "idempotency_key": str(uuid4()),
            "provider_network_version": 2,
        }
        self.assertEqual(
            self.client_post(
                "/json/agent/profiles",
                {"payload": json.dumps({"schema_version": 1, **missing_grant})},
            ).status_code,
            400,
        )
        self.assertEqual(counts(), unchanged)

    def test_setup_recovery_uses_owner_and_existing_retry_key(self) -> None:
        retry_key = str(uuid4())
        created = self.post_agent(
            "profiles", {**self.profile_payload(), "idempotency_key": retry_key}
        )
        before = agents.AgentProbeGrant.objects.count()
        recovered = self.assert_json_success(
            self.client_get("/json/agent/profiles/recover", {"idempotency_key": retry_key})
        )
        self.assertEqual(recovered["profile"]["id"], created["profile"]["id"])
        self.assertEqual(recovered["setup"]["id"], created["setup_id"])
        self.assertEqual(agents.AgentProbeGrant.objects.count(), before)
        member = self.example_user("othello")
        self.login_user(member)
        self.assertEqual(
            self.client_get(
                "/json/agent/profiles/recover", {"idempotency_key": retry_key}
            ).status_code,
            400,
        )
        self.login_user(self.owner)
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
        other = self.post_agent(
            "profiles", {**self.profile_payload(), "idempotency_key": retry_key}
        )
        self.assertNotEqual(other["profile"]["id"], created["profile"]["id"])
        self.assertEqual(
            self.assert_json_success(
                self.client_get("/json/agent/profiles/recover", {"idempotency_key": retry_key})
            )["setup"]["id"],
            other["setup_id"],
        )

    def test_grant_and_attachment_summary_respect_visibility(self) -> None:
        created = self.post_agent("profiles", self.profile_payload())
        profile = agents.AgentProfile.objects.get(id=created["profile"]["id"])
        stream = get_stream("Denmark", self.owner.realm)
        member = self.example_user("othello")
        self.post_agent(
            "grants",
            {
                "target_kind": "profile",
                "target_id": str(profile.id),
                "expected_revision": 1,
                "principal_user_id": member.id,
                "actions": ["profile.use"],
            },
        )
        grants = self.assert_json_success(
            self.client_get(
                "/json/agent/grants",
                {"target_kind": "profile", "target_id": str(profile.id), "limit": 1},
            )
        )
        self.assertEqual(grants["count"], 1)
        self.assertEqual(grants["grants"][0]["principal"]["user_id"], member.id)
        self.post_agent(
            f"profiles/{profile.id}/attach-channel",
            {"expected_revision": 1, "stream_id": stream.id},
        )
        detail = self.assert_json_success(self.client_get(f"/json/agent/profiles/{profile.id}"))
        self.assertEqual(detail["attachments"][0]["stream_id"], stream.id)
        channel = self.assert_json_success(
            self.client_get(f"/json/agent/channels/{stream.id}/attachments")
        )
        self.assertEqual(channel["attachments"][0]["profile"]["id"], str(profile.id))
        self.login_user(member)
        member_grants = self.assert_json_success(
            self.client_get(
                "/json/agent/grants", {"target_kind": "profile", "target_id": str(profile.id)}
            )
        )
        self.assertEqual(member_grants["grants"][0]["principal"]["kind"], "current_user")
        self.assertEqual(member_grants["grants"][0]["allowed_actions"], [])
        self.assertIsNone(
            self.assert_json_success(self.client_get(f"/json/agent/profiles/{profile.id}"))["setup"]
        )

    def test_runner_stale_heartbeat_projects_unknown_observation(self) -> None:
        self.runner.status = "online"
        self.runner.last_heartbeat_at = now() - timedelta(minutes=3)
        self.runner.save(update_fields=["status", "last_heartbeat_at"])
        row = self.assert_json_success(self.client_get("/json/agent/runners"))["runners"][0]
        self.assertEqual(row["status"], "unknown")
        self.assertIsNotNone(row["last_heartbeat_at"])

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
        self.login_user(member)
        self.assertEqual(
            self.assert_json_success(self.client_get("/json/agent/runners"))["count"], 0
        )
        self.login_user(self.owner)
        grant = self.post_agent(
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
        runner_data = self.assert_json_success(self.client_get("/json/agent/runners"))["runners"][0]
        self.assertIsNone(runner_data["catalog"])
        self.assertEqual(
            runner_data["catalog_summary"],
            {
                "revision": 1,
                "reported_at": None,
                "adapters": [{"id": "acp", "version": "1", "auth_state": "ready"}],
                "sandboxes": [{"alias": "default"}],
            },
        )
        self.assertNotIn("image_digest", json.dumps(runner_data["catalog_summary"]))
        created = self.post_agent("profiles", self.profile_payload())
        profile = agents.AgentProfile.objects.get(runner=self.runner)
        self.assertEqual(profile.owner_id, member.id)
        self.assertEqual(
            self.assert_json_success(self.client_get("/json/agent/profiles"))["count"], 1
        )
        self.assertEqual(self.client_get(f"/json/agent/profiles/{profile.id}").status_code, 200)
        self.login_user(self.owner)
        self.post_agent(f"grants/{grant['grant']['id']}/revoke", {"expected_revision": 1})
        self.login_user(member)
        self.assertEqual(
            self.assert_json_success(self.client_get("/json/agent/runners"))["count"], 0
        )
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

    def test_shared_profile_does_not_disclose_owner_controls(self) -> None:
        self.post_agent("profiles", self.profile_payload())
        profile = agents.AgentProfile.objects.get(runner=self.runner)
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
        self.post_agent(
            "grants",
            {
                "target_kind": "profile",
                "target_id": str(profile.id),
                "expected_revision": 1,
                "principal_user_id": member.id,
                "actions": ["profile.use"],
            },
        )
        self.login_user(member)
        detail = self.assert_json_success(self.client_get(f"/json/agent/profiles/{profile.id}"))
        self.assertEqual(detail["profile"]["allowed_actions"], [])

    def test_share_and_unshare_endpoints_round_trip(self) -> None:
        self.post_agent("profiles", self.profile_payload())
        profile = agents.AgentProfile.objects.get(runner=self.runner)
        member = self.example_user("othello")
        result = self.post_agent(f"profiles/{profile.id}/share", {"principal_user_id": member.id})
        self.assertEqual(result["skipped"], [])
        shared_kinds = {grant["target_kind"] for grant in result["grants"]}
        self.assertEqual(shared_kinds, {"profile", "runner"})
        detail = self.assert_json_success(self.client_get(f"/json/agent/profiles/{profile.id}"))
        self.assertEqual(
            detail["profile"]["shared_with"],
            [{"principal_kind": "user", "principal_id": member.id, "complete": True}],
        )
        # A repeat share creates no duplicate grant.
        shared = agents.AgentGrant.objects.filter(owner=self.owner, principal_user=member)
        before = shared.count()
        self.post_agent(f"profiles/{profile.id}/share", {"principal_user_id": member.id})
        self.assertEqual(shared.count(), before)
        self.post_agent(f"profiles/{profile.id}/unshare", {"principal_user_id": member.id})
        self.assertFalse(shared.filter(revoked_at__isnull=True).exists())
        detail = self.assert_json_success(self.client_get(f"/json/agent/profiles/{profile.id}"))
        self.assertEqual(detail["profile"]["shared_with"], [])

    def test_share_requires_profile_ownership_over_http(self) -> None:
        member = self.example_user("othello")
        profile = agents.AgentProfile.objects.create(
            realm=self.owner.realm,
            owner=member,
            runner=self.runner,
            bot_user=self.example_user("default_bot"),
            name="profile",
            adapter_id="acp",
            adapter_version="1",
        )
        response = self.client_post(
            f"/json/agent/profiles/{profile.id}/share",
            {"payload": json.dumps({"schema_version": 1, "principal_user_id": self.owner.id})},
        )
        self.assert_json_error(response, "Agent request rejected.")

    def test_job_check_summary_uses_frozen_attempt_descriptor(self) -> None:
        from zerver.actions import agent_jobs
        from zerver.actions.agents import enable_profile, record_readiness
        from zerver.models import Message

        repository_response = self.post_agent(
            "repositories",
            {
                "runner_id": str(self.runner.id),
                "workspace_alias": "checks",
                "canonical_origin": None,
                "allowed_refs": ["main"],
                "required_checks": [{"id": "check-a", "argv": ["true"]}],
            },
        )
        repository = agents.AgentRepository.objects.get(id=repository_response["repository"]["id"])
        created = self.post_agent(
            "profiles",
            {
                **self.profile_payload(),
                "default_mode": "code",
                "repository_id": str(repository.id),
                "actions": ["context.read", "repository.read", "repository.edit", "checks.run"],
            },
        )
        profile = agents.AgentProfile.objects.get(id=created["profile"]["id"])
        setup = agents.AgentSetupOperation.objects.get(profile=profile)
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
                "capabilities": {
                    "chat_ready": True,
                    "code_ready": True,
                    "tool_calling": "passed",
                    "sandbox": "passed",
                    "config_version": 1,
                },
            },
        )
        profile.refresh_from_db()
        enable_profile(self.owner, profile, expected_revision=profile.revision)
        message = Message.objects.get(
            id=self.send_group_direct_message(
                self.owner, [profile.bot_user, self.example_user("iago")], "Code task"
            )
        )
        job = agent_jobs.create_job(
            self.owner,
            profile=profile,
            source=message,
            request="Check it",
            idempotency_key=uuid4(),
            job_kind="code",
            delivery_target="patch",
            repository=repository,
            base_ref="main",
        )
        agent_jobs.claim_work(self.runner, claim_key=uuid4())
        attempt = agents.AgentAttempt.objects.get(job=job)
        repository.required_checks = [
            {"id": "check-b", "argv": ["false"], "cwd": ".", "timeout_seconds": 120}
        ]
        repository.save(update_fields=["required_checks"])
        detail = self.assert_json_success(self.client_get(f"/json/agent/jobs/{job.id}"))
        self.assertEqual(detail["required_checks"], [{"check_id": "check-a", "outcome": "missing"}])
        self.assertEqual(detail["attempts"][0]["id"], str(attempt.id))
        attempt.tree_hash = "b" * 40
        attempt.save(update_fields=["tree_hash"])
        job.status = "waiting_for_approval"
        job.save(update_fields=["status"])
        operation = agents.AgentOperation.objects.create(
            realm=self.owner.realm,
            attempt=attempt,
            operation_id=uuid4(),
            tool_class="checks.run",
            argument_digest="c" * 64,
            arguments={
                "action": "checks.run",
                "repository_id": str(repository.id),
                "check_ids": ["check-a"],
                "tree_hash": attempt.tree_hash,
            },
            scope_binding={
                "attempt_id": str(attempt.id),
                "lease_epoch": attempt.lease_epoch,
                "policy_version": profile.policy_version,
                "tree_hash": attempt.tree_hash,
            },
            status="proposed",
        )
        approval = agents.AgentApproval.objects.create(
            realm=self.owner.realm,
            job=job,
            attempt=attempt,
            operation=operation,
            operation_hash=operation.argument_digest,
            policy_version=profile.policy_version,
            tree_hash=attempt.tree_hash,
            arguments=operation.arguments,
            expires_at=now() + timedelta(minutes=5),
        )
        detail = self.assert_json_success(self.client_get(f"/json/agent/jobs/{job.id}"))
        self.assertTrue(detail["operations"][0]["can_decide"])
        self.assertEqual(detail["operations"][0]["nonce"], str(approval.nonce))
        approval.expires_at = now() - timedelta(seconds=1)
        approval.save(update_fields=["expires_at"])
        detail = self.assert_json_success(self.client_get(f"/json/agent/jobs/{job.id}"))
        self.assertFalse(detail["operations"][0]["can_decide"])
        self.assertIsNone(detail["operations"][0]["nonce"])
        agents.AgentOperation.objects.create(
            realm=self.owner.realm,
            attempt=attempt,
            operation_id=uuid4(),
            tool_class="checks.run",
            argument_digest="d" * 64,
            arguments=operation.arguments,
            scope_binding=operation.scope_binding,
            status="authorized",
        )
        page = self.assert_json_success(
            self.client_get(f"/json/agent/jobs/{job.id}", {"operation_limit": 1})
        )
        self.assertEqual(len(page["operations"]), 1)
        self.assertTrue(page["operations_cursor"]["truncated"])

        # Artifact pages must retain attempt identity after a resume.
        previous_artifact = agents.AgentArtifact.objects.create(
            realm=self.owner.realm,
            attempt=attempt,
            kind="diff",
            checksum="a" * 64,
            size=1,
            storage_ref=f"artifacts/{uuid4()}",
            filename="first.patch",
            expires_at=now() + timedelta(days=1),
        )
        attempt.active = False
        attempt.ended_at = now()
        attempt.process_state = "stopped"
        attempt.save(update_fields=["active", "ended_at", "process_state"])
        resumed = agents.AgentAttempt.objects.create(
            realm=self.owner.realm,
            job=job,
            runner=self.runner,
            number=attempt.number + 1,
            lease_epoch=attempt.lease_epoch + 1,
            lease_expires_at=now() + timedelta(minutes=5),
            descriptor=attempt.descriptor,
            descriptor_digest=attempt.descriptor_digest,
            process_state="active",
            tree_hash="e" * 40,
        )
        current_artifact = agents.AgentArtifact.objects.create(
            realm=self.owner.realm,
            attempt=resumed,
            kind="diff",
            checksum="b" * 64,
            size=1,
            storage_ref=f"artifacts/{uuid4()}",
            filename="second.patch",
            expires_at=now() + timedelta(days=1),
        )
        first_page = self.assert_json_success(
            self.client_get(f"/json/agent/jobs/{job.id}", {"artifact_limit": 1})
        )
        second_page = self.assert_json_success(
            self.client_get(
                f"/json/agent/jobs/{job.id}", {"artifact_offset": 1, "artifact_limit": 1}
            )
        )
        self.assertEqual(first_page["artifacts"][0]["id"], str(previous_artifact.id))
        self.assertEqual(first_page["artifacts"][0]["attempt_id"], str(attempt.id))
        self.assertEqual(second_page["artifacts"][0]["id"], str(current_artifact.id))
        self.assertEqual(second_page["artifacts"][0]["attempt_id"], str(resumed.id))
        self.assertTrue(first_page["artifacts_cursor"]["truncated"])

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
            data = self.assert_json_success(self.client_get("/json/agent/profiles", {"limit": 1}))
            self.assertEqual(data["count"], 1)
            self.assertEqual(data["profiles"][0]["access"]["complete"], index == 3)
            self.assertEqual(
                self.client_get(f"/json/agent/profiles/{profile.id}").status_code,
                200,
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
    def test_configuration_edits_serialize_with_setup_claim_and_result(self) -> None:
        from concurrent.futures import ThreadPoolExecutor
        from threading import Event

        from django.db import connection, connections

        from zerver.actions import agents as actions
        from zerver.lib.agent_protocol import CapabilityReport
        from zerver.lib.agent_requests import SetupResult
        from zerver.models import Client as APIClient
        from zerver.models import RealmAuditLog, UserGroup, UserProfile

        owner = self.example_user("hamlet")
        initial_clients = set(APIClient.objects.values_list("id", flat=True))
        initial_users = set(UserProfile.objects.values_list("id", flat=True))
        initial_groups = set(UserGroup.objects.values_list("id", flat=True))
        initial_audits = set(RealmAuditLog.objects.values_list("id", flat=True))
        settings_record, settings_created = agents.AgentRealmSettings.objects.get_or_create(
            realm=owner.realm, defaults={"enabled": True}
        )
        original_enabled = settings_record.enabled
        settings_record.enabled = True
        settings_record.save(update_fields=["enabled"])
        runner = agents.AgentRunner.objects.create(
            realm=owner.realm,
            owner=owner,
            name="lock-order-runner",
            fingerprint="y" * 64,
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
        runner.catalog_revision = 1
        runner.save(update_fields=["catalog_revision"])
        try:
            provider = actions.register_provider(
                owner,
                runner,
                name="lock-order-provider",
                base_url="https://example.com",
                model_id="model",
                allowed_models=["model"],
                context_window_tokens=1000,
                max_output_tokens=100,
                local_credential_ref="local",
            )
            profile = actions.create_profile(
                owner,
                name="Lock order profile",
                runner=runner,
                adapter_id="acp",
                adapter_version="1",
                mode="endpoint",
                provider=provider,
                idempotency_key=uuid4(),
            )
            for edit_kind in ("profile", "provider"):
                for setup_action in ("claim", "result"):
                    profile.refresh_from_db()
                    provider.refresh_from_db()
                    setup = actions.retry_profile_setup(
                        owner, profile, expected_revision=profile.revision, retry_key=uuid4()
                    )
                    self.assertEqual(setup.profile_id, profile.id)
                    self.assertEqual(setup.provider_id, provider.id)
                    claim_key = uuid4()
                    if setup_action == "result":
                        setup = actions.claim_setup(runner, setup.id, claim_key)
                    result = SetupResult(
                        schema_version=1,
                        setup_id=setup.id,
                        claim_key=claim_key,
                        lease_epoch=max(1, setup.lease_epoch),
                        descriptor_digest=setup.descriptor_digest,
                        configuration_digest=setup.configuration_digest,
                        state="needs_action",
                        capabilities=CapabilityReport(
                            config_version=setup.provider_config_version or setup.profile_revision
                        ),
                    )
                    before_profile_revision = profile.revision
                    before_provider_version = provider.config_version
                    first_locked = Event()
                    second_attempting = Event()

                    def operation(
                        first: bool,
                        *,
                        first_locked: Event = first_locked,
                        second_attempting: Event = second_attempting,
                        edit_kind: str = edit_kind,
                        setup_action: str = setup_action,
                        setup: agents.AgentSetupOperation = setup,
                        result: SetupResult = result,
                        claim_key: UUID = claim_key,
                        before_profile_revision: int = before_profile_revision,
                        before_provider_version: int = before_provider_version,
                    ) -> tuple[str, list[str]]:
                        lock_rows: list[str] = []

                        def trace(
                            execute: object, sql: str, params: object, many: bool, context: object
                        ) -> object:
                            if "FOR UPDATE" in sql:
                                for table in (
                                    "zerver_agentrunner",
                                    "zerver_agentprovider",
                                    "zerver_agentprofile",
                                    "zerver_agentsetupoperation",
                                ):
                                    if f'FROM "{table}"' in sql:
                                        lock_rows.append(table)
                                        if table == "zerver_agentrunner":
                                            if first:
                                                answer = execute(sql, params, many, context)  # type: ignore[operator]
                                                first_locked.set()
                                                if not second_attempting.wait(5):
                                                    raise AssertionError(
                                                        "Second connection did not attempt runner lock"
                                                    )
                                                return answer
                                            second_attempting.set()
                                        break
                            return execute(sql, params, many, context)  # type: ignore[operator]

                        try:
                            with connection.cursor() as cursor:
                                cursor.execute("SET statement_timeout = '4000ms'")
                            with connection.execute_wrapper(trace):
                                if first and edit_kind == "profile":
                                    actions.update_profile(
                                        owner,
                                        profile,
                                        expected_metadata_revision=profile.metadata_revision,
                                        expected_revision=before_profile_revision,
                                        name=profile.name,
                                        description=profile.description,
                                        provider=provider,
                                        repository=None,
                                        adapter_id=profile.adapter_id,
                                        adapter_version=profile.adapter_version,
                                        mode=profile.mode,
                                        default_mode=profile.default_mode,
                                        sandbox_alias=profile.policy["sandbox"]["alias"],
                                        actions=profile.policy["actions"],
                                        network=profile.policy["network"],
                                        retain_network=False,
                                        hard_cost_cap=not profile.policy["hard_cost_cap"],
                                        budget=profile.budget,
                                    )
                                elif first:
                                    actions.update_provider(
                                        owner,
                                        provider,
                                        expected_config_version=before_provider_version,
                                        expected_metadata_revision=provider.metadata_revision,
                                        name=provider.name,
                                        base_url=provider.base_url,
                                        model_id=provider.model_id,
                                        allowed_models=provider.allowed_models,
                                        context_window_tokens=provider.context_window_tokens,
                                        max_output_tokens=provider.max_output_tokens + 1,
                                        api_mode=provider.api_mode,
                                        network_policy=provider.network_policy,
                                        data_scope=provider.data_scope,
                                    )
                                elif setup_action == "claim":
                                    try:
                                        actions.claim_setup(runner, setup.id, claim_key)
                                    except ValueError as error:
                                        if str(error) != "Probe authority is unavailable.":
                                            raise
                                        return "stale", lock_rows
                                else:
                                    try:
                                        actions.record_setup_result(runner, result)
                                    except ValueError as error:
                                        if str(error) != "Probe authority is unavailable.":
                                            raise
                                        return "stale", lock_rows
                            return "success", lock_rows
                        finally:
                            connections.close_all()

                    with ThreadPoolExecutor(max_workers=2) as pool:
                        edit = pool.submit(operation, True)
                        self.assertTrue(first_locked.wait(5))
                        setup_operation = pool.submit(operation, False)
                        self.assertTrue(second_attempting.wait(5))
                        edit_status, edit_locks = edit.result(timeout=10)
                        setup_status, setup_locks = setup_operation.result(timeout=10)
                    self.assertEqual(edit_status, "success")
                    self.assertEqual(setup_status, "stale")
                    self.assertEqual(edit_locks[0], "zerver_agentrunner")
                    self.assertEqual(setup_locks[0], "zerver_agentrunner")
                    profile.refresh_from_db()
                    provider.refresh_from_db()
                    if edit_kind == "profile":
                        self.assertEqual(profile.revision, before_profile_revision + 1)
                    else:
                        self.assertEqual(provider.config_version, before_provider_version + 1)
                    self.assertEqual(profile.readiness_state, "unchecked")
                    self.assertFalse(
                        agents.AgentProbeGrant.objects.filter(
                            setup_operation=setup, revoked_at__isnull=True
                        ).exists()
                    )
        finally:
            agents.AgentProbeGrant.objects.filter(runner=runner).delete()
            agents.AgentSetupOperation.objects.filter(runner=runner).delete()
            agents.AgentProfile.objects.filter(runner=runner).delete()
            agents.AgentProvider.objects.filter(runner=runner).delete()
            runner.delete()
            if settings_created:
                settings_record.delete()
            else:
                settings_record.enabled = original_enabled
                settings_record.save(update_fields=["enabled"])
            RealmAuditLog.objects.exclude(id__in=initial_audits).delete()
            UserProfile.objects.exclude(id__in=initial_users).delete()
            UserGroup.objects.exclude(id__in=initial_groups).delete()
            APIClient.objects.exclude(id__in=initial_clients).delete()

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
