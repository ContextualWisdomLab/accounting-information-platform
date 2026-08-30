"""PostgreSQL regressions for reconciliation-run command provenance."""

from __future__ import annotations

import unittest
import uuid

import psycopg

from tests import test_postgres_posting as posting
from tests.test_reconciliation_run_api import ReconciliationRunApiTests


class ReconciliationRunCommandProvenanceTests(unittest.TestCase):
    """Prove command evidence is validated even when it is attached after a run row."""

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

    @staticmethod
    def _insert_run(
        connection: psycopg.Connection[object],
        scope: tuple[object, ...],
        command: dict[str, object],
    ) -> object:
        """Insert one evaluating run directly and return its database identity."""
        return connection.execute(
            """
            INSERT INTO accounting_core.reconciliation_run (
                tenant_account_id, legal_entity_id, accounting_book_id,
                bank_account_assignment_id, currency_code, bank_cutoff_at,
                book_cutoff_at, matching_policy_version, knowledge_cutoff_at,
                run_status_code
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'evaluating')
            RETURNING reconciliation_run_id
            """,
            (
                scope[0],
                scope[1],
                scope[2],
                scope[3],
                scope[4],
                command["bank_cutoff_at"],
                command["book_cutoff_at"],
                command["matching_policy_version"],
                command["knowledge_cutoff_at"],
            ),
        ).fetchone()[0]

    @staticmethod
    def _insert_command(
        connection: psycopg.Connection[object],
        tenant_account_id: object,
        run_id: object,
        statement_id: object,
        source_payload_hash: str,
        source_payload_reference: str | None = None,
    ) -> None:
        """Attach one direct-SQL command row to an existing reconciliation run."""
        connection.execute(
            """
            INSERT INTO accounting_core.reconciliation_run_command (
                tenant_account_id, reconciliation_run_id,
                bank_statement_record_id, reconciliation_idempotency_key,
                reconciliation_command_hash, source_payload_hash,
                source_payload_reference
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            (
                tenant_account_id,
                run_id,
                statement_id,
                f"direct-provenance-{uuid.uuid4().hex}",
                "sha256:" + "1" * 64,
                source_payload_hash,
                source_payload_reference or f"memory:{source_payload_hash}",
            ),
        )

    def test_database_rejects_false_command_artifact_reference(self) -> None:
        """A command cannot publish an artifact URI different from retained evidence."""
        statement, command = self.helper._statement_and_command()
        scope = self.helper._assignment_scope()
        assert scope is not None
        connection = psycopg.connect(posting.DATABASE_URL)
        self.addCleanup(connection.close)
        run_id = self._insert_run(connection, scope, command)
        self._insert_command(
            connection,
            scope[0],
            run_id,
            statement["bank_statement_record_id"],
            command["source_payload_hash"],
            source_payload_reference="memory:sha256:" + "9" * 64,
        )
        with self.assertRaisesRegex(psycopg.Error, "source payload hash"):
            connection.commit()
        connection.rollback()

    def test_database_rejects_false_command_source_hash(self) -> None:
        """A command cannot claim source bytes different from its referenced statement."""
        statement, command = self.helper._statement_and_command()
        scope = self.helper._assignment_scope()
        assert scope is not None
        connection = psycopg.connect(posting.DATABASE_URL)
        self.addCleanup(connection.close)
        run_id = self._insert_run(connection, scope, command)
        self._insert_command(
            connection,
            scope[0],
            run_id,
            statement["bank_statement_record_id"],
            "sha256:" + "0" * 64,
        )
        with self.assertRaisesRegex(psycopg.Error, "source payload hash"):
            connection.commit()
        connection.rollback()

    def test_command_insert_validates_a_preexisting_run(self) -> None:
        """A command inserted later must validate provenance for a legacy run row."""
        statement, command = self.helper._statement_and_command()
        scope = self.helper._assignment_scope()
        assert scope is not None
        connection = psycopg.connect(posting.DATABASE_URL)
        self.addCleanup(connection.close)

        # Simulate a run row that predates migration 0019 without leaving the
        # production trigger disabled after this transaction.
        connection.execute(
            """
            ALTER TABLE accounting_core.reconciliation_run
            DISABLE TRIGGER reconciliation_run_command_provenance_guard
            """
        )
        run_id = self._insert_run(connection, scope, command)
        connection.execute(
            """
            ALTER TABLE accounting_core.reconciliation_run
            ENABLE TRIGGER reconciliation_run_command_provenance_guard
            """
        )
        connection.commit()

        self._insert_command(
            connection,
            scope[0],
            run_id,
            statement["bank_statement_record_id"],
            "sha256:" + "0" * 64,
        )
        with self.assertRaisesRegex(psycopg.Error, "source payload hash"):
            connection.commit()
        connection.rollback()

    def test_migration_rejects_a_commandless_preexisting_run(self) -> None:
        """Upgrade may not hide a historical run that lacks reconstructable command evidence."""
        _statement, command = self.helper._statement_and_command()
        scope = self.helper._assignment_scope()
        assert scope is not None
        migration_sql = (
            posting.ROOT / "database/migrations/0019_reconciliation_run_command_evidence.sql"
        ).read_text(encoding="utf-8")
        self.assertTrue(migration_sql.startswith("BEGIN;\n"))
        self.assertTrue(migration_sql.rstrip().endswith("COMMIT;"))
        migration_body = migration_sql.removeprefix("BEGIN;\n").rsplit("\nCOMMIT;", 1)[0]

        with psycopg.connect(posting.DATABASE_URL) as connection:
            # Transactionally reconstruct the pre-0019 schema boundary. The
            # context manager rolls the catalog back if this RED assertion
            # fails, so no shared fixture state escapes the test.
            connection.execute(
                """
                DROP TRIGGER reconciliation_run_command_provenance_guard
                ON accounting_core.reconciliation_run
                """
            )
            connection.execute(
                "DROP TABLE accounting_core.reconciliation_run_command CASCADE"
            )
            self._insert_run(connection, scope, command)

            with self.assertRaisesRegex(
                psycopg.errors.CheckViolation,
                "reconciliation_run_command_upgrade_required",
            ):
                connection.execute(migration_body)
            connection.rollback()


if __name__ == "__main__":
    unittest.main()