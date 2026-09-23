"""Real PostgreSQL RED for reversal target-reference runtime admission."""

from __future__ import annotations

from datetime import date
import unittest
from unittest.mock import patch

from accounting_information_platform import AccountingValidationError

import tests.test_postgres_posting as postgres_posting


class _HostileReference(str):
    """Expose caller-owned target formatting if command identity precedes admission."""

    def __format__(self, format_spec: str) -> str:
        """Fail if the derived reversal command key formats this subclass first."""
        raise AssertionError("caller-defined reversal-target formatting executed")


class PostgresReversalTargetReferenceRuntimeDomainTests(unittest.TestCase):
    """Keep PostgreSQL reversal behind current journal-reference admission."""

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

    def _authority_counts(self) -> tuple[int, ...]:
        """Snapshot durable posting/reversal authority without assuming later totals."""
        return (
            self.case._count_table("accounting_core.general_journal"),
            self.case._count_table("accounting_core.journal_entry_line"),
            self.case._count_table("accounting_core.journal_reversal"),
            self.case._count_table("accounting_integration.posting_receipt"),
            self.case._count_table("accounting_integration.outbox_event"),
        )

    def _post_original(self):
        """Create the canonical posted journal that a reversal command targets."""
        return self.case.ledger.post(self.case._two_line_proposal(), self.case.policy)

    def test_first_reversal_revalidates_target_before_session(self) -> None:
        """Malformed or subclass targets fail before PostgreSQL authority is opened."""
        original = self._post_original()
        baseline = self._authority_counts()
        invalid_references = (
            "accounting:general_journal:malformed",
            _HostileReference(original.journal_reference),
        )

        for invalid_reference in invalid_references:
            with self.subTest(invalid_type=type(invalid_reference).__name__):
                with patch.object(
                    self.case.ledger,
                    "_session",
                    side_effect=AssertionError(
                        "PostgreSQL session opened before reversal-target admission"
                    ),
                ) as session:
                    with self.assertRaisesRegex(
                        AccountingValidationError,
                        "journal reference must be a CWL URN",
                    ):
                        self.case.ledger.reverse(
                            invalid_reference,
                            date(2026, 8, 31),
                            "billing_correction",
                            self.case.policy,
                            reversal_idempotency_key="reversal-target-command-v1",
                        )

                session.assert_not_called()
                self.assertEqual(self._authority_counts(), baseline)

    def test_derived_command_key_cannot_format_target_before_admission(self) -> None:
        """Omitted command keys must not execute target-subclass behavior first."""
        original = self._post_original()
        baseline = self._authority_counts()
        hostile_reference = _HostileReference(original.journal_reference)

        with patch.object(
            self.case.ledger,
            "_session",
            side_effect=AssertionError(
                "PostgreSQL session opened before reversal-target admission"
            ),
        ) as session:
            with self.assertRaisesRegex(
                AccountingValidationError,
                "journal reference must be a CWL URN",
            ):
                self.case.ledger.reverse(
                    hostile_reference,
                    date(2026, 8, 31),
                    "billing_correction",
                    self.case.policy,
                )

        session.assert_not_called()
        self.assertEqual(self._authority_counts(), baseline)

    def test_replay_revalidates_target_before_session(self) -> None:
        """Retained reversal evidence must not hide an invalid current target type."""
        original = self._post_original()
        command_key = "reversal-target-command-v1"
        first = self.case.ledger.reverse(
            original.journal_reference,
            date(2026, 8, 31),
            "billing_correction",
            self.case.policy,
            reversal_idempotency_key=command_key,
        )
        self.assertEqual(
            self.case.ledger.reverse(
                original.journal_reference,
                date(2026, 8, 31),
                "billing_correction",
                self.case.policy,
                reversal_idempotency_key=command_key,
            ),
            first,
        )
        baseline = self._authority_counts()

        with patch.object(
            self.case.ledger,
            "_session",
            side_effect=AssertionError(
                "PostgreSQL session opened before replay reversal-target admission"
            ),
        ) as session:
            with self.assertRaisesRegex(
                AccountingValidationError,
                "journal reference must be a CWL URN",
            ):
                self.case.ledger.reverse(
                    _HostileReference(original.journal_reference),
                    date(2026, 8, 31),
                    "billing_correction",
                    self.case.policy,
                    reversal_idempotency_key=command_key,
                )

        session.assert_not_called()
        self.assertEqual(self._authority_counts(), baseline)

    def test_exact_builtin_target_preserves_reversal_and_replay(self) -> None:
        """A canonical built-in reference retains the supported PostgreSQL contract."""
        original = self._post_original()
        command_key = "reversal-target-command-v1"

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

        self.assertEqual(replay, first)
        self.assertEqual(first.reversal_of_journal_reference, original.journal_reference)
        self.assertEqual(self.case._count_table("accounting_core.journal_reversal"), 1)
        self.assertEqual(self.case._count_table("accounting_core.general_journal"), 2)


if __name__ == "__main__":
    unittest.main()
