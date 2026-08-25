"""Real PostgreSQL regression for durable reversal command idempotency."""

from __future__ import annotations

from datetime import date
import unittest

from accounting_information_platform import AccountingValidationError, IdempotencyConflictError

import tests.test_postgres_posting as postgres_posting


class DurableReversalIdempotencyTests(unittest.TestCase):
    """Keep PostgreSQL reversal replay bound to immutable command evidence."""

    @classmethod
    def setUpClass(cls) -> None:
        """Apply the same real PostgreSQL foundation used by posting integration tests."""
        postgres_posting.PostgresPostingTests.setUpClass()

    def setUp(self) -> None:
        """Seed one isolated tenant, legal entity, book, period, and chart catalog."""
        self.case = postgres_posting.PostgresPostingTests("setUp")
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.addCleanup(self.case.tearDown)

    def test_changed_reversal_command_does_not_replay_prior_receipt(self) -> None:
        """Same command replays; changed date, reason, or key conflicts without a second reversal."""
        original = self.case.ledger.post(self.case._two_line_proposal(), self.case.policy)
        command_key = f"reversal:{original.journal_reference}"

        first = self.case.ledger.reverse(
            original.journal_reference,
            date(2026, 8, 31),
            "billing_correction",
            self.case.policy,
            reversal_idempotency_key=command_key,
        )
        replay = self.case.ledger.reverse(
            original.journal_reference,
            date(2026, 8, 31),
            "billing_correction",
            self.case.policy,
            reversal_idempotency_key=command_key,
        )

        self.assertEqual(first, replay)
        self.assertEqual(self.case._count_table("accounting_core.journal_reversal"), 1)
        self.assertEqual(self.case._count_table("accounting_core.general_journal"), 2)

        with self.assertRaises(IdempotencyConflictError):
            self.case.ledger.reverse(
                original.journal_reference,
                date(2026, 8, 30),
                "billing_correction",
                self.case.policy,
                reversal_idempotency_key=command_key,
            )
        with self.assertRaises(IdempotencyConflictError):
            self.case.ledger.reverse(
                original.journal_reference,
                date(2026, 8, 31),
                "customer_dispute",
                self.case.policy,
                reversal_idempotency_key=command_key,
            )
        with self.assertRaisesRegex(AccountingValidationError, "already reversed"):
            self.case.ledger.reverse(
                original.journal_reference,
                date(2026, 8, 31),
                "billing_correction",
                self.case.policy,
                reversal_idempotency_key="reversal-command:changed",
            )

        self.assertEqual(self.case._count_table("accounting_core.journal_reversal"), 1)
        self.assertEqual(self.case._count_table("accounting_core.general_journal"), 2)


if __name__ == "__main__":
    unittest.main()
