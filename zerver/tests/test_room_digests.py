"""Tests for Kaki's opt-in morning room digest (PLAN.md WP24)."""

import json
from datetime import datetime, timedelta
from datetime import timezone as dt_timezone
from unittest import mock
from uuid import uuid4

import time_machine

from zerver.actions.agent_jobs import create_job
from zerver.actions.agents import create_profile, enable_profile, record_readiness
from zerver.actions.create_user import do_create_user
from zerver.actions.users import do_deactivate_user
from zerver.lib.room_digests import (
    CONTEXT_MESSAGE_LIMIT,
    _coerce_str_list,
    _recent_message_ids,
    create_room_digest_job,
    fill_room_digest,
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
from zerver.tests.test_room_meta import create_kaki

# A UTC instant that is exactly 07:00 in Asia/Jakarta (UTC+7), the digest hour.
AT_SEVEN_JAKARTA = datetime(2026, 9, 27, 0, 0, tzinfo=dt_timezone.utc)
# A UTC instant safely past 07:00 in Asia/Jakarta, for tests where the
# schedule gate itself is not what is under test.
AFTER_DIGEST_HOUR = datetime(2026, 9, 27, 6, 0, tzinfo=dt_timezone.utc)


def _set_realm_settings(realm: Realm, *, timezone: str = "Asia/Jakarta") -> None:
    AgentRealmSettings.objects.update_or_create(
        realm=realm, defaults={"enabled": True, "timezone": timezone}
    )


def _opt_in(stream: Stream, owner: UserProfile) -> RoomMeta:
    room_meta, _created = RoomMeta.objects.get_or_create(stream=stream)
    room_meta.summary_enabled = True
    room_meta.summary_enabled_by = owner
    room_meta.save(update_fields=["summary_enabled", "summary_enabled_by"])
    return room_meta


def _enabled_kaki(owner: UserProfile, bot_user: UserProfile) -> agents.AgentProfile:
    """A builtin "Kaki" profile good enough for create_job's allow_blocked
    path: that path only swallows the readiness failure when
    desired_state == "enabled" (zerver/actions/agent_jobs.py create_job),
    which this sets by hand instead of running the full readiness dance a
    job that must actually be claimed and completed needs (see
    _ready_kaki_profile below)."""
    profile = create_kaki(owner, bot_user)
    profile.desired_state = "enabled"
    # agent_profile_enable_revision requires enabled_revision whenever
    # desired_state is "enabled".
    profile.enabled_revision = profile.revision
    # create_job validates policy against protocol.Policy (AgentJob.clean).
    # create_kaki never sets policy, so give it the same shape
    # zerver.actions.agents._default_policy gives a real, freshly created
    # profile.
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
    # create_job also validates budget against protocol.Budget. AgentProfile
    # defaults it to {}, which create_kaki never replaces.
    profile.budget = {"input_tokens": 1000, "output_tokens": 1000}
    profile.save(update_fields=["desired_state", "enabled_revision", "policy", "budget"])
    return profile


def _ready_kaki_profile(owner: UserProfile) -> agents.AgentProfile:
    """A fully ready, enabled, built-in "Kaki" profile with a real bot
    user, so a job created against it can be claimed and run through to
    completion. Mirrors test_agents_lifecycle.AgentLifecycleTests.setUp's
    own readiness dance, as a standalone function."""
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
        name="Kaki",
        runner=runner,
        adapter_id="acp",
        adapter_version="1",
        idempotency_key=uuid4(),
        # "Kaki" is a reserved display name; only a builtin profile may
        # take it (zerver.actions.agents.create_profile).
        is_builtin=True,
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


class RoomDigestScheduleTest(ZulipTestCase):
    def test_skips_before_seven_local(self) -> None:
        owner = self.example_user("hamlet")
        realm = owner.realm
        _set_realm_settings(realm)
        room = self.subscribe(owner, "wp24-schedule-early")
        _opt_in(room, owner)
        self.send_stream_message(owner, "wp24-schedule-early")

        with time_machine.travel(AT_SEVEN_JAKARTA - timedelta(minutes=1), tick=False):
            self.assertEqual(send_realm_room_digests(realm), 0)
        self.assertFalse(RoomDigest.objects.filter(stream=room).exists())

    def test_runs_at_seven_local(self) -> None:
        owner = self.example_user("hamlet")
        realm = owner.realm
        _set_realm_settings(realm)
        _ready_kaki_profile(owner)
        room = self.subscribe(owner, "wp24-schedule-on-time")
        _opt_in(room, owner)
        self.send_stream_message(owner, "wp24-schedule-on-time")

        with time_machine.travel(AT_SEVEN_JAKARTA, tick=False):
            self.assertEqual(send_realm_room_digests(realm), 1)
        self.assertTrue(
            RoomDigest.objects.filter(stream=room, date=AT_SEVEN_JAKARTA.date()).exists()
        )

    def test_uses_realm_own_timezone_not_a_fixed_one(self) -> None:
        owner = self.example_user("hamlet")
        realm = owner.realm
        _set_realm_settings(realm, timezone="America/New_York")
        room = self.subscribe(owner, "wp24-schedule-tz")
        _opt_in(room, owner)
        self.send_stream_message(owner, "wp24-schedule-tz")

        # The same instant that is 07:00 in Jakarta is 20:00 the previous
        # evening in New York (EDT, UTC-4 in September): still too early.
        with time_machine.travel(AT_SEVEN_JAKARTA, tick=False):
            self.assertEqual(send_realm_room_digests(realm), 0)
        self.assertFalse(RoomDigest.objects.filter(stream=room).exists())


class RoomDigestOptInTest(ZulipTestCase):
    def test_summary_disabled_room_is_skipped(self) -> None:
        owner = self.example_user("hamlet")
        realm = owner.realm
        _set_realm_settings(realm)
        _enabled_kaki(owner, self.example_user("default_bot"))
        room = self.subscribe(owner, "wp24-optin-disabled")
        self.send_stream_message(owner, "wp24-optin-disabled")
        # No _opt_in(room, owner) call: summary stays off by default.

        with time_machine.travel(AFTER_DIGEST_HOUR, tick=False):
            self.assertEqual(send_realm_room_digests(realm), 0)
        self.assertFalse(RoomDigest.objects.filter(stream=room).exists())

    def test_budget_exceeded_skips_realm(self) -> None:
        owner = self.example_user("hamlet")
        realm = owner.realm
        _set_realm_settings(realm)
        room = self.subscribe(owner, "wp24-optin-budget")
        _opt_in(room, owner)
        self.send_stream_message(owner, "wp24-optin-budget")

        with (
            time_machine.travel(AFTER_DIGEST_HOUR, tick=False),
            mock.patch("zerver.lib.room_digests.model_budget_state", return_value="exceeded"),
        ):
            self.assertEqual(send_realm_room_digests(realm), 0)
        self.assertFalse(RoomDigest.objects.filter(stream=room).exists())

    def test_room_with_no_new_messages_is_skipped(self) -> None:
        owner = self.example_user("hamlet")
        realm = owner.realm
        _set_realm_settings(realm)
        _ready_kaki_profile(owner)
        room = self.subscribe(owner, "wp24-optin-stale")
        room_meta = _opt_in(room, owner)
        self.send_stream_message(owner, "wp24-optin-stale")
        # last_digest_at is after the only message: nothing new to summarize.
        room_meta.last_digest_at = AFTER_DIGEST_HOUR
        room_meta.save(update_fields=["last_digest_at"])

        with time_machine.travel(AFTER_DIGEST_HOUR + timedelta(hours=1), tick=False):
            self.assertEqual(send_realm_room_digests(realm), 0)
        self.assertFalse(RoomDigest.objects.filter(stream=room).exists())

    def test_room_with_new_messages_gets_a_digest(self) -> None:
        owner = self.example_user("hamlet")
        realm = owner.realm
        _set_realm_settings(realm)
        kaki = _ready_kaki_profile(owner)
        room = self.subscribe(owner, "wp24-optin-fresh")
        _opt_in(room, owner)
        self.send_stream_message(owner, "wp24-optin-fresh", "Let's ship this.")

        with time_machine.travel(AFTER_DIGEST_HOUR, tick=False):
            self.assertEqual(send_realm_room_digests(realm), 1)

        digest = RoomDigest.objects.get(stream=room, date=AFTER_DIGEST_HOUR.date())
        self.assertEqual(digest.message_count, 1)
        self.assertIsNotNone(digest.job)
        posted = Message.objects.filter(sender=kaki.bot_user, recipient=room.recipient).latest("id")
        self.assertIn("JSON", posted.content)

        # A second run the same day must not create a second digest row or
        # a second source message.
        with time_machine.travel(AFTER_DIGEST_HOUR + timedelta(hours=1), tick=False):
            self.assertEqual(send_realm_room_digests(realm), 0)
        self.assertEqual(RoomDigest.objects.filter(stream=room).count(), 1)

    def test_falls_back_to_room_owner_when_enabler_is_deactivated(self) -> None:
        owner = self.example_user("hamlet")
        enabler = self.example_user("othello")
        realm = owner.realm
        _set_realm_settings(realm)
        _ready_kaki_profile(owner)
        room = self.subscribe(owner, "wp24-optin-fallback")
        self.subscribe(enabler, "wp24-optin-fallback")
        room.creator = owner
        room.save(update_fields=["creator"])
        _opt_in(room, enabler)
        do_deactivate_user(enabler, acting_user=None)
        self.send_stream_message(owner, "wp24-optin-fallback")

        # The room's enabler is no longer active: get_room_owner's fallback
        # to the room's creator must still let the digest go out (WP14
        # review defect 13's precedent, reused here for the same reason).
        with time_machine.travel(AFTER_DIGEST_HOUR, tick=False):
            self.assertEqual(send_realm_room_digests(realm), 1)
        self.assertTrue(RoomDigest.objects.filter(stream=room).exists())

    def test_posts_to_a_private_room_kaki_is_not_yet_subscribed_to(self) -> None:
        owner = self.example_user("hamlet")
        realm = owner.realm
        _set_realm_settings(realm)
        # A bot with no owner of its own: access_stream_for_send_message's
        # bot-owner escape hatch (a bot may post through a subscribed
        # owner without subscribing itself) does not apply, so posting
        # here must go through the subscribe-and-retry path instead.
        bot_user = do_create_user(
            "wp24-kaki-bot@zulip.testserver",
            password=None,
            realm=realm,
            full_name="Kaki",
            bot_type=UserProfile.DEFAULT_BOT,
            acting_user=None,
        )
        _enabled_kaki(owner, bot_user)
        # History public to subscribers: kaki's bot can read the room's
        # existing messages once it subscribes, the same as any other
        # newly-added member of this kind of private channel.
        self.make_stream("wp24-optin-private", invite_only=True, history_public_to_subscribers=True)
        room = self.subscribe(owner, "wp24-optin-private")
        _opt_in(room, owner)
        self.send_stream_message(owner, "wp24-optin-private", "Ship it.")

        with time_machine.travel(AFTER_DIGEST_HOUR, tick=False):
            self.assertEqual(send_realm_room_digests(realm), 1)

        self.assertTrue(subscribed_to_stream(bot_user, room.id))
        self.assertTrue(RoomDigest.objects.filter(stream=room).exists())

    def test_skips_room_when_no_active_owner_remains(self) -> None:
        owner = self.example_user("hamlet")
        realm = owner.realm
        _set_realm_settings(realm)
        # Kaki's own profile belongs to a separate, never-deactivated
        # user: do_deactivate_user also deactivates the bots its target
        # owns, and kaki's bot must stay active for this realm's digest
        # to run at all (T-21 is about the room's owner, not Kaki's).
        _ready_kaki_profile(self.example_user("othello"))
        room = self.subscribe(owner, "wp24-optin-no-owner")
        room.creator = None
        room.save(update_fields=["creator"])
        _opt_in(room, owner)
        self.send_stream_message(owner, "wp24-optin-no-owner", "Ship it.")
        do_deactivate_user(owner, acting_user=None)
        UserProfile.objects.filter(
            realm=realm, role=UserProfile.ROLE_REALM_OWNER, is_active=True
        ).update(is_active=False)

        # No creator, the enabler is deactivated, and no active human Owner
        # remains anywhere in the realm: nothing to attribute the digest
        # to, so the room is skipped rather than crashing.
        with time_machine.travel(AFTER_DIGEST_HOUR, tick=False):
            self.assertEqual(send_realm_room_digests(realm), 0)
        self.assertFalse(RoomDigest.objects.filter(stream=room).exists())

    def test_broken_room_config_does_not_crash_the_realm(self) -> None:
        owner = self.example_user("hamlet")
        realm = owner.realm
        _set_realm_settings(realm)
        _ready_kaki_profile(owner)
        room = self.subscribe(owner, "wp24-optin-broken")
        _opt_in(room, owner)
        self.send_stream_message(owner, "wp24-optin-broken", "Ship it.")

        with (
            time_machine.travel(AFTER_DIGEST_HOUR, tick=False),
            mock.patch(
                "zerver.lib.room_digests.create_room_digest_job", side_effect=ValueError("boom")
            ),
        ):
            # One room's broken configuration must not raise out of the
            # whole realm's run (T-21); it is skipped, not left half-written.
            self.assertEqual(send_realm_room_digests(realm), 0)
        self.assertFalse(RoomDigest.objects.filter(stream=room).exists())


class RecentMessageIdsTest(ZulipTestCase):
    def test_caps_at_context_message_limit_oldest_first(self) -> None:
        owner = self.example_user("hamlet")
        stream = self.subscribe(owner, "wp24-cap")
        sent_ids = [
            self.send_stream_message(owner, "wp24-cap", f"msg {i}")
            for i in range(CONTEXT_MESSAGE_LIMIT + 5)
        ]

        ids, total = _recent_message_ids(stream, since=None)
        self.assertEqual(total, len(sent_ids))
        self.assertEqual(len(ids), CONTEXT_MESSAGE_LIMIT)
        # Oldest first, and the newest CONTEXT_MESSAGE_LIMIT messages win.
        self.assertEqual(ids, sorted(sent_ids[-CONTEXT_MESSAGE_LIMIT:]))

    def test_since_excludes_older_messages(self) -> None:
        owner = self.example_user("hamlet")
        stream = self.subscribe(owner, "wp24-since")
        old_id = self.send_stream_message(owner, "wp24-since", "old")
        cutoff = AFTER_DIGEST_HOUR
        _backdate(old_id, cutoff - timedelta(hours=1))
        new_id = self.send_stream_message(owner, "wp24-since", "new")
        _backdate(new_id, cutoff + timedelta(hours=1))

        ids, total = _recent_message_ids(stream, since=cutoff)
        self.assertEqual((ids, total), ([new_id], 1))


class CoerceStrListTest(ZulipTestCase):
    def test_list_keeps_non_blank_items_as_strings(self) -> None:
        self.assertEqual(_coerce_str_list(["a", 1, "  ", ""]), ["a", "1"])

    def test_single_non_blank_string_becomes_a_one_item_list(self) -> None:
        self.assertEqual(_coerce_str_list("Ship it."), ["Ship it."])

    def test_blank_string_and_other_types_become_an_empty_list(self) -> None:
        self.assertEqual(_coerce_str_list("   "), [])
        self.assertEqual(_coerce_str_list(None), [])
        self.assertEqual(_coerce_str_list(3), [])


class RoomDigestEndpointTest(ZulipTestCase):
    def test_summarize_requires_summary_enabled(self) -> None:
        owner = self.example_user("hamlet")
        stream = self.subscribe(owner, "wp24-ep-disabled")
        stream.creator = owner
        stream.save(update_fields=["creator"])

        self.login_user(owner)
        result = self.client_post(f"/json/streams/{stream.id}/summarize")
        self.assert_json_error(result, "Turn on summaries for this channel first.")

    def test_summarize_requires_owner_and_permission(self) -> None:
        owner = self.example_user("hamlet")
        admin = self.example_user("iago")
        stream = self.subscribe(owner, "wp24-ep-perm")
        stream.creator = owner
        stream.save(update_fields=["creator"])
        self.subscribe(admin, "wp24-ep-perm")
        _opt_in(stream, owner)

        self.login_user(admin)
        result = self.client_post(f"/json/streams/{stream.id}/summarize")
        self.assert_json_error(result, "You do not have permission to change this channel.")

    def test_summarize_without_kaki_says_not_available(self) -> None:
        owner = self.example_user("hamlet")
        stream = self.subscribe(owner, "wp24-ep-nokaki")
        stream.creator = owner
        stream.save(update_fields=["creator"])
        _opt_in(stream, owner)

        self.login_user(owner)
        result = self.client_post(f"/json/streams/{stream.id}/summarize")
        self.assert_json_error(result, "Summaries are not available for this organization.")

    def test_summarize_success_creates_job_and_digest(self) -> None:
        owner = self.example_user("hamlet")
        _set_realm_settings(owner.realm)
        _ready_kaki_profile(owner)
        stream = self.subscribe(owner, "wp24-ep-success")
        stream.creator = owner
        stream.save(update_fields=["creator"])
        _opt_in(stream, owner)
        self.send_stream_message(owner, "wp24-ep-success")

        self.login_user(owner)
        result = self.client_post(f"/json/streams/{stream.id}/summarize")
        data = self.assert_json_success(result)
        self.assertIn("job_id", data)
        self.assertTrue(RoomDigest.objects.filter(stream=stream).exists())

    def test_summarize_reraises_missing_realm_settings(self) -> None:
        owner = self.example_user("hamlet")
        _set_realm_settings(owner.realm)
        _ready_kaki_profile(owner)
        stream = self.subscribe(owner, "wp24-ep-norealmsettings")
        stream.creator = owner
        stream.save(update_fields=["creator"])
        _opt_in(stream, owner)
        self.send_stream_message(owner, "wp24-ep-norealmsettings")

        self.login_user(owner)
        with mock.patch(
            "zerver.lib.room_digests.create_room_digest_job",
            side_effect=AgentRealmSettings.DoesNotExist,
        ):
            result = self.client_post(f"/json/streams/{stream.id}/summarize")
        self.assert_json_error(result, "Summaries are not available for this organization.")

    def test_summarize_reraises_value_error_as_is(self) -> None:
        owner = self.example_user("hamlet")
        _set_realm_settings(owner.realm)
        _ready_kaki_profile(owner)
        stream = self.subscribe(owner, "wp24-ep-valueerror")
        stream.creator = owner
        stream.save(update_fields=["creator"])
        _opt_in(stream, owner)
        self.send_stream_message(owner, "wp24-ep-valueerror")

        self.login_user(owner)
        with mock.patch(
            "zerver.lib.room_digests.create_room_digest_job",
            side_effect=ValueError("Too many context references."),
        ):
            result = self.client_post(f"/json/streams/{stream.id}/summarize")
        self.assert_json_error(result, "Too many context references.")

    def test_digest_view_requires_stream_access(self) -> None:
        owner = self.example_user("hamlet")
        stream = self.make_stream("wp24-ep-private", invite_only=True)
        stream.creator = owner
        stream.save(update_fields=["creator"])

        outsider = self.example_user("othello")
        self.login_user(outsider)
        result = self.client_get(f"/json/streams/{stream.id}/digest")
        self.assert_json_error(result, "Invalid channel ID")

    def test_digest_view_returns_null_when_absent(self) -> None:
        owner = self.example_user("hamlet")
        stream = self.subscribe(owner, "wp24-ep-absent")

        self.login_user(owner)
        result = self.client_get(f"/json/streams/{stream.id}/digest")
        data = self.assert_json_success(result)
        self.assertIsNone(data["digest"])

    def test_digest_view_returns_data_for_requested_date(self) -> None:
        owner = self.example_user("hamlet")
        stream = self.subscribe(owner, "wp24-ep-present")
        RoomDigest.objects.create(
            stream=stream,
            date=AT_SEVEN_JAKARTA.date(),
            decided=["Ship WP24."],
            blocker=[],
            waiting=["Design review."],
            summary="Quiet day.",
            message_count=3,
        )

        self.login_user(owner)
        result = self.client_get(
            f"/json/streams/{stream.id}/digest",
            {"date": AT_SEVEN_JAKARTA.date().isoformat()},
        )
        data = self.assert_json_success(result)
        assert data["digest"] is not None
        self.assertEqual(data["digest"]["decided"], ["Ship WP24."])
        self.assertEqual(data["digest"]["waiting"], ["Design review."])
        self.assertEqual(data["digest"]["message_count"], 3)

    def test_digest_view_rejects_bad_date(self) -> None:
        owner = self.example_user("hamlet")
        stream = self.subscribe(owner, "wp24-ep-baddate")

        self.login_user(owner)
        result = self.client_get(f"/json/streams/{stream.id}/digest", {"date": "30-10-2026"})
        self.assert_json_error(result, "Enter the date as YYYY-MM-DD.")


class FillRoomDigestTest(ZulipTestCase):
    def _digest_job(self) -> tuple[agents.AgentJob, RoomDigest]:
        owner = self.example_user("hamlet")
        _set_realm_settings(owner.realm)
        kaki = _enabled_kaki(owner, self.example_user("default_bot"))
        stream = self.subscribe(owner, "wp24-fill")
        room_meta = _opt_in(stream, owner)
        job = create_room_digest_job(
            stream,
            room_meta,
            owner,
            kaki,
            today=AFTER_DIGEST_HOUR.date(),
            context_ids=[],
            message_count=0,
        )
        return job, RoomDigest.objects.get(job=job)

    def test_valid_json_fills_fields_and_renders_text(self) -> None:
        job, digest = self._digest_job()
        answer = json.dumps(
            {
                "decided": ["Ship the fast preset later."],
                "blocker": [],
                "waiting": ["Design review."],
                "summary": "Quiet day.",
            }
        )

        text = fill_room_digest(job, answer)

        digest.refresh_from_db()
        self.assertEqual(digest.decided, ["Ship the fast preset later."])
        self.assertEqual(digest.blocker, [])
        self.assertEqual(digest.waiting, ["Design review."])
        self.assertEqual(digest.summary, "Quiet day.")
        assert text is not None
        self.assertIn("Quiet day.", text)
        self.assertIn("Decisions:", text)
        self.assertIn("Ship the fast preset later.", text)
        self.assertIn("Waiting on:", text)
        self.assertNotIn("Blocker:", text)  # An empty section is left out.

    def test_broken_json_falls_back_to_raw_text(self) -> None:
        job, digest = self._digest_job()
        raw = "not json at all"

        text = fill_room_digest(job, raw)

        digest.refresh_from_db()
        self.assertEqual(digest.summary, raw)
        self.assertEqual(digest.decided, [])
        self.assertEqual(text, raw)

    def test_json_that_is_not_an_object_falls_back_to_raw_text(self) -> None:
        job, digest = self._digest_job()
        raw = json.dumps(["not", "an", "object"])

        text = fill_room_digest(job, raw)

        digest.refresh_from_db()
        self.assertEqual(digest.summary, raw)
        self.assertEqual(text, raw)

    def test_non_digest_job_returns_none(self) -> None:
        owner = self.example_user("hamlet")
        _set_realm_settings(owner.realm)
        kaki = _enabled_kaki(owner, self.example_user("default_bot"))
        self.subscribe(owner, "wp24-fill-plain")
        message_id = self.send_stream_message(owner, "wp24-fill-plain", "hi")
        job = create_job(
            owner,
            profile=kaki,
            source=Message.objects.get(id=message_id),
            request="Please answer",
            idempotency_key=uuid4(),
            job_kind="answer",
            delivery_target="answer",
            allow_blocked=True,
        )
        self.assertIsNone(fill_room_digest(job, "anything"))


class PublishResultFillsRoomDigestTest(ZulipTestCase):
    """One end-to-end check that a completed digest job's answer actually
    reaches RoomDigest and the posted message, through the real
    agent_results.publish_result pipeline (not only the pure function)."""

    def test_publish_result_fills_digest_and_renders_text(self) -> None:
        import hashlib
        import tempfile

        from django.test import override_settings
        from django.utils.timezone import now

        from zerver.lib import agent_protocol as p
        from zerver.lib.agent_results import publish_result, store_artifact

        owner = self.example_user("hamlet")
        _set_realm_settings(owner.realm)
        kaki = _ready_kaki_profile(owner)
        stream = self.subscribe(owner, "wp24-publish")
        room_meta = _opt_in(stream, owner)
        job = create_room_digest_job(
            stream,
            room_meta,
            owner,
            kaki,
            today=AFTER_DIGEST_HOUR.date(),
            context_ids=[],
            message_count=0,
        )

        create_job_module = __import__(
            "zerver.actions.agent_jobs", fromlist=["claim_work", "record_event"]
        )
        create_job_module.claim_work(kaki.runner, claim_key=uuid4())
        attempt = agents.AgentAttempt.objects.get(job=job)

        def event(sequence: int, kind: str, payload: dict[str, object]) -> None:
            create_job_module.record_event(
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

        event(1, "attempt.started", {"process_state": "active"})
        answer = json.dumps(
            {"decided": ["Ship it."], "blocker": ["Nothing."], "waiting": [], "summary": "Done."}
        )
        with (
            tempfile.TemporaryDirectory() as directory,
            override_settings(AGENT_ARTIFACT_ROOT=directory),
        ):
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
            event(2, "result.prepared", {"artifact_ids": [str(artifact.id)], "summary": answer})
            with self.captureOnCommitCallbacks(execute=True):
                event(3, "attempt.stopped", {"process_state": "stopped", "stop_confirmed": True})

        job.refresh_from_db()
        self.assertEqual(job.status, "completed")
        publish_result(job.id)

        digest = RoomDigest.objects.get(job=job)
        self.assertEqual(digest.decided, ["Ship it."])
        self.assertEqual(digest.blocker, ["Nothing."])
        self.assertEqual(digest.summary, "Done.")

        assert job.result_message_id is not None
        posted = Message.objects.get(id=job.result_message_id)
        self.assertNotIn("{", posted.content)  # Rendered text, not raw JSON.
        self.assertIn("Ship it.", posted.content)
