"""PostgreSQL regressions for exact reversal-command replay and conflict detection."""

from __future__ import annotations

import unittest
import uuid
from datetime import date

import psycopg

from accounting_information_platform import IdempotencyConflictError
import tests.test_postgres_posting as postgres_posting


class ReversalCommandIdempotencyPostgresTests(unittest.TestCase):
    """Durable reversal commands replay only for one immutable command identity."""

    @classmethod
    def setUpClass(cls) -> None:
        """Install the checked-in foundation on the real PostgreSQL test database."""
        postgres_posting.PostgresPostingTests.setUpClass()

    def setUp(self) -> None:
        """Seed an isolated tenant using the canonical PostgreSQL fixture."""
        self.case = postgres_posting.PostgresPostingTests("setUp")
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)

    def test_exact_reversal_command_replays_and_changed_payload_conflicts(self) -> None:
        """Same key/date/reason returns one receipt; changed evidence cannot replay it."""
        original = self.case.ledger.post(
            self.case._two_line_proposal(
                proposal_id=str(uuid.uuid4()),
                idempotency_key=f"reversal-command-original-{uuid.uuid4().hex}",
                source_payload_hash="sha256:" + "a" * 64,
                transaction_date=date(2026, 8, 20),
                accounting_date=date(2026, 8, 20),
            ),
            self.case.policy,
        )
        command_key = f"reversal-command-{uuid.uuid4().hex}"

        first = self.case.ledger.reverse(
            original.journal_reference,
            date(2026, 8, 21),
            "billing_correction",
            self.case.policy,
            reversal_idempotency_key=command_key,
        )
        replay = self.case.ledger.reverse(
            original.journal_reference,
            date(2026, 8, 21),
            "billing_correction",
            self.case.policy,
            reversal_idempotency_key=command_key,
        )

        self.assertEqual(replay, first)
        with self.assertRaises(IdempotencyConflictError):
            self.case.ledger.reverse(
                original.journal_reference,
                date(2026, 8, 21),
                "duplicate_charge",
                self.case.policy,
                reversal_idempotency_key=command_key,
            )

        with psycopg.connect(postgres_posting.DATABASE_URL) as connection:
            connection.execute(
                "SELECT set_config('app.tenant_account_id', %s, true)",
                (str(self.case.tenant_id),),
            )
            stored = connection.execute(
                """
                SELECT proposal.idempotency_key,
                       proposal.source_payload_hash,
                       reversal.reversal_reason_code,
                       journal.accounting_date,
                       COUNT(*) OVER ()
                FROM accounting_core.journal_reversal AS reversal
                JOIN accounting_core.general_journal AS journal
                  ON journal.tenant_account_id = reversal.tenant_account_id
                 AND journal.general_journal_id = reversal.reversal_journal_id
                JOIN accounting_integration.journal_proposal_record AS proposal
                  ON proposal.tenant_account_id = journal.tenant_account_id
                 AND proposal.proposal_record_id = journal.source_proposal_record_id
                WHERE reversal.tenant_account_id = %s
                """,
                (self.case.tenant_id,),
            ).fetchone()

        self.assertEqual(str(stored[0]), command_key)
        self.assertRegex(str(stored[1]), r"^sha256:[0-9a-f]{64}$")
        self.assertEqual(str(stored[2]), "billing_correction")
        self.assertEqual(stored[3], date(2026, 8, 21))
        self.assertEqual(int(stored[4]), 1)


if __name__ == "__main__":
    unittest.main()
