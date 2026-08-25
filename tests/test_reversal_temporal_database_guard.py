"""PostgreSQL regression for reversal temporal ordering."""

from __future__ import annotations

import unittest
import uuid
from datetime import date

import psycopg

import tests.test_postgres_posting as postgres_posting


class ReversalTemporalDatabaseGuardTests(unittest.TestCase):
    """A reversal relation cannot make a correction effective before its source fact."""

    @classmethod
    def setUpClass(cls) -> None:
        """Install the checked-in foundation on the real PostgreSQL test database."""
        postgres_posting.PostgresPostingTests.setUpClass()

    def setUp(self) -> None:
        """Seed an isolated tenant using the canonical PostgreSQL fixture."""
        self.case = postgres_posting.PostgresPostingTests("setUp")
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)

    def test_database_rejects_reversal_journal_dated_before_original(self) -> None:
        """Direct SQL cannot link an earlier posted journal as a later fact's reversal."""
        original = self.case.ledger.post(
            self.case._two_line_proposal(
                proposal_id=str(uuid.uuid4()),
                idempotency_key=f"reversal-temporal-original-{uuid.uuid4().hex}",
                source_payload_hash="sha256:" + "8" * 64,
                transaction_date=date(2026, 8, 20),
                accounting_date=date(2026, 8, 20),
            ),
            self.case.policy,
        )
        earlier_candidate = self.case.ledger.post(
            self.case._two_line_proposal(
                proposal_id=str(uuid.uuid4()),
                idempotency_key=f"reversal-temporal-earlier-{uuid.uuid4().hex}",
                source_payload_hash="sha256:" + "9" * 64,
                transaction_date=date(2026, 8, 19),
                accounting_date=date(2026, 8, 19),
            ),
            self.case.policy,
        )

        with psycopg.connect(postgres_posting.DATABASE_URL) as connection:
            connection.execute(
                "SELECT set_config('app.tenant_account_id', %s, true)",
                (str(self.case.tenant_id),),
            )
            rows = connection.execute(
                """
                SELECT journal_reference, general_journal_id
                FROM accounting_core.general_journal
                WHERE tenant_account_id = %s
                  AND journal_reference = ANY(%s)
                """,
                (
                    self.case.tenant_id,
                    [original.journal_reference, earlier_candidate.journal_reference],
                ),
            ).fetchall()
            journal_ids = {str(reference): journal_id for reference, journal_id in rows}

            with self.assertRaisesRegex(
                psycopg.errors.CheckViolation,
                "reversal accounting date cannot precede original accounting date",
            ):
                connection.execute(
                    """
                    INSERT INTO accounting_core.journal_reversal (
                        tenant_account_id,
                        original_journal_id,
                        reversal_journal_id,
                        reversal_reason_code
                    )
                    VALUES (%s, %s, %s, 'temporal_regression')
                    """,
                    (
                        self.case.tenant_id,
                        journal_ids[original.journal_reference],
                        journal_ids[earlier_candidate.journal_reference],
                    ),
                )

        self.assertEqual(
            self.case._count_table("accounting_core.journal_reversal"),
            0,
        )


if __name__ == "__main__":
    unittest.main()
