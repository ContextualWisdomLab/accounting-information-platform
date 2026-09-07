"""PostgreSQL regressions for reconciliation-run command provenance."""

from __future__ import annotations

import unittest
import uuid

import psycopg

from tests import test_postgres_posting as posting
from tests import test_reconciliation_run_api as reconciliation_run_api


class ReconciliationRunCommandProvenanceTests(unittest.TestCase):
    """Prove command evidence is validated even when it is attached after a run row."""

    @classmethod
    def setUpClass(cls) -> None:
        reconciliation_run_api.ReconciliationRunApiTests.setUpClass()

    def setUp(self) -> None:
        self.helper = reconciliation_run_api.ReconciliationRunApiTests(
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

    def test_database_owns_persisted_command_digest(self) -> None:
        """Direct SQL may not persist a caller-selected format-valid command hash."""
        statement, command = self.helper._statement_and_command()
        scope = self.helper._assignment_scope()
        assert scope is not None
        connection = psycopg.connect(posting.DATABASE_URL)
        self.addCleanup(connection.close)
        run_id = self._insert_run(connection, scope, command)
        supplied_digest = "sha256:" + "1" * 64
        self._insert_command(
            connection,
            scope[0],
            run_id,
            statement["bank_statement_record_id"],
            command["source_payload_hash"],
        )
        connection.commit()

        persisted_digest = connection.execute(
            """
            SELECT reconciliation_command_hash
            FROM accounting_core.reconciliation_run_command
            WHERE tenant_account_id = %s
              AND reconciliation_run_id = %s
            """,
            (scope[0], run_id),
        ).fetchone()[0]
        self.assertRegex(persisted_digest, r"^sha256:[0-9a-f]{64}$")
        self.assertNotEqual(persisted_digest, supplied_digest)

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
            # Reconstruct the pre-0019 catalog inside this transaction instead
            # of making the production migration silently idempotent. Later
            # migrations may have added dependencies to these objects, so the
            # rollback-only fixture removes the complete 0019-owned table and
            # trigger surface before replaying the canonical migration body.
            connection.execute(
                """
                DROP TRIGGER IF EXISTS reconciliation_run_command_provenance_guard
                ON accounting_core.reconciliation_run
                """
            )
            connection.execute(
                """
                DROP TRIGGER IF EXISTS accounting_reconciliation_run_transition_guard
                ON accounting_core.reconciliation_run
                """
            )
            for table_name, trigger_name in (
                (
                    "reconciliation_candidate",
                    "accounting_reconciliation_lifecycle_candidate_guard",
                ),
                (
                    "reconciliation_match",
                    "accounting_reconciliation_lifecycle_match_guard",
                ),
                (
                    "statement_match_allocation",
                    "accounting_reconciliation_lifecycle_statement_allocation_guard",
                ),
                (
                    "journal_match_allocation",
                    "accounting_reconciliation_lifecycle_journal_allocation_guard",
                ),
                (
                    "reconciliation_approval",
                    "accounting_reconciliation_lifecycle_approval_guard",
                ),
                (
                    "reconciliation_exception",
                    "accounting_reconciliation_lifecycle_exception_guard",
                ),
            ):
                connection.execute(
                    f"DROP TRIGGER IF EXISTS {trigger_name} "
                    f"ON accounting_core.{table_name}"
                )
            connection.execute(
                "DROP TABLE accounting_core.reconciliation_run_transition_command CASCADE"
            )
            connection.execute(
                "DROP TABLE accounting_core.reconciliation_run_command CASCADE"
            )
            connection.execute(
                "DROP TABLE accounting_core.reconciliation_command_identity CASCADE"
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
