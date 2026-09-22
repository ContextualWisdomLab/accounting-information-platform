"""Real PostgreSQL RED for journal-line role admission before posting replay."""

from __future__ import annotations

import unittest

from accounting_information_platform import AccountingValidationError

import tests.test_postgres_posting as postgres_posting


class _HostileAccountRole(str):
    """Expose any attempt to hash caller-owned string-subclass behavior."""

    def __hash__(self) -> int:
        """Fail if PostgreSQL posting reaches mapping/cache behavior before admission."""
        raise AssertionError("hostile account-role hash executed")


class PostgresLineRoleRuntimeDomainTests(unittest.TestCase):
    """Keep durable posting and replay behind the journal-line role admission boundary."""

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

    def test_first_post_revalidates_current_line_role_before_mapping(self) -> None:
        """A mutated role subclass fails before chart-account lookup or durable writes."""
        proposal = self.case._two_line_proposal()
        object.__setattr__(
            proposal.lines[0],
            "account_role_code",
            _HostileAccountRole("accounts_receivable"),
        )

        with self.assertRaisesRegex(
            AccountingValidationError,
            "account role code must be a built-in string",
        ):
            self.case.ledger.post(proposal, self.case.policy)

        self.assertEqual(self.case.ledger.journal_count, 0)
        self.assertEqual(self.case._count_table("accounting_core.general_journal"), 0)
        self.assertEqual(self.case._count_table("accounting_core.journal_entry_line"), 0)
        self.assertEqual(
            self.case._count_table("accounting_integration.posting_receipt"), 0
        )

    def test_replay_revalidates_current_line_role_before_cached_receipt(self) -> None:
        """A mutated replay fails before an existing authoritative receipt is returned."""
        proposal = self.case._two_line_proposal()
        first = self.case.ledger.post(proposal, self.case.policy)
        self.assertEqual(self.case.ledger.post(proposal, self.case.policy), first)

        object.__setattr__(
            proposal.lines[0],
            "account_role_code",
            _HostileAccountRole("accounts_receivable"),
        )

        with self.assertRaisesRegex(
            AccountingValidationError,
            "account role code must be a built-in string",
        ):
            self.case.ledger.post(proposal, self.case.policy)

        self.assertEqual(self.case.ledger.journal_count, 1)
        self.assertEqual(self.case._count_table("accounting_core.general_journal"), 1)
        self.assertEqual(self.case._count_table("accounting_core.journal_entry_line"), 2)
        self.assertEqual(
            self.case._count_table("accounting_integration.posting_receipt"), 1
        )


if __name__ == "__main__":
    unittest.main()
