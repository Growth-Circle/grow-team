"""Tests for zerver/lib/role_permissions.py: the Sanji role permission
matrix, 5 roles x 17 permission keys."""

from typing_extensions import override

from zerver.actions.realm_settings import do_change_realm_permission_group_setting
from zerver.lib.role_permissions import (
    LOCK_OWNER_ALWAYS,
    LOCK_OWNER_ONLY_ADMIN_COLUMN,
    LOCK_SECURITY,
    PERMISSION_KEYS,
    has_role_permission,
    lock_reason,
    permission_matrix,
)
from zerver.lib.test_classes import ZulipTestCase
from zerver.lib.user_groups import get_role_based_system_groups_dict
from zerver.models import RolePermission, UserProfile
from zerver.models.groups import SystemGroups
from zerver.models.realms import get_realm

OWNER = UserProfile.ROLE_REALM_OWNER
ADMIN = UserProfile.ROLE_REALM_ADMINISTRATOR
MODERATOR = UserProfile.ROLE_MODERATOR
MEMBER = UserProfile.ROLE_MEMBER
GUEST = UserProfile.ROLE_GUEST

# Transcribed independently from the product spec, not imported from
# role_permissions.py, so a typo in either place is caught instead of a
# test that only compares a module to itself.
EXPECTED_MATRIX: dict[str, dict[int, bool]] = {
    "ws_settings": {OWNER: True, ADMIN: True, MODERATOR: False, MEMBER: False, GUEST: False},
    "billing": {OWNER: True, ADMIN: False, MODERATOR: False, MEMBER: False, GUEST: False},
    "ws_delete": {OWNER: True, ADMIN: False, MODERATOR: False, MEMBER: False, GUEST: False},
    "invite": {OWNER: True, ADMIN: True, MODERATOR: True, MEMBER: False, GUEST: False},
    "change_role": {OWNER: True, ADMIN: True, MODERATOR: False, MEMBER: False, GUEST: False},
    "deactivate": {OWNER: True, ADMIN: True, MODERATOR: False, MEMBER: False, GUEST: False},
    "room_create": {OWNER: True, ADMIN: True, MODERATOR: True, MEMBER: True, GUEST: False},
    "room_archive": {OWNER: True, ADMIN: True, MODERATOR: True, MEMBER: False, GUEST: False},
    "room_summary": {OWNER: True, ADMIN: True, MODERATOR: True, MEMBER: True, GUEST: False},
    "agent_create": {OWNER: True, ADMIN: True, MODERATOR: True, MEMBER: True, GUEST: False},
    "agent_task": {OWNER: True, ADMIN: True, MODERATOR: True, MEMBER: True, GUEST: False},
    "agent_admin": {OWNER: True, ADMIN: True, MODERATOR: False, MEMBER: False, GUEST: False},
    "approve": {OWNER: True, ADMIN: True, MODERATOR: True, MEMBER: True, GUEST: False},
    "runner": {OWNER: True, ADMIN: True, MODERATOR: False, MEMBER: True, GUEST: False},
    "integ": {OWNER: True, ADMIN: True, MODERATOR: False, MEMBER: False, GUEST: False},
    "folder": {OWNER: True, ADMIN: True, MODERATOR: True, MEMBER: True, GUEST: False},
    "audit": {OWNER: True, ADMIN: True, MODERATOR: False, MEMBER: False, GUEST: False},
}


class RolePermissionTests(ZulipTestCase):
    @override
    def setUp(self) -> None:
        super().setUp()
        self.realm = get_realm("zulip")
        # Pin the group-mapped settings to the Sanji default explicitly,
        # before fetching any user (example_user() caches .realm on the
        # object it returns). The shared test realm may have been built
        # before this default changed; a unit test of the matrix should
        # not depend on that.
        groups = get_role_based_system_groups_dict(self.realm)
        do_change_realm_permission_group_setting(
            self.realm,
            "can_invite_users_group",
            groups[SystemGroups.MODERATORS],
            acting_user=None,
        )

        self.owner = self.example_user("desdemona")
        self.admin = self.example_user("iago")
        self.moderator = self.example_user("shiva")
        self.member = self.example_user("hamlet")
        self.guest = self.example_user("polonius")
        self.users_by_role = {
            OWNER: self.owner,
            ADMIN: self.admin,
            MODERATOR: self.moderator,
            MEMBER: self.member,
            GUEST: self.guest,
        }

    def test_keys_cover_the_whole_spec_table(self) -> None:
        self.assertEqual(set(PERMISSION_KEYS), set(EXPECTED_MATRIX))
        self.assert_length(PERMISSION_KEYS, 17)

    def test_default_matrix_matches_spec_for_every_role(self) -> None:
        for key in PERMISSION_KEYS:
            for role, user in self.users_by_role.items():
                self.assertEqual(
                    has_role_permission(user, key),
                    EXPECTED_MATRIX[key][role],
                    f"{key} / role {role}",
                )

    def test_owner_ignores_a_conflicting_override(self) -> None:
        RolePermission.objects.create(
            realm=self.realm, permission_key="billing", role=OWNER, allowed=False
        )
        self.assertTrue(has_role_permission(self.owner, "billing"))

    def test_locked_cell_ignores_override(self) -> None:
        self.assertEqual(lock_reason("billing", ADMIN), LOCK_SECURITY)
        RolePermission.objects.create(
            realm=self.realm, permission_key="billing", role=ADMIN, allowed=True
        )
        self.assertFalse(has_role_permission(self.admin, "billing"))

    def test_override_works_for_a_changeable_cell(self) -> None:
        # ws_settings/moderator is unlocked and defaults to False.
        self.assertIsNone(lock_reason("ws_settings", MODERATOR))
        self.assertFalse(has_role_permission(self.moderator, "ws_settings"))
        RolePermission.objects.create(
            realm=self.realm, permission_key="ws_settings", role=MODERATOR, allowed=True
        )
        self.assertTrue(has_role_permission(self.moderator, "ws_settings"))

        # room_summary/member is unlocked and defaults to True. (room_create
        # is mapped to a Zulip group setting instead and never reads
        # RolePermission; see test_group_mapped_setting_is_read_live_....)
        self.assertIsNone(lock_reason("room_summary", MEMBER))
        self.assertTrue(has_role_permission(self.member, "room_summary"))
        RolePermission.objects.create(
            realm=self.realm, permission_key="room_summary", role=MEMBER, allowed=False
        )
        self.assertFalse(has_role_permission(self.member, "room_summary"))

        # permission_matrix must reflect the same override, not just
        # has_role_permission.
        rows = {row["key"]: row["cells"] for row in permission_matrix(self.realm)}
        self.assertTrue(rows["ws_settings"]["moderator"]["allowed"])
        self.assertFalse(rows["room_summary"]["member"]["allowed"])

    def test_unknown_key_raises(self) -> None:
        with self.assertRaises(ValueError):
            has_role_permission(self.member, "not_a_real_key")

    def test_lock_reasons(self) -> None:
        self.assertEqual(lock_reason("ws_settings", OWNER), LOCK_OWNER_ALWAYS)
        self.assertEqual(lock_reason("billing", MEMBER), LOCK_SECURITY)
        self.assertIsNone(lock_reason("room_create", MEMBER))
        # A guest can never satisfy can_create_public/private_channel_group
        # (neither allows the everyone/guest group), so this cell is locked
        # even though it is not read from RolePermission.
        self.assertEqual(lock_reason("room_create", GUEST), LOCK_SECURITY)
        # The Admin column is only locked for a non-Owner viewer.
        self.assertIsNone(lock_reason("ws_settings", ADMIN))
        self.assertEqual(
            lock_reason("ws_settings", ADMIN, viewer_role=ADMIN), LOCK_OWNER_ONLY_ADMIN_COLUMN
        )
        self.assertIsNone(lock_reason("ws_settings", ADMIN, viewer_role=OWNER))

    def test_group_mapped_setting_is_read_live_from_the_realm_group(self) -> None:
        # "invite" -> can_invite_users_group, defaulted to Moderators and up.
        self.assertTrue(has_role_permission(self.moderator, "invite"))
        self.assertTrue(has_role_permission(self.admin, "invite"))

        administrators = get_role_based_system_groups_dict(self.realm)[SystemGroups.ADMINISTRATORS]
        do_change_realm_permission_group_setting(
            self.realm, "can_invite_users_group", administrators, acting_user=None
        )

        # Tightening the realm setting takes moderators out immediately;
        # this is read live, never cached in RolePermission. Re-fetch each
        # user so a cached .realm from the checks above cannot hide that.
        self.assertFalse(has_role_permission(self.example_user("shiva"), "invite"))
        self.assertTrue(has_role_permission(self.example_user("iago"), "invite"))
        self.assertTrue(has_role_permission(self.example_user("desdemona"), "invite"))
        self.assertFalse(has_role_permission(self.example_user("polonius"), "invite"))

    def test_group_mapped_setting_locked_to_nobody_locks_out_the_owner(self) -> None:
        # The product spec's "None" option for who may assign agent-admin
        # tasks maps to role:nobody. The Owner column is not a blanket
        # bypass: a key mapped to a Zulip group setting must read that
        # setting's real membership, and role:nobody has no members at all.
        nobody = get_role_based_system_groups_dict(self.realm)[SystemGroups.NOBODY]
        do_change_realm_permission_group_setting(
            self.realm, "can_command_administrator_agents_group", nobody, acting_user=None
        )
        self.assertFalse(has_role_permission(self.example_user("desdemona"), "agent_admin"))
        rows = {row["key"]: row["cells"] for row in permission_matrix(self.realm)}
        self.assertFalse(rows["agent_admin"]["owner"]["allowed"])

    def test_permission_matrix_reports_group_value_lock_and_reason(self) -> None:
        with self.assert_database_query_count(7):
            rows = {row["key"]: row for row in permission_matrix(self.realm)}
        self.assertEqual(set(rows), set(PERMISSION_KEYS))

        billing_row = rows["billing"]
        self.assertEqual(billing_row["group"], "workspace")
        owner_cell = billing_row["cells"]["owner"]
        self.assertTrue(owner_cell["allowed"])
        self.assertTrue(owner_cell["locked"])
        self.assertEqual(owner_cell["reason"], LOCK_OWNER_ALWAYS)

        admin_cell = billing_row["cells"]["admin"]
        self.assertFalse(admin_cell["allowed"])
        self.assertTrue(admin_cell["locked"])
        self.assertEqual(admin_cell["reason"], LOCK_SECURITY)

        moderator_cell = rows["ws_settings"]["cells"]["moderator"]
        self.assertFalse(moderator_cell["allowed"])
        self.assertFalse(moderator_cell["locked"])
        self.assertIsNone(moderator_cell["reason"])

        # room_create is mapped to a Zulip group setting; the matrix must
        # read the live value, matching has_role_permission. The guest cell
        # can never turn on (see test_lock_reasons), so it stays locked.
        self.assertEqual(rows["room_create"]["group"], "rooms")
        self.assertTrue(rows["room_create"]["cells"]["member"]["allowed"])
        guest_cell = rows["room_create"]["cells"]["guest"]
        self.assertFalse(guest_cell["allowed"])
        self.assertTrue(guest_cell["locked"])
        self.assertEqual(guest_cell["reason"], LOCK_SECURITY)

    def test_permission_matrix_viewer_role_locks_the_admin_column(self) -> None:
        owner_view = {
            row["key"]: row["cells"] for row in permission_matrix(self.realm, viewer_role=OWNER)
        }
        self.assertIsNone(owner_view["ws_settings"]["admin"]["reason"])

        admin_view = {
            row["key"]: row["cells"] for row in permission_matrix(self.realm, viewer_role=ADMIN)
        }
        self.assertEqual(admin_view["ws_settings"]["admin"]["reason"], LOCK_OWNER_ONLY_ADMIN_COLUMN)
        # Passing no viewer_role at all reports only the cells locked for
        # every viewer, so the Admin column is not reported as locked.
        no_viewer = {row["key"]: row["cells"] for row in permission_matrix(self.realm)}
        self.assertIsNone(no_viewer["ws_settings"]["admin"]["reason"])
