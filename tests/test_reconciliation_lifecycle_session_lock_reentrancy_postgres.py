"""Real PostgreSQL acceptance for lifecycle session-lock acquisition idempotency."""

from __future__ import annotations

import unittest

import psycopg

from accounting_information_platform import accept_reconciliation_run
from tests import test_postgres_posting as posting
from tests.test_reconciliation_run_api import ReconciliationRunApiTests


class ReconciliationLifecycleSessionLockReentrancyPostgresTests(unittest.TestCase):
    """Prevent one backend from stacking hidden lifecycle session-lock holds."""

    @classmethod
    def setUpClass(cls) -> None:
        """Install the complete shared PostgreSQL migration chain."""
        posting.PostgresPostingTests.setUpClass()

    def setUp(self) -> None:
        """Open one evaluating reconciliation run for the lock scope under test."""
        self.fixture = ReconciliationRunApiTests(
            "test_open_run_binds_statement_scope_and_replays"
        )
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.addCleanup(self.fixture.tearDown)
        _statement, command = self.fixture._statement_and_command()
        self.opened = accept_reconciliation_run(
            command,
            posting.DATABASE_URL,
            self.fixture.case.policy.tenant_reference,
        )

    def test_repeated_acquire_requires_only_one_release(self) -> None:
        """Repeated owner acquisition must not increment the PostgreSQL lock count."""
        tenant_reference = self.fixture.case.policy.tenant_reference
        run_id = self.opened["reconciliation_run_id"]
        lifecycle_scope = f"reconciliation_run_lifecycle:{run_id}"

        with psycopg.connect(posting.DATABASE_URL) as owner:
            try:
                owner.execute(
                    "SELECT accounting_core.acquire_reconciliation_lifecycle_session(%s, %s)",
                    (tenant_reference, run_id),
                )
                owner.commit()

                # A retry/nested caller on the same backend must reuse the active
                # lease instead of issuing another pg_advisory_lock request. Session
                # advisory locks stack, so two acquisitions followed by one release
                # would otherwise leave an invisible hold until connection teardown.
                owner.execute(
                    "SELECT accounting_core.acquire_reconciliation_lifecycle_session(%s, %s)",
                    (tenant_reference, run_id),
                )
                owner.commit()

                released = owner.execute(
                    "SELECT accounting_core.release_reconciliation_lifecycle_session(%s, %s)",
                    (tenant_reference, run_id),
                ).fetchone()[0]
                owner.commit()
                self.assertTrue(released)

                with psycopg.connect(posting.DATABASE_URL, autocommit=True) as contender:
                    acquired = contender.execute(
                        "SELECT pg_try_advisory_lock(hashtext(%s), hashtext(%s))",
                        (tenant_reference, lifecycle_scope),
                    ).fetchone()[0]
                    try:
                        self.assertTrue(
                            acquired,
                            "one release left a stacked lifecycle session lock on the owner backend",
                        )
                    finally:
                        if acquired:
                            contender.execute(
                                "SELECT pg_advisory_unlock(hashtext(%s), hashtext(%s))",
                                (tenant_reference, lifecycle_scope),
                            )
            finally:
                # RED cleanup must never leave the shared PostgreSQL fixture blocked.
                owner.rollback()
                owner.execute("SELECT pg_advisory_unlock_all()")
                owner.commit()

    def test_release_drains_same_backend_raw_duplicate_hold(self) -> None:
        """Canonical release must not leave a raw duplicate hold on the same key."""
        tenant_reference = self.fixture.case.policy.tenant_reference
        run_id = self.opened["reconciliation_run_id"]
        lifecycle_scope = f"reconciliation_run_lifecycle:{run_id}"

        with psycopg.connect(posting.DATABASE_URL) as owner:
            try:
                owner.execute(
                    "SELECT accounting_core.acquire_reconciliation_lifecycle_session(%s, %s)",
                    (tenant_reference, run_id),
                )
                owner.commit()

                # PostgreSQL advisory locks are session-reentrant independently of
                # the canonical helper. A direct duplicate acquisition on the same
                # backend must not survive the owner-controlled release boundary.
                owner.execute(
                    "SELECT pg_advisory_lock(hashtext(%s), hashtext(%s))",
                    (tenant_reference, lifecycle_scope),
                )
                owner.commit()

                released = owner.execute(
                    "SELECT accounting_core.release_reconciliation_lifecycle_session(%s, %s)",
                    (tenant_reference, run_id),
                ).fetchone()[0]
                owner.commit()
                self.assertTrue(released)

                with psycopg.connect(posting.DATABASE_URL, autocommit=True) as contender:
                    acquired = contender.execute(
                        "SELECT pg_try_advisory_lock(hashtext(%s), hashtext(%s))",
                        (tenant_reference, lifecycle_scope),
                    ).fetchone()[0]
                    try:
                        self.assertTrue(
                            acquired,
                            "canonical release left a raw duplicate lifecycle session hold",
                        )
                    finally:
                        if acquired:
                            contender.execute(
                                "SELECT pg_advisory_unlock(hashtext(%s), hashtext(%s))",
                                (tenant_reference, lifecycle_scope),
                            )
            finally:
                owner.rollback()
                owner.execute("SELECT pg_advisory_unlock_all()")
                owner.commit()


if __name__ == "__main__":
    unittest.main()
