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

    def test_deactivated_resource_owner_invalidates_shared_grants(self) -> None:
        self.grant(target_kind="profile", action="profile.use")
        self.grant(target_kind="runner", action="runner.use")
        check_agent_access(self.member, self.profile, None, None, "profile.use")
        self.owner.is_active = False
        self.owner.save(update_fields=["is_active"])
        with self.assertRaises(AgentAccessDenied):
            check_agent_access(self.member, self.profile, None, None, "profile.use")

    def test_team_default_accepts_an_active_scoped_group_grant(self) -> None:
        from uuid import uuid4

        from zerver.actions.agents import (
            create_profile,
            enable_profile,
            record_readiness,
            update_team_default,
        )

        self.owner.role = UserProfile.ROLE_REALM_ADMINISTRATOR
        self.owner.save(update_fields=["role"])
        settings = agents.AgentRealmSettings.objects.create(realm=self.realm, enabled=True)
        self.runner.catalog_report = {
            "revision": 1,
            "adapters": [
                {
                    "id": "codex-acp",
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
        }
        self.runner.save(update_fields=["catalog_report"])
        self.profile = create_profile(
            self.owner,
            name="Active profile",
            runner=self.runner,
            adapter_id="codex-acp",
            adapter_version="1",
            idempotency_key=uuid4(),
        )
        setup = agents.AgentSetupOperation.objects.get(profile=self.profile)
        record_readiness(
            self.runner,
            setup,
            {
                "schema_version": 1,
                "profile_id": str(self.profile.id),
                "profile_revision": self.profile.revision,
                "runner_id": str(self.runner.id),
                "descriptor_digest": setup.descriptor_digest,
                "configuration_digest": setup.configuration_digest,
                "state": "ready",
                "capabilities": {"chat_ready": True, "config_version": 1},
            },
        )
        self.profile.refresh_from_db()
        enable_profile(self.owner, self.profile, expected_revision=self.profile.revision)
        group = UserGroup.objects.create(realm=self.realm)
        agents.AgentGrant.objects.create(
            realm=self.realm,
            owner=self.owner,
            principal_group=group,
            target_kind="profile",
            profile=self.profile,
            actions=["profile.use"],
            scope={
                "kind": "direct",
                "participant_user_ids": [self.owner.id, self.profile.bot_user_id],
            },
        )
        update_team_default(
            self.owner,
            profile=self.profile,
            expected_selection_revision=settings.default_selection_revision,
        )
        settings.refresh_from_db()
        self.assertEqual(settings.default_profile_id, self.profile.id)

    def test_realm_admin_does_not_bypass_runner_grant(self) -> None:
        self.grant(target_kind="profile", action="profile.use")
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

    def test_scoped_grant_rejects_another_topic(self) -> None:
        from zerver.models import Message

        self.subscribe(self.member, "Denmark")
        self.subscribe(self.profile.bot_user, "Denmark")
        message_id = self.send_stream_message(self.owner, "Denmark", topic_name="other")
        message = Message.objects.get(id=message_id)
        self.grant(target_kind="runner", action="runner.use")
        agents.AgentGrant.objects.create(
            realm=self.realm,
            owner=self.owner,
            principal_user=self.member,
            target_kind="profile",
            profile=self.profile,
            actions=["profile.use"],
            scope={"kind": "stream", "stream_id": message.recipient.type_id, "topic": "allowed"},
        )
        with self.assertRaises(AgentAccessDenied):
            check_agent_access(self.member, self.profile, None, message, "profile.use")

    def test_revoked_runner_denies_owner_execution(self) -> None:
        self.runner.revoked_at = now()
        self.runner.save(update_fields=["revoked_at"])
        with self.assertRaises(AgentAccessDenied):
            check_agent_access(self.owner, self.profile, None, None, "profile.use")

    def test_direct_scope_rejects_another_audience(self) -> None:
        from zerver.lib.agent_policy import scope_matches
        from zerver.models import Message

        message = Message.objects.get(id=self.send_personal_message(self.owner, self.member))
        self.assertTrue(
            scope_matches(
                {"kind": "direct", "participant_user_ids": [self.owner.id, self.member.id]}, message
            )
        )
        self.assertFalse(
            scope_matches(
                {
                    "kind": "direct",
                    "participant_user_ids": [self.owner.id, self.example_user("iago").id],
                },
                message,
            )
        )

    def test_owner_cannot_widen_profile_tool_policy(self) -> None:
        self.profile.policy = {"actions": ["context.read"]}
        self.profile.save(update_fields=["policy"])
        with self.assertRaises(AgentAccessDenied):
            check_agent_access(self.owner, self.profile, None, None, "shell.run")

    def test_repository_restricted_profile_grant_requires_repository(self) -> None:
        repository = agents.AgentRepository.objects.create(
            realm=self.realm, owner=self.owner, runner=self.runner, workspace_alias="work"
        )
        self.grant(target_kind="runner", action="runner.use")
        agents.AgentGrant.objects.create(
            realm=self.realm,
            owner=self.owner,
            principal_user=self.member,
            target_kind="profile",
            profile=self.profile,
            repository=repository,
            actions=["profile.use"],
        )
        with self.assertRaises(AgentAccessDenied):
            check_agent_access(self.member, self.profile, None, None, "profile.use")
