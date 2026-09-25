"""One workspace's agent writes must not block another workspace's.

agent_transaction() keys its advisory lock off the current realm instead of
one fixed global key. These tests exercise the lock directly with two real
Postgres connections; the badge-count tests below exercise the read side,
which now filters candidates in SQL before checking each one, instead of
checking every running job in the realm.
"""

import json
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Event
from time import sleep
from uuid import uuid4

from django.db import connection, connections
from django.utils.timezone import now as timezone_now
from typing_extensions import override

from zerver.actions.agent_jobs import create_job
from zerver.actions.agents import create_profile, enable_profile, record_readiness
from zerver.actions.tasks import do_create_task
from zerver.lib.agent_context import AgentBusy, agent_realm, agent_transaction
from zerver.lib.agent_secrets import hash_agent_credential
from zerver.lib.tasks import get_or_create_default_board, running_agent_job_count, work_counts
from zerver.lib.test_classes import ZulipTestCase, ZulipTransactionTestCase
from zerver.models import Client, Message, Realm, Stream, UserProfile, agents
from zerver.models.realms import get_realm


def _retry_on_lock_contention(attempt: Callable[[], None]) -> None:
    """LOCK TABLE ... IN SHARE MODE NOWAIT (agent_context.py) can lose a
    race against an unrelated table-level lock, such as autovacuum's, on
    one of the 20 ACL tables. Retry a path that expects to succeed up to
    3 times before trusting AgentBusy; a path that expects AgentBusy for
    a real reason is not wrapped in this, so it is unaffected."""
    for remaining in range(2, -1, -1):
        try:
            attempt()
            return
        except AgentBusy:
            if remaining == 0:
                raise
            sleep(0.1)


def _ready_profile(
    owner: UserProfile, *, name: str, fingerprint: str
) -> tuple[agents.AgentRunner, agents.AgentProfile]:
    """A minimal enabled profile with a working runner, for tests that need
    a real job or a real runner credential, not only the lock machinery."""
    agents.AgentRealmSettings.objects.update_or_create(
        realm=owner.realm, defaults={"enabled": True}
    )
    runner = agents.AgentRunner.objects.create(
        realm=owner.realm,
        owner=owner,
        name=name,
        fingerprint=fingerprint,
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
    profile = create_profile(
        owner,
        name=name,
        runner=runner,
        adapter_id="acp",
        adapter_version="1",
        idempotency_key=uuid4(),
    )
    setup = agents.AgentSetupOperation.objects.get(profile=profile)
    record_readiness(
        runner,
        setup,
        {
            "schema_version": 1,
            "profile_id": str(profile.id),
            "profile_revision": 1,
            "runner_id": str(runner.id),
            "descriptor_digest": setup.descriptor_digest,
            "configuration_digest": setup.configuration_digest,
            "state": "ready",
            "capabilities": {"chat_ready": True, "config_version": 1},
        },
    )
    profile.refresh_from_db()
    enable_profile(owner, profile, expected_revision=profile.revision)
    return runner, profile


def _cleanup_ready_profile(
    runner: agents.AgentRunner,
    profile: agents.AgentProfile,
    *,
    settings_existed: bool,
    jobs: list[agents.AgentJob] = [],  # noqa: B006
) -> None:
    """Undo _ready_profile() (and any jobs made with it) for a
    ZulipTransactionTestCase, which commits for real and so is not
    rolled back between tests the way ZulipTestCase is."""
    bot_user = profile.bot_user
    for job in jobs:
        conversation_id = job.conversation_id
        agents.AgentAttempt.objects.filter(job=job).delete()
        agents.AgentContextRef.objects.filter(job=job).delete()
        agents.AgentAuditEvent.objects.filter(job=job).delete()
        agents.AgentOutbox.objects.filter(job=job).delete()
        agents.AgentJob.objects.filter(id=job.id).update(follows_job=None, resume_checkpoint=None)
        job.delete()
        agents.AgentConversation.objects.filter(id=conversation_id).delete()
    agents.AgentGrant.objects.filter(profile=profile).delete()
    agents.AgentProbeGrant.objects.filter(setup_operation__profile=profile).delete()
    agents.AgentSetupOperation.objects.filter(profile=profile).delete()
    profile.delete()
    bot_user.delete()
    runner.delete()
    if not settings_existed:
        agents.AgentRealmSettings.objects.filter(realm=runner.realm).delete()


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
            def write_realm_one() -> None:
                with agent_realm(1), agent_transaction():
                    pass

            _retry_on_lock_contention(write_realm_one)
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

    def test_realm_three_does_not_use_the_legacy_global_key(self) -> None:
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

            def write_realm_three() -> None:
                with agent_realm(3), agent_transaction():
                    pass

            _retry_on_lock_contention(write_realm_three)
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

    def test_no_realm_transaction_waits_for_a_realms_open_transaction(self) -> None:
        """The reverse of test_no_realm_transaction_blocks_every_realm: a
        realm's shared hold on (174621, 0) also blocks a no-realm caller's
        exclusive attempt on that same key, the direction the (174621, 0)
        comment in agent_context.py describes but no test exercised."""
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
            with self.assertRaises(AgentBusy), agent_transaction():
                pass
            release.set()
            pending.result(timeout=5)

    def test_acl_table_write_waits_on_other_realm_transaction(self) -> None:
        """map-backend.md R1 is not fixed by the per-realm advisory lock
        alone: the ACL table lock (agent_context.py) is still server-wide.
        Two realms' agent_transaction() calls do not conflict with each
        other by themselves (their SHARE holds coexist), but a write to
        one of the 20 ACL tables needs ROW EXCLUSIVE, which conflicts with
        every other open agent transaction's SHARE hold, in any realm.
        This pins that remaining wait so a future change does not widen
        it by accident; replacing it with row locks needs an ACL security
        review (map-backend.md R1) and is not done by this fix."""
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
            with self.assertRaises(AgentBusy), agent_realm(1), agent_transaction():
                # No row needs to exist: UPDATE takes its ROW EXCLUSIVE
                # table lock before it matches any row.
                Realm.objects.filter(id=0).update(description="contention probe")
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

    def test_unknown_credential_is_rejected_before_any_lock(self) -> None:
        """A Bearer token that matches no AgentRunnerCredential must never
        reach agent_transaction(): endpoint() (agent_runner.py) looks the
        token up by hash and rejects it outright, instead of falling
        through to a view that would take a lock before ever checking
        it. Holding the whole-server shared slot proves this: the old
        fallback (return view(request)) would have taken the realm-less,
        exclusive branch of that same key and failed busy (503)."""
        held, release = Event(), Event()

        def hold_shared_slot() -> None:
            try:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT pg_advisory_lock_shared(174621, 0)")
                held.set()
                assert release.wait(3)
                with connection.cursor() as cursor:
                    cursor.execute("SELECT pg_advisory_unlock_shared(174621, 0)")
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(hold_shared_slot)
            self.assertTrue(held.wait(3))
            response = self.client_get(
                "/api/v1/agent/runner/leases", HTTP_AUTHORIZATION="Bearer " + "z" * 40
            )
            release.set()
            pending.result(timeout=5)
        self.assertEqual(response.status_code, 401)

    def test_view_without_agent_realm_locks_the_logged_in_users_realm(self) -> None:
        """cancel_job() (zerver/actions/agent_jobs.py) never calls
        agent_realm(): it relies on _resolve_realm_id()'s fallback to the
        logged-in request's user. A lock on a different realm must not
        block it; a lock on the user's own realm must."""
        hamlet = self.example_user("hamlet")
        self.login_user(hamlet)
        settings_existed = agents.AgentRealmSettings.objects.filter(realm=hamlet.realm).exists()
        clients_before = set(Client.objects.values_list("id", flat=True))
        runner, profile = _ready_profile(hamlet, name="Cancel probe", fingerprint="d" * 64)
        message = Message.objects.get(
            id=self.send_stream_message(hamlet, "Denmark", "Please answer")
        )
        job = create_job(
            hamlet,
            profile=profile,
            source=message,
            request="Please answer",
            idempotency_key=uuid4(),
            job_kind="answer",
            delivery_target="answer",
        )
        body = json.dumps({"schema_version": 1, "expected_version": job.version})

        def cancel() -> int:
            url = f"/json/agent/jobs/{job.id}/cancel"
            return self.client_post(url, {"payload": body}).status_code

        def hold(realm_id: int, held: Event, release: Event) -> None:
            try:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT pg_advisory_lock(174622, %s)", [realm_id])
                held.set()
                assert release.wait(3)
                with connection.cursor() as cursor:
                    cursor.execute("SELECT pg_advisory_unlock(174622, %s)", [realm_id])
            finally:
                connections.close_all()

        try:
            for realm_id, expected in [(get_realm("lear").id, 200), (hamlet.realm_id, 503)]:
                held, release = Event(), Event()
                with ThreadPoolExecutor(max_workers=1) as pool:
                    pending = pool.submit(hold, realm_id, held, release)
                    self.assertTrue(held.wait(3))
                    self.assertEqual(cancel(), expected)
                    release.set()
                    pending.result(timeout=5)
        finally:
            # ZulipTransactionTestCase commits for real (needed above so
            # the locking thread's own connection sees these rows); undo
            # them here instead of leaving them for the next test.
            _cleanup_ready_profile(
                runner, profile, settings_existed=settings_existed, jobs=[job]
            )
            Client.objects.exclude(id__in=clients_before).delete()

    def test_endpoint_decorator_locks_the_runners_own_realm(self) -> None:
        """endpoint() (agent_runner.py) wraps a valid credential's view
        call in agent_realm(runner.realm_id). A lock on a different realm
        must not block a runner call; a lock on the runner's own realm,
        or on the whole-server shared slot, must."""
        hamlet = self.example_user("hamlet")
        # leases() only reads AgentAttempt rows through the runner; it
        # needs no profile, so a bare runner keeps this test's cleanup
        # (below) to the two rows it actually creates.
        runner = agents.AgentRunner.objects.create(
            realm=hamlet.realm,
            owner=hamlet,
            name="Leases probe",
            fingerprint="e" * 64,
            catalog_report={"revision": 1, "adapters": [], "sandboxes": []},
        )
        token = "synthetic-leases-token-" + "a" * 40
        credential = agents.AgentRunnerCredential.objects.create(
            runner=runner,
            realm=hamlet.realm,
            token_hash=hash_agent_credential(token),
            refresh_hash=hash_agent_credential("refresh" + token),
            expires_at=timezone_now() + timedelta(hours=1),
            refresh_expires_at=timezone_now() + timedelta(days=1),
        )

        def leases() -> int:
            return self.client_get(
                "/api/v1/agent/runner/leases", HTTP_AUTHORIZATION=f"Bearer {token}"
            ).status_code

        def hold(key: tuple[int, int], held: Event, release: Event) -> None:
            try:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT pg_advisory_lock(%s, %s)", list(key))
                held.set()
                assert release.wait(3)
                with connection.cursor() as cursor:
                    cursor.execute("SELECT pg_advisory_unlock(%s, %s)", list(key))
            finally:
                connections.close_all()

        try:
            for key, expected in [
                ((174622, get_realm("lear").id), 200),
                ((174622, hamlet.realm_id), 503),
                ((174621, 0), 503),
            ]:
                held, release = Event(), Event()
                with ThreadPoolExecutor(max_workers=1) as pool:
                    pending = pool.submit(hold, key, held, release)
                    self.assertTrue(held.wait(3))
                    self.assertEqual(leases(), expected)
                    release.set()
                    pending.result(timeout=5)
        finally:
            # ZulipTransactionTestCase commits for real (needed above so
            # the locking thread's own connection sees these rows); undo
            # them here instead of leaving them for the next test.
            credential.delete()
            runner.delete()


class AgentBadgeCountTests(ZulipTestCase):
    """running_agent_job_count() narrows its candidates in SQL before
    checking each one, and work_counts() totals its rows in one grouped
    query instead of a Python loop over every visible task. These pin
    the counts on a small fixture and the query count that reads it."""

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

    def test_running_job_count_excludes_jobs_hamlet_cannot_see(self) -> None:
        """The SQL filter in running_agent_job_count() drops a job before
        require_job_access() runs on it, not only before it is counted.
        Two running jobs on a channel hamlet cannot read must cost the
        same as zero running jobs: the query count must not grow, not
        only the returned count."""
        iago = self.example_user("iago")
        channel = self.make_stream(
            "hidden-work", invite_only=True, history_public_to_subscribers=False
        )
        self.subscribe(iago, channel.name)
        self.subscribe(self.profile.bot_user, channel.name)
        # iago is not self.profile's owner; a grant lets him create a job
        # on it. This is what lets create_job() succeed, not what this
        # test is about: the audience (channel subscribers) excludes
        # hamlet either way.
        agents.AgentGrant.objects.create(
            realm=self.owner.realm,
            owner=self.owner,
            target_kind="profile",
            profile=self.profile,
            principal_user=iago,
            actions=["profile.use", "context.read"],
        )
        agents.AgentGrant.objects.create(
            realm=self.owner.realm,
            owner=self.owner,
            target_kind="runner",
            runner=self.runner,
            principal_user=iago,
            actions=["runner.use"],
        )
        for text in ("Hidden first", "Hidden second"):
            message = Message.objects.get(id=self.send_stream_message(iago, channel.name, text))
            job = create_job(
                iago,
                profile=self.profile,
                source=message,
                request=text,
                idempotency_key=uuid4(),
                job_kind="answer",
                delivery_target="answer",
            )
            agents.AgentJob.objects.filter(id=job.id).update(status="running")

        # One query, same as with no running jobs at all: the filter
        # excludes both rows, so the per-job loop never runs.
        with self.assert_database_query_count(1):
            count = running_agent_job_count(self.owner)
        self.assertEqual(count, 0)

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
        # job above, plus two for the task totals: the board, and one
        # query grouped by stream_id that covers every counter at once.
        with self.assert_database_query_count(8):
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

    def test_work_counts_only_counts_tasks_from_readable_streams(self) -> None:
        """visible.filter(Q(stream_id__in=allowed)) (tasks.py) had no
        fixture that gave it a non-empty candidate: every existing task
        fixture used a manual card with no stream_id. A card from a
        public channel hamlet can read must count; a card from a private
        channel hamlet cannot read must not."""
        iago = self.example_user("iago")
        board = get_or_create_default_board(self.owner.realm)
        columns = list(board.columns.all())
        public_stream = Stream.objects.get(realm=self.owner.realm, name="Denmark")
        private_stream = self.make_stream(
            "work-private", invite_only=True, history_public_to_subscribers=False
        )
        self.subscribe(iago, private_stream.name)

        do_create_task(
            user_profile=iago,
            board=board,
            column=columns[0],
            title="From a public channel",
            stream_id=public_stream.id,
        )
        do_create_task(
            user_profile=iago,
            board=board,
            column=columns[0],
            title="From a private channel hamlet cannot read",
            stream_id=private_stream.id,
        )

        self.assertEqual(work_counts(self.owner)["task_board"], 1)
