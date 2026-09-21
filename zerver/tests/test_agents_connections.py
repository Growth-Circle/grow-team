import base64
import json
import tempfile
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

from django.db import transaction
from django.test import RequestFactory, override_settings
from django.utils.timezone import now
from typing_extensions import override

from zerver.actions.agents import (
    approve_pairing,
    authenticate_runner_token,
    create_profile,
    exchange_pairing,
    record_readiness,
    register_repository,
    revoke_runner,
    rotate_runner_credential,
    start_pairing,
)
from zerver.lib.agent_secrets import decrypt_agent_secret, encrypt_agent_secret
from zerver.lib.test_classes import ZulipTestCase
from zerver.models import agents
from zerver.views.agent_devices import rotate_device_credential


class AgentConnectionTests(ZulipTestCase):
    @override
    def setUp(self) -> None:
        super().setUp()
        self.owner = self.example_user("hamlet")

    def test_pairing_is_bound_only_by_authenticated_approval_and_exchanges_once(self) -> None:
        pairing = start_pairing("Laptop", "a" * 64, "ABCD-EFGH", "polling-secret")
        self.assertIsNone(pairing.realm_id)
        approve_pairing(self.owner, pairing, "ABCD-EFGH")
        token, refresh = exchange_pairing(pairing, "polling-secret")
        self.assertTrue(token)
        self.assertTrue(refresh)
        with self.assertRaises(ValueError):
            exchange_pairing(pairing, "polling-secret")

    def test_expired_and_failed_pairing_are_rejected(self) -> None:
        pairing = start_pairing(
            "Laptop",
            "b" * 64,
            "WXYZ-ABCD",
            "polling-secret",
            expires_at=now() - timedelta(seconds=1),
        )
        with self.assertRaises(ValueError):
            approve_pairing(self.owner, pairing, "WXYZ-ABCD")

    def test_failed_approval_is_recorded_before_the_safe_error(self) -> None:
        pairing = start_pairing("Laptop", "i" * 64, "CODE-FOUR", "polling-four")
        with self.assertRaises(ValueError):
            approve_pairing(self.owner, pairing, "wrong-code")
        pairing.refresh_from_db()
        self.assertEqual(pairing.failed_attempts, 1)
        self.assertEqual(pairing.state, "pending")

    def test_credential_rotation_and_revocation_do_not_revive_old_tokens(self) -> None:
        pairing = start_pairing("Laptop", "c" * 64, "ABCD-WXYZ", "polling")
        approve_pairing(self.owner, pairing, "ABCD-WXYZ")
        _, refresh = exchange_pairing(pairing, "polling")
        pairing.refresh_from_db()
        credential = agents.AgentRunnerCredential.objects.get(runner=pairing.runner)
        self.assertGreater(credential.expires_at, now() + timedelta(hours=23))
        credential.expires_at = now() - timedelta(seconds=1)
        credential.save(update_fields=["expires_at"])
        replacement, _, replacement_refresh = rotate_runner_credential(credential, refresh)
        with self.assertRaises(ValueError), transaction.atomic():
            rotate_runner_credential(credential, refresh)
        self.assertIsNotNone(pairing.runner)
        runner = pairing.runner
        assert runner is not None
        revoke_runner(runner)
        with self.assertRaises(ValueError):
            rotate_runner_credential(replacement, replacement_refresh)

    def test_failed_exchange_rejects_a_pairing_after_a_small_bounded_number(self) -> None:
        pairing = start_pairing("Laptop", "d" * 64, "CODE-ONE", "polling")
        approve_pairing(self.owner, pairing, "CODE-ONE")
        for _ in range(4):
            with self.assertRaises(ValueError):
                exchange_pairing(pairing, "wrong")
        pairing.refresh_from_db()
        self.assertEqual(pairing.state, "approved")
        with self.assertRaises(ValueError):
            exchange_pairing(pairing, "wrong")
        pairing.refresh_from_db()
        self.assertEqual(pairing.state, "rejected")

    def test_envelope_secret_binds_realm_and_owner(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            key_file = Path(directory) / "agent-keys.json"
            key_file.write_text(
                '{"current":"v1","keys":{"v1":"' + base64.b64encode(b"a" * 32).decode() + '"}}'
            )
            with override_settings(AGENT_SECRET_MASTER_KEY_FILE=str(key_file)):
                ciphertext, wrapped_key, key_id = encrypt_agent_secret(
                    "secret-value", realm_id=self.owner.realm_id, owner_id=self.owner.id, version=1
                )
                self.assertEqual(
                    decrypt_agent_secret(
                        ciphertext,
                        wrapped_key,
                        key_id=key_id,
                        realm_id=self.owner.realm_id,
                        owner_id=self.owner.id,
                        version=1,
                    ),
                    "secret-value",
                )
                with self.assertRaises(ValueError):
                    decrypt_agent_secret(
                        ciphertext,
                        wrapped_key,
                        key_id=key_id,
                        realm_id=self.owner.realm_id + 1,
                        owner_id=self.owner.id,
                        version=1,
                    )

    def test_profile_retry_keeps_one_bot_and_readiness_uses_the_saved_descriptor(self) -> None:
        runner = agents.AgentRunner.objects.create(
            realm=self.owner.realm,
            owner=self.owner,
            name="ready runner",
            fingerprint="e" * 64,
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
        repository = register_repository(
            self.owner,
            runner,
            workspace_alias="workspace",
            canonical_origin="https://example.com/repo.git",
            allowed_refs=["main"],
        )
        key = uuid4()
        profile = create_profile(
            self.owner,
            name="Build Agent",
            runner=runner,
            adapter_id="acp",
            adapter_version="1",
            repository=repository,
            idempotency_key=key,
        )
        second = create_profile(
            self.owner,
            name="Build Agent",
            runner=runner,
            adapter_id="acp",
            adapter_version="1",
            repository=repository,
            idempotency_key=key,
        )
        self.assertEqual(profile.id, second.id)
        self.assertEqual(agents.AgentProfile.objects.filter(bot_user=profile.bot_user).count(), 1)
        setup = agents.AgentSetupOperation.objects.get(profile=profile)
        report = {
            "schema_version": 1,
            "profile_id": str(profile.id),
            "profile_revision": profile.revision,
            "runner_id": str(runner.id),
            "descriptor_digest": setup.descriptor_digest,
            "configuration_digest": setup.configuration_digest,
            "state": "ready",
            "capabilities": {"chat_ready": True, "config_version": 1},
        }
        record_readiness(runner, setup, report)
        profile.refresh_from_db()
        self.assertIsNotNone(profile.readiness_configuration)
        configuration = profile.readiness_configuration
        assert configuration is not None
        self.assertEqual(profile.desired_state, "enabled")
        self.assertEqual(profile.readiness_configuration_digest, setup.configuration_digest)
        self.assertNotIn("runner_supplied", configuration)
        self.assertEqual(
            configuration["workspace_binding"]["repository_id"],
            str(repository.id),
        )
        self.assertEqual(profile.policy["scope"]["participant_user_ids"], [self.owner.id])
        profile.revision += 1
        profile.save(update_fields=["revision"])
        with transaction.atomic():  # noqa: SIM117
            with self.assertRaises(ValueError):
                record_readiness(runner, setup, report)

    def test_repository_rejects_host_paths_and_runner_token_is_not_a_browser_identity(self) -> None:
        runner = agents.AgentRunner.objects.create(
            realm=self.owner.realm, owner=self.owner, name="runner", fingerprint="f" * 64
        )
        with transaction.atomic():  # noqa: SIM117
            with self.assertRaises(ValueError):
                register_repository(
                    self.owner,
                    runner,
                    workspace_alias="work",
                    canonical_origin="file:///srv/repo",
                    allowed_refs=["main"],
                )
        pairing = start_pairing("Laptop", "g" * 64, "CODE-TWO", "polling-two")
        approve_pairing(self.owner, pairing, "CODE-TWO")
        token, _ = exchange_pairing(pairing, "polling-two")
        pairing.refresh_from_db()
        self.assertEqual(authenticate_runner_token(token).runner_id, pairing.runner_id)
        with self.assertRaises(ValueError):
            authenticate_runner_token("not-a-token")

    def test_device_routes_keep_pairing_secrets_out_of_the_start_response(self) -> None:
        response = self.client.post(
            "/api/agents/device/pairings",
            data=json.dumps(
                {
                    "schema_version": 1,
                    "device_name": "Laptop",
                    "fingerprint": "h" * 64,
                    "user_code": "CODE-THREE",
                    "polling_secret": "pairing-secret",
                }
            ),
            content_type="application/json",
        )
        data = self.assert_json_success(response)
        self.assertEqual(data["state"], "pending")
        self.assertNotIn("pairing-secret", response.content.decode())

    def test_device_bearer_route_rejects_a_browser_user(self) -> None:
        request = RequestFactory().post(
            "/api/agents/device/credentials/rotate", data="{}", content_type="application/json"
        )
        request.user = self.owner
        response = rotate_device_credential(request)
        self.assertEqual(response.status_code, 401)
