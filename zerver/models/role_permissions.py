"""One row per (realm, permission key, role) override of the default role
permission matrix. See zerver/lib/role_permissions.py for the defaults, the
locked cells, and the has_role_permission/permission_matrix helpers that
read this table."""

from django.db import models

from zerver.models.realms import Realm
from zerver.models.users import UserProfile

ROLE_PERMISSION_ROLES = [
    UserProfile.ROLE_REALM_OWNER,
    UserProfile.ROLE_REALM_ADMINISTRATOR,
    UserProfile.ROLE_MODERATOR,
    UserProfile.ROLE_MEMBER,
    UserProfile.ROLE_GUEST,
]


class RolePermission(models.Model):
    realm = models.ForeignKey(Realm, on_delete=models.CASCADE)
    permission_key = models.CharField(max_length=40)
    role = models.PositiveSmallIntegerField()
    allowed = models.BooleanField(default=False)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["realm", "permission_key", "role"], name="role_permission_unique"
            ),
            models.CheckConstraint(
                condition=models.Q(role__in=ROLE_PERMISSION_ROLES),
                name="role_permission_role_valid",
            ),
        ]
