"""The Sanji role permission matrix: 5 roles x 17 permission keys.

Four keys already have a Zulip realm group setting; has_role_permission
delegates to it directly. The other 13 keys default from PERMISSION_DEFAULTS
below and can be overridden per (realm, key, role) in the RolePermission
table, except for a locked cell, which always keeps its default and is
never read from the table.
"""

from typing import TypedDict

from zerver.models import Realm, RolePermission, UserProfile


class PermissionCell(TypedDict):
    allowed: bool
    locked: bool
    reason: str | None


class PermissionRow(TypedDict):
    key: str
    group: str
    cells: dict[str, PermissionCell]


OWNER = UserProfile.ROLE_REALM_OWNER
ADMIN = UserProfile.ROLE_REALM_ADMINISTRATOR
MODERATOR = UserProfile.ROLE_MODERATOR
MEMBER = UserProfile.ROLE_MEMBER
GUEST = UserProfile.ROLE_GUEST

ROLES: tuple[int, ...] = (OWNER, ADMIN, MODERATOR, MEMBER, GUEST)

# The settings-page role name for each role, keyed the way the frontend
# expects permission_matrix's "cells" to be keyed.
ROLE_NAMES: dict[int, str] = {
    OWNER: "owner",
    ADMIN: "admin",
    MODERATOR: "moderator",
    MEMBER: "member",
    GUEST: "guest",
}

# Why a locked cell cannot be changed from the settings page.
LOCK_OWNER_ALWAYS = "owner_always"
LOCK_OWNER_ONLY_ADMIN_COLUMN = "owner_only_admin_column"
LOCK_SECURITY = "security_locked"

# permission_key -> the realm group setting(s) it reads instead of
# RolePermission. All of these already forbid the everyone/guest group.
GROUP_SETTING_MAP: dict[str, tuple[str, ...]] = {
    "invite": ("can_invite_users_group",),
    "room_create": ("can_create_public_channel_group", "can_create_private_channel_group"),
    "agent_create": ("can_create_bots_group",),
    "agent_admin": ("can_command_administrator_agents_group",),
}

# permission_key -> the settings-page group label (permission_matrix
# "group"), in the same section order the permission list groups them.
PERMISSION_GROUPS: dict[str, str] = {
    "ws_settings": "workspace",
    "billing": "workspace",
    "ws_delete": "workspace",
    "invite": "members",
    "change_role": "members",
    "deactivate": "members",
    "room_create": "rooms",
    "room_archive": "rooms",
    "room_summary": "rooms",
    "agent_create": "agents",
    "agent_task": "agents",
    "agent_admin": "agents",
    "approve": "agents",
    "runner": "agents",
    "integ": "security",
    "folder": "security",
    "audit": "security",
}

# permission_key -> {role: allowed}, the value with no override in play.
PERMISSION_DEFAULTS: dict[str, dict[int, bool]] = {
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

PERMISSION_KEYS: tuple[str, ...] = tuple(PERMISSION_DEFAULTS)

# permission_key -> {role: reason}, for a non-Owner cell that is locked at
# its default and never overridable. The Owner column is always locked too
# (LOCK_OWNER_ALWAYS), listed separately in is_locked/lock_reason below
# instead of once per row here.
_LOCKED_NON_OWNER: dict[str, dict[int, str]] = {
    "ws_settings": {GUEST: LOCK_SECURITY},
    "billing": {
        ADMIN: LOCK_SECURITY,
        MODERATOR: LOCK_SECURITY,
        MEMBER: LOCK_SECURITY,
        GUEST: LOCK_SECURITY,
    },
    "ws_delete": {
        ADMIN: LOCK_SECURITY,
        MODERATOR: LOCK_SECURITY,
        MEMBER: LOCK_SECURITY,
        GUEST: LOCK_SECURITY,
    },
    "invite": {GUEST: LOCK_SECURITY},
    "change_role": {MODERATOR: LOCK_SECURITY, MEMBER: LOCK_SECURITY, GUEST: LOCK_SECURITY},
    "deactivate": {MEMBER: LOCK_SECURITY, GUEST: LOCK_SECURITY},
    # No group-mapped setting allows the everyone/guest group (see
    # GROUP_SETTING_MAP), so a guest can never turn this cell on. Lock it
    # instead of showing a toggle that would have no effect. The design
    # mockup drew this one cell as unlocked; reported to the orchestrator
    # as a mockup/backend mismatch rather than silently matched to it.
    "room_create": {GUEST: LOCK_SECURITY},
    "room_archive": {GUEST: LOCK_SECURITY},
    "room_summary": {GUEST: LOCK_SECURITY},
    "agent_create": {GUEST: LOCK_SECURITY},
    "agent_task": {},
    "agent_admin": {GUEST: LOCK_SECURITY},
    "approve": {GUEST: LOCK_SECURITY},
    "runner": {GUEST: LOCK_SECURITY},
    "integ": {GUEST: LOCK_SECURITY},
    "folder": {GUEST: LOCK_SECURITY},
    "audit": {GUEST: LOCK_SECURITY},
}


def is_locked(key: str, role: int) -> bool:
    """True when this cell always keeps its default and ignores overrides."""
    return role == OWNER or role in _LOCKED_NON_OWNER.get(key, {})


def lock_reason(key: str, role: int, *, viewer_role: int | None = None) -> str | None:
    """Why the settings page cannot change this cell, or None when it can.

    viewer_role is the role of the person looking at the matrix. Pass it to
    also lock the Admin column for a viewer who is not the Owner; leave it
    out to get only the cells that are locked for everyone.
    """
    if role == OWNER:
        return LOCK_OWNER_ALWAYS
    reason = _LOCKED_NON_OWNER.get(key, {}).get(role)
    if reason is not None:
        return reason
    if role == ADMIN and viewer_role is not None and viewer_role != OWNER:
        return LOCK_OWNER_ONLY_ADMIN_COLUMN
    return None


def has_role_permission(user_profile: UserProfile, key: str) -> bool:
    """Does this user currently have permission `key`?

    A key mapped to a Zulip group setting reads that setting's live group
    membership for every role, Owner included: a realm that locks the
    setting to "role:nobody" locks out the Owner too. Every other key
    gives the Owner every permission, and a locked cell keeps its default
    and ignores any RolePermission row.
    """
    if key not in PERMISSION_DEFAULTS:
        raise ValueError(f"Unknown permission key: {key}")
    role = user_profile.role
    if key in GROUP_SETTING_MAP:
        return all(user_profile.has_permission(setting) for setting in GROUP_SETTING_MAP[key])
    if role == OWNER:
        return True
    if is_locked(key, role):
        return PERMISSION_DEFAULTS[key][role]
    override = (
        RolePermission.objects.filter(realm_id=user_profile.realm_id, permission_key=key, role=role)
        .values_list("allowed", flat=True)
        .first()
    )
    if override is not None:
        return override
    return PERMISSION_DEFAULTS[key][role]


def _group_mapped_allowed(realm: Realm) -> dict[str, dict[int, bool]]:
    """{permission_key: {role: allowed}} for every key in GROUP_SETTING_MAP.

    One query for the realm's role-based system groups, then one
    get_recursive_subgroups query per unique Zulip group setting (5
    settings today, so 6 queries total), instead of one get_recursive_
    subgroups query per (key, role) cell (about 30 queries before this).
    """
    from zerver.lib.user_groups import get_recursive_subgroups, get_role_based_system_groups_dict
    from zerver.models.groups import NamedUserGroup

    system_groups = get_role_based_system_groups_dict(realm)
    role_group_id = {
        role: system_groups[NamedUserGroup.SYSTEM_USER_GROUP_ROLE_MAP[role]["name"]].id
        for role in ROLES
        if role != GUEST
    }
    setting_names = {name for settings in GROUP_SETTING_MAP.values() for name in settings}
    member_ids = {
        name: set(
            get_recursive_subgroups(getattr(realm, f"{name}_id")).values_list("id", flat=True)
        )
        for name in setting_names
    }
    allowed_by_key: dict[str, dict[int, bool]] = {}
    for key, settings in GROUP_SETTING_MAP.items():
        allowed_by_key[key] = {
            role: all(role_group_id[role] in member_ids[name] for name in settings)
            for role in role_group_id
        }
        # No mapped setting allows the everyone/guest group, so a guest
        # never qualifies no matter which group is configured.
        allowed_by_key[key][GUEST] = False
    return allowed_by_key


def permission_matrix(realm: Realm, *, viewer_role: int | None = None) -> list[PermissionRow]:
    """One row per permission key for the Roles & permissions settings tab:
    its group, and for each role the current value, whether it is locked,
    and why. Pass viewer_role to also lock the Admin column for a
    non-Owner viewer."""
    overrides = {
        (row.permission_key, row.role): row.allowed
        for row in RolePermission.objects.filter(realm=realm, permission_key__in=PERMISSION_KEYS)
    }
    group_mapped = _group_mapped_allowed(realm)
    rows: list[PermissionRow] = []
    for key in PERMISSION_KEYS:
        cells: dict[str, PermissionCell] = {}
        for role in ROLES:
            if key in GROUP_SETTING_MAP:
                allowed = group_mapped[key][role]
            elif role == OWNER:
                allowed = True
            elif is_locked(key, role):
                allowed = PERMISSION_DEFAULTS[key][role]
            else:
                allowed = overrides.get((key, role), PERMISSION_DEFAULTS[key][role])
            cells[ROLE_NAMES[role]] = {
                "allowed": allowed,
                "locked": is_locked(key, role),
                "reason": lock_reason(key, role, viewer_role=viewer_role),
            }
        rows.append({"key": key, "group": PERMISSION_GROUPS[key], "cells": cells})
    return rows
