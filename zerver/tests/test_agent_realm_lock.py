"""One workspace's agent writes must not block another workspace's.

agent_transaction() keys its advisory lock off the current realm instead of
one fixed global key. These tests exercise the lock directly with two real
Postgres connections.
"""

from concurrent.futures import ThreadPoolExecutor
from threading import Event

from django.db import connection, connections

from zerver.lib.agent_context import AgentBusy, agent_realm, agent_transaction
from zerver.lib.test_classes import ZulipTransactionTestCase


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
