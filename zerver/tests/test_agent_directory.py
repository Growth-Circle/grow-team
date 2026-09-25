"""Tests for WP13: reserved agent names, bot email slugs, per-profile
stats, the access-filtered audit feed, guest grants, task links to agent
jobs, and the ensure_builtin_agents command (spec 05, 06, 10, 11; §4.5)."""

from uuid import uuid4

from typing_extensions import override

from zerver.actions.agent_jobs import create_job
from zerver.actions.agents import (
    create_agent_grant,
    create_profile,
    enable_profile,
    record_readiness,
)
from zerver.actions.create_user import do_create_user
from zerver.actions.users import do_change_user_role
from zerver.lib.agent_names import (
    RESERVED_AGENT_NAMES,
    agent_name_available,
    bot_email_for_agent_name,
)
from zerver.lib.agent_policy import _principal_matches
from zerver.lib.test_classes import ZulipTestCase
from zerver.lib.user_groups import get_role_based_system_groups_dict
from zerver.models import agents
from zerver.models.messages import Message
from zerver.models.users import UserProfile


def catalog_report() -> dict[str, object]:
    return {
        "revision": 1,
        "adapters": [
            {
                "id": "acp",
                "version": "1",
                "auth_state": "ready",
                "capabilities": {"config_version": 1},
            },
            {
                "id": "endpoint",
                "version": "0.1.0",
                "auth_state": "ready",
                "capabilities": {"config_version": 1},
            },
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


class AgentNameTests(ZulipTestCase):
    def test_reserved_names_are_rejected(self) -> None:
        realm = self.example_user("hamlet").realm
        for name in ("Admin", "sanji", "Kaki", "ALL", "Channel", "ＡＬＬ"):
            self.assertFalse(agent_name_available(name, realm), name)

    def test_nfkc_casefold_clash_with_an_active_user(self) -> None:
        hamlet = self.example_user("hamlet")
        do_create_user(
            "klepon-bot@zulip.testserver",
            None,
            hamlet.realm,
            "Klepon",
            bot_type=UserProfile.DEFAULT_BOT,
            bot_owner=hamlet,
            acting_user=hamlet,
            add_initial_stream_subscriptions=False,
        )
        # Case and Unicode form do not matter: the full-width name
        # NFKC-normalizes to the plain one.
        for name in ("KLEPON", "klepon", "Ｋｌｅｐｏｎ"):
            self.assertFalse(agent_name_available(name, hamlet.realm), name)

    def test_deactivated_user_does_not_block_a_name(self) -> None:
        hamlet = self.example_user("hamlet")
        hamlet.is_active = False
        hamlet.save(update_fields=["is_active"])
        self.assertTrue(agent_name_available(hamlet.full_name, hamlet.realm))

    def test_available_name_passes(self) -> None:
        realm = self.example_user("hamlet").realm
        self.assertTrue(agent_name_available("Klepon", realm))
        self.assertNotIn("klepon", RESERVED_AGENT_NAMES)

    def test_rename_excludes_the_profile_s_own_bot(self) -> None:
        hamlet = self.example_user("hamlet")
        bot = do_create_user(
            "klepon-bot@zulip.testserver",
            None,
            hamlet.realm,
            "Klepon",
            bot_type=UserProfile.DEFAULT_BOT,
            bot_owner=hamlet,
            acting_user=hamlet,
            add_initial_stream_subscriptions=False,
        )
        self.assertFalse(agent_name_available("Klepon", hamlet.realm))
        self.assertTrue(agent_name_available("Klepon", hamlet.realm, exclude_user_id=bot.id))

    def test_email_gets_a_suffix_only_on_a_real_clash(self) -> None:
        hamlet = self.example_user("hamlet")
        _short, email = bot_email_for_agent_name("Klepon Manis!", hamlet.realm)
        self.assertEqual(email.split("@")[0], "klepon-manis-bot")

        do_create_user(
            email,
            None,
            hamlet.realm,
            "Old Klepon",
            bot_type=UserProfile.DEFAULT_BOT,
            bot_owner=hamlet,
            acting_user=hamlet,
            add_initial_stream_subscriptions=False,
        )
        _short2, email2 = bot_email_for_agent_name("Klepon Manis!", hamlet.realm)
        self.assertNotEqual(email, email2)
        self.assertTrue(email2.startswith("klepon-manis-"))


class AgentDirectoryAPITestCase(ZulipTestCase):
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
            catalog_report=catalog_report(),
        )

    def make_provider(self) -> agents.AgentProvider:
        return agents.AgentProvider.objects.create(
            realm=self.owner.realm,
            owner=self.owner,
            runner=self.runner,
            name="Provider",
            base_url="https://example.com",
            model_id="model",
            allowed_models=["model"],
            context_window_tokens=1000,
            max_output_tokens=100,
            local_credential_ref="local-secret",
            data_scope=["synthetic"],
        )

    def report_ready(self, profile: agents.AgentProfile) -> None:
        setup = agents.AgentSetupOperation.objects.filter(profile=profile).latest("created_at")
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

    def create_ready_profile(self, *, name: str = "Agent") -> agents.AgentProfile:
        profile = create_profile(
            self.owner,
            name=name,
            runner=self.runner,
            adapter_id="acp",
            adapter_version="1",
            mode="acp",
            default_mode="answer",
            idempotency_key=uuid4(),
        )
        self.report_ready(profile)
        profile.refresh_from_db()
        enable_profile(self.owner, profile, expected_revision=profile.revision)
        profile.refresh_from_db()
        return profile

    def make_job(
        self, profile: agents.AgentProfile, stream_name: str = "Verona"
    ) -> agents.AgentJob:
        source = Message.objects.get(
            id=self.send_stream_message(self.owner, stream_name, "Job source")
        )
        return create_job(
            self.owner,
            profile=profile,
            source=source,
            request="Do the thing",
            idempotency_key=uuid4(),
            job_kind="answer",
            delivery_target="answer",
        )


class GuestGrantTest(AgentDirectoryAPITestCase):
    def test_grant_to_a_guest_is_rejected(self) -> None:
        guest = self.example_user("polonius")
        with self.assertRaises(ValueError):
            create_agent_grant(
                self.owner,
                principal_user=guest,
                target_kind="runner",
                target=self.runner,
                actions=["runner.use"],
            )

    def test_grant_to_a_member_still_works(self) -> None:
        member = self.example_user("cordelia")
        grant = create_agent_grant(
            self.owner,
            principal_user=member,
            target_kind="runner",
            target=self.runner,
            actions=["runner.use"],
        )
        self.assertEqual(grant.principal_user_id, member.id)

    def test_guest_matches_no_group_grant(self) -> None:
        everyone = get_role_based_system_groups_dict(self.owner.realm)["role:everyone"]
        grant = create_agent_grant(
            self.owner,
            principal_group_id=everyone.id,
            target_kind="runner",
            target=self.runner,
            actions=["runner.use"],
        )
        self.assertTrue(_principal_matches(self.example_user("cordelia"), grant))
        self.assertFalse(_principal_matches(self.example_user("polonius"), grant))

    def test_member_changed_to_guest_loses_a_direct_grant(self) -> None:
        member = self.example_user("cordelia")
        grant = create_agent_grant(
            self.owner,
            principal_user=member,
            target_kind="runner",
            target=self.runner,
            actions=["runner.use"],
        )
        do_change_user_role(member, UserProfile.ROLE_GUEST, acting_user=None, notify=False)
        self.assertFalse(_principal_matches(member, grant))
