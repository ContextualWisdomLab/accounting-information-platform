"""Durable reversal-command idempotency regressions against PostgreSQL."""

from __future__ import annotations

import unittest
from datetime import date

from accounting_information_platform import IdempotencyConflictError

import tests.test_postgres_posting as postgres_posting


class PostgresReversalCommandIdempotencyTests(unittest.TestCase):
    """Keep durable reversal replay bound to the immutable command, not only the journal."""

    @classmethod
    def setUpClass(cls) -> None:
        """Apply the same real-PostgreSQL foundation used by the posting suite."""
        postgres_posting.PostgresPostingTests.setUpClass()

    def setUp(self) -> None:
        """Create an isolated tenant fixture using the production posting adapter."""
        self.case = postgres_posting.PostgresPostingTests("setUp")
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.addCleanup(self.case.tearDown)

    def test_changed_reversal_command_conflicts_after_exact_replay(self) -> None:
        """Exact replay succeeds; changed reason or date fails closed without a second reversal."""
        case = self.case
        posted = case.ledger.post(
            case._two_line_proposal(
                accounting_date=date(2026, 8, 30),
                transaction_date=date(2026, 8, 30),
            ),
            case.policy,
        )

        first = case.ledger.reverse(
            posted.journal_reference,
            date(2026, 8, 30),
            "billing_correction",
            case.policy,
        )
        replayed = case.ledger.reverse(
            posted.journal_reference,
            date(2026, 8, 30),
            "billing_correction",
            case.policy,
        )

        self.assertEqual(replayed, first)
        with self.assertRaises(IdempotencyConflictError):
            case.ledger.reverse(
                posted.journal_reference,
                date(2026, 8, 30),
                "duplicate_invoice_correction",
                case.policy,
            )
        with self.assertRaises(IdempotencyConflictError):
            case.ledger.reverse(
                posted.journal_reference,
                date(2026, 8, 31),
                "billing_correction",
                case.policy,
            )

        self.assertEqual(case._count_table("accounting_core.general_journal"), 2)
        self.assertEqual(case._count_table("accounting_core.journal_reversal"), 1)
        self.assertEqual(case._count_table("accounting_integration.posting_receipt"), 2)
        self.assertEqual(case._count_table("accounting_integration.outbox_event"), 2)


if __name__ == "__main__":
    unittest.main()
