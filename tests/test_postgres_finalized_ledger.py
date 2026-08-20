"""Real PostgreSQL regressions for finalized accounting-fact immutability."""

from __future__ import annotations

import unittest

import psycopg

from tests import test_postgres_posting as posting


class PostgresFinalizedLedgerTests(unittest.TestCase):
    """Prove issued accounting evidence cannot be rewritten or extended in place."""

    @classmethod
    def setUpClass(cls) -> None:
        posting.PostgresPostingTests.setUpClass()

    def setUp(self) -> None:
        self.case = posting.PostgresPostingTests("setUp")
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.addCleanup(self.case.tearDown)

    def test_finalized_posting_facts_reject_update_and_delete(self) -> None:
        """Journal, source, reversal, receipt, and proposal evidence are append-only."""
        receipt = self.case.ledger.post(self.case._two_line_proposal(), self.case.policy)
        self.case.ledger.reverse(
            receipt.journal_reference,
            self.case.policy.open_period_end,
            "billing_correction",
            self.case.policy,
        )
        tenant_id = self.case.tenant_id
        journal_id, proposal_id, line_id, source_id, receipt_id, reversal_id = (
            self._finalized_fact_ids(receipt.journal_reference)
        )
        mutations = (
            (
                "UPDATE accounting_core.general_journal SET posting_rule_version = 'tampered' "
                "WHERE tenant_account_id = %s AND general_journal_id = %s",
                (tenant_id, journal_id),
            ),
            (
                "DELETE FROM accounting_core.general_journal "
                "WHERE tenant_account_id = %s AND general_journal_id = %s",
                (tenant_id, journal_id),
            ),
            (
                "UPDATE accounting_core.journal_entry_line SET line_description = 'tampered' "
                "WHERE tenant_account_id = %s AND journal_entry_line_id = %s",
                (tenant_id, line_id),
            ),
            (
                "DELETE FROM accounting_core.journal_entry_line "
                "WHERE tenant_account_id = %s AND journal_entry_line_id = %s",
                (tenant_id, line_id),
            ),
            (
                "UPDATE accounting_core.journal_source_reference "
                "SET source_reference = source_reference || ':tampered' "
                "WHERE tenant_account_id = %s AND journal_source_reference_id = %s",
                (tenant_id, source_id),
            ),
            (
                "DELETE FROM accounting_core.journal_source_reference "
                "WHERE tenant_account_id = %s AND journal_source_reference_id = %s",
                (tenant_id, source_id),
            ),
            (
                "UPDATE accounting_core.journal_reversal SET reversal_reason_code = 'tampered' "
                "WHERE tenant_account_id = %s AND journal_reversal_id = %s",
                (tenant_id, reversal_id),
            ),
            (
                "DELETE FROM accounting_core.journal_reversal "
                "WHERE tenant_account_id = %s AND journal_reversal_id = %s",
                (tenant_id, reversal_id),
            ),
            (
                "UPDATE accounting_integration.posting_receipt "
                "SET receipt_payload_hash = %s "
                "WHERE tenant_account_id = %s AND posting_receipt_id = %s",
                ("sha256:" + "e" * 64, tenant_id, receipt_id),
            ),
            (
                "DELETE FROM accounting_integration.posting_receipt "
                "WHERE tenant_account_id = %s AND posting_receipt_id = %s",
                (tenant_id, receipt_id),
            ),
            (
                "UPDATE accounting_integration.journal_proposal_record "
                "SET source_payload_hash = %s "
                "WHERE tenant_account_id = %s AND proposal_record_id = %s",
                ("sha256:" + "f" * 64, tenant_id, proposal_id),
            ),
            (
                "DELETE FROM accounting_integration.journal_proposal_record "
                "WHERE tenant_account_id = %s AND proposal_record_id = %s",
                (tenant_id, proposal_id),
            ),
        )
        for statement, parameters in mutations:
            with self.subTest(statement=statement.split()[0:3]):
                self._assert_check_violation(statement, parameters)

    def test_finalized_journal_rejects_late_line_and_source_reference(self) -> None:
        """A receipt freezes the journal line and source-reference populations."""
        receipt = self.case.ledger.post(self.case._two_line_proposal(), self.case.policy)
        tenant_id = self.case.tenant_id
        with psycopg.connect(posting.DATABASE_URL) as connection:
            connection.execute(
                "SELECT set_config('app.tenant_account_id', %s, false)",
                (tenant_id,),
            )
            journal_id = connection.execute(
                """
                SELECT general_journal_id
                FROM accounting_core.general_journal
                WHERE tenant_account_id = %s AND journal_reference = %s
                """,
                (tenant_id, receipt.journal_reference),
            ).fetchone()[0]
            chart_account_id = connection.execute(
                """
                SELECT chart_account_id
                FROM accounting_core.journal_entry_line
                WHERE tenant_account_id = %s AND general_journal_id = %s
                ORDER BY line_number
                LIMIT 1
                """,
                (tenant_id, journal_id),
            ).fetchone()[0]

        self._assert_check_violation(
            """
            INSERT INTO accounting_core.journal_entry_line (
                tenant_account_id, general_journal_id, line_number, chart_account_id,
                account_role_code, debit_amount, credit_amount
            ) VALUES (%s, %s, 99, %s, 'accounts_receivable', 1, 0)
            """,
            (tenant_id, journal_id, chart_account_id),
        )
        self._assert_check_violation(
            """
            INSERT INTO accounting_core.journal_source_reference (
                tenant_account_id, general_journal_id, source_reference, source_payload_hash
            ) VALUES (%s, %s, %s, %s)
            """,
            (
                tenant_id,
                journal_id,
                "urn:cwl:evidence:late-source",
                "sha256:" + "a" * 64,
            ),
        )

    def _finalized_fact_ids(self, journal_reference: str) -> tuple[object, ...]:
        """Return durable identifiers for one posted-and-reversed journal population."""
        with psycopg.connect(posting.DATABASE_URL) as connection:
            connection.execute(
                "SELECT set_config('app.tenant_account_id', %s, false)",
                (self.case.tenant_id,),
            )
            row = connection.execute(
                """
                SELECT general_journal.general_journal_id,
                       general_journal.source_proposal_record_id,
                       journal_entry_line.journal_entry_line_id,
                       journal_source_reference.journal_source_reference_id,
                       posting_receipt.posting_receipt_id,
                       journal_reversal.journal_reversal_id
                FROM accounting_core.general_journal
                JOIN accounting_core.journal_entry_line
                  ON journal_entry_line.tenant_account_id = general_journal.tenant_account_id
                 AND journal_entry_line.general_journal_id = general_journal.general_journal_id
                 AND journal_entry_line.line_number = 1
                JOIN accounting_core.journal_source_reference
                  ON journal_source_reference.tenant_account_id = general_journal.tenant_account_id
                 AND journal_source_reference.general_journal_id = general_journal.general_journal_id
                JOIN accounting_integration.posting_receipt
                  ON posting_receipt.tenant_account_id = general_journal.tenant_account_id
                 AND posting_receipt.general_journal_id = general_journal.general_journal_id
                JOIN accounting_core.journal_reversal
                  ON journal_reversal.tenant_account_id = general_journal.tenant_account_id
                 AND journal_reversal.original_journal_id = general_journal.general_journal_id
                WHERE general_journal.tenant_account_id = %s
                  AND general_journal.journal_reference = %s
                ORDER BY journal_source_reference.journal_source_reference_id
                LIMIT 1
                """,
                (self.case.tenant_id, journal_reference),
            ).fetchone()
        assert row is not None
        return tuple(row)

    def _assert_check_violation(
        self, statement: str, parameters: tuple[object, ...]
    ) -> None:
        """Execute one direct mutation and require the database immutability guard."""
        with psycopg.connect(posting.DATABASE_URL) as connection:
            connection.execute(
                "SELECT set_config('app.tenant_account_id', %s, false)",
                (self.case.tenant_id,),
            )
            with self.assertRaises(psycopg.errors.CheckViolation):
                connection.execute(statement, parameters)


if __name__ == "__main__":
    unittest.main()
