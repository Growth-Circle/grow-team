"""Tests for WP13: reserved agent names, bot email slugs, per-profile
stats, the access-filtered audit feed, guest grants, task links to agent
jobs, and the ensure_builtin_agents command (spec 05, 06, 10, 11; §4.5)."""

from zerver.actions.create_user import do_create_user
from zerver.lib.agent_names import (
    RESERVED_AGENT_NAMES,
    agent_name_available,
    bot_email_for_agent_name,
)
from zerver.lib.test_classes import ZulipTestCase
from zerver.models.users import UserProfile


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
