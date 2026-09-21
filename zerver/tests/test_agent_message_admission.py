"""Agent message admission uses renderer provenance and durable lifecycle records."""

import json
from uuid import uuid4

from django.db import transaction
from typing_extensions import override

from zerver.actions.agents import create_profile, record_readiness
from zerver.lib.markdown import render_message_markdown
from zerver.lib.test_classes import ZulipTestCase
from zerver.models import Message, agents
from zerver.models.clients import get_client


class AgentMessageAdmissionTests(ZulipTestCase):
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
            name="Message admission",
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
        self.profile = create_profile(
            self.owner,
            name="Message admission",
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

    def test_personal_agent_mention_creates_one_durable_job(self) -> None:
        self.send_personal_message(
            self.owner,
            self.profile.bot_user,
            f"Please answer @**{self.profile.bot_user.full_name}|{self.profile.bot_user_id}**",
        )

        self.assertEqual(agents.AgentDispatchReceipt.objects.count(), 1)
        receipt = agents.AgentDispatchReceipt.objects.get()
        self.assertEqual(receipt.decision, "accepted", receipt.reason)
        self.assertEqual(agents.AgentJob.objects.count(), 1)
        self.assertEqual(agents.AgentOutbox.objects.filter(event_type="job.wake").count(), 1)

    def test_same_agent_send_key_replays_the_first_message(self) -> None:
        key = str(uuid4())
        payload = {
            "type": "direct",
            "to": json.dumps([self.profile.bot_user.email]),
            "content": f"Please answer @**{self.profile.bot_user.full_name}|{self.profile.bot_user_id}**",
            "agent_send_key": key,
        }
        first = self.api_post(self.owner, "/api/v1/messages", payload)
        second = self.api_post(self.owner, "/api/v1/messages", payload)

        self.assertEqual(first.status_code, 200, first.content)
        self.assertEqual(second.status_code, 200, second.content)
        self.assertEqual(json.loads(first.content)["id"], json.loads(second.content)["id"])
        self.assertEqual(agents.AgentSendIntent.objects.count(), 1)
        self.assertEqual(agents.AgentJob.objects.count(), 1)

        with transaction.atomic():
            changed = self.api_post(
                self.owner,
                "/api/v1/messages",
                {**payload, "content": "A changed payload"},
            )
        self.assertEqual(changed.status_code, 400)
        self.assertEqual(agents.AgentSendIntent.objects.count(), 1)
        self.assertEqual(agents.AgentJob.objects.count(), 1)

    def test_one_human_one_agent_direct_message_is_an_implicit_trigger(self) -> None:
        self.send_personal_message(self.owner, self.profile.bot_user, "Please answer")

        receipt = agents.AgentDispatchReceipt.objects.get()
        self.assertEqual(receipt.trigger_kind, "direct_message")
        self.assertEqual(receipt.decision, "accepted")
        self.assertEqual(agents.AgentJob.objects.count(), 1)

    def test_renderer_records_only_non_silent_personal_mention_provenance(self) -> None:
        message = Message(sender=self.owner, sending_client=get_client("test"), realm=self.owner.realm)
        mention = f"@**{self.profile.bot_user.full_name}|{self.profile.bot_user_id}**"

        self.assertEqual(
            render_message_markdown(message, mention).personal_mention_user_ids,
            {self.profile.bot_user_id},
        )
        for content in [f"@_**{self.profile.bot_user.full_name}|{self.profile.bot_user_id}**", f"`{mention}`", f"> {mention}"]:
            with self.subTest(content=content):
                self.assertEqual(render_message_markdown(message, content).personal_mention_user_ids, set())
