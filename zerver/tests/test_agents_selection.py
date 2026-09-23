"""Selection and team default regressions use current server authority."""

import json
from datetime import timedelta
from uuid import uuid4

from django.utils.timezone import now
from typing_extensions import override

from zerver.actions import agent_jobs
from zerver.actions.agents import (
    create_profile,
    enable_profile,
    record_readiness,
    register_repository,
)
from zerver.actions.user_groups import check_add_user_group
from zerver.lib.agent_policy import AgentAccessDenied
from zerver.lib.test_classes import ZulipTestCase
from zerver.models import RealmAuditLog, UserProfile, agents
from zerver.models.realm_audit_logs import AuditLogEventType
from zerver.models.streams import get_stream


class AgentSelectionTests(ZulipTestCase):
    @override
    def setUp(self) -> None:
        super().setUp()
        self.owner = self.example_user("hamlet")
        self.owner.role = UserProfile.ROLE_REALM_ADMINISTRATOR
        self.owner.save(update_fields=["role"])
        self.member = self.example_user("othello")
        self.other_admin = self.example_user("iago")
        self.other_admin.role = UserProfile.ROLE_REALM_ADMINISTRATOR
        self.other_admin.save(update_fields=["role"])
        agents.AgentRealmSettings.objects.update_or_create(
            realm=self.owner.realm, defaults={"enabled": True}
        )
        self.runner = agents.AgentRunner.objects.create(
            realm=self.owner.realm,
            owner=self.owner,
            name="Workstation",
            fingerprint="s" * 64,
            status="offline",
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
        self.group = check_add_user_group(
            self.owner.realm, "Agent audience", [self.owner, self.member], acting_user=self.owner
        )
        self.subscribe(self.owner, "Denmark")
        self.subscribe(self.member, "Denmark")
        self.stream = get_stream("Denmark", self.owner.realm)

    def post(self, actor: UserProfile, path: str, data: dict[str, object]) -> dict[str, object]:
        request = self.api_patch if path == "team-default" else self.api_post
        response = request(
            actor, "/api/v1/agent/" + path, {"payload": json.dumps({"schema_version": 1, **data})}
        )
        self.assert_json_success(response)
        return response.json()

    def resolve(self, actor: UserProfile, **data: object) -> dict[str, object]:
        return self.post(
            actor,
            "selection/resolve",
            {
                "destination": {"kind": "stream", "stream_id": self.stream.id, "topic": "test"},
                **data,
            },
        )["selection"]

    def ready_profile(self, *, code: bool = False) -> agents.AgentProfile:
        repository = None
        policy = None
        if code:
            repository = register_repository(
                self.owner,
                self.runner,
                workspace_alias="work",
                canonical_origin=None,
                allowed_refs=["main"],
                required_checks=[{"id": "test", "argv": ["true"]}],
            )
            from zerver.actions.agents import _default_policy

            policy = _default_policy(self.owner, self.runner)
            policy["actions"] = [
                "context.read",
                "repository.read",
                "repository.edit",
                "checks.run",
            ]
        profile = create_profile(
            self.owner,
            name="Selection Code" if code else "Selection Answer",
            runner=self.runner,
            adapter_id="acp",
            adapter_version="1",
            default_mode="code" if code else "answer",
            repository=repository,
            policy=policy,
            idempotency_key=uuid4(),
        )
        setup = agents.AgentSetupOperation.objects.get(profile=profile)
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
                "capabilities": {
                    "chat_ready": True,
                    "code_ready": code,
                    "tool_calling": "passed" if code else "unknown",
                    "sandbox": "passed" if code else "unknown",
                    "config_version": 1,
                },
            },
        )
        return enable_profile(self.owner, profile, expected_revision=profile.revision)

    def grant(self, profile: agents.AgentProfile, *, member: bool = False) -> None:
        agents.AgentGrant.objects.create(
            realm=self.owner.realm,
            owner=self.owner,
            target_kind="profile",
            profile=profile,
            principal_group=self.group,
            actions=["profile.use", "context.read"],
        )
        if member:
            for kind, resource, actions in [
                ("runner", self.runner, ["runner.use"]),
                (
                    "repository",
                    profile.default_repository,
                    ["repository.read", "repository.edit", "checks.run"],
                ),
            ]:
                if resource is not None:
                    agents.AgentGrant.objects.create(
                        realm=self.owner.realm,
                        owner=self.owner,
                        target_kind=kind,
                        **{kind: resource},
                        principal_group=self.group,
                        actions=actions,
                    )

    def test_stale_online_heartbeat_is_unknown_in_selection(self) -> None:
        profile = self.ready_profile()
        self.subscribe(profile.bot_user, "Denmark")
        self.grant(profile, member=True)
        self.post(
            self.owner,
            "team-default",
            {"expected_selection_revision": 1, "profile_id": str(profile.id)},
        )
        self.runner.status = "online"
        self.runner.last_heartbeat_at = now() - timedelta(minutes=3)
        self.runner.save(update_fields=["status", "last_heartbeat_at"])
        choice = self.resolve(self.member)
        self.assertEqual(choice["runner"]["status"], "unknown")
        self.assertEqual(choice["reason"], "runner_unknown")

    def test_default_write_cas_audit_clear_and_offline_selection(self) -> None:
        profile = self.ready_profile()
        self.subscribe(profile.bot_user, "Denmark")
        self.grant(profile, member=True)
        before = self.resolve(self.owner)
        self.assertEqual(before["reason"], "no_eligible_default")
        self.assertIsNone(before["profile_id"])
        setup_count = agents.AgentSetupOperation.objects.count()
        selected = self.post(
            self.owner,
            "team-default",
            {"expected_selection_revision": 1, "profile_id": str(profile.id)},
        )
        self.assertEqual(selected["default"]["profile"]["id"], str(profile.id))
        choice = self.resolve(self.member)
        self.assertEqual(choice["profile_id"], str(profile.id))
        self.assertEqual(choice["job_kind"], "answer")
        self.assertEqual(choice["reason"], "runner_offline")
        self.assertTrue(choice["queue_permitted"])
        self.assertEqual(choice["selection_revision"], 2)
        stale = self.api_patch(
            self.other_admin,
            "/api/v1/agent/team-default",
            {
                "payload": json.dumps(
                    {"schema_version": 1, "expected_selection_revision": 1, "profile_id": None}
                )
            },
        )
        self.assertEqual(stale.status_code, 400)
        self.post(
            self.other_admin, "team-default", {"expected_selection_revision": 2, "profile_id": None}
        )
        logs = RealmAuditLog.objects.filter(
            realm=self.owner.realm, event_type=AuditLogEventType.AGENT_TEAM_DEFAULT_CHANGED
        ).order_by("id")
        self.assertEqual(logs.count(), 2)
        self.assertEqual(logs[0].extra_data["old_profile_id"], None)
        self.assertEqual(logs[0].extra_data["new_profile_id"], str(profile.id))
        self.assertEqual(logs[1].extra_data["old_profile_id"], str(profile.id))
        self.assertEqual(logs[1].extra_data["new_profile_id"], None)
        self.assertEqual(self.resolve(self.member)["reason"], "no_eligible_default")
        self.assertFalse(agents.AgentJob.objects.exists())
        self.assertFalse(agents.AgentOutbox.objects.exists())
        self.assertFalse(agents.AgentConversation.objects.exists())
        self.assertEqual(agents.AgentSetupOperation.objects.count(), setup_count)

    def test_explicit_modes_repository_and_changed_authority(self) -> None:
        answer = self.ready_profile()
        self.subscribe(answer.bot_user, "Denmark")
        base = {"explicit_profile_id": str(answer.id), "selection_state": "explicit"}
        self.assertEqual(self.resolve(self.owner, **base)["reason"], "runner_offline")
        self.assertEqual(
            self.resolve(self.owner, **base, job_kind="code")["reason"], "coding_unavailable"
        )
        code = self.ready_profile(code=True)
        self.subscribe(code.bot_user, "Denmark")
        selected = self.resolve(
            self.owner,
            explicit_profile_id=str(code.id),
            selection_state="explicit",
            job_kind="answer",
        )
        self.assertEqual(selected["job_kind"], "answer")
        self.assertTrue(selected["eligible"])
        # The profile's own repository resolves for anyone who may use it, even
        # while job_kind stays "answer": the composer needs it to offer Coding.
        self.assertEqual(
            selected["repository"],
            {"id": str(code.default_repository_id), "alias": "work", "base_ref": "main"},
        )
        self.assertIsNone(self.resolve(self.owner, **base)["repository"])
        selected = self.resolve(
            self.owner,
            explicit_profile_id=str(code.id),
            selection_state="explicit",
            job_kind="code",
            repository_id=str(code.default_repository_id),
        )
        self.assertEqual(selected["repository_id"], str(code.default_repository_id))
        self.assertTrue(selected["eligible"])
        other = register_repository(
            self.owner,
            self.runner,
            workspace_alias="other",
            canonical_origin=None,
            allowed_refs=["main"],
        )
        self.assertFalse(
            self.resolve(
                self.owner,
                explicit_profile_id=str(code.id),
                selection_state="explicit",
                job_kind="code",
                repository_id=str(other.id),
            )["eligible"]
        )
        agents.AgentRealmSettings.objects.filter(realm=self.owner.realm).update(
            profile_queue_limit=0
        )
        self.assertEqual(self.resolve(self.owner, **base)["reason"], "queue_full")
        agents.AgentRealmSettings.objects.filter(realm=self.owner.realm).update(
            profile_queue_limit=20
        )
        agents.AgentProfile.objects.filter(id=answer.id).update(desired_state="paused")
        self.assertEqual(self.resolve(self.owner, **base)["reason"], "profile_unavailable")
        agents.AgentProfile.objects.filter(id=answer.id).update(
            desired_state="enabled", readiness_revision=None
        )
        self.assertEqual(self.resolve(self.owner, **base)["reason"], "profile_not_ready")
        self.assertEqual(self.resolve(self.owner, selection_state="cleared")["reason"], "cleared")
        self.assertFalse(agents.AgentJob.objects.exists())

    def test_hidden_default_and_clear_without_target_access(self) -> None:
        profile = self.ready_profile()
        self.grant(profile)
        self.post(
            self.owner,
            "team-default",
            {"expected_selection_revision": 1, "profile_id": str(profile.id)},
        )
        hidden = self.resolve(self.member)
        self.assertEqual(hidden["selection_source"], "none")
        self.assertEqual(hidden["reason"], "no_eligible_default")
        self.assertIsNone(hidden["selection_revision"])
        self.assertIsNone(hidden["profile_id"])
        agents.AgentGrant.objects.filter(profile=profile).update(revoked_at=now())
        hidden_admin = self.assert_json_success(
            self.api_get(self.other_admin, "/api/v1/agent/team-default")
        )["default"]
        self.assertTrue(hidden_admin["has_default"])
        self.assertNotIn("profile", hidden_admin)
        self.post(
            self.other_admin, "team-default", {"expected_selection_revision": 2, "profile_id": None}
        )
        self.assertIsNone(
            agents.AgentRealmSettings.objects.get(realm=self.owner.realm).default_profile_id
        )

    def test_source_destination_validation_and_revocation(self) -> None:
        profile = self.ready_profile()
        self.subscribe(profile.bot_user, "Denmark")
        message_id = self.send_stream_message(self.owner, "Denmark", "Source")
        selection = self.post(
            self.owner,
            "selection/resolve",
            {
                "source_message_id": message_id,
                "explicit_profile_id": str(profile.id),
                "selection_state": "explicit",
            },
        )["selection"]
        self.assertTrue(selection["eligible"])
        invalid = self.api_post(
            self.owner,
            "/api/v1/agent/selection/resolve",
            {
                "payload": json.dumps(
                    {
                        "schema_version": 1,
                        "source_message_id": message_id,
                        "destination": {"kind": "stream", "stream_id": self.stream.id},
                    }
                )
            },
        )
        self.assertEqual(invalid.status_code, 400)
        inaccessible = self.api_post(
            self.owner,
            "/api/v1/agent/selection/resolve",
            {
                "payload": json.dumps(
                    {
                        "schema_version": 1,
                        "destination": {"kind": "stream", "stream_id": 999999999},
                        "selection_state": "cleared",
                    }
                )
            },
        )
        self.assertEqual(inaccessible.status_code, 400)
        agents.AgentRunner.objects.filter(id=self.runner.id).update(
            revoked_at=now(), status="revoked"
        )
        after = self.resolve(
            self.owner, explicit_profile_id=str(profile.id), selection_state="explicit"
        )
        self.assertEqual(after["reason"], "runner_unavailable")
        self.assertFalse(after["queue_permitted"])

    def test_scoped_default_and_revoked_grant_between_selection_and_admission(self) -> None:
        profile = self.ready_profile()
        self.subscribe(profile.bot_user, "Denmark")
        self.grant(profile, member=True)
        agents.AgentGrant.objects.filter(profile=profile).update(
            scope={"kind": "stream", "stream_id": self.stream.id}
        )
        self.post(
            self.owner,
            "team-default",
            {"expected_selection_revision": 1, "profile_id": str(profile.id)},
        )
        self.assertTrue(self.resolve(self.member)["eligible"])
        message_id = self.send_stream_message(self.member, "Denmark", "Run task")
        source_choice = self.post(
            self.member, "selection/resolve", {"source_message_id": message_id}
        )["selection"]
        self.assertEqual(source_choice["profile_id"], str(profile.id))
        agents.AgentGrant.objects.filter(profile=profile).update(revoked_at=now())
        hidden = self.resolve(self.member)
        self.assertEqual(hidden["reason"], "no_eligible_default")
        self.assertIsNone(hidden["profile_id"])
        from zerver.models import Message

        with self.assertRaises(AgentAccessDenied):
            agent_jobs.create_job(
                self.member,
                profile=profile,
                source=Message.objects.get(id=message_id),
                request="Run task",
                idempotency_key=uuid4(),
                job_kind="answer",
                delivery_target="answer",
            )
        self.assertFalse(agents.AgentJob.objects.exists())

    def test_default_rejects_unready_and_inactive_owner(self) -> None:
        profile = self.ready_profile()
        self.grant(profile, member=True)
        agents.AgentProfile.objects.filter(id=profile.id).update(readiness_revision=None)
        stale = self.api_patch(
            self.owner,
            "/api/v1/agent/team-default",
            {
                "payload": json.dumps(
                    {
                        "schema_version": 1,
                        "expected_selection_revision": 1,
                        "profile_id": str(profile.id),
                    }
                )
            },
        )
        self.assertEqual(stale.status_code, 400)
        self.assertFalse(
            RealmAuditLog.objects.filter(
                realm=self.owner.realm, event_type=AuditLogEventType.AGENT_TEAM_DEFAULT_CHANGED
            ).exists()
        )
        agents.AgentProfile.objects.filter(id=profile.id).update(
            readiness_revision=profile.revision
        )
        self.post(
            self.owner,
            "team-default",
            {"expected_selection_revision": 1, "profile_id": str(profile.id)},
        )
        self.owner.is_active = False
        self.owner.save(update_fields=["is_active"])
        self.assertEqual(self.resolve(self.member)["reason"], "no_eligible_default")

    def test_nonowner_admin_can_select_with_readable_scoped_grants(self) -> None:
        profile = self.ready_profile()
        self.subscribe(profile.bot_user, "Denmark")
        self.subscribe(self.other_admin, "Denmark")
        admin_group = check_add_user_group(
            self.owner.realm, "Agent administrators", [self.other_admin], acting_user=self.owner
        )
        scope = {"kind": "stream", "stream_id": self.stream.id}
        for kind, resource, actions in [
            ("profile", profile, ["profile.use", "context.read"]),
            ("runner", self.runner, ["runner.use"]),
        ]:
            agents.AgentGrant.objects.create(
                realm=self.owner.realm,
                owner=self.owner,
                target_kind=kind,
                **{kind: resource},
                principal_group=admin_group,
                scope=scope,
                actions=actions,
            )
        selected = self.post(
            self.other_admin,
            "team-default",
            {"expected_selection_revision": 1, "profile_id": str(profile.id)},
        )
        self.assertEqual(selected["default"]["profile"]["id"], str(profile.id))
        self.assertEqual(self.resolve(self.other_admin)["selection_source"], "team_default")

    def test_code_default_requires_repository_actions_for_nonowner_admin(self) -> None:
        profile = self.ready_profile(code=True)
        self.subscribe(profile.bot_user, "Denmark")
        self.subscribe(self.other_admin, "Denmark")
        admin_group = check_add_user_group(
            self.owner.realm, "Code administrators", [self.other_admin], acting_user=self.owner
        )
        grants = []
        for kind, resource, actions in [
            (
                "profile",
                profile,
                ["profile.use", "context.read", "repository.read", "repository.edit", "checks.run"],
            ),
            ("runner", self.runner, ["runner.use"]),
            ("repository", profile.default_repository, ["repository.read"]),
        ]:
            grants.append(
                agents.AgentGrant.objects.create(
                    realm=self.owner.realm,
                    owner=self.owner,
                    target_kind=kind,
                    **{kind: resource},
                    principal_group=admin_group,
                    scope={"kind": "stream", "stream_id": self.stream.id},
                    actions=actions,
                )
            )
        payload = {
            "payload": json.dumps(
                {
                    "schema_version": 1,
                    "expected_selection_revision": 1,
                    "profile_id": str(profile.id),
                }
            )
        }
        self.assertEqual(
            self.api_patch(self.other_admin, "/api/v1/agent/team-default", payload).status_code,
            400,
        )
        grants[-1].actions = ["repository.read", "repository.edit", "checks.run"]
        grants[-1].save(update_fields=["actions"])
        selected = self.post(
            self.other_admin,
            "team-default",
            {"expected_selection_revision": 1, "profile_id": str(profile.id)},
        )
        self.assertEqual(selected["default"]["profile"]["id"], str(profile.id))
