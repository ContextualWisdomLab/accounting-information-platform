"""Database-owned artifact provenance regressions for reconciliation commands."""

from __future__ import annotations

import unittest

import psycopg

from tests import test_postgres_posting as posting
from tests.test_reconciliation_run_api import ReconciliationRunApiTests
from tests.test_reconciliation_run_command_provenance import (
    ReconciliationRunCommandProvenanceTests,
)


class ReconciliationRunArtifactProvenanceTests(unittest.TestCase):
    """Prove copied statement hashes cannot replace the retained artifact identity."""

    @classmethod
    def setUpClass(cls) -> None:
        ReconciliationRunApiTests.setUpClass()

    def setUp(self) -> None:
        self.helper = ReconciliationRunApiTests(
            "test_open_run_binds_statement_scope_and_replays"
        )
        self.helper.setUp()
        self.addCleanup(self.helper.doCleanups)
        self.addCleanup(self.helper.tearDown)

    def test_command_rejects_statement_hash_drift_from_artifact(self) -> None:
        """Command provenance follows the artifact row, not a corrupted copied hash."""
        statement, command = self.helper._statement_and_command()
        scope = self.helper._assignment_scope()
        assert scope is not None
        false_hash = "sha256:" + "0" * 64

        with psycopg.connect(posting.DATABASE_URL) as connection:
            # Simulate storage corruption while holding an ACCESS EXCLUSIVE lock;
            # the immutable trigger is restored before the transaction commits.
            connection.execute(
                """
                ALTER TABLE accounting_integration.bank_statement_record
                DISABLE TRIGGER bank_statement_record_immutable_guard
                """
            )
            connection.execute(
                """
                UPDATE accounting_integration.bank_statement_record
                SET source_artifact_hash = %s
                WHERE bank_statement_record_id = %s
                """,
                (false_hash, statement["bank_statement_record_id"]),
            )
            connection.execute(
                """
                ALTER TABLE accounting_integration.bank_statement_record
                ENABLE TRIGGER bank_statement_record_immutable_guard
                """
            )
            connection.commit()

            run_id = ReconciliationRunCommandProvenanceTests._insert_run(
                connection, scope, command
            )
            ReconciliationRunCommandProvenanceTests._insert_command(
                connection,
                scope[0],
                run_id,
                statement["bank_statement_record_id"],
                false_hash,
            )
            with self.assertRaisesRegex(psycopg.Error, "source payload hash"):
                connection.commit()
            connection.rollback()


if __name__ == "__main__":
    unittest.main()
