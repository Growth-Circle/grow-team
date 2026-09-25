from zerver.actions.realm_settings import do_change_realm_plan_type
from zerver.lib.realm_logo import get_realm_logo_url
from zerver.lib.test_classes import ZulipTestCase
from zerver.models import Realm


class RealmLogoNightTest(ZulipTestCase):
    """The default (non-uploaded) logo must use a dark-theme file for
    night mode, and a cache-busting query value that matches the sanji
    asset set."""

    def test_night_branch_uses_dark_file(self) -> None:
        realm = self.example_user("hamlet").realm
        day_url = get_realm_logo_url(realm, night=False)
        night_url = get_realm_logo_url(realm, night=True)
        self.assertIn("/logo/zulip-org-logo.svg", day_url)
        self.assertIn("/logo/zulip-org-logo-night.svg", night_url)
        self.assertNotEqual(day_url, night_url)

    def test_version_query_is_sanji(self) -> None:
        realm = self.example_user("hamlet").realm
        self.assertIn("?version=sanji-1", get_realm_logo_url(realm, night=False))
        self.assertIn("?version=sanji-1", get_realm_logo_url(realm, night=True))

    def test_limited_plan_keeps_night_branch(self) -> None:
        # A limited plan always falls back to the default logo, but the
        # night branch must still select the dark-theme file.
        realm = self.example_user("hamlet").realm
        do_change_realm_plan_type(realm, Realm.PLAN_TYPE_LIMITED, acting_user=None)
        self.assertIn("/logo/zulip-org-logo.svg", get_realm_logo_url(realm, night=False))
        self.assertIn("/logo/zulip-org-logo-night.svg", get_realm_logo_url(realm, night=True))
