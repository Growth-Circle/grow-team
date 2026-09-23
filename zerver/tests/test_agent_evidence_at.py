"""Evidence tests for AT criteria. The code exists. No test proved it yet.

Each test names its criterion ID from
internals/docs/spec/2026-09-21-agent-connections-and-coding-harness.md.
"""

import base64
import hashlib
import json
import tempfile
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

from django.contrib.auth.models import AnonymousUser
from django.http import HttpRequest
from django.test import RequestFactory, override_settings
from django.utils.timezone import now
from typing_extensions import override

from zerver.actions import agent_jobs as job_actions
from zerver.actions.agents import (
    create_profile,
    enable_profile,
    record_readiness,
    register_provider,
    register_repository,
    start_pairing,
)
from zerver.lib import agent_protocol as p
from zerver.lib.agent_results import store_artifact
from zerver.lib.agent_secrets import hash_agent_credential
from zerver.lib.test_classes import ZulipTestCase
from zerver.models import Message, UserProfile, agents
from zerver.views import agent_devices as device_views


def sign_in(test_case: ZulipTestCase, user_profile: UserProfile) -> None:
    """Log in with a password this test sets itself.

    A fresh worktree generates its own secrets file. The cached test
    database can carry fixture password hashes from a different
    worktree's secrets, so the shared login helper can fail here for a
    reason that has nothing to do with the code under test. Setting the
    password directly keeps the test on the real session-login path.
    """
    password = "evidence-test-password-1"
    user_profile.set_password(password)
    user_profile.save(update_fields=["password"])
    request = HttpRequest()
    request.session = test_case.client.session
    test_case.assertTrue(
        test_case.client.login(
            request=request,
            username=user_profile.delivery_email,
            password=password,
            realm=user_profile.realm,
        )
    )


class AgentPairingApprovalViewTest(ZulipTestCase):
    """AT-01: the human approval view binds the runner to the approver."""

    @override
    def setUp(self) -> None:
        super().setUp()
        self.owner = self.example_user("hamlet")
        agents.AgentRealmSettings.objects.update_or_create(
            realm=self.owner.realm, defaults={"enabled": True}
        )
        sign_in(self, self.owner)

    def test_pairing_approve_view_binds_runner_owner_and_realm_to_the_approver(self) -> None:
        pairing = start_pairing(
            "Laptop", "e" * 64, "APPR-OVE1", "polling-approvexxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
        )
        response = self.client_post(
            "/json/agent/pairings/approve",
            {
                "payload": json.dumps(
                    {"schema_version": 1, "pairing_id": str(pairing.id), "user_code": "APPR-OVE1"}
                )
            },
        )
        result = self.assert_json_success(response)
        self.assertEqual(result["pairing"]["state"], "approved")
        pairing.refresh_from_db()
        self.assertEqual(pairing.owner_id, self.owner.id)
        self.assertEqual(pairing.realm_id, self.owner.realm_id)

        # Exchange runs on the anonymous device route, as a real laptop would.
        request = RequestFactory().post(
            "/api/v1/agent/",
            json.dumps(
                {
                    "schema_version": 1,
                    "pairing_id": str(pairing.id),
                    "polling_secret": "polling-approvexxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
                }
            ),
            content_type="application/json",
        )
        request.user = AnonymousUser()
        response = device_views.exchange_pairing_device(request)
        self.assertEqual(response.status_code, 200)
        issued = json.loads(response.content)
        runner = agents.AgentRunner.objects.get(id=issued["runner_id"])
        self.assertEqual(runner.owner_id, self.owner.id)
        self.assertEqual(runner.realm_id, self.owner.realm_id)


class AgentEvidenceTests(ZulipTestCase):
    """AT-21 and AT-32: live re-checks that run on every reference or event."""

    @override
    def setUp(self) -> None:
        super().setUp()
        self.owner = self.example_user("hamlet")
        agents.AgentRealmSettings.objects.update_or_create(
            realm=self.owner.realm, defaults={"enabled": True}
        )
        self.runner = agents.AgentRunner.objects.create(
            realm=self.owner.realm,
            owner=self.owner,
            name="Evidence",
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
        self.profile = create_profile(
            self.owner,
            name="Evidence",
            runner=self.runner,
            adapter_id="acp",
            adapter_version="1",
            idempotency_key=uuid4(),
        )
        setup = agents.AgentSetupOperation.objects.get(profile=self.profile)
        record_readiness(
            self.runner,
            setup,
            {
                "schema_version": 1,
                "profile_id": str(self.profile.id),
                "profile_revision": 1,
                "runner_id": str(self.runner.id),
                "descriptor_digest": setup.descriptor_digest,
                "configuration_digest": setup.configuration_digest,
                "state": "ready",
                "capabilities": {"chat_ready": True, "config_version": 1},
            },
        )
        self.profile.refresh_from_db()
        enable_profile(self.owner, self.profile, expected_revision=self.profile.revision)
        self.profile.refresh_from_db()
        message_id = self.send_group_direct_message(
            self.owner, [self.profile.bot_user, self.example_user("iago")], "Please answer"
        )
        self.message = Message.objects.get(id=message_id)

    def runner_credential(self) -> str:
        token = "evidence-token-" + uuid4().hex
        agents.AgentRunnerCredential.objects.create(
            realm=self.owner.realm,
            runner=self.runner,
            token_hash=hash_agent_credential(token),
            refresh_hash=hash_agent_credential("refresh" + token),
            expires_at=now() + timedelta(hours=1),
            refresh_expires_at=now() + timedelta(days=1),
        )
        return token

    def test_at_21_context_read_denies_a_message_after_the_requester_leaves_the_channel(
        self,
    ) -> None:
        stream_name = "agent-context-private"
        self.subscribe(self.owner, stream_name, invite_only=True)
        self.subscribe(self.profile.bot_user, stream_name)
        message_id = self.send_stream_message(self.owner, stream_name, "Private context body")
        message = Message.objects.get(id=message_id)
        job = job_actions.create_job(
            self.owner,
            profile=self.profile,
            source=message,
            request="Read context",
            idempotency_key=uuid4(),
            job_kind="answer",
            delivery_target="answer",
        )
        job_actions.claim_work(self.runner, claim_key=uuid4())
        job.refresh_from_db()
        attempt = agents.AgentAttempt.objects.get(job=job)
        ref = agents.AgentContextRef.objects.get(job=job, kind="message")
        token = self.runner_credential()

        def read_context() -> object:
            return self.client.post(
                "/api/v1/agent/runner/context",
                data=json.dumps(
                    {
                        "schema_version": 1,
                        "job_id": str(job.id),
                        "attempt_id": str(attempt.id),
                        "lease_epoch": 1,
                        "job_version": job.version,
                        "reference_ids": [str(ref.id)],
                    }
                ),
                content_type="application/json",
                HTTP_AUTHORIZATION="Bearer " + token,
            )

        # The requester is still a member: the reference reads normally.
        self.assertEqual(read_context().status_code, 200)

        # The requester leaves the private channel. The same reference must
        # now be denied, even though the job and its context row are unchanged.
        self.unsubscribe(self.owner, stream_name)
        self.assertEqual(read_context().status_code, 400)

    def test_at_21_job_access_denies_repository_read_after_the_grant_is_revoked(self) -> None:
        actor = self.example_user("iago")
        repository = register_repository(
            self.owner,
            self.runner,
            workspace_alias="ctx-repo",
            canonical_origin=None,
            allowed_refs=["main"],
        )
        job = job_actions.create_job(
            self.owner,
            profile=self.profile,
            source=self.message,
            request="Fix it",
            idempotency_key=uuid4(),
            job_kind="answer",
            delivery_target="answer",
        )
        job_actions.claim_work(self.runner, claim_key=uuid4())
        # Attach the repository straight to the job row. require_job_access
        # only reads it from there; going through create_job would also
        # require the profile's own default_repository and readiness to
        # match, which this test does not need to exercise.
        agents.AgentJob.objects.filter(id=job.id).update(repository=repository)
        job.refresh_from_db()
        agents.AgentGrant.objects.create(
            realm=self.owner.realm,
            owner=self.owner,
            principal_user=actor,
            target_kind="profile",
            profile=self.profile,
            actions=["profile.use"],
        )
        agents.AgentGrant.objects.create(
            realm=self.owner.realm,
            owner=self.owner,
            principal_user=actor,
            target_kind="runner",
            runner=self.runner,
            actions=["runner.use"],
        )
        repository_grant = agents.AgentGrant.objects.create(
            realm=self.owner.realm,
            owner=self.owner,
            principal_user=actor,
            target_kind="repository",
            repository=repository,
            actions=["repository.read"],
        )

        sign_in(self, actor)
        # A live repository.read grant lets the member see the job.
        self.assertEqual(self.client_get(f"/json/agent/jobs/{job.id}").status_code, 200)

        # The owner revokes the repository grant. The same job must now be hidden.
        repository_grant.revoked_at = now()
        repository_grant.save(update_fields=["revoked_at"])
        self.assertEqual(self.client_get(f"/json/agent/jobs/{job.id}").status_code, 400)

    def secret_key_file(self, directory: str) -> str:
        key_file = Path(directory) / "keys.json"
        key_file.write_text(
            json.dumps({"current": "one", "keys": {"one": base64.b64encode(b"k" * 32).decode()}})
        )
        return str(key_file)

    def test_at_32_reject_secrets_blocks_a_stored_credential_in_an_event_and_stores_nothing(
        self,
    ) -> None:
        canary = "canary-secret-plaintext-" + "q" * 20
        with (
            tempfile.TemporaryDirectory() as directory,
            override_settings(AGENT_SECRET_MASTER_KEY_FILE=self.secret_key_file(directory)),
        ):
            job = job_actions.create_job(
                self.owner,
                profile=self.profile,
                source=self.message,
                request="Answer",
                idempotency_key=uuid4(),
                job_kind="answer",
                delivery_target="answer",
            )
            job_actions.claim_work(self.runner, claim_key=uuid4())
            job.refresh_from_db()
            # Attach the provider after the attempt is claimed: readiness was
            # already tested and frozen without it, so adding it earlier
            # would make the profile's readiness stale for claim_work itself.
            provider = register_provider(
                self.owner,
                self.runner,
                name="Canary provider",
                base_url="https://example.com",
                model_id="model",
                allowed_models=["model"],
                context_window_tokens=1000,
                max_output_tokens=100,
                credential=canary,
            )
            agents.AgentProfile.objects.filter(id=self.profile.id).update(provider=provider)
            self.profile.refresh_from_db()
            attempt = agents.AgentAttempt.objects.get(job=job)
            self.assertEqual(job.status, "running")
            # Claiming the job already wrote its own "attempt.starting" audit
            # row. The canary write must add nothing on top of that baseline.
            audit_ids_before = set(
                agents.AgentAuditEvent.objects.filter(job=job).values_list("id", flat=True)
            )

            event = p.RunnerEvent.model_validate(
                {
                    "schema_version": 1,
                    "job_id": str(job.id),
                    "attempt_id": str(attempt.id),
                    "lease_epoch": attempt.lease_epoch,
                    "sequence": attempt.event_cursor + 1,
                    "event_id": str(uuid4()),
                    "occurred_at": now().isoformat(),
                    "type": "input.requested",
                    "payload": {"question": f"Tool output printed {canary} to the log."},
                }
            )
            with self.assertRaisesRegex(ValueError, "Sensitive"):
                job_actions.record_event(self.runner, event)

            # The failed write must not leave a trace: no new audit row, no
            # status change, and the canary text is in no audit payload.
            audit_rows = list(agents.AgentAuditEvent.objects.filter(job=job))
            self.assertEqual({row.id for row in audit_rows}, audit_ids_before)
            for row in audit_rows:
                self.assertNotIn(canary, json.dumps(row.payload))
            job.refresh_from_db()
            attempt.refresh_from_db()
            self.assertEqual(job.status, "running")
            self.assertEqual(attempt.event_cursor, 0)

    def test_at_32_reject_secrets_blocks_a_stored_credential_in_an_artifact(self) -> None:
        canary = "canary-secret-plaintext-" + "r" * 20
        with (
            tempfile.TemporaryDirectory() as directory,
            override_settings(
                AGENT_SECRET_MASTER_KEY_FILE=self.secret_key_file(directory),
                AGENT_ARTIFACT_ROOT=directory,
            ),
        ):
            job = job_actions.create_job(
                self.owner,
                profile=self.profile,
                source=self.message,
                request="Answer",
                idempotency_key=uuid4(),
                job_kind="answer",
                delivery_target="answer",
            )
            job_actions.claim_work(self.runner, claim_key=uuid4())
            job.refresh_from_db()
            # Attach the provider after the attempt is claimed: readiness was
            # already tested and frozen without it, so adding it earlier
            # would make the profile's readiness stale for claim_work itself.
            provider = register_provider(
                self.owner,
                self.runner,
                name="Canary provider",
                base_url="https://example.com",
                model_id="model",
                allowed_models=["model"],
                context_window_tokens=1000,
                max_output_tokens=100,
                credential=canary,
            )
            agents.AgentProfile.objects.filter(id=self.profile.id).update(provider=provider)
            self.profile.refresh_from_db()
            attempt = agents.AgentAttempt.objects.get(job=job)
            content = f"tool output includes {canary}".encode()

            with self.assertRaisesRegex(ValueError, "Sensitive"):
                store_artifact(
                    self.runner,
                    job.id,
                    attempt.id,
                    1,
                    chunks=[content],
                    checksum=hashlib.sha256(content).hexdigest(),
                    kind="summary",
                    filename="summary.txt",
                    media_type="text/plain",
                )
            self.assertEqual(agents.AgentArtifact.objects.filter(attempt=attempt).count(), 0)
