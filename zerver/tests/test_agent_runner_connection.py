"""Device connection retries preserve identity and reject lost credentials."""

import json
from collections.abc import Callable
from datetime import timedelta
from typing import Any

from django.http import HttpRequest, HttpResponse
from django.test import RequestFactory
from django.utils.timezone import now

from zerver.actions.agents import approve_pairing, authenticate_runner_token
from zerver.lib.test_classes import ZulipTestCase
from zerver.models import agents
from zerver.views import agent_devices as views


class RunnerConnectionTest(ZulipTestCase):
    def device(
        self, view: Callable[[HttpRequest], HttpResponse], payload: dict[str, Any], token: str = ""
    ) -> HttpResponse:
        from django.contrib.auth.models import AnonymousUser

        request = RequestFactory().post(
            "/api/v1/agent/",
            json.dumps({"schema_version": 1, **payload}),
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {token}",
        )
        request.user = AnonymousUser()
        return view(request)

    def pair(self) -> tuple[dict[str, Any], dict[str, Any]]:
        payload = {"device_name": "test", "fingerprint": "f" * 64, "polling_secret": "s" * 48}
        response = self.device(views.start_pairing_device, payload)
        self.assertEqual(response.status_code, 200)
        started = json.loads(response.content)
        pairing = agents.AgentPairing.objects.get(id=started["pairing_id"])
        approve_pairing(self.example_user("hamlet"), pairing, started["user_code"])
        identity = {
            "pairing_id": started["pairing_id"],
            "polling_secret": payload["polling_secret"],
        }
        response = self.device(views.exchange_pairing_device, identity)
        self.assertEqual(response.status_code, 200)
        return identity, json.loads(response.content)

    def test_server_code_status_and_lost_exchange_do_not_reissue(self) -> None:
        identity, issued = self.pair()
        self.assertIn("expires_at", issued)
        self.assertIn("refresh_expires_at", issued)
        response = self.device(views.pairing_status_device, identity)
        status = json.loads(response.content)
        self.assertEqual(status["state"], "exchanged")
        self.assertEqual(status["runner_id"], issued["runner_id"])
        self.assertEqual(status["recovery"], "re_pair_and_revoke_orphan")
        self.assertNotIn("token", status)
        self.assertEqual(self.device(views.exchange_pairing_device, identity).status_code, 400)
        self.assertEqual(agents.AgentRunner.objects.count(), 1)
        self.assertEqual(
            self.device(
                views.pairing_status_device, {**identity, "polling_secret": "x" * 48}
            ).status_code,
            400,
        )

    def test_workspace_replay_revision_and_catalog_replay(self) -> None:
        _, issued = self.pair()
        token = issued["token"]
        payload = {
            "workspace_alias": "app",
            "canonical_origin": "https://example.com/owner/app.git",
            "allowed_refs": ["main"],
            "required_checks": [],
            "revision": 1,
        }
        first = self.device(views.register_workspace_device, payload, token)
        self.assertEqual(first.status_code, 200)
        self.assertEqual(
            first.content, self.device(views.register_workspace_device, payload, token).content
        )
        self.assertEqual(agents.AgentRepository.objects.count(), 1)
        changed = {**payload, "allowed_refs": ["main", "dev"]}
        self.assertEqual(
            self.device(views.register_workspace_device, changed, token).status_code, 400
        )
        self.assertEqual(
            self.device(
                views.register_workspace_device, {**changed, "revision": 2}, token
            ).status_code,
            200,
        )
        self.assertEqual(
            self.device(
                views.register_workspace_device, {**payload, "host_path": "/private"}, token
            ).status_code,
            400,
        )
        catalog = {"catalog": {"revision": 1, "adapters": [], "sandboxes": []}}
        self.assertEqual(self.device(views.update_runner_catalog, catalog, token).status_code, 200)
        self.assertEqual(self.device(views.update_runner_catalog, catalog, token).status_code, 200)

    def test_expired_revoked_rotation_classification(self) -> None:
        _, issued = self.pair()
        credential = authenticate_runner_token(issued["token"])
        credential.expires_at = now() - timedelta(seconds=1)
        credential.save(update_fields=["expires_at"])
        response = self.device(views.update_runner_catalog, {"catalog": {}}, issued["token"])
        self.assertEqual(response.status_code, 401)
        self.assertEqual(json.loads(response.content)["code"], "credential_expired")
        rotated = self.device(
            views.rotate_device_credential, {"refresh_token": issued["refresh_token"]}
        )
        self.assertEqual(rotated.status_code, 200)
        self.assertIn("expires_at", json.loads(rotated.content))
        replay = self.device(
            views.rotate_device_credential, {"refresh_token": issued["refresh_token"]}
        )
        self.assertEqual(replay.status_code, 401)
        self.assertEqual(json.loads(replay.content)["code"], "credential_revoked")

    def test_http_status_route_bounds_and_invalid_pairing(self) -> None:
        response = self.client_post(
            "/api/v1/agent/pairings/status",
            json.dumps({"schema_version": 1, "pairing_id": "bad", "polling_secret": "s" * 48}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertNotIn("s" * 48, response.content.decode())
        self.assertEqual(self.client_get("/api/v1/agent/pairings/status").status_code, 405)

    def test_pending_expired_status_does_not_mint_credentials(self) -> None:
        payload = {"device_name": "test", "fingerprint": "f" * 64, "polling_secret": "pending" * 8}
        response = self.device(views.start_pairing_device, payload)
        started = json.loads(response.content)
        identity = {
            "pairing_id": started["pairing_id"],
            "polling_secret": payload["polling_secret"],
        }
        self.assertEqual(
            json.loads(self.device(views.pairing_status_device, identity).content)["state"],
            "pending",
        )
        agents.AgentPairing.objects.filter(id=started["pairing_id"]).update(
            expires_at=now() - timedelta(seconds=1)
        )
        self.assertEqual(
            json.loads(self.device(views.pairing_status_device, identity).content)["state"],
            "expired",
        )
        self.assertEqual(agents.AgentRunnerCredential.objects.count(), 0)

    def test_exact_catalog_replay_preserves_ready_profile(self) -> None:
        from uuid import uuid4

        from zerver.actions.agents import create_profile

        _, issued = self.pair()
        credential = authenticate_runner_token(issued["token"])
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
        self.assertEqual(
            self.device(
                views.update_runner_catalog, {"catalog": catalog}, issued["token"]
            ).status_code,
            200,
        )
        credential.runner.refresh_from_db()
        profile = create_profile(
            self.example_user("hamlet"),
            runner=credential.runner,
            name="Ready",
            adapter_id="acp",
            adapter_version="1",
            idempotency_key=uuid4(),
        )
        profile.readiness_state = "ready"
        profile.save(update_fields=["readiness_state"])
        self.assertEqual(
            self.device(
                views.update_runner_catalog, {"catalog": catalog}, issued["token"]
            ).status_code,
            200,
        )
        profile.refresh_from_db()
        self.assertEqual(profile.readiness_state, "ready")
