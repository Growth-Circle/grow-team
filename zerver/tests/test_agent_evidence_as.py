"""Evidence tests for release 23 AS acceptance criteria.

Each test proves one criterion of
internals/docs/spec/2026-09-22-agent-settings-connections-and-team-defaults.md
that the release audit marked "code_done_needs_evidence": the behavior
already exists, but no automated test asserted it before this file.
"""

import base64
import json
import tempfile
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from django.test import override_settings
from typing_extensions import override

from zerver.actions.agent_jobs import claim_work, create_job
from zerver.actions.agents import (
    archive_profile,
    create_profile,
    enable_profile,
    record_readiness,
    register_provider,
    update_runner_metadata,
)
from zerver.actions.user_groups import check_add_user_group
from zerver.lib.test_classes import ZulipTestCase
from zerver.models import Message, UserProfile, agents
from zerver.models.streams import get_stream


def _catalog_report() -> dict[str, object]:
    return {
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


class AgentEvidenceASTests(ZulipTestCase):
    @override
    def setUp(self) -> None:
        super().setUp()
        self.owner = self.example_user("hamlet")
        self.owner.role = UserProfile.ROLE_REALM_ADMINISTRATOR
        self.owner.save(update_fields=["role"])
        agents.AgentRealmSettings.objects.update_or_create(
            realm=self.owner.realm, defaults={"enabled": True}
        )
        self.runner = agents.AgentRunner.objects.create(
            realm=self.owner.realm,
            owner=self.owner,
            name="Evidence runner",
            fingerprint="e" * 64,
            catalog_report=_catalog_report(),
        )

    def ready_profile(self, *, name: str = "Evidence agent") -> agents.AgentProfile:
        profile = create_profile(
            self.owner,
            name=name,
            runner=self.runner,
            adapter_id="acp",
            adapter_version="1",
            idempotency_key=uuid4(),
        )
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
                "capabilities": {"chat_ready": True, "config_version": 1},
            },
        )
        profile.refresh_from_db()
        return enable_profile(self.owner, profile, expected_revision=profile.revision)

    def grant_audience(self, profile: agents.AgentProfile) -> None:
        # update_team_default requires an existing group grant on the
        # profile before it accepts it as the team default, even for the
        # owner's own profile. A throwaway one-person group is enough.
        group = check_add_user_group(
            self.owner.realm, f"Audience for {profile.id}", [self.owner], acting_user=self.owner
        )
        agents.AgentGrant.objects.create(
            realm=self.owner.realm,
            owner=self.owner,
            target_kind="profile",
            profile=profile,
            principal_group=group,
            actions=["profile.use", "context.read"],
        )

    def set_team_default(
        self, actor: UserProfile, expected_selection_revision: int, profile_id: str | None
    ) -> None:
        response = self.api_patch(
            actor,
            "/api/v1/agent/team-default",
            {
                "payload": json.dumps(
                    {
                        "schema_version": 1,
                        "expected_selection_revision": expected_selection_revision,
                        "profile_id": profile_id,
                    }
                )
            },
        )
        self.assert_json_success(response)

    def test_as_10_provider_probe_never_opens_a_network_socket_from_the_server(self) -> None:
        """AS-10: a loopback provider probe only records a setup row here.

        proof_needed: create a provider at http://127.0.0.1 with the loopback
        network flag, probe it, and confirm this process never opens a
        socket. The runner performs the real network call, not the server.
        """
        created = self.assert_json_success(
            self.api_post(
                self.owner,
                "/api/v1/agent/providers",
                {
                    "payload": json.dumps(
                        {
                            "schema_version": 1,
                            "runner_id": str(self.runner.id),
                            "name": "Loopback",
                            "base_url": "http://127.0.0.1:11434",
                            "model_id": "model",
                            "allowed_models": ["model"],
                            "context_window_tokens": 1000,
                            "max_output_tokens": 100,
                            "local_credential_ref": "local-secret",
                            "network": {
                                "targets": [
                                    {
                                        "hostname": "127.0.0.1",
                                        "port": 11434,
                                        "allow_private": True,
                                        "allow_http_loopback": True,
                                    }
                                ],
                                "public_https_only": True,
                                "block_metadata": True,
                                "cross_origin_authorization": False,
                                "project_network": False,
                            },
                        }
                    )
                },
            )
        )
        provider_id = created["provider"]["id"]
        with (
            patch("socket.create_connection") as mock_create_connection,
            patch("socket.socket.connect") as mock_socket_connect,
        ):
            response = self.assert_json_success(
                self.api_post(
                    self.owner,
                    f"/api/v1/agent/providers/{provider_id}/probe",
                    {
                        "payload": json.dumps(
                            {
                                "schema_version": 1,
                                "expected_revision": 1,
                                "retry_key": str(uuid4()),
                            }
                        )
                    },
                )
            )
        mock_create_connection.assert_not_called()
        mock_socket_connect.assert_not_called()
        setup = agents.AgentSetupOperation.objects.get(id=response["setup_id"])
        self.assertEqual(setup.owner_id, self.owner.id)

    def test_as_16_team_default_rejects_inaccessible_or_unknown_profiles_without_new_grants(
        self,
    ) -> None:
        """AS-16: a bad team-default choice is rejected and creates no grant.

        proof_needed: an admin with no grant to a private profile, and an
        admin choosing a profile id unknown in this realm, both get 400. The
        grant count and the stored default stay unchanged.
        """
        profile = self.ready_profile()
        other_admin = self.example_user("iago")
        other_admin.role = UserProfile.ROLE_REALM_ADMINISTRATOR
        other_admin.save(update_fields=["role"])
        grants_before = agents.AgentGrant.objects.count()

        private_choice = self.api_patch(
            other_admin,
            "/api/v1/agent/team-default",
            {
                "payload": json.dumps(
                    {
                        "schema_version": 1,
                        "expected_selection_revision": 1,
                        "profile_id": str(profile.id),
                    }
                )
            },
        )
        self.assertEqual(private_choice.status_code, 400)

        unknown_choice = self.api_patch(
            self.owner,
            "/api/v1/agent/team-default",
            {
                "payload": json.dumps(
                    {
                        "schema_version": 1,
                        "expected_selection_revision": 1,
                        "profile_id": str(uuid4()),
                    }
                )
            },
        )
        self.assertEqual(unknown_choice.status_code, 400)

        self.assertEqual(agents.AgentGrant.objects.count(), grants_before)
        self.assertIsNone(
            agents.AgentRealmSettings.objects.get(realm=self.owner.realm).default_profile_id
        )

    def test_as_17_two_audience_members_use_the_same_team_default_independently(self) -> None:
        """AS-17: two members of the audience group share one team default.

        proof_needed: both resolve the same default profile and each create
        their own job with their own requester and the same bot. A third
        person outside the audience group gets no default at all.
        """
        profile = self.ready_profile()
        member_a = self.example_user("othello")
        member_b = self.example_user("cordelia")
        outsider = self.example_user("prospero")
        group = check_add_user_group(
            self.owner.realm,
            "AS-17 audience",
            [self.owner, member_a, member_b],
            acting_user=self.owner,
        )
        for user in (profile.bot_user, member_a, member_b, outsider):
            self.subscribe(user, "Denmark")
        agents.AgentGrant.objects.create(
            realm=self.owner.realm,
            owner=self.owner,
            target_kind="profile",
            profile=profile,
            principal_group=group,
            actions=["profile.use", "context.read"],
        )
        agents.AgentGrant.objects.create(
            realm=self.owner.realm,
            owner=self.owner,
            target_kind="runner",
            runner=self.runner,
            principal_group=group,
            actions=["runner.use"],
        )
        self.set_team_default(self.owner, 1, str(profile.id))

        def resolve_and_create(member: UserProfile, text: str) -> agents.AgentJob:
            message_id = self.send_stream_message(member, "Denmark", text)
            resolution = self.assert_json_success(
                self.api_post(
                    member,
                    "/api/v1/agent/selection/resolve",
                    {"payload": json.dumps({"schema_version": 1, "source_message_id": message_id})},
                )
            )["selection"]
            self.assertTrue(resolution["eligible"], resolution["reason"])
            self.assertEqual(resolution["profile_id"], str(profile.id))
            return create_job(
                member,
                profile=profile,
                source=Message.objects.get(id=message_id),
                request=text,
                idempotency_key=uuid4(),
                job_kind="answer",
                delivery_target="answer",
            )

        job_a = resolve_and_create(member_a, "AS-17 task from A")
        job_b = resolve_and_create(member_b, "AS-17 task from B")
        self.assertNotEqual(job_a.id, job_b.id)
        self.assertEqual(job_a.requester_id, member_a.id)
        self.assertEqual(job_b.requester_id, member_b.id)
        self.assertEqual(job_a.profile_id, profile.id)
        self.assertEqual(job_b.profile_id, profile.id)
        self.assertEqual(agents.AgentJob.objects.filter(profile=profile).count(), 2)

        outsider_message_id = self.send_stream_message(outsider, "Denmark", "Not in the group")
        outsider_resolution = self.assert_json_success(
            self.api_post(
                outsider,
                "/api/v1/agent/selection/resolve",
                {
                    "payload": json.dumps(
                        {"schema_version": 1, "source_message_id": outsider_message_id}
                    )
                },
            )
        )["selection"]
        self.assertFalse(outsider_resolution["eligible"])
        self.assertEqual(outsider_resolution["reason"], "no_eligible_default")

    def test_as_24_team_default_does_not_auto_trigger_outside_mention_or_dm(self) -> None:
        """AS-24: a team default never substitutes for an explicit mention.

        proof_needed: with a default set, a plain channel message, a group
        direct message without a mention, and a message from the bot itself
        must not create any receipt or job. A mention of a different agent
        creates a receipt and job only for that mentioned agent.
        """
        default_profile = self.ready_profile(name="AS-24 default")
        other_profile = self.ready_profile(name="AS-24 mentioned")
        member = self.example_user("othello")
        third = self.example_user("cordelia")
        for user in (default_profile.bot_user, other_profile.bot_user, member, third):
            self.subscribe(user, "Denmark")
        self.grant_audience(default_profile)
        self.set_team_default(self.owner, 1, str(default_profile.id))

        self.send_stream_message(self.owner, "Denmark", "Just talking, no agent named")
        self.assertEqual(agents.AgentDispatchReceipt.objects.count(), 0)

        self.send_group_direct_message(self.owner, [member, third], "Group chat, no mention")
        self.assertEqual(agents.AgentDispatchReceipt.objects.count(), 0)

        self.send_stream_message(
            default_profile.bot_user,
            "Denmark",
            f"Bot chatter mentioning @**{other_profile.bot_user.full_name}"
            f"|{other_profile.bot_user_id}**",
        )
        self.assertEqual(agents.AgentDispatchReceipt.objects.count(), 0)

        self.send_stream_message(
            self.owner,
            "Denmark",
            f"@**{other_profile.bot_user.full_name}|{other_profile.bot_user_id}** please answer",
        )
        self.assertEqual(agents.AgentDispatchReceipt.objects.count(), 1)
        receipt = agents.AgentDispatchReceipt.objects.get()
        self.assertEqual(receipt.profile_id, other_profile.id)
        self.assertEqual(agents.AgentJob.objects.filter(profile=default_profile).count(), 0)
        self.assertEqual(agents.AgentJob.objects.filter(profile=other_profile).count(), 1)

    def test_as_26_job_post_without_profile_id_is_rejected_despite_a_team_default(self) -> None:
        """AS-26: /json/agent/jobs still requires an explicit profile id.

        proof_needed: with a team default set, POST a job payload that omits
        profile_id. The request is rejected and creates no job, outbox
        entry, or attempt.
        """
        profile = self.ready_profile()
        self.subscribe(profile.bot_user, "Denmark")
        self.grant_audience(profile)
        self.set_team_default(self.owner, 1, str(profile.id))
        message_id = self.send_stream_message(self.owner, "Denmark", "Missing profile id")

        response = self.api_post(
            self.owner,
            "/api/v1/agent/jobs",
            {
                "payload": json.dumps(
                    {
                        "schema_version": 1,
                        "source_message_id": message_id,
                        "request": "Missing profile id",
                        "idempotency_key": str(uuid4()),
                        "job_kind": "answer",
                        "delivery_target": "answer",
                    }
                )
            },
        )
        self.assertEqual(response.status_code, 400)
        self.assertFalse(agents.AgentJob.objects.exists())
        self.assertFalse(agents.AgentOutbox.objects.exists())
        self.assertFalse(agents.AgentAttempt.objects.exists())

    def test_as_29_active_job_survives_default_change_and_runner_metadata_edit(self) -> None:
        """AS-29: unrelated admin actions do not touch a running job.

        proof_needed: with an active attempt, change the team default, edit
        the runner's metadata, then clear the default. The job's status,
        version, allowed actions, and budget stay the same, and the attempt
        stays active.
        """
        profile = self.ready_profile()
        self.subscribe(profile.bot_user, "Denmark")
        other_profile = self.ready_profile(name="AS-29 other")
        message_id = self.send_stream_message(self.owner, "Denmark", "Keep me running")
        job = create_job(
            self.owner,
            profile=profile,
            source=Message.objects.get(id=message_id),
            request="Keep me running",
            idempotency_key=uuid4(),
            job_kind="answer",
            delivery_target="answer",
        )
        claim_work(self.runner, claim_key=uuid4())
        job.refresh_from_db()
        attempt = agents.AgentAttempt.objects.get(job=job)
        budget_before = dict(job.budget)
        before = self.assert_json_success(self.api_get(self.owner, f"/api/v1/agent/jobs/{job.id}"))[
            "job"
        ]
        self.assertEqual(before["status"], "running")

        self.grant_audience(other_profile)
        self.set_team_default(self.owner, 1, str(other_profile.id))
        self.runner.refresh_from_db()
        update_runner_metadata(
            self.owner,
            self.runner,
            expected_metadata_revision=self.runner.metadata_revision,
            name="Renamed device",
            host_kind="server",
        )
        self.set_team_default(self.owner, 2, None)

        job.refresh_from_db()
        attempt.refresh_from_db()
        after = self.assert_json_success(self.api_get(self.owner, f"/api/v1/agent/jobs/{job.id}"))[
            "job"
        ]
        self.assertEqual(after, before)
        self.assertEqual(dict(job.budget), budget_before)
        self.assertTrue(attempt.active)

    def test_as_31_moving_to_a_new_runner_keeps_the_old_jobs_profile_and_bot(self) -> None:
        """AS-31: archiving the old profile does not rewrite past jobs.

        proof_needed: a job created on the first runner keeps its original
        profile, runner, and bot after a second profile is created on a new
        runner and the first profile is archived.
        """
        profile = self.ready_profile(name="AS-31 laptop agent")
        self.subscribe(profile.bot_user, "Denmark")
        message_id = self.send_stream_message(self.owner, "Denmark", "Old runner task")
        job = create_job(
            self.owner,
            profile=profile,
            source=Message.objects.get(id=message_id),
            request="Old runner task",
            idempotency_key=uuid4(),
            job_kind="answer",
            delivery_target="answer",
        )

        server_runner = agents.AgentRunner.objects.create(
            realm=self.owner.realm,
            owner=self.owner,
            name="Server",
            fingerprint="s" * 64,
            host_kind="server",
            catalog_report=_catalog_report(),
        )
        new_profile = create_profile(
            self.owner,
            name="AS-31 server agent",
            runner=server_runner,
            adapter_id="acp",
            adapter_version="1",
            idempotency_key=uuid4(),
        )
        archive_profile(self.owner, profile, expected_revision=profile.revision)

        job.refresh_from_db()
        self.assertEqual(job.profile_id, profile.id)
        self.assertEqual(job.runner_id, self.runner.id)
        self.assertNotEqual(job.profile_id, new_profile.id)
        data = self.assert_json_success(self.api_get(self.owner, f"/api/v1/agent/jobs/{job.id}"))[
            "job"
        ]
        self.assertEqual(data["profile_id"], str(profile.id))
        self.assertEqual(data["requester_id"], self.owner.id)
        self.assertEqual(agents.AgentProfile.objects.get(id=profile.id).desired_state, "archived")

    def test_as_28_credential_replacement_never_returns_the_secret(self) -> None:
        """AS-28: a changed credential invalidates readiness and never leaks.

        proof_needed: replacing a provider's server-stored credential drops
        the readiness of every profile that uses it, and the plaintext
        secret, old or new, never appears in a profile or provider response,
        even to the owner who is rotating it.
        """
        with tempfile.TemporaryDirectory() as directory:
            key_file = Path(directory) / "agent-keys.json"
            key_file.write_text(
                '{"current":"v1","keys":{"v1":"' + base64.b64encode(b"a" * 32).decode() + '"}}'
            )
            with override_settings(AGENT_SECRET_MASTER_KEY_FILE=str(key_file)):
                provider = register_provider(
                    self.owner,
                    self.runner,
                    name="Evidence provider",
                    base_url="https://example.com",
                    model_id="model",
                    allowed_models=["model"],
                    context_window_tokens=1000,
                    max_output_tokens=100,
                    credential="sk-original-secret-value",
                )
                profile = create_profile(
                    self.owner,
                    name="AS-28 agent",
                    runner=self.runner,
                    adapter_id="acp",
                    adapter_version="1",
                    provider=provider,
                    idempotency_key=uuid4(),
                )
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
                        "capabilities": {"chat_ready": True, "config_version": 1},
                    },
                )
                profile.refresh_from_db()
                self.assertEqual(profile.readiness_state, "ready")

                response = self.api_patch(
                    self.owner,
                    f"/api/v1/agent/providers/{provider.id}",
                    {
                        "payload": json.dumps(
                            {
                                "schema_version": 1,
                                "expected_config_version": provider.config_version,
                                "expected_metadata_revision": provider.metadata_revision,
                                "name": provider.name,
                                "base_url": provider.base_url,
                                "model_id": provider.model_id,
                                "allowed_models": provider.allowed_models,
                                "context_window_tokens": provider.context_window_tokens,
                                "max_output_tokens": provider.max_output_tokens,
                                "credential_replacement": "sk-rotated-secret-value",
                            }
                        )
                    },
                )
                self.assert_json_success(response)
                self.assertNotIn("sk-original-secret-value", response.content.decode())
                self.assertNotIn("sk-rotated-secret-value", response.content.decode())

                profile.refresh_from_db()
                self.assertEqual(profile.readiness_state, "unchecked")

                for url in (
                    f"/api/v1/agent/profiles/{profile.id}",
                    f"/api/v1/agent/providers/{provider.id}",
                ):
                    detail = self.api_get(self.owner, url)
                    self.assert_json_success(detail)
                    self.assertNotIn("sk-original-secret-value", detail.content.decode())
                    self.assertNotIn("sk-rotated-secret-value", detail.content.decode())

    def test_as_30_archived_or_paused_default_is_reported_unavailable(self) -> None:
        """AS-30: a paused or archived default says so, it does not hide.

        proof_needed: pausing or archiving the team default profile reports
        profile_unavailable through selection instead of silently looking
        like no default exists, so the reader gets a real recovery path.
        """
        profile = self.ready_profile()
        self.grant_audience(profile)
        self.set_team_default(self.owner, 1, str(profile.id))
        self.subscribe(self.owner, "Denmark")
        self.subscribe(profile.bot_user, "Denmark")
        stream = get_stream("Denmark", self.owner.realm)

        def reason() -> str:
            response = self.api_post(
                self.owner,
                "/api/v1/agent/selection/resolve",
                {
                    "payload": json.dumps(
                        {
                            "schema_version": 1,
                            "destination": {
                                "kind": "stream",
                                "stream_id": stream.id,
                                "topic": "AS-30",
                            },
                        }
                    )
                },
            )
            return str(self.assert_json_success(response)["selection"]["reason"])

        before = reason()
        self.assertNotIn(before, {"profile_unavailable", "no_eligible_default"})
        agents.AgentProfile.objects.filter(id=profile.id).update(desired_state="paused")
        self.assertEqual(reason(), "profile_unavailable")
        agents.AgentProfile.objects.filter(id=profile.id).update(desired_state="archived")
        self.assertEqual(reason(), "profile_unavailable")
