"""Real PostgreSQL RED for exception-resolution command/status/outbox atomicity."""

from __future__ import annotations

import unittest
from datetime import datetime, timezone

import psycopg

from accounting_information_platform import reconciliation_exception_resolution as resolution
from tests import test_postgres_posting as posting
from tests.test_reconciliation_exception_resolution_postgres import (
    ReconciliationExceptionResolutionPostgresTests,
    _EVIDENCE_HASH,
)


class ReconciliationExceptionResolutionOutboxAtomicityRedTests(unittest.TestCase):
    """Require the database to reject command/status commits without their event."""

    @classmethod
    def setUpClass(cls) -> None:
        """Install the same complete PostgreSQL migration fixture as the command suite."""
        ReconciliationExceptionResolutionPostgresTests.setUpClass()

    def setUp(self) -> None:
        """Create one evaluating run, open exception, and retained review artifact."""
        self.case = ReconciliationExceptionResolutionPostgresTests(
            "test_named_command_resolves_exception_and_emits_atomic_outbox"
        )
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.addCleanup(self.case.tearDown)

    def test_command_and_terminal_status_without_outbox_fail_at_deferred_boundary(self) -> None:
        """Direct SQL cannot commit authority while omitting the matching outbox event."""
        command = self.case._command(
            reconciliation_idempotency_key=f"missing-outbox-{self.case.exception_id}"
        )
        source_payload_hash = resolution._source_payload_hash(command)
        with psycopg.connect(posting.DATABASE_URL) as connection:
            tenant_id = self.case._tenant_id(connection)
            connection.execute(
                """
                INSERT INTO accounting_core.reconciliation_exception_resolution_command (
                    tenant_account_id,
                    reconciliation_run_id,
                    reconciliation_exception_id,
                    reconciliation_resolution_idempotency_key,
                    target_resolution_status_code,
                    resolution_evidence_reference,
                    resolution_evidence_hash,
                    source_payload_hash,
                    reconciliation_exception_resolution_command_hash,
                    actor_reference,
                    purpose_code,
                    effective_at
                )
                VALUES (%s, %s, %s, %s, 'resolved', %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    tenant_id,
                    self.case.opened["reconciliation_run_id"],
                    self.case.exception_id,
                    command["reconciliation_idempotency_key"],
                    self.case.evidence_reference,
                    _EVIDENCE_HASH,
                    source_payload_hash,
                    "sha256:" + "0" * 64,
                    "urn:cwl:principal:independent_reviewer",
                    "bank_reconciliation_exception_review",
                    datetime(2026, 9, 2, 0, 20, tzinfo=timezone.utc),
                ),
            )
            connection.execute(
                """
                UPDATE accounting_core.reconciliation_exception
                SET resolution_status_code = 'resolved'
                WHERE tenant_account_id = %s
                  AND reconciliation_exception_id = %s
                """,
                (tenant_id, self.case.exception_id),
            )
            try:
                connection.execute(
                    "SET CONSTRAINTS reconciliation_exception_resolution_status_pair_guard IMMEDIATE"
                )
            except psycopg.Error as error:
                connection.rollback()
                self.assertIn("reconciliation_exception_resolution_atomic_pair", str(error))
            else:
                connection.rollback()
                self.fail(
                    "database accepted exception command/status authority without the matching outbox event"
                )

        self.case._assert_no_resolution_side_effects()


if __name__ == "__main__":
    unittest.main()
