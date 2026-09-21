"""PostgreSQL races use distinct connections and bounded, repeatable requests."""

import json
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from pathlib import Path
from threading import Barrier
from typing import Any
from uuid import uuid4

from django.db import connection, connections
from typing_extensions import override

from zerver.actions import agent_jobs as jobs
from zerver.lib.agent_context import AgentBusy
from zerver.lib.exceptions import JsonableError
from zerver.lib.test_classes import ZulipTransactionTestCase
from zerver.models import (
    Client,
    Message,
    Stream,
    Subscription,
    UserGroupMembership,
    UserProfile,
    UserTopic,
    agents,
)


class AgentMessageAdmissionRaceTests(ZulipTransactionTestCase):
    @override
    def setUp(self) -> None:
        super().setUp()
        self.owner = self.example_user("hamlet")
        self.stream = Stream.objects.get(realm=self.owner.realm, name="Denmark")
        self.stream_before = Stream.objects.filter(id=self.stream.id).values().get()
        self.topics_before = list(UserTopic.objects.values())
        self.users_before = list(UserProfile.objects.values())
        self.subscriptions_before = list(Subscription.objects.values())
        self.memberships_before = list(UserGroupMembership.objects.values())
        self.messages_before = set(Message.objects.values_list("id", flat=True))
        self.clients_before = set(Client.objects.values_list("id", flat=True))
        self.realm_settings, self.realm_settings_created = (
            agents.AgentRealmSettings.objects.get_or_create(realm=self.owner.realm)
        )
        self.old_enabled = self.realm_settings.enabled
        self.realm_settings.enabled = True
        self.realm_settings.save(update_fields=["enabled"])
        fixture = json.loads(
            (Path(__file__).parent / "fixtures/agents/protocol-v1.json").read_text()
        )["valid"][0]["payload"]
        self.policy = deepcopy(fixture["policy"])
        self.policy["actions"] = ["context.read"]
        self.policy["scope"] = {"kind": "stream", "stream_id": self.stream.id}
        self.runner = agents.AgentRunner.objects.create(
            realm=self.owner.realm,
            owner=self.owner,
            name="Race",
            fingerprint="q" * 64,
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
                "sandboxes": [self.policy["sandbox"]],
            },
        )
        configuration = deepcopy(fixture["tested_configuration"])
        configuration.update(
            runner_id=str(self.runner.id),
            profile_revision=1,
            adapter={"id": "acp", "version": "1", "mode": "acp"},
            provider=None,
            workspace_binding=None,
            actions=["context.read"],
            policy_version=self.policy["version"],
            network=self.policy["network"],
            sandbox=self.policy["sandbox"],
            hard_cost_cap=self.policy["hard_cost_cap"],
            budget=fixture["budget"],
        )
        self.profile = agents.AgentProfile.objects.create(
            realm=self.owner.realm,
            owner=self.owner,
            bot_user=self.example_user("default_bot"),
            runner=self.runner,
            name="Race",
            adapter_id="acp",
            adapter_version="1",
            revision=1,
            enabled_revision=1,
            readiness_revision=1,
            desired_state="enabled",
            readiness_state="ready",
            readiness_configuration=configuration,
            readiness_configuration_digest=jobs.digest(configuration),
            policy=self.policy,
            budget=fixture["budget"],
        )
        self.source = Message.objects.get(
            id=self.send_stream_message(self.owner, "Denmark", "Race task")
        )

    def race(self, first: Callable[[], object], second: Callable[[], object]) -> list[object]:
        barrier = Barrier(2)

        def run(call: Callable[[], object]) -> tuple[int, object]:
            try:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT pg_backend_pid()")
                    pid = cursor.fetchone()[0]
                    cursor.execute("SET statement_timeout = '5s'")
                barrier.wait(timeout=5)
                for retry in range(20):
                    try:
                        return pid, call()
                    except AgentBusy:
                        if retry == 19:
                            raise
                        time.sleep(0.02)
                raise AssertionError("Retry loop did not terminate")
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as pool:
            a, b = pool.submit(run, first), pool.submit(run, second)
            results = [a.result(timeout=15), b.result(timeout=15)]
        self.assertNotEqual(results[0][0], results[1][0])
        return [item[1] for item in results]

    @override
    def tearDown(self) -> None:
        agents.AgentSendIntent.objects.filter(realm=self.owner.realm).delete()
        agents.AgentDispatchReceipt.objects.filter(realm=self.owner.realm).delete()
        agents.AgentApproval.objects.filter(realm=self.owner.realm).delete()
        agents.AgentVerification.objects.filter(realm=self.owner.realm).delete()
        agents.AgentAuditEvent.objects.filter(realm=self.owner.realm).delete()
        agents.AgentOutbox.objects.filter(realm=self.owner.realm).delete()
        agents.AgentOperation.objects.filter(realm=self.owner.realm).delete()
        agents.AgentInput.objects.filter(realm=self.owner.realm).delete()
        agents.AgentJob.objects.filter(runner=self.runner).update(resume_checkpoint=None)
        agents.AgentAttempt.objects.filter(runner=self.runner).update(source_checkpoint=None)
        agents.AgentCheckpoint.objects.filter(realm=self.owner.realm).delete()
        agents.AgentArtifact.objects.filter(realm=self.owner.realm).delete()
        agents.AgentContextRef.objects.filter(realm=self.owner.realm).delete()
        agents.AgentAttempt.objects.filter(runner=self.runner).delete()
        agents.AgentJob.objects.filter(runner=self.runner).delete()
        agents.AgentConversation.objects.filter(profile=self.profile).delete()
        agents.AgentGrant.objects.filter(profile=self.profile).delete()
        agents.AgentGrant.objects.filter(runner=self.runner).delete()
        self.profile.delete()
        agents.AgentRepository.objects.filter(runner=self.runner).delete()
        self.runner.delete()
        if self.realm_settings_created:
            self.realm_settings.delete()
        else:
            agents.AgentRealmSettings.objects.filter(id=self.realm_settings.id).update(
                enabled=self.old_enabled
            )
        Message.objects.exclude(id__in=self.messages_before).delete()
        Client.objects.exclude(id__in=self.clients_before).delete()
        UserTopic.objects.exclude(id__in=[row["id"] for row in self.topics_before]).delete()
        for row in self.topics_before:
            UserTopic.objects.filter(id=row["id"]).update(**row)
        Stream.objects.filter(id=self.stream.id).update(**self.stream_before)
        from zerver.models import RealmAuditLog

        RealmAuditLog.objects.exclude(
            id__in=self.models_pks_set[RealmAuditLog._meta.db_table]
        ).delete()
        UserGroupMembership.objects.exclude(
            id__in=[row["id"] for row in self.memberships_before]
        ).delete()
        Subscription.objects.exclude(
            id__in=[row["id"] for row in self.subscriptions_before]
        ).delete()
        for user_row in self.users_before:
            UserProfile.objects.update_or_create(id=user_row["id"], defaults=user_row)
        for subscription_row in self.subscriptions_before:
            Subscription.objects.update_or_create(
                id=subscription_row["id"], defaults=subscription_row
            )
        for membership_row in self.memberships_before:
            UserGroupMembership.objects.update_or_create(
                id=membership_row["id"], defaults=membership_row
            )
        from zerver.models import ArchiveTransaction

        ArchiveTransaction.objects.exclude(
            id__in=self.models_pks_set[ArchiveTransaction._meta.db_table]
        ).delete()
        from analytics.models import UserCount

        UserCount.objects.exclude(id__in=self.models_pks_set[UserCount._meta.db_table]).delete()
        from zerver.models import DirectMessageGroup, Recipient

        DirectMessageGroup.objects.exclude(
            id__in=self.models_pks_set[DirectMessageGroup._meta.db_table]
        ).delete()
        Recipient.objects.exclude(id__in=self.models_pks_set[Recipient._meta.db_table]).delete()
        super().tearDown()

    def send(self, key: object, content: str = "Race send") -> int:
        from uuid import UUID

        from zerver.actions.message_send import check_send_message
        from zerver.models.clients import get_client

        assert isinstance(key, UUID)
        return check_send_message(
            self.owner,
            get_client("test"),
            "private",
            [self.profile.bot_user_id],
            None,
            content,
            realm=self.owner.realm,
            agent_send_key=key,
        ).message_id

    def test_concurrent_same_key_replays_one_message_and_job(self) -> None:
        key = uuid4()
        result = self.race(lambda: self.send(key), lambda: self.send(key))
        self.assertEqual(result[0], result[1])
        self.assertEqual(agents.AgentSendIntent.objects.count(), 1)
        self.assertEqual(agents.AgentJob.objects.count(), 1)
        self.assertEqual(agents.AgentOutbox.objects.filter(event_type="job.wake").count(), 1)

    def test_concurrent_conflicting_payload_keeps_first_intact(self) -> None:
        key = uuid4()

        def send(content: str) -> object:
            try:
                return self.send(key, content)
            except JsonableError:
                return "conflict"

        result = self.race(lambda: send("first"), lambda: send("second"))
        self.assertEqual(result.count("conflict"), 1)
        self.assertEqual(agents.AgentJob.objects.count(), 1)
        intent = agents.AgentSendIntent.objects.get()
        assert intent.sent_message_id is not None
        self.assertIn(Message.objects.get(id=intent.sent_message_id).content, {"first", "second"})

    def test_outer_statement_timeout_rolls_back_message_job_and_intent(self) -> None:
        from unittest.mock import patch

        from zerver.actions import agent_dispatch

        original = agent_dispatch.admit_message

        def delayed(*args: Any, **kwargs: Any) -> object:
            result = original(*args, **kwargs)
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT current_setting('lock_timeout'), current_setting('statement_timeout')"
                )
                self.assertEqual(cursor.fetchone(), ("250ms", "2500ms"))
                cursor.execute("SELECT pg_sleep(3)")
            return result

        key = uuid4()
        with (
            patch("zerver.actions.agent_dispatch.admit_message", side_effect=delayed),
            self.assertRaises(AgentBusy),
        ):
            self.send(key)
        self.assertFalse(agents.AgentSendIntent.objects.exists())
        self.assertFalse(agents.AgentJob.objects.exists())
        self.assertFalse(agents.AgentOutbox.objects.exists())
        self.assertEqual(
            set(Message.objects.values_list("id", flat=True)),
            self.messages_before | {self.source.id},
        )
        with connection.cursor() as cursor:
            cursor.execute("SHOW lock_timeout")
            self.assertEqual(cursor.fetchone()[0], "0")
        self.send(key)
        self.assertEqual(agents.AgentJob.objects.count(), 1)

    def test_actual_pause_writer_races_with_admission(self) -> None:
        from zerver.actions.agents import pause_profile

        self.race(
            lambda: self.send(uuid4(), "Concurrent pause"),
            lambda: pause_profile(self.owner, self.profile, expected_revision=1),
        )
        receipt = agents.AgentDispatchReceipt.objects.get()
        self.assertIn(receipt.decision, {"accepted", "rejected"})
        self.assertEqual(agents.AgentJob.objects.count(), int(receipt.decision == "accepted"))
        self.assertEqual(agents.AgentOutbox.objects.count(), int(receipt.decision == "accepted"))
        self.send(uuid4(), "After pause")
        self.assertEqual(
            agents.AgentDispatchReceipt.objects.filter(decision="rejected").count(),
            1 + int(receipt.decision == "rejected"),
        )
        self.assertFalse(agents.AgentAttempt.objects.exists())
