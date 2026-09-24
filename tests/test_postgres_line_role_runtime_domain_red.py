"""Real PostgreSQL RED for journal-line role admission before posting replay."""

from __future__ import annotations

import unittest

from accounting_information_platform import AccountingValidationError

import tests.test_postgres_posting as postgres_posting


class _HostileAccountRole(str):
    """Expose caller-owned comparison or hashing before repository admission."""

    def __eq__(self, other: object) -> bool:
        """Fail if retained-earnings comparison runs before exact-string admission."""
        raise AssertionError("hostile account-role equality executed")

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

    def _reject_command_lock(self) -> list[str]:
        """Fail if an invalid current line reaches PostgreSQL command serialization."""
        lock_calls: list[str] = []

        def unexpected_lock(_connection: object, command_scope: str) -> None:
            lock_calls.append(command_scope)
            raise AssertionError("command lock acquired before journal-line admission")

        self.case.ledger._acquire_command_lock = unexpected_lock
        return lock_calls

    def test_first_post_revalidates_current_line_role_before_mapping(self) -> None:
        """A mutated role subclass fails before lock, role comparison, mapping, or writes."""
        proposal = self.case._two_line_proposal()
        object.__setattr__(
            proposal.lines[0],
            "account_role_code",
            _HostileAccountRole("accounts_receivable"),
        )
        lock_calls = self._reject_command_lock()

        with self.assertRaisesRegex(
            AccountingValidationError,
            "account role code must be a built-in string",
        ):
            self.case.ledger.post(proposal, self.case.policy)

        self.assertEqual(lock_calls, [])
        self.assertEqual(self.case.ledger.journal_count, 0)
        self.assertEqual(self.case._count_table("accounting_core.general_journal"), 0)
        self.assertEqual(self.case._count_table("accounting_core.journal_entry_line"), 0)
        self.assertEqual(
            self.case._count_table("accounting_integration.posting_receipt"), 0
        )

    def test_replay_revalidates_current_line_role_before_cached_receipt(self) -> None:
        """A mutated replay fails before command lock or authoritative receipt lookup."""
        proposal = self.case._two_line_proposal()
        first = self.case.ledger.post(proposal, self.case.policy)
        self.assertEqual(self.case.ledger.post(proposal, self.case.policy), first)

        object.__setattr__(
            proposal.lines[0],
            "account_role_code",
            _HostileAccountRole("accounts_receivable"),
        )
        lock_calls = self._reject_command_lock()

        with self.assertRaisesRegex(
            AccountingValidationError,
            "account role code must be a built-in string",
        ):
            self.case.ledger.post(proposal, self.case.policy)

        self.assertEqual(lock_calls, [])
        self.assertEqual(self.case.ledger.journal_count, 1)
        self.assertEqual(self.case._count_table("accounting_core.general_journal"), 1)
        self.assertEqual(self.case._count_table("accounting_core.journal_entry_line"), 2)
        self.assertEqual(
            self.case._count_table("accounting_integration.posting_receipt"), 1
        )


if __name__ == "__main__":
    unittest.main()
