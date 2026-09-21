from datetime import timedelta

from django.utils.timezone import now
from typing_extensions import override

from zerver.lib.agent_policy import AgentAccessDenied, check_agent_access
from zerver.lib.test_classes import ZulipTestCase
from zerver.models import UserProfile, agents
from zerver.models.groups import UserGroup, UserGroupMembership


class AgentPolicyTests(ZulipTestCase):
    @override
    def setUp(self) -> None:
        super().setUp()
        self.owner = self.example_user("hamlet")
        self.member = self.example_user("othello")
        self.realm = self.owner.realm
        self.runner = agents.AgentRunner.objects.create(
            realm=self.realm, owner=self.owner, name="runner", fingerprint="a" * 64
        )
        self.profile = agents.AgentProfile.objects.create(
            realm=self.realm,
            owner=self.owner,
            runner=self.runner,
            bot_user=self.example_user("default_bot"),
            name="profile",
            adapter_id="codex-acp",
            adapter_version="1",
        )

    def grant(self, *, target_kind: str, action: str) -> None:
        fields: dict[str, object] = {
            "realm": self.realm,
            "owner": self.owner,
            "principal_user": self.member,
            "target_kind": target_kind,
            "actions": [action],
            "expires_at": now() + timedelta(minutes=5),
        }
        fields[target_kind] = getattr(self, target_kind)
        agents.AgentGrant.objects.create(**fields)

    def test_shared_member_requires_each_resource_grant(self) -> None:
        self.grant(target_kind="profile", action="profile.use")
        with self.assertRaises(AgentAccessDenied):
            check_agent_access(self.member, self.profile, None, None, "profile.use")
        self.grant(target_kind="runner", action="runner.use")
        check_agent_access(self.member, self.profile, None, None, "profile.use")

    def test_realm_admin_does_not_bypass_runner_grant(self) -> None:
        admin = self.member
        admin.role = UserProfile.ROLE_REALM_ADMINISTRATOR
        admin.save(update_fields=["role"])
        with self.assertRaises(AgentAccessDenied):
            check_agent_access(admin, self.profile, None, None, "profile.use")

    def test_expired_and_revoked_grants_do_not_authorize(self) -> None:
        grant = agents.AgentGrant.objects.create(
            realm=self.realm,
            owner=self.owner,
            principal_user=self.member,
            target_kind="profile",
            profile=self.profile,
            actions=["profile.use"],
            expires_at=now() - timedelta(seconds=1),
        )
        self.grant(target_kind="runner", action="runner.use")
        with self.assertRaises(AgentAccessDenied):
            check_agent_access(self.member, self.profile, None, None, "profile.use")
        grant.expires_at = now() + timedelta(minutes=5)
        grant.revoked_at = now()
        grant.save(update_fields=["expires_at", "revoked_at"])
        with self.assertRaises(AgentAccessDenied):
            check_agent_access(self.member, self.profile, None, None, "profile.use")

    def test_group_grants_use_current_membership(self) -> None:
        group = UserGroup.objects.create(realm=self.realm)
        UserGroupMembership.objects.create(user_group=group, user_profile=self.member)
        for target_kind, target, action in [
            ("profile", self.profile, "profile.use"),
            ("runner", self.runner, "runner.use"),
        ]:
            fields = {
                "realm": self.realm,
                "owner": self.owner,
                "principal_group": group,
                "target_kind": target_kind,
                "actions": [action],
                target_kind: target,
            }
            agents.AgentGrant.objects.create(**fields)
        check_agent_access(self.member, self.profile, None, None, "profile.use")
        UserGroupMembership.objects.filter(user_group=group, user_profile=self.member).delete()
        with self.assertRaises(AgentAccessDenied):
            check_agent_access(self.member, self.profile, None, None, "profile.use")
