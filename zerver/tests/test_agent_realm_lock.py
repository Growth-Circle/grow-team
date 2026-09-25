"""One workspace's agent writes must not block another workspace's.

agent_transaction() keys its advisory lock off the current realm instead of
one fixed global key. These tests exercise the lock directly with two real
Postgres connections; the badge-count tests below exercise the read side,
which now also queries in bulk instead of looping per row.
"""

from concurrent.futures import ThreadPoolExecutor
from threading import Event
from uuid import uuid4

from django.db import connection, connections
from django.utils.timezone import now as timezone_now
from typing_extensions import override

from zerver.actions.agent_jobs import create_job
from zerver.actions.agents import create_profile, enable_profile, record_readiness
from zerver.actions.tasks import do_create_task
from zerver.lib.agent_context import AgentBusy, agent_realm, agent_transaction
from zerver.lib.tasks import get_or_create_default_board, running_agent_job_count, work_counts
from zerver.lib.test_classes import ZulipTestCase, ZulipTransactionTestCase
from zerver.models import Message, agents


class AgentRealmLockTests(ZulipTransactionTestCase):
    """Real cross-connection contention. See ZulipTransactionTestCase for why
    these cannot use the default ZulipTestCase, which wraps a test in one
    uncommitted transaction and so cannot show two sessions blocking."""

    def test_two_realms_do_not_block_each_other(self) -> None:
        entered, release = Event(), Event()

        def hold_realm_two() -> None:
            try:
                with agent_realm(2), agent_transaction():
                    entered.set()
                    assert release.wait(3)
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(hold_realm_two)
            self.assertTrue(entered.wait(3))
            # A write for a different realm proceeds right away.
            with agent_realm(1), agent_transaction():
                pass
            release.set()
            pending.result(timeout=5)

    def test_same_realm_is_still_busy(self) -> None:
        entered, release = Event(), Event()

        def hold_realm_one() -> None:
            try:
                with agent_realm(1), agent_transaction():
                    entered.set()
                    assert release.wait(3)
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(hold_realm_one)
            self.assertTrue(entered.wait(3))
            with self.assertRaises(AgentBusy), agent_realm(1), agent_transaction():
                pass
            release.set()
            pending.result(timeout=5)

    def test_realm_id_three_does_not_share_the_no_realm_key(self) -> None:
        """The fork used to lock (174621, 3) for every agent write. Guard
        against a realm whose id is 3 ever sharing that key again."""
        held, release = Event(), Event()

        def hold_legacy_key() -> None:
            try:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT pg_advisory_lock(174621, 3)")
                held.set()
                assert release.wait(3)
                with connection.cursor() as cursor:
                    cursor.execute("SELECT pg_advisory_unlock(174621, 3)")
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(hold_legacy_key)
            self.assertTrue(held.wait(3))
            with agent_realm(3), agent_transaction():
                pass
            release.set()
            pending.result(timeout=5)

    def test_no_realm_transaction_blocks_every_realm(self) -> None:
        """Reconcile and other whole-server work still take an exclusive
        lock, so they wait for every realm's agent writes, and every
        realm's agent writes wait for them."""
        entered, release = Event(), Event()

        def hold_no_realm() -> None:
            try:
                with agent_transaction():
                    entered.set()
                    assert release.wait(3)
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(hold_no_realm)
            self.assertTrue(entered.wait(3))
            with self.assertRaises(AgentBusy), agent_realm(1), agent_transaction():
                pass
            release.set()
            pending.result(timeout=5)

    def test_stale_request_does_not_leak_a_realm_after_it_closes(self) -> None:
        """two_factor's ThreadLocals never clears get_current_request()
        once a request ends. Without _request_open, agent code that runs
        in the same OS thread afterwards (a test, or a future in-process
        worker) could inherit that request's user and its realm."""
        from zerver.lib.agent_context import _resolve_realm_id

        self.assertIsNone(_resolve_realm_id())
        self.login("hamlet")
        self.client_get("/json/users/me")
        self.assertIsNone(_resolve_realm_id())


class AgentBadgeCountTests(ZulipTestCase):
    """running_agent_job_count() and work_counts() now query in bulk
    instead of looping with a permission check, or a Python total, per
    row. These pin the counts on a small fixture and the query count
    that reads it."""

    @override
    def setUp(self) -> None:
        super().setUp()
        self.owner = self.example_user("hamlet")
        agents.AgentRealmSettings.objects.update_or_create(
            realm=self.owner.realm, defaults={"enabled": True}
        )
        self.runner = agents.AgentRunner.objects.create(
            realm=self.owner.realm,
            owner=self.owner,
            name="Badge",
            fingerprint="c" * 64,
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
        self.profile = create_profile(
            self.owner,
            name="Badge",
            runner=self.runner,
            adapter_id="acp",
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
                "profile_revision": 1,
                "runner_id": str(self.runner.id),
                "descriptor_digest": setup.descriptor_digest,
                "configuration_digest": setup.configuration_digest,
                "state": "ready",
                "capabilities": {"chat_ready": True, "config_version": 1},
            },
        )
        self.profile.refresh_from_db()
        enable_profile(self.owner, self.profile, expected_revision=self.profile.revision)

    def _running_job_on_new_message(self, text: str) -> agents.AgentJob:
        message_id = self.send_group_direct_message(
            self.owner, [self.profile.bot_user, self.example_user("iago")], text
        )
        job = create_job(
            self.owner,
            profile=self.profile,
            source=Message.objects.get(id=message_id),
            request=text,
            idempotency_key=uuid4(),
            job_kind="answer",
            delivery_target="answer",
        )
        agents.AgentJob.objects.filter(id=job.id).update(status="running")
        return job

    def test_running_job_count_matches_the_old_per_job_loop(self) -> None:
        self._running_job_on_new_message("First request")
        self._running_job_on_new_message("Second request")

        # One query loads both jobs (with every relation require_job_access
        # dereferences); five more per job cover what select_related cannot
        # pre-load (the actor row, the latest attempt, and message access).
        with self.assert_database_query_count(11):
            count = running_agent_job_count(self.owner)
        self.assertEqual(count, 2)

    def test_work_counts_matches_the_old_python_totals(self) -> None:
        self._running_job_on_new_message("Third request")
        board = get_or_create_default_board(self.owner.realm)
        columns = list(board.columns.all())
        review_column = next(column for column in columns if column.is_review)

        do_create_task(
            user_profile=self.owner,
            board=board,
            column=columns[0],
            title="Assigned to me",
            assignee=self.owner,
        )
        do_create_task(
            user_profile=self.owner,
            board=board,
            column=review_column,
            title="Mine to review",
            reviewer=self.owner,
        )
        done = do_create_task(
            user_profile=self.owner,
            board=board,
            column=columns[0],
            title="Already done",
            assignee=self.owner,
        )
        done.completed_at = timezone_now()
        done.save(update_fields=["completed_at"])

        # Six of running_agent_job_count()'s own queries for the one agent
        # job above, plus four for the task totals: the board, its review
        # columns, the task board's stream ids, and one aggregate query.
        with self.assert_database_query_count(10):
            counts = work_counts(self.owner)
        self.assertEqual(
            counts,
            {
                "task_board": 3,
                "my_tasks": 1,
                "awaiting_my_review": 1,
                "agent_running": 1,
            },
        )
