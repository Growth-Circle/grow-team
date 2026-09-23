"""Evidence tests for AF criteria. The code exists. No test proved it yet.

Each test names its criterion ID from
internals/docs/spec/2026-09-21-agent-lifecycle-and-mention-flow.md.
"""

from uuid import uuid4

from django.http import HttpRequest
from django.utils.timezone import now
from typing_extensions import override

from zerver.actions import agent_jobs as job_actions
from zerver.actions.agents import (
    archive_profile,
    create_profile,
    enable_profile,
    pause_profile,
    record_readiness,
)
from zerver.actions.user_settings import do_change_full_name
from zerver.lib.test_classes import ZulipTestCase
from zerver.models import Message, UserProfile, agents


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


class AgentEvidenceTests(ZulipTestCase):
    """AF-14, AF-15, AF-33, AF-34: server behavior that already exists."""

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
            fingerprint="d" * 64,
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

    def test_af_14_a_late_starting_runner_claims_the_queued_job_exactly_once(self) -> None:
        mention = f"@**{self.profile.bot_user.full_name}|{self.profile.bot_user_id}**"
        message_id = self.send_group_direct_message(
            self.owner, [self.profile.bot_user, self.example_user("iago")], mention
        )
        job = agents.AgentJob.objects.get(source_message_id=message_id)
        self.assertEqual(job.status, "queued")
        self.assertFalse(agents.AgentAttempt.objects.filter(job=job).exists())

        # The runner was offline when the mention admitted the job. It polls late.
        first = job_actions.claim_work(self.runner, claim_key=uuid4())
        self.assertIsNotNone(first)
        job.refresh_from_db()
        self.assertEqual(job.status, "running")
        self.assertEqual(agents.AgentAttempt.objects.filter(job=job).count(), 1)

        # A second, overlapping late start must not admit the same job twice.
        second = job_actions.claim_work(self.runner, claim_key=uuid4())
        self.assertIsNone(second)
        self.assertEqual(agents.AgentAttempt.objects.filter(job=job).count(), 1)

    def test_af_15_claim_work_holds_a_queued_job_while_its_grant_is_gone_then_resumes(
        self,
    ) -> None:
        member = self.example_user("cordelia")
        grant = agents.AgentGrant.objects.create(
            realm=self.owner.realm,
            owner=self.owner,
            principal_user=member,
            target_kind="profile",
            profile=self.profile,
            actions=["profile.use", "context.read"],
        )
        agents.AgentGrant.objects.create(
            realm=self.owner.realm,
            owner=self.owner,
            principal_user=member,
            target_kind="runner",
            runner=self.runner,
            actions=["runner.use"],
        )
        # A direct message to the bot admits on its own; a second, explicit
        # create_job call here would only add an unrelated duplicate job.
        message_id = self.send_personal_message(member, self.profile.bot_user, "Please answer")
        job = agents.AgentJob.objects.get(source_message_id=message_id)
        self.assertEqual(job.requester_id, member.id)
        self.assertEqual(job.status, "queued")

        # The owner revokes the member's grant while the job still waits in the queue.
        grant.revoked_at = now()
        grant.save(update_fields=["revoked_at"])
        self.assertIsNone(job_actions.claim_work(self.runner, claim_key=uuid4()))
        job.refresh_from_db()
        self.assertEqual(job.status, "queued")
        self.assertFalse(agents.AgentAttempt.objects.filter(job=job).exists())

        # The owner restores the grant before the deadline. The same job runs.
        grant.revoked_at = None
        grant.save(update_fields=["revoked_at"])
        self.assertIsNotNone(job_actions.claim_work(self.runner, claim_key=uuid4()))
        job.refresh_from_db()
        self.assertEqual(job.status, "running")
        self.assertTrue(agents.AgentAttempt.objects.filter(job=job, active=True).exists())

    def test_af_33_pausing_a_profile_leaves_an_active_attempt_and_blocks_a_queued_claim(
        self,
    ) -> None:
        # Capacity two isolates the effect of pause from the runner's own slot limit.
        agents.AgentRunner.objects.filter(id=self.runner.id).update(capacity=2)

        first_id = self.send_group_direct_message(
            self.owner, [self.profile.bot_user, self.example_user("iago")], "First"
        )
        job_a = job_actions.create_job(
            self.owner,
            profile=self.profile,
            source=Message.objects.get(id=first_id),
            request="First",
            idempotency_key=uuid4(),
            job_kind="answer",
            delivery_target="answer",
        )
        job_actions.claim_work(self.runner, claim_key=uuid4())
        attempt_a = agents.AgentAttempt.objects.get(job=job_a)

        second_id = self.send_group_direct_message(
            self.owner, [self.profile.bot_user, self.example_user("iago")], "Second"
        )
        job_b = job_actions.create_job(
            self.owner,
            profile=self.profile,
            source=Message.objects.get(id=second_id),
            request="Second",
            idempotency_key=uuid4(),
            job_kind="answer",
            delivery_target="answer",
        )
        self.assertEqual(job_b.status, "queued")

        self.profile.refresh_from_db()
        pause_profile(self.owner, self.profile, expected_revision=self.profile.revision)

        # The active attempt keeps running: pause never touches it directly.
        attempt_a.refresh_from_db()
        self.assertTrue(attempt_a.active)
        self.assertEqual(attempt_a.process_state, "starting")

        # The queued job cannot be claimed while the profile is paused.
        self.assertIsNone(job_actions.claim_work(self.runner, claim_key=uuid4()))
        job_b.refresh_from_db()
        self.assertEqual(job_b.status, "queued")
        self.assertFalse(agents.AgentAttempt.objects.filter(job=job_b).exists())

    def test_af_34_dispatch_and_job_detail_keep_stable_ids_after_rename_and_archive(
        self,
    ) -> None:
        mention = f"@**{self.profile.bot_user.full_name}|{self.profile.bot_user_id}**"
        message_id = self.send_group_direct_message(
            self.owner, [self.profile.bot_user, self.example_user("iago")], mention
        )
        job = agents.AgentJob.objects.get(source_message_id=message_id)
        profile_id = self.profile.id
        bot_user_id = self.profile.bot_user_id

        do_change_full_name(
            self.profile.bot_user, "Renamed Bot", acting_user=self.owner, notify=False
        )
        self.profile.refresh_from_db()
        archive_profile(self.owner, self.profile, expected_revision=self.profile.revision)

        sign_in(self, self.owner)
        dispatch = self.assert_json_success(
            self.client_get(f"/json/agent/messages/{message_id}/dispatch")
        )
        self.assertEqual(len(dispatch["dispatch_receipts"]), 1)
        receipt = dispatch["dispatch_receipts"][0]
        self.assertEqual(receipt["profile_id"], str(profile_id))
        self.assertEqual(receipt["job_id"], str(job.id))

        detail = self.assert_json_success(self.client_get(f"/json/agent/jobs/{job.id}"))
        self.assertEqual(detail["job"]["profile_id"], str(profile_id))

        job.refresh_from_db()
        self.profile.refresh_from_db()
        self.assertEqual(job.profile_id, profile_id)
        self.assertEqual(self.profile.bot_user_id, bot_user_id)
        self.assertEqual(self.profile.desired_state, "archived")
