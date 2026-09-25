"""Tests for WP13: reserved agent names, bot email slugs, per-profile
stats, the access-filtered audit feed, guest grants, task links to agent
jobs, and the ensure_builtin_agents command (spec 05, 06, 10, 11; §4.5)."""

from datetime import timedelta
from uuid import uuid4

from django.utils.timezone import now
from typing_extensions import override

from zerver.actions.agent_jobs import audit as audit_job_event
from zerver.actions.agent_jobs import create_job
from zerver.actions.agents import (
    create_agent_grant,
    create_profile,
    enable_profile,
    record_readiness,
    share_agent_profile,
)
from zerver.actions.create_user import do_create_user
from zerver.actions.users import do_change_user_role
from zerver.lib.agent_names import (
    RESERVED_AGENT_NAMES,
    agent_name_available,
    bot_email_for_agent_name,
)
from zerver.lib.agent_policy import _principal_matches
from zerver.lib.agent_stats import profile_stats
from zerver.lib.tasks import get_or_create_default_board
from zerver.lib.test_classes import ZulipTestCase
from zerver.lib.user_groups import get_role_based_system_groups_dict
from zerver.models import agents
from zerver.models.messages import Message
from zerver.models.tasks import Task
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


class ProfileStatsTest(AgentDirectoryAPITestCase):
    def make_card(self, counter: int, **fields: object) -> Task:
        board = get_or_create_default_board(self.owner.realm)
        column = board.columns.order_by("order").first()
        assert column is not None
        return Task.objects.create(
            realm=self.owner.realm,
            board=board,
            column=column,
            counter=counter,
            title=f"Card {counter}",
            creator=self.owner,
            **fields,
        )

    def make_approval(self, job: agents.AgentJob, decision: str) -> agents.AgentApproval:
        attempt = agents.AgentAttempt.objects.filter(job=job).first()
        if attempt is None:
            attempt = agents.AgentAttempt.objects.create(
                realm=job.realm,
                job=job,
                runner=self.runner,
                number=1,
                lease_epoch=1,
                lease_expires_at=now(),
                descriptor_digest="a" * 64,
            )
        operation = agents.AgentOperation.objects.create(
            realm=job.realm,
            attempt=attempt,
            operation_id=uuid4(),
            tool_class="send_message",
            argument_digest="a" * 64,
            arguments={"action": "send_message"},
        )
        consumed = decision == "consumed"
        return agents.AgentApproval.objects.create(
            realm=job.realm,
            job=job,
            attempt=attempt,
            operation=operation,
            approver=self.owner if decision != "pending" else None,
            operation_hash="a" * 64,
            policy_version=1,
            tree_hash="a" * 64,
            expires_at=now(),
            decision=decision,
            decided_at=now(),
            consumed_at=now() if consumed else None,
        )

    def test_tasks_per_week_counts_cards_the_agent_finished(self) -> None:
        profile = self.create_ready_profile()
        self.make_card(1, agent_profile=profile, completed_at=now())
        # 05-D8: a card assigned to the agent's bot counts too.
        self.make_card(2, assignee=profile.bot_user, completed_at=now())
        # A card that is not done, or was done earlier, does not count.
        self.make_card(3, agent_profile=profile)
        self.make_card(4, agent_profile=profile, completed_at=now() - timedelta(days=8))
        self.make_card(5, assignee=self.owner, completed_at=now())

        stats = profile_stats([profile.id])
        self.assertEqual(stats[profile.id]["tasks_per_week"], 2)
        self.assertIsNone(stats[profile.id]["approve_rate"])

    def test_approve_rate_counts_consumed_approvals_as_approved(self) -> None:
        profile = self.create_ready_profile()
        job = self.make_job(profile)
        for decision in ("approved", "consumed", "consumed", "rejected", "pending"):
            self.make_approval(job, decision)
        old = self.make_approval(job, "rejected")
        agents.AgentApproval.objects.filter(id=old.id).update(created_at=now() - timedelta(days=31))

        stats = profile_stats([profile.id])
        self.assertEqual(stats[profile.id]["approve_rate"], 0.6)

    def test_stats_endpoint_lists_every_visible_profile(self) -> None:
        profile = self.create_ready_profile()
        result = self.assert_json_success(self.client_get("/json/agent/profiles/stats"))
        self.assertIn(str(profile.id), result["stats"])
        self.assertEqual(result["stats"][str(profile.id)]["tasks_per_week"], 0)


class AuditFeedTest(AgentDirectoryAPITestCase):
    @override
    def setUp(self) -> None:
        super().setUp()
        self.profile = self.create_ready_profile()
        self.job = self.make_job(self.profile)
        # A "server" event: a test cannot sign a runner event envelope.
        audit_job_event(
            self.job,
            "approval.requested",
            {
                "approval_id": str(uuid4()),
                "operation_hash": "a" * 64,
                "version": 1,
                "decision": "pending",
            },
            actor=self.owner,
        )
        audit_job_event(
            self.job,
            "attempt.stop_requested",
            {"status": "cancel_requested", "reason": ""},
            actor=self.owner,
        )

    def add_tool_event(self, event_type: str, tool_class: str) -> agents.AgentAuditEvent:
        """Store a runner tool event as the runner sends it, without its
        signed envelope."""
        self.job.event_sequence += 1
        self.job.save(update_fields=["event_sequence"])
        return agents.AgentAuditEvent.objects.create(
            realm=self.job.realm,
            job=self.job,
            sequence=self.job.event_sequence,
            authority="runner",
            type=event_type,
            payload={"tool_class": tool_class, "operation_id": str(uuid4())},
        )

    def feed(self, **params: object) -> dict[str, object]:
        return self.assert_json_success(self.client_get("/json/agent/audit", params))

    def test_owner_sees_categorized_events(self) -> None:
        self.add_tool_event("tool.started", "repository.edit")
        self.add_tool_event("tool.finished", "repository.edit")
        self.add_tool_event("tool.finished", "shell.run")
        self.add_tool_event("tool.finished", "context.read")
        result = self.feed(profile_id=str(self.profile.id))
        events = result["events"]
        assert isinstance(events, list)
        job_rows = [(event["type"], event["category"]) for event in events if event["job_id"]]
        # Job lifecycle events and the start of a tool call do not show.
        self.assertEqual(
            job_rows,
            [
                ("tool.finished", "read"),
                ("tool.finished", "written"),
                ("tool.finished", "written"),
                ("approval.requested", "review"),
            ],
        )
        profile_rows = [event["type"] for event in events if not event["job_id"]]
        self.assertEqual(profile_rows, ["profile.enabled", "profile.created"])
        self.assertIsNone(result["next_before"])

    def test_profile_filter_narrows_the_feed(self) -> None:
        other = create_profile(
            self.owner,
            name="Other",
            runner=self.runner,
            adapter_id="acp",
            adapter_version="1",
            mode="acp",
            default_mode="answer",
            idempotency_key=uuid4(),
        )
        result = self.feed(profile_id=str(other.id))
        # `other` has no job activity of its own yet, so its feed holds only
        # its own creation, none of `self.profile`'s approval/stop events.
        events = result["events"]
        assert isinstance(events, list)
        self.assertEqual([event["type"] for event in events], ["profile.created"])

    def test_older_profile_rows_are_found_behind_newer_ones(self) -> None:
        newer = self.create_ready_profile(name="Newer")
        for _ in range(3):
            enable_profile(self.owner, newer, expected_revision=newer.revision)
        result = self.feed(profile_id=str(self.profile.id), limit=1)
        events = result["events"]
        assert isinstance(events, list)
        self.assertEqual([event["type"] for event in events], ["approval.requested"])

    def test_pages_follow_next_before_without_gaps(self) -> None:
        for _ in range(4):
            self.add_tool_event("tool.finished", "context.read")
        # Two events share one time; a page must not split them.
        stamp = now()
        agents.AgentAuditEvent.objects.filter(job=self.job, type="tool.finished").update(
            occurred_at=stamp
        )
        seen: list[object] = []
        params: dict[str, object] = {"profile_id": str(self.profile.id), "limit": 2}
        while True:
            result = self.feed(**params)
            events = result["events"]
            assert isinstance(events, list)
            seen += [event["id"] for event in events]
            if result["next_before"] is None:
                break
            params["before"] = result["next_before"]
        # 4 tool events, 1 approval request, 2 profile rows, each once.
        self.assert_length(seen, 7)
        self.assert_length(set(seen), 7)

    def test_invalid_cursor_or_limit_is_rejected(self) -> None:
        for params in ({"before": 2**60}, {"before": -1}, {"limit": 0}, {"limit": 201}):
            result = self.client_get("/json/agent/audit", params)
            self.assert_json_error(result, "Agent request rejected.")

    def test_unrelated_member_sees_nothing(self) -> None:
        self.login_user(self.example_user("cordelia"))
        result = self.feed()
        self.assertEqual(result["events"], [])

    def test_profile_viewer_without_job_access_sees_no_job_events(self) -> None:
        cordelia = self.example_user("cordelia")
        stream = self.make_stream("owner-only", invite_only=True)
        self.subscribe(self.owner, stream.name)
        self.subscribe(self.profile.bot_user, stream.name)
        private_job = self.make_job(self.profile, stream_name=stream.name)
        audit_job_event(
            private_job,
            "approval.requested",
            {
                "approval_id": str(uuid4()),
                "operation_hash": "b" * 64,
                "version": 1,
                "decision": "pending",
            },
            actor=self.owner,
        )
        share_agent_profile(self.owner, self.profile, principal_user=cordelia)

        self.login_user(cordelia)
        result = self.feed(profile_id=str(self.profile.id))
        events = result["events"]
        assert isinstance(events, list)
        self.assertNotIn(str(private_job.id), {event["job_id"] for event in events})
        self.assertIn("profile.shared", {event["type"] for event in events})


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
