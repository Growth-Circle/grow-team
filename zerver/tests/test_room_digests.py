"""Tests for Kaki's opt-in morning summary of a room."""

import hashlib
import io
import json
import tempfile
from datetime import date, datetime, timedelta
from datetime import timezone as dt_timezone
from typing import TYPE_CHECKING
from unittest import mock
from uuid import uuid4

import time_machine
from django.core.management import call_command
from django.test import override_settings
from django.utils.timezone import now
from typing_extensions import override

from zerver.actions.agent_jobs import (
    card_extra_data,
    check_attempt_access,
    claim_work,
    create_job,
    record_event,
)
from zerver.actions.agents import create_profile, enable_profile, record_readiness
from zerver.actions.create_user import do_create_user
from zerver.actions.streams import do_deactivate_stream
from zerver.actions.users import do_deactivate_user
from zerver.lib import agent_context
from zerver.lib import agent_protocol as p
from zerver.lib.agent_context import (
    AgentBusy,
    current_audience,
    require_job_access,
    stream_audience_cache,
)
from zerver.lib.agent_policy import AgentAccessDenied
from zerver.lib.agent_results import (
    deliver_result_privately,
    post_admission_card,
    publish_result,
    store_artifact,
)
from zerver.lib.room_digests import (
    CONTEXT_MESSAGE_LIMIT,
    ROOM_DIGEST_MODEL_PRESET,
    ROOM_DIGEST_TOPIC_NAME,
    _coerce_str_list,
    _recent_message_ids,
    create_room_digest_job,
    fill_room_digest,
    is_room_digest_job,
    send_realm_room_digests,
)
from zerver.lib.streams import subscribed_to_stream
from zerver.lib.test_classes import ZulipTestCase
from zerver.models import (
    AgentRealmSettings,
    Message,
    Realm,
    RoomDigest,
    RoomMeta,
    Stream,
    UserProfile,
    agents,
)
from zerver.tests import test_agents
from zerver.tests.test_room_meta import create_kaki

if TYPE_CHECKING:
    from django.test.client import _MonkeyPatchedWSGIResponse as TestHttpResponse

# A UTC instant that is exactly 07:00 in Asia/Jakarta (UTC+7), the digest hour.
AT_SEVEN_JAKARTA = datetime(2026, 9, 27, 0, 0, tzinfo=dt_timezone.utc)
# A UTC instant well after 07:00 in Asia/Jakarta, for the tests that are not
# about the schedule.
AFTER_DIGEST_HOUR = datetime(2026, 9, 27, 6, 0, tzinfo=dt_timezone.utc)


def _set_realm_settings(realm: Realm, *, timezone: str = "Asia/Jakarta") -> None:
    AgentRealmSettings.objects.update_or_create(
        realm=realm, defaults={"enabled": True, "timezone": timezone, "agent_language": "en"}
    )


def _opt_in(stream: Stream, enabler: UserProfile) -> RoomMeta:
    room_meta, _created = RoomMeta.objects.get_or_create(stream=stream)
    room_meta.summary_enabled = True
    room_meta.summary_enabled_by = enabler
    room_meta.save(update_fields=["summary_enabled", "summary_enabled_by"])
    return room_meta


def _own_stream(stream: Stream, owner: UserProfile) -> Stream:
    stream.creator = owner
    stream.save(update_fields=["creator"])
    return stream


def _enabled_kaki(owner: UserProfile, bot_user: UserProfile) -> agents.AgentProfile:
    """A built-in planner profile that create_job accepts as blocked: that
    path swallows the readiness failure only when the profile is enabled.
    A job for it can be created, but not claimed (see _ready_kaki_profile)."""
    profile = create_kaki(owner, bot_user)
    profile.agent_role = "planner"
    profile.desired_state = "enabled"
    profile.enabled_revision = profile.revision
    # create_job checks the policy and the budget against the protocol, and
    # create_kaki sets neither.
    profile.policy = {
        "version": 1,
        "actions": ["context.read"],
        "scope": {"kind": "direct", "participant_user_ids": [owner.id]},
        "sandbox": {
            "alias": "default",
            "image_digest": "sha256:" + "a" * 64,
            "toolchain_digest": "b" * 64,
            "catalog_revision": 1,
            "cpu_millicores": 100,
            "memory_bytes": 67108864,
            "pids_limit": 16,
            "temporary_bytes": 1048576,
        },
        "network": {},
    }
    profile.budget = {"input_tokens": 1000, "output_tokens": 1000}
    profile.save(
        update_fields=["agent_role", "desired_state", "enabled_revision", "policy", "budget"]
    )
    return profile


def _ready_kaki_profile(owner: UserProfile, *, name: str = "Kaki") -> agents.AgentProfile:
    """A ready and enabled built-in planner profile with its own bot user.
    A job for it can be claimed and run to its end."""
    runner = agents.AgentRunner.objects.create(
        realm=owner.realm,
        owner=owner,
        name="Kaki runner",
        fingerprint="k" * 64,
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
    profile = create_profile(
        owner,
        name=name,
        runner=runner,
        adapter_id="acp",
        adapter_version="1",
        idempotency_key=uuid4(),
        # Only a built-in profile may take a reserved name such as "Kaki".
        is_builtin=True,
        agent_role="planner",
    )
    setup = agents.AgentSetupOperation.objects.get(profile=profile)
    record_readiness(
        runner,
        setup,
        {
            "schema_version": 1,
            "profile_id": str(profile.id),
            "profile_revision": 1,
            "runner_id": str(runner.id),
            "descriptor_digest": setup.descriptor_digest,
            "configuration_digest": setup.configuration_digest,
            "state": "ready",
            "capabilities": {"chat_ready": True, "config_version": 1},
        },
    )
    profile.refresh_from_db()
    enable_profile(owner, profile, expected_revision=profile.revision)
    profile.refresh_from_db()
    return profile


def _backdate(message_id: int, when: datetime) -> None:
    Message.objects.filter(id=message_id).update(date_sent=when)


def _context_ids(job: agents.AgentJob) -> set[int]:
    """The ids of the messages that `job` reads."""
    return set(
        agents.AgentContextRef.objects.filter(job=job, kind="message").values_list(
            "message_id", flat=True
        )
    )


class RoomDigestTestCase(ZulipTestCase):
    def run_job_to_its_end(
        self, kaki: agents.AgentProfile, job: agents.AgentJob, answer: str, *, publish: bool
    ) -> None:
        """Run `job` the way its runner does: claim it, prepare `answer`, and
        stop the attempt. `publish` also runs the hooks that publish the
        answer when the attempt stops."""
        claim_work(kaki.runner, claim_key=uuid4())
        attempt = agents.AgentAttempt.objects.get(job=job)
        sequence = 0

        def event(kind: str, payload: dict[str, object]) -> None:
            nonlocal sequence
            sequence += 1
            record_event(
                kaki.runner,
                p.RunnerEvent.model_validate(
                    {
                        "schema_version": 1,
                        "job_id": str(job.id),
                        "attempt_id": str(attempt.id),
                        "lease_epoch": 1,
                        "event_id": str(uuid4()),
                        "sequence": sequence,
                        "type": kind,
                        "occurred_at": now().isoformat(),
                        "payload": payload,
                    }
                ),
            )

        event("attempt.started", {"process_state": "active"})
        content = answer.encode()
        artifact = store_artifact(
            kaki.runner,
            job.id,
            attempt.id,
            1,
            chunks=[content],
            checksum=hashlib.sha256(content).hexdigest(),
            kind="summary",
            filename="answer.txt",
            media_type="text/plain",
        )
        event("result.prepared", {"artifact_ids": [str(artifact.id)], "summary": answer})
        with self.captureOnCommitCallbacks(execute=publish):
            event("attempt.stopped", {"process_state": "stopped", "stop_confirmed": True})

    def send_at(
        self,
        sender: UserProfile,
        stream_name: str,
        when: datetime,
        content: str = "A message.",
        topic_name: str = "test",
    ) -> int:
        message_id = self.send_stream_message(sender, stream_name, content, topic_name=topic_name)
        _backdate(message_id, when)
        return message_id


class RoomDigestScheduleTest(RoomDigestTestCase):
    @override
    def setUp(self) -> None:
        super().setUp()
        self.owner = self.example_user("hamlet")
        self.realm = self.owner.realm
        _set_realm_settings(self.realm)
        self.kaki = _ready_kaki_profile(self.owner)
        self.room = self.subscribe(self.owner, "digest-schedule")
        _opt_in(self.room, self.owner)
        self.send_at(self.owner, "digest-schedule", AT_SEVEN_JAKARTA - timedelta(hours=2))

    def test_waits_until_seven_local(self) -> None:
        with time_machine.travel(AT_SEVEN_JAKARTA - timedelta(minutes=1), tick=False):
            self.assertEqual(send_realm_room_digests(self.realm), 0)
        self.assertFalse(RoomDigest.objects.filter(stream=self.room).exists())

    def test_runs_at_seven_local(self) -> None:
        with time_machine.travel(AT_SEVEN_JAKARTA, tick=False):
            self.assertEqual(send_realm_room_digests(self.realm), 1)
        self.assertTrue(
            RoomDigest.objects.filter(stream=self.room, date=AT_SEVEN_JAKARTA.date()).exists()
        )

    def test_uses_the_timezone_of_the_workspace(self) -> None:
        _set_realm_settings(self.realm, timezone="America/New_York")
        # 06:00 UTC is 13:00 in Jakarta, but 02:00 in New York (UTC-4 in
        # September): too early there.
        with time_machine.travel(AFTER_DIGEST_HOUR, tick=False):
            self.assertEqual(send_realm_room_digests(self.realm), 0)
        # 07:00 in New York comes five hours later.
        with time_machine.travel(AFTER_DIGEST_HOUR + timedelta(hours=5), tick=False):
            self.assertEqual(send_realm_room_digests(self.realm), 1)
        digest = RoomDigest.objects.get(stream=self.room)
        self.assertEqual(digest.date, AFTER_DIGEST_HOUR.date())

    def test_one_digest_a_day(self) -> None:
        with time_machine.travel(AT_SEVEN_JAKARTA, tick=False):
            self.assertEqual(send_realm_room_digests(self.realm), 1)
        self.send_at(self.owner, "digest-schedule", AT_SEVEN_JAKARTA + timedelta(hours=3))

        # More news the same day does not start a second digest.
        with time_machine.travel(AT_SEVEN_JAKARTA + timedelta(hours=5), tick=False):
            self.assertEqual(send_realm_room_digests(self.realm), 0)
        self.assertEqual(RoomDigest.objects.filter(stream=self.room).count(), 1)

        # The next morning it does, and it covers only the news since the
        # digest before.
        with time_machine.travel(AT_SEVEN_JAKARTA + timedelta(days=1), tick=False):
            self.assertEqual(send_realm_room_digests(self.realm), 1)
        second = RoomDigest.objects.get(
            stream=self.room, date=AT_SEVEN_JAKARTA.date() + timedelta(1)
        )
        self.assertEqual(second.message_count, 1)

    def test_budget_used_up_skips_the_workspace(self) -> None:
        with (
            time_machine.travel(AT_SEVEN_JAKARTA, tick=False),
            mock.patch("zerver.lib.room_digests.model_budget_state", return_value="exceeded"),
        ):
            self.assertEqual(send_realm_room_digests(self.realm), 0)
        self.assertFalse(RoomDigest.objects.filter(stream=self.room).exists())

    def test_skips_a_workspace_without_kaki(self) -> None:
        agents.AgentProfile.objects.filter(id=self.kaki.id).update(agent_role="custom")
        with time_machine.travel(AT_SEVEN_JAKARTA, tick=False):
            self.assertEqual(send_realm_room_digests(self.realm), 0)

    def test_finds_kaki_by_role_after_a_rename(self) -> None:
        agents.AgentProfile.objects.filter(id=self.kaki.id).update(name="Sensei")
        with time_machine.travel(AT_SEVEN_JAKARTA, tick=False):
            self.assertEqual(send_realm_room_digests(self.realm), 1)


class SendRoomDigestsCommandTest(RoomDigestTestCase):
    def test_starts_the_digest_of_each_workspace(self) -> None:
        owner = self.example_user("hamlet")
        _set_realm_settings(owner.realm)
        _ready_kaki_profile(owner)
        room = self.subscribe(owner, "digest-command")
        _opt_in(room, owner)
        self.send_at(owner, "digest-command", AT_SEVEN_JAKARTA - timedelta(hours=1))

        out = io.StringIO()
        with time_machine.travel(AT_SEVEN_JAKARTA, tick=False):
            call_command("send_room_digests", stdout=out)

        self.assertEqual(out.getvalue().strip(), "Started 1 summary job(s).")
        self.assertTrue(RoomDigest.objects.filter(stream=room).exists())

    def test_starts_nothing_before_seven(self) -> None:
        owner = self.example_user("hamlet")
        _set_realm_settings(owner.realm)
        _ready_kaki_profile(owner)
        room = self.subscribe(owner, "digest-command-early")
        _opt_in(room, owner)
        self.send_at(owner, "digest-command-early", AT_SEVEN_JAKARTA - timedelta(hours=1))

        out = io.StringIO()
        with time_machine.travel(AT_SEVEN_JAKARTA - timedelta(minutes=1), tick=False):
            call_command("send_room_digests", stdout=out)

        self.assertEqual(out.getvalue().strip(), "Started 0 summary job(s).")


class RoomDigestRoomTest(RoomDigestTestCase):
    @override
    def setUp(self) -> None:
        super().setUp()
        self.owner = self.example_user("hamlet")
        self.realm = self.owner.realm
        _set_realm_settings(self.realm)
        self.when = AFTER_DIGEST_HOUR - timedelta(hours=2)

    def run_digests(self) -> int:
        with time_machine.travel(AFTER_DIGEST_HOUR, tick=False):
            return send_realm_room_digests(self.realm)

    def test_room_that_did_not_opt_in_is_not_read(self) -> None:
        _enabled_kaki(self.owner, self.example_user("default_bot"))
        room = self.subscribe(self.owner, "digest-off")
        self.send_at(self.owner, "digest-off", self.when)

        with mock.patch("zerver.lib.room_digests._recent_message_ids") as read_history:
            self.assertEqual(self.run_digests(), 0)
        read_history.assert_not_called()
        self.assertFalse(RoomDigest.objects.filter(stream=room).exists())
        self.assertFalse(agents.AgentJob.objects.filter(realm=self.realm).exists())

    def test_room_that_turned_summaries_off_again_is_not_read(self) -> None:
        _enabled_kaki(self.owner, self.example_user("default_bot"))
        room = self.subscribe(self.owner, "digest-off-again")
        room_meta = _opt_in(room, self.owner)
        room_meta.summary_enabled = False
        room_meta.save(update_fields=["summary_enabled"])
        self.send_at(self.owner, "digest-off-again", self.when)

        with mock.patch("zerver.lib.room_digests._recent_message_ids") as read_history:
            self.assertEqual(self.run_digests(), 0)
        read_history.assert_not_called()

    def test_room_without_new_messages_is_skipped(self) -> None:
        _ready_kaki_profile(self.owner)
        room = self.subscribe(self.owner, "digest-stale")
        room_meta = _opt_in(room, self.owner)
        self.send_at(self.owner, "digest-stale", self.when)
        room_meta.last_digest_at = self.when + timedelta(minutes=30)
        room_meta.save(update_fields=["last_digest_at"])

        self.assertEqual(self.run_digests(), 0)
        self.assertFalse(RoomDigest.objects.filter(stream=room).exists())

    def test_first_digest_covers_the_last_day_only(self) -> None:
        _ready_kaki_profile(self.owner)
        room = self.subscribe(self.owner, "digest-old")
        _opt_in(room, self.owner)
        self.send_at(self.owner, "digest-old", AFTER_DIGEST_HOUR - timedelta(days=3))

        self.assertEqual(self.run_digests(), 0)

    def test_new_messages_start_a_digest_job_for_kaki(self) -> None:
        room_owner = self.example_user("othello")
        kaki = _ready_kaki_profile(self.owner)
        room = _own_stream(self.subscribe(room_owner, "digest-fresh"), room_owner)
        self.subscribe(self.owner, "digest-fresh")
        _opt_in(room, self.owner)
        first = self.send_at(self.owner, "digest-fresh", self.when, topic_name="alpha")
        second = self.send_at(room_owner, "digest-fresh", self.when, topic_name="beta")

        self.assertEqual(self.run_digests(), 1)

        digest = RoomDigest.objects.get(stream=room, date=AFTER_DIGEST_HOUR.date())
        assert digest.job is not None and digest.source_message is not None
        # The digest waits for its job.
        self.assertEqual((digest.summary, digest.decided, digest.message_count), ("", [], 2))
        job = digest.job
        self.assertEqual(job.profile_id, kaki.id)
        # The job runs for the person who turned summaries on, not for the
        # owner of the room.
        self.assertEqual(job.requester_id, self.owner.id)
        self.assertEqual(job.job_kind, "answer")
        self.assertEqual(job.trigger_kind, "manual")
        self.assertEqual(job.source_message_id, digest.source_message_id)
        # The messages of every topic are the context.
        self.assertEqual(_context_ids(job), {first, second, digest.source_message_id})
        # Kaki opens the topic of its summaries with a line that people read.
        self.assertEqual(digest.source_message.sender_id, kaki.bot_user_id)
        self.assertEqual(digest.source_message.topic_name(), str(ROOM_DIGEST_TOPIC_NAME))
        self.assertEqual(
            digest.source_message.content, "Summarize this channel since the last summary."
        )
        self.assertTrue(job.request.startswith(digest.source_message.content))
        self.assertIn('"decided"', job.request)
        room_meta = RoomMeta.objects.get(stream=room)
        self.assertEqual(room_meta.last_digest_at, AFTER_DIGEST_HOUR)

    def test_card_title_is_the_first_line_of_the_request(self) -> None:
        _ready_kaki_profile(self.owner)
        room = self.subscribe(self.owner, "digest-title")
        _opt_in(room, self.owner)
        self.send_at(self.owner, "digest-title", self.when)
        self.assertEqual(self.run_digests(), 1)

        job = RoomDigest.objects.get(stream=room).job
        assert job is not None
        self.assertEqual(
            card_extra_data(job)["title"], "Summarize this channel since the last summary."
        )

    def test_digest_of_an_earlier_day_does_not_keep_a_quiet_room_awake(self) -> None:
        _ready_kaki_profile(self.owner)
        room = self.subscribe(self.owner, "digest-quiet")
        _opt_in(room, self.owner)
        self.send_at(self.owner, "digest-quiet", self.when)
        self.assertEqual(self.run_digests(), 1)
        job = RoomDigest.objects.get(stream=room).job
        assert job is not None
        # Kaki's own line and card sit in the room now.
        post_admission_card(job.id, acknowledge=False)
        job.refresh_from_db()
        self.assertIsNotNone(job.result_message_id)

        with time_machine.travel(AFTER_DIGEST_HOUR + timedelta(days=1), tick=False):
            self.assertEqual(send_realm_room_digests(self.realm), 0)
        self.assertEqual(RoomDigest.objects.filter(stream=room).count(), 1)

    def test_falls_back_to_the_room_owner_when_the_enabler_left(self) -> None:
        enabler = self.example_user("othello")
        _ready_kaki_profile(self.owner)
        room = _own_stream(self.subscribe(self.owner, "digest-fallback"), self.owner)
        self.subscribe(enabler, "digest-fallback")
        _opt_in(room, enabler)
        do_deactivate_user(enabler, acting_user=None)
        self.send_at(self.owner, "digest-fallback", self.when)

        self.assertEqual(self.run_digests(), 1)
        job = RoomDigest.objects.get(stream=room).job
        assert job is not None
        self.assertEqual(job.requester_id, self.owner.id)

    def test_skips_a_room_that_has_no_person_to_act_for(self) -> None:
        # Kaki belongs to a user who stays active: deactivating a user also
        # deactivates the bots that this user owns.
        _ready_kaki_profile(self.example_user("othello"))
        room = self.subscribe(self.owner, "digest-nobody")
        room.creator = None
        room.save(update_fields=["creator"])
        _opt_in(room, self.owner)
        self.send_at(self.owner, "digest-nobody", self.when)
        do_deactivate_user(self.owner, acting_user=None)
        UserProfile.objects.filter(
            realm=self.realm, role=UserProfile.ROLE_REALM_OWNER, is_active=True
        ).update(is_active=False)

        self.assertEqual(self.run_digests(), 0)
        self.assertFalse(RoomDigest.objects.filter(stream=room).exists())

    def test_skips_an_archived_room(self) -> None:
        _ready_kaki_profile(self.owner)
        room = self.subscribe(self.owner, "digest-archived")
        _opt_in(room, self.owner)
        self.send_at(self.owner, "digest-archived", self.when)
        do_deactivate_stream(room, acting_user=None)

        self.assertEqual(self.run_digests(), 0)

    def test_adds_kaki_to_a_private_room(self) -> None:
        # A bot that has no owner cannot post through an owner who is in the
        # room, so Kaki must join the room itself.
        bot_user = do_create_user(
            "digest-kaki-bot@zulip.testserver",
            password=None,
            realm=self.realm,
            full_name="Kaki",
            bot_type=UserProfile.DEFAULT_BOT,
            acting_user=None,
        )
        _enabled_kaki(self.owner, bot_user)
        self.make_stream("digest-private", invite_only=True, history_public_to_subscribers=True)
        room = self.subscribe(self.owner, "digest-private")
        _opt_in(room, self.owner)
        self.send_at(self.owner, "digest-private", self.when)

        self.assertEqual(self.run_digests(), 1)
        self.assertTrue(subscribed_to_stream(bot_user, room.id))

    def test_a_room_that_fails_does_not_stop_the_others(self) -> None:
        _ready_kaki_profile(self.owner)
        broken = self.subscribe(self.owner, "digest-broken")
        fine = self.subscribe(self.owner, "digest-fine")
        for room in (broken, fine):
            _opt_in(room, self.owner)
            self.send_at(self.owner, room.name, self.when)
        real_create = create_room_digest_job

        def create(
            stream: Stream,
            actor: UserProfile,
            kaki: agents.AgentProfile,
            *,
            today: date,
            context_ids: list[int],
        ) -> agents.AgentJob:
            if stream.id == broken.id:
                raise ValueError("Broken.")
            return real_create(stream, actor, kaki, today=today, context_ids=context_ids)

        with (
            mock.patch("zerver.lib.room_digests.create_room_digest_job", side_effect=create),
            self.assertLogs("zerver.lib.room_digests", level="WARNING") as logs,
        ):
            self.assertEqual(self.run_digests(), 1)
        self.assertIn(f"No digest for channel {broken.id}", logs.output[0])
        self.assertFalse(RoomDigest.objects.filter(stream=broken).exists())
        self.assertTrue(RoomDigest.objects.filter(stream=fine).exists())

    def test_a_workspace_that_turned_agents_off_is_left_alone(self) -> None:
        _ready_kaki_profile(self.owner)
        room = self.subscribe(self.owner, "digest-no-agents")
        _opt_in(room, self.owner)
        self.send_at(self.owner, "digest-no-agents", self.when)
        AgentRealmSettings.objects.filter(realm=self.realm).update(enabled=False)

        with mock.patch("zerver.lib.room_digests._recent_message_ids") as read_history:
            self.assertEqual(self.run_digests(), 0)
        read_history.assert_not_called()
        self.assertFalse(RoomDigest.objects.filter(stream=room).exists())

    def test_a_busy_lock_leaves_the_room_for_the_next_run(self) -> None:
        _ready_kaki_profile(self.owner)
        room = self.subscribe(self.owner, "digest-busy")
        _opt_in(room, self.owner)
        self.send_at(self.owner, "digest-busy", self.when)

        with mock.patch(
            "zerver.lib.room_digests.create_room_digest_job", side_effect=AgentBusy("Busy.")
        ):
            self.assertEqual(self.run_digests(), 0)
        self.assertFalse(RoomDigest.objects.filter(stream=room).exists())
        self.assertEqual(self.run_digests(), 1)

    def test_two_runs_at_once_start_one_digest(self) -> None:
        _ready_kaki_profile(self.owner)
        room = self.subscribe(self.owner, "digest-race")
        _opt_in(room, self.owner)
        self.send_at(self.owner, "digest-race", self.when)
        real_read = _recent_message_ids

        def read_then_lose_the_race(stream: Stream, since: datetime) -> list[int]:
            ids = real_read(stream, since)
            # Another run finishes its digest of this room after this run
            # decided to start one.
            RoomDigest.objects.create(stream=stream, date=AFTER_DIGEST_HOUR.date())
            return ids

        with mock.patch(
            "zerver.lib.room_digests._recent_message_ids", side_effect=read_then_lose_the_race
        ):
            self.assertEqual(self.run_digests(), 0)
        self.assertEqual(RoomDigest.objects.filter(stream=room).count(), 1)
        self.assertFalse(agents.AgentJob.objects.filter(realm=self.realm).exists())

    def test_summaries_turned_off_at_the_last_moment_start_nothing(self) -> None:
        _ready_kaki_profile(self.owner)
        room = self.subscribe(self.owner, "digest-late-off")
        room_meta = _opt_in(room, self.owner)
        self.send_at(self.owner, "digest-late-off", self.when)
        real_read = _recent_message_ids

        def read_then_turn_off(stream: Stream, since: datetime) -> list[int]:
            ids = real_read(stream, since)
            RoomMeta.objects.filter(id=room_meta.id).update(summary_enabled=False)
            return ids

        with mock.patch(
            "zerver.lib.room_digests._recent_message_ids", side_effect=read_then_turn_off
        ):
            self.assertEqual(self.run_digests(), 0)
        self.assertFalse(agents.AgentJob.objects.filter(realm=self.realm).exists())


class RecentMessageIdsTest(RoomDigestTestCase):
    def test_caps_at_the_context_limit_and_keeps_the_newest(self) -> None:
        owner = self.example_user("hamlet")
        stream = self.subscribe(owner, "digest-cap")
        since = AFTER_DIGEST_HOUR - timedelta(days=1)
        sent_ids = [
            self.send_at(owner, "digest-cap", AFTER_DIGEST_HOUR, f"message {i}")
            for i in range(CONTEXT_MESSAGE_LIMIT + 5)
        ]

        ids = _recent_message_ids(stream, since)
        self.assertEqual(ids, sorted(sent_ids[-CONTEXT_MESSAGE_LIMIT:]))

    def test_keeps_only_messages_after_the_time(self) -> None:
        owner = self.example_user("hamlet")
        stream = self.subscribe(owner, "digest-since")
        old_id = self.send_at(owner, "digest-since", AFTER_DIGEST_HOUR - timedelta(hours=1))
        new_id = self.send_at(owner, "digest-since", AFTER_DIGEST_HOUR + timedelta(hours=1))

        self.assertEqual(_recent_message_ids(stream, AFTER_DIGEST_HOUR), [new_id])
        self.assertIn(old_id, _recent_message_ids(stream, AFTER_DIGEST_HOUR - timedelta(days=1)))

    def test_leaves_out_the_line_and_the_card_of_a_digest(self) -> None:
        owner = self.example_user("hamlet")
        stream = self.subscribe(owner, "digest-own-posts")
        line_id = self.send_at(owner, "digest-own-posts", AFTER_DIGEST_HOUR)
        card_id = self.send_at(owner, "digest-own-posts", AFTER_DIGEST_HOUR)
        person_id = self.send_at(owner, "digest-own-posts", AFTER_DIGEST_HOUR)
        kaki = _enabled_kaki(owner, self.example_user("default_bot"))
        _set_realm_settings(owner.realm)
        digest_job = create_job(
            owner,
            profile=kaki,
            source=Message.objects.get(id=line_id),
            request="Summarize.",
            idempotency_key=uuid4(),
            job_kind="answer",
            delivery_target="answer",
            allow_blocked=True,
        )
        agents.AgentJob.objects.filter(id=digest_job.id).update(result_message_id=card_id)
        RoomDigest.objects.create(
            stream=stream,
            date=AFTER_DIGEST_HOUR.date(),
            source_message_id=line_id,
            job=digest_job,
        )

        ids = _recent_message_ids(stream, AFTER_DIGEST_HOUR - timedelta(days=1))
        self.assertEqual(ids, [person_id])


class StreamAudienceCacheTest(RoomDigestTestCase):
    """A digest job reads dozens of messages of one room. Working out the
    room's audience for each of them would not fit in the time of the job's
    transaction in a big workspace."""

    def digest_job(self, message_count: int) -> agents.AgentJob:
        owner = self.example_user("hamlet")
        _set_realm_settings(owner.realm)
        kaki = _enabled_kaki(owner, self.example_user("default_bot"))
        stream = self.subscribe(owner, "digest-audience")
        _opt_in(stream, owner)
        context_ids = [
            self.send_stream_message(owner, "digest-audience", f"Message {i}.")
            for i in range(message_count)
        ]
        return create_room_digest_job(
            stream, owner, kaki, today=AFTER_DIGEST_HOUR.date(), context_ids=context_ids
        )

    def test_a_job_works_out_the_audience_of_its_room_once(self) -> None:
        with mock.patch.object(
            agent_context, "_stream_members", wraps=agent_context._stream_members
        ) as members:
            self.digest_job(message_count=10)
        # Once for the conversation of the job, and once for all the messages.
        self.assertEqual(members.call_count, 2)

    def test_the_block_gives_the_same_audience_as_no_block(self) -> None:
        job = self.digest_job(message_count=1)
        args = (job.conversation, job.requester, job.profile.bot_user)
        plain = current_audience(*args)

        with (
            stream_audience_cache(),
            mock.patch.object(
                agent_context, "_stream_members", wraps=agent_context._stream_members
            ) as members,
        ):
            first = current_audience(*args)
            second = current_audience(*args)

        self.assertEqual(members.call_count, 1)
        self.assertEqual(plain, first)
        self.assertEqual(plain, second)

    def test_outside_the_block_every_call_works_it_out(self) -> None:
        job = self.digest_job(message_count=1)
        args = (job.conversation, job.requester, job.profile.bot_user)

        with mock.patch.object(
            agent_context, "_stream_members", wraps=agent_context._stream_members
        ) as members:
            current_audience(*args)
            current_audience(*args)

        self.assertEqual(members.call_count, 2)


class CoerceStrListTest(ZulipTestCase):
    def test_list_keeps_the_non_blank_items_as_lines(self) -> None:
        self.assertEqual(_coerce_str_list(["a", 1, "  ", "", "b\n  c"]), ["a", "1", "b c"])

    def test_one_string_is_a_list_of_one_item(self) -> None:
        self.assertEqual(_coerce_str_list("Ship it."), ["Ship it."])

    def test_blank_string_and_other_types_are_an_empty_list(self) -> None:
        self.assertEqual(_coerce_str_list("   "), [])
        self.assertEqual(_coerce_str_list(None), [])
        self.assertEqual(_coerce_str_list(3), [])


class SummarizeRoomTest(RoomDigestTestCase):
    @override
    def setUp(self) -> None:
        super().setUp()
        self.owner = self.example_user("hamlet")
        self.realm = self.owner.realm
        _set_realm_settings(self.realm)
        self.room = _own_stream(self.subscribe(self.owner, "digest-summarize"), self.owner)

    def summarize(self, user: UserProfile | None = None) -> "TestHttpResponse":
        self.login_user(user or self.owner)
        return self.client_post(f"/json/streams/{self.room.id}/summarize")

    def test_only_the_owner_may_ask(self) -> None:
        admin = self.example_user("iago")
        self.subscribe(admin, "digest-summarize")
        _opt_in(self.room, self.owner)

        result = self.summarize(admin)
        self.assert_json_error(result, "Only the channel owner can ask for a summary.")

    def test_a_person_without_access_to_the_room_is_refused(self) -> None:
        private = _own_stream(self.make_stream("digest-secret", invite_only=True), self.owner)
        self.login_user(self.example_user("othello"))
        result = self.client_post(f"/json/streams/{private.id}/summarize")
        self.assert_json_error(result, "Invalid channel ID")

    def test_needs_summaries_turned_on(self) -> None:
        result = self.summarize()
        self.assert_json_error(result, "Turn on summaries for this channel first.")
        self.assertFalse(RoomDigest.objects.filter(stream=self.room).exists())

    def test_needs_budget(self) -> None:
        _opt_in(self.room, self.owner)
        with mock.patch("zerver.lib.room_digests.model_budget_state", return_value="exceeded"):
            result = self.summarize()
        self.assert_json_error(
            result, "The budget for this month is used up. Kaki cannot write a summary now."
        )

    def test_needs_kaki(self) -> None:
        _opt_in(self.room, self.owner)
        result = self.summarize()
        self.assert_json_error(
            result, "Kaki is not available in this workspace. Ask an admin to turn it on."
        )

    def test_needs_new_messages(self) -> None:
        _ready_kaki_profile(self.owner)
        _opt_in(self.room, self.owner)
        self.send_at(self.owner, "digest-summarize", now() - timedelta(days=2), "An old message.")

        result = self.summarize()
        self.assert_json_error(result, "There are no new messages to summarize.")
        self.assertFalse(RoomDigest.objects.filter(stream=self.room).exists())

    def test_starts_a_summary_of_the_last_day(self) -> None:
        kaki = _ready_kaki_profile(self.owner)
        room_meta = _opt_in(self.room, self.owner)
        first = self.send_stream_message(self.owner, "digest-summarize", "One.", topic_name="a")
        second = self.send_stream_message(self.owner, "digest-summarize", "Two.", topic_name="b")

        result = self.summarize()

        job_id = self.assert_json_success(result)["job_id"]
        digest = RoomDigest.objects.get(stream=self.room)
        assert digest.job is not None
        self.assertEqual(str(digest.job.id), job_id)
        self.assertEqual(digest.job.profile_id, kaki.id)
        self.assertEqual(digest.job.requester_id, self.owner.id)
        self.assertEqual(digest.message_count, 2)
        self.assertEqual(_context_ids(digest.job), {first, second, digest.source_message_id})
        # A summary that a person asked for leaves the schedule alone.
        room_meta.refresh_from_db()
        self.assertIsNone(room_meta.last_digest_at)

    def test_a_summary_that_is_being_written_is_not_started_twice(self) -> None:
        _ready_kaki_profile(self.owner)
        _opt_in(self.room, self.owner)
        self.send_stream_message(self.owner, "digest-summarize", "One.")

        first = self.assert_json_success(self.summarize())["job_id"]
        source_count = Message.objects.filter(recipient=self.room.recipient).count()
        second = self.assert_json_success(self.summarize())["job_id"]

        self.assertEqual(first, second)
        self.assertEqual(
            Message.objects.filter(recipient=self.room.recipient).count(), source_count
        )
        self.assertEqual(agents.AgentJob.objects.filter(realm=self.realm).count(), 1)

    def test_a_summary_that_ended_is_written_again(self) -> None:
        _ready_kaki_profile(self.owner)
        _opt_in(self.room, self.owner)
        self.send_stream_message(self.owner, "digest-summarize", "One.")
        first = self.assert_json_success(self.summarize())["job_id"]
        agents.AgentJob.objects.filter(id=first).update(status="cancelled")

        second = self.assert_json_success(self.summarize())["job_id"]

        self.assertNotEqual(first, second)
        self.assertEqual(RoomDigest.objects.filter(stream=self.room).count(), 1)
        self.assertEqual(str(RoomDigest.objects.get(stream=self.room).job_id), second)

    def test_a_job_that_cannot_start_says_so_without_detail(self) -> None:
        _ready_kaki_profile(self.owner)
        _opt_in(self.room, self.owner)
        self.send_stream_message(self.owner, "digest-summarize", "One.")

        for error in (
            ValueError("Agent queue is full."),
            AgentAccessDenied("Agent access denied."),
            AgentRealmSettings.DoesNotExist(),
        ):
            with mock.patch("zerver.lib.room_digests.create_room_digest_job", side_effect=error):
                result = self.summarize()
            self.assert_json_error(
                result, "Kaki cannot write a summary for this channel right now."
            )


class GetRoomDigestTest(RoomDigestTestCase):
    def test_needs_access_to_the_room(self) -> None:
        owner = self.example_user("hamlet")
        private = _own_stream(self.make_stream("digest-view-secret", invite_only=True), owner)

        self.login_user(self.example_user("othello"))
        result = self.client_get(f"/json/streams/{private.id}/digest")
        self.assert_json_error(result, "Invalid channel ID")

    def test_is_null_without_a_digest(self) -> None:
        owner = self.example_user("hamlet")
        room = self.subscribe(owner, "digest-view-none")

        self.login_user(owner)
        data = self.assert_json_success(self.client_get(f"/json/streams/{room.id}/digest"))
        self.assertIsNone(data["digest"])

    def test_returns_the_digest_of_the_day(self) -> None:
        owner = self.example_user("hamlet")
        _set_realm_settings(owner.realm)
        room = self.subscribe(owner, "digest-view")
        RoomDigest.objects.create(
            stream=room,
            date=AFTER_DIGEST_HOUR.date(),
            decided=["Ship it."],
            waiting=["A design review."],
            summary="A quiet day.",
            message_count=3,
        )

        self.login_user(owner)
        with mock.patch("zerver.lib.room_digests.timezone_now", return_value=AFTER_DIGEST_HOUR):
            data = self.assert_json_success(self.client_get(f"/json/streams/{room.id}/digest"))
        self.assertEqual(
            data["digest"],
            {
                "date": "2026-09-27",
                "decided": ["Ship it."],
                "blocker": [],
                "waiting": ["A design review."],
                "summary": "A quiet day.",
                "message_count": 3,
            },
        )

    def test_today_is_the_day_of_the_workspace(self) -> None:
        owner = self.example_user("hamlet")
        _set_realm_settings(owner.realm)
        room = self.subscribe(owner, "digest-view-tz")
        RoomDigest.objects.create(stream=room, date=AFTER_DIGEST_HOUR.date(), summary="Yesterday.")

        self.login_user(owner)
        # 18:00 UTC is 01:00 of the next day in Jakarta.
        with mock.patch(
            "zerver.lib.room_digests.timezone_now",
            return_value=AFTER_DIGEST_HOUR + timedelta(hours=12),
        ):
            data = self.assert_json_success(self.client_get(f"/json/streams/{room.id}/digest"))
        self.assertIsNone(data["digest"])

    def test_returns_the_digest_of_the_day_asked_for(self) -> None:
        owner = self.example_user("hamlet")
        room = self.subscribe(owner, "digest-view-date")
        RoomDigest.objects.create(stream=room, date=AFTER_DIGEST_HOUR.date(), summary="A day.")

        self.login_user(owner)
        data = self.assert_json_success(
            self.client_get(f"/json/streams/{room.id}/digest", {"date": "2026-09-27"})
        )
        self.assertEqual(data["digest"]["summary"], "A day.")
        other = self.assert_json_success(
            self.client_get(f"/json/streams/{room.id}/digest", {"date": "2026-09-28"})
        )
        self.assertIsNone(other["digest"])

    def test_rejects_a_date_that_is_not_a_date(self) -> None:
        owner = self.example_user("hamlet")
        room = self.subscribe(owner, "digest-view-bad")

        self.login_user(owner)
        result = self.client_get(f"/json/streams/{room.id}/digest", {"date": "30-10-2026"})
        self.assert_json_error(result, "Enter the date as YYYY-MM-DD.")


class FillRoomDigestTest(RoomDigestTestCase):
    def digest_job(self) -> tuple[agents.AgentJob, RoomDigest]:
        owner = self.example_user("hamlet")
        _set_realm_settings(owner.realm)
        kaki = _enabled_kaki(owner, self.example_user("default_bot"))
        stream = self.subscribe(owner, "digest-fill")
        _opt_in(stream, owner)
        job = create_room_digest_job(
            stream, owner, kaki, today=AFTER_DIGEST_HOUR.date(), context_ids=[]
        )
        return job, RoomDigest.objects.get(job=job)

    def test_fills_the_digest_and_writes_a_message_for_people(self) -> None:
        job, digest = self.digest_job()
        answer = json.dumps(
            {
                "decided": ["Ship it."],
                "blocker": [],
                "waiting": ["A design review."],
                "summary": "A quiet day.",
            }
        )

        text = fill_room_digest(job, answer)

        digest.refresh_from_db()
        self.assertEqual(digest.decided, ["Ship it."])
        self.assertEqual(digest.blocker, [])
        self.assertEqual(digest.waiting, ["A design review."])
        self.assertEqual(digest.summary, "A quiet day.")
        self.assertEqual(
            text,
            "A quiet day.\n\n**Decisions**\n\n- Ship it.\n\n**Waiting on**\n\n- A design review.",
        )

    def test_reads_json_in_a_code_fence(self) -> None:
        job, digest = self.digest_job()

        text = fill_room_digest(job, '```json\n{"decided": ["A"], "summary": "S"}\n```')

        digest.refresh_from_db()
        self.assertEqual((digest.decided, digest.summary), (["A"], "S"))
        assert text is not None
        self.assertNotIn("```", text)

    def test_reads_json_after_a_line_of_prose(self) -> None:
        job, digest = self.digest_job()

        fill_room_digest(job, 'Here is the summary: {"waiting": ["B"], "summary": "S"}')

        digest.refresh_from_db()
        self.assertEqual((digest.waiting, digest.summary), (["B"], "S"))

    def test_broken_json_stays_as_text_in_the_summary(self) -> None:
        job, digest = self.digest_job()
        raw = '{"decided": ["A"'

        text = fill_room_digest(job, raw)

        digest.refresh_from_db()
        self.assertEqual(digest.summary, raw)
        self.assertEqual(digest.decided, [])
        self.assertEqual(text, raw)

    def test_json_that_is_not_an_object_stays_as_text(self) -> None:
        job, digest = self.digest_job()
        raw = json.dumps(["not", "an", "object"])

        text = fill_room_digest(job, raw)

        digest.refresh_from_db()
        self.assertEqual(digest.summary, raw)
        self.assertEqual(text, raw)

    def test_an_answer_with_nothing_to_report_reads_as_such(self) -> None:
        job, digest = self.digest_job()

        text = fill_room_digest(
            job, json.dumps({"decided": [], "blocker": [], "waiting": [], "summary": ""})
        )

        digest.refresh_from_db()
        self.assertEqual(digest.summary, "")
        self.assertEqual(text, "Nothing to report.")

    def test_sends_an_event_so_an_open_room_shows_the_banner(self) -> None:
        job, digest = self.digest_job()

        with self.capture_send_event_calls(expected_num_events=1) as events:
            fill_room_digest(job, json.dumps({"summary": "S"}))
        self.assertEqual(events[0]["event"]["type"], "room_meta")
        self.assertEqual(events[0]["event"]["stream_id"], digest.stream_id)

    def test_keeps_the_room_out_of_it_when_told_to(self) -> None:
        job, digest = self.digest_job()

        with self.capture_send_event_calls(expected_num_events=0):
            text = fill_room_digest(job, json.dumps({"summary": "S"}), keep=False)

        digest.refresh_from_db()
        self.assertEqual(digest.summary, "")
        self.assertEqual(text, "S")

    def test_tells_a_digest_job_from_any_other_job(self) -> None:
        job, _digest = self.digest_job()
        self.assertTrue(is_room_digest_job(job))
        self.assertEqual(ROOM_DIGEST_MODEL_PRESET, "fast")

    def test_ignores_a_job_that_is_not_a_digest(self) -> None:
        owner = self.example_user("hamlet")
        _set_realm_settings(owner.realm)
        kaki = _enabled_kaki(owner, self.example_user("default_bot"))
        self.subscribe(owner, "digest-fill-plain")
        message_id = self.send_stream_message(owner, "digest-fill-plain", "Hello.")
        job = create_job(
            owner,
            profile=kaki,
            source=Message.objects.get(id=message_id),
            request="Please answer.",
            idempotency_key=uuid4(),
            job_kind="answer",
            delivery_target="answer",
            allow_blocked=True,
        )
        self.assertIsNone(fill_room_digest(job, "Anything."))
        self.assertFalse(is_room_digest_job(job))


class PublishRoomDigestTest(RoomDigestTestCase):
    """A digest job runs through the real result pipeline."""

    @override
    def setUp(self) -> None:
        super().setUp()
        self.owner = self.example_user("hamlet")
        _set_realm_settings(self.owner.realm)
        self.kaki = _ready_kaki_profile(self.owner)
        self.room = self.subscribe(self.owner, "digest-publish")
        _opt_in(self.room, self.owner)
        self.job = create_room_digest_job(
            self.room, self.owner, self.kaki, today=AFTER_DIGEST_HOUR.date(), context_ids=[]
        )
        self.answer = json.dumps(
            {
                "decided": ["Ship it."],
                "blocker": ["Nothing."],
                "waiting": [],
                "summary": "Done.",
            }
        )

    def test_the_room_gets_a_digest_that_people_can_read(self) -> None:
        with (
            tempfile.TemporaryDirectory() as directory,
            override_settings(AGENT_ARTIFACT_ROOT=directory),
        ):
            self.run_job_to_its_end(self.kaki, self.job, self.answer, publish=True)
            self.job.refresh_from_db()
            self.assertEqual(self.job.status, "completed")
            publish_result(self.job.id)

        digest = RoomDigest.objects.get(job=self.job)
        self.assertEqual(digest.decided, ["Ship it."])
        self.assertEqual(digest.blocker, ["Nothing."])
        self.assertEqual(digest.summary, "Done.")
        self.job.refresh_from_db()
        assert self.job.result_message_id is not None
        posted = Message.objects.get(id=self.job.result_message_id)
        self.assertNotIn("{", posted.content)
        self.assertIn("Ship it.", posted.content)
        assert posted.rendered_content is not None
        self.assertIn("<ul>", posted.rendered_content)

    def test_an_answer_held_back_goes_to_one_person_and_not_to_the_room(self) -> None:
        with (
            tempfile.TemporaryDirectory() as directory,
            override_settings(AGENT_ARTIFACT_ROOT=directory),
        ):
            self.run_job_to_its_end(self.kaki, self.job, self.answer, publish=False)
            # The room's audience moves on while the answer waits.
            conversation = self.job.conversation
            binding = conversation.audience_binding
            assert binding is not None
            agents.AgentConversation.objects.filter(id=conversation.id).update(
                audience_binding={**binding, "epoch": binding["epoch"] + 1}
            )
            with self.assertRaises(ValueError):
                publish_result(self.job.id)
            self.job.refresh_from_db()
            deliver_result_privately(self.owner, self.job.id, self.job.version)

        digest = RoomDigest.objects.get(job=self.job)
        self.assertEqual((digest.summary, digest.decided), ("", []))
        self.job.refresh_from_db()
        assert self.job.result_receipt is not None
        private = Message.objects.get(id=self.job.result_receipt["message_id"])
        self.assertIn("Ship it.", private.content)
        self.assertNotIn("{", private.content)


class WorkAgentJobAccessTest(test_agents.AgentDirectoryTestCase):
    """A job of a member's work agent needs no runner or provider grant, the
    same rule as for starting the job."""

    @override
    def setUp(self) -> None:
        super().setUp()
        self.settings_row.work_runner = self.runner
        self.settings_row.work_provider = self.provider
        self.settings_row.save(update_fields=["work_runner", "work_provider"])
        self.login_user(self.member)
        result = self.post_agent(
            "profiles",
            self.profile_payload(
                name="Member's work agent",
                provider_id=None,
                mode="acp",
                adapter_id="bogus-adapter",
                adapter_version="0",
            ),
        )
        self.profile = self.make_ready(agents.AgentProfile.objects.get(id=result["profile"]["id"]))
        self.post_agent(
            f"profiles/{self.profile.id}/enable", {"expected_revision": self.profile.revision}
        )
        source = Message.objects.get(
            id=self.send_stream_message(self.member, "Verona", "Plan the launch")
        )
        self.job = create_job(
            self.member,
            profile=self.profile,
            source=source,
            request="Plan the launch",
            idempotency_key=uuid4(),
            job_kind="answer",
            delivery_target="answer",
        )

    def test_the_member_reads_the_job(self) -> None:
        require_job_access(self.member, self.job)

    def test_the_runner_claims_the_job_and_the_member_keeps_access(self) -> None:
        descriptor = claim_work(self.runner, claim_key=uuid4())
        assert descriptor is not None
        attempt = agents.AgentAttempt.objects.get(job=self.job)

        check_attempt_access(self.member, self.job, attempt, "profile.use")

    def test_a_changed_work_provider_closes_the_path(self) -> None:
        descriptor = claim_work(self.runner, claim_key=uuid4())
        assert descriptor is not None
        attempt = agents.AgentAttempt.objects.get(job=self.job)
        self.settings_row.work_provider = self.make_provider("Another provider")
        self.settings_row.save(update_fields=["work_provider"])

        with self.assertRaises(AgentAccessDenied):
            require_job_access(self.member, self.job)
        with self.assertRaises(AgentAccessDenied):
            check_attempt_access(self.member, self.job, attempt, "profile.use")
