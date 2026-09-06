"""Real PostgreSQL regression for public cross-family idempotency races."""

from __future__ import annotations

import threading
import unittest
import unittest.mock as mock
import uuid
from decimal import Decimal
from types import SimpleNamespace

import psycopg

from accounting_information_platform import (
    IdempotencyConflictError,
    PostgresPostingLedger,
    accept_reconciliation_run,
    reconcile_reconciliation_run,
)
from accounting_information_platform import reconciliation_close_package as close_package
from tests import test_postgres_posting as posting
from tests.test_reconciliation_run_api import ReconciliationRunApiTests


def _bridge(run_id: str) -> SimpleNamespace:
    """Return a deterministic exact bridge for the lifecycle race fixture."""
    return SimpleNamespace(
        reconciliation_run_reference=run_id,
        statement_population_reference="sha256:" + "1" * 64,
        book_population_reference="sha256:" + "2" * 64,
        currency_code="KRW",
        statement_opening_balance=Decimal("100000.00"),
        statement_period_movements=Decimal("15000.00"),
        statement_closing_balance=Decimal("115000.00"),
        book_opening_balance=Decimal("100000.00"),
        posted_cash_book_movements=Decimal("0.00"),
        book_closing_balance=Decimal("100000.00"),
        reconciled_book_balance=Decimal("100000.00"),
        outstanding_bank_items=Decimal("0.00"),
        outstanding_book_items=Decimal("15000.00"),
        unexplained_difference=Decimal("0.00"),
        status_code="reconciled",
    )


class ReconciliationCommandIdentityPublicRacePostgresTests(unittest.TestCase):
    """Require public commands to normalize a database-decided identity race."""

    @classmethod
    def setUpClass(cls) -> None:
        """Install the complete migration chain in real PostgreSQL."""
        posting.PostgresPostingTests.setUpClass()

    def setUp(self) -> None:
        """Open the lifecycle target and prepare independent bank evidence."""
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

    def _tenant_id(self) -> object:
        """Resolve the internal tenant identity for the opened aggregate."""
        with psycopg.connect(posting.DATABASE_URL) as connection:
            return connection.execute(
                """
                SELECT tenant_account_id
                FROM accounting_core.reconciliation_run
                WHERE reconciliation_run_id = %s
                """,
                (self.opened["reconciliation_run_id"],),
            ).fetchone()[0]

    def test_public_commands_normalize_concurrent_cross_family_key_conflict(self) -> None:
        """Exactly one public command wins while the loser returns a domain conflict."""
        key = f"public-cross-family-{uuid.uuid4().hex}"
        _statement, opening_command = self.fixture._statement_and_command()
        opening_command["reconciliation_idempotency_key"] = key
        lifecycle_command = {
            "tenant_reference": self.fixture.case.policy.tenant_reference,
            "reconciliation_action_code": "reconcile",
            "reconciliation_run_id": self.opened["reconciliation_run_id"],
            "reconciliation_idempotency_key": key,
            "actor_reference": "urn:cwl:principal:cross_family_race_controller",
            "purpose_code": "month_end_reconciliation",
            "effective_at": "2026-09-02T00:00:00Z",
        }
        barrier = threading.Barrier(2)
        failures: list[BaseException] = []
        outcomes: list[str] = []
        original_lock = PostgresPostingLedger._acquire_command_lock
        synchronized_scopes = {
            f"reconciliation_run_key:{key}",
            f"reconciliation_run_transition_key:{key}",
        }

        def synchronized_lock(
            ledger: PostgresPostingLedger,
            connection: object,
            command_scope: str,
        ) -> None:
            """Hold both distinct preflight locks until both snapshots are ready."""
            original_lock(ledger, connection, command_scope)
            if command_scope in synchronized_scopes:
                barrier.wait(timeout=5)

        def execute(name: str, command_call: object) -> None:
            """Record only public domain outcomes; provider errors are test failures."""
            try:
                command_call()
                outcomes.append(f"{name}:success")
            except IdempotencyConflictError:
                outcomes.append(f"{name}:conflict")
            except BaseException as error:  # captured for the main test thread
                failures.append(error)

        opening = threading.Thread(
            target=execute,
            args=(
                "opening",
                lambda: accept_reconciliation_run(
                    opening_command,
                    posting.DATABASE_URL,
                    self.fixture.case.policy.tenant_reference,
                ),
            ),
            name="public-reconciliation-opening",
        )
        lifecycle = threading.Thread(
            target=execute,
            args=(
                "lifecycle",
                lambda: reconcile_reconciliation_run(
                    lifecycle_command,
                    posting.DATABASE_URL,
                    self.fixture.case.policy.tenant_reference,
                ),
            ),
            name="public-reconciliation-lifecycle",
        )

        with mock.patch.object(
            PostgresPostingLedger,
            "_acquire_command_lock",
            synchronized_lock,
        ), mock.patch.object(
            close_package,
            "_database_owned_close_projection_evidence",
            return_value=_bridge(str(self.opened["reconciliation_run_id"])),
        ):
            opening.start()
            lifecycle.start()
            opening.join(timeout=15)
            lifecycle.join(timeout=15)

        self.assertFalse(opening.is_alive())
        self.assertFalse(lifecycle.is_alive())
        self.assertEqual(failures, [])
        self.assertEqual(len(outcomes), 2)
        self.assertEqual(sum(item.endswith(":success") for item in outcomes), 1)
        self.assertEqual(sum(item.endswith(":conflict") for item in outcomes), 1)

        with psycopg.connect(posting.DATABASE_URL) as connection:
            identities = connection.execute(
                """
                SELECT command_family_code
                FROM accounting_core.reconciliation_command_identity
                WHERE tenant_account_id = %s
                  AND reconciliation_command_identity_key = %s
                """,
                (self._tenant_id(), key),
            ).fetchall()
        self.assertEqual(len(identities), 1)


if __name__ == "__main__":
    unittest.main()
