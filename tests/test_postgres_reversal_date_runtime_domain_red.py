"""Real PostgreSQL RED for reversal-date runtime-domain admission."""

from __future__ import annotations

from datetime import date, datetime
import unittest
from unittest.mock import patch

from accounting_information_platform import AccountingValidationError

import tests.test_postgres_posting as postgres_posting


class _HostileDate(date):
    """Expose caller-owned date behavior if reversal hashing precedes admission."""

    def isoformat(self) -> str:
        """Fail if command hashing reaches subclass behavior before validation."""
        raise AssertionError("caller-defined reversal-date behavior executed")


class PostgresReversalDateRuntimeDomainTests(unittest.TestCase):
    """Keep PostgreSQL reversal behind exact current calendar-date admission."""

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

    def test_first_reversal_revalidates_date_before_hash_and_session(self) -> None:
        """Invalid runtime dates fail before caller behavior or PostgreSQL authority."""
        original = self._post_original()
        baseline = self._authority_counts()
        invalid_dates = (
            datetime(2026, 8, 31, 0, 0),
            _HostileDate(2026, 8, 31),
        )

        for invalid_date in invalid_dates:
            with self.subTest(invalid_type=type(invalid_date).__name__):
                with patch.object(
                    self.case.ledger,
                    "_session",
                    side_effect=AssertionError(
                        "PostgreSQL session opened before reversal-date admission"
                    ),
                ) as session:
                    with self.assertRaisesRegex(
                        AccountingValidationError,
                        "reversal_date must be an exact calendar date",
                    ):
                        self.case.ledger.reverse(
                            original.journal_reference,
                            invalid_date,  # type: ignore[arg-type]
                            "billing_correction",
                            self.case.policy,
                        )

                session.assert_not_called()
                self.assertEqual(self._authority_counts(), baseline)

    def test_replay_revalidates_date_before_hash_and_session(self) -> None:
        """A retained reversal receipt must not hide an invalid current date."""
        original = self._post_original()
        reversal_date = date(2026, 8, 31)
        first = self.case.ledger.reverse(
            original.journal_reference,
            reversal_date,
            "billing_correction",
            self.case.policy,
        )
        self.assertEqual(
            self.case.ledger.reverse(
                original.journal_reference,
                reversal_date,
                "billing_correction",
                self.case.policy,
            ),
            first,
        )
        baseline = self._authority_counts()

        with patch.object(
            self.case.ledger,
            "_session",
            side_effect=AssertionError(
                "PostgreSQL session opened before replay reversal-date admission"
            ),
        ) as session:
            with self.assertRaisesRegex(
                AccountingValidationError,
                "reversal_date must be an exact calendar date",
            ):
                self.case.ledger.reverse(
                    original.journal_reference,
                    datetime(2026, 8, 31, 0, 0),
                    "billing_correction",
                    self.case.policy,
                )

        session.assert_not_called()
        self.assertEqual(self._authority_counts(), baseline)

    def test_exact_builtin_date_preserves_reversal_and_replay(self) -> None:
        """An exact built-in date retains the supported PostgreSQL replay contract."""
        original = self._post_original()
        reversal_date = date(2026, 8, 31)

        first = self.case.ledger.reverse(
            original.journal_reference,
            reversal_date,
            "billing_correction",
            self.case.policy,
        )
        replay = self.case.ledger.reverse(
            original.journal_reference,
            reversal_date,
            "billing_correction",
            self.case.policy,
        )

        self.assertEqual(replay, first)
        self.assertEqual(self.case._count_table("accounting_core.journal_reversal"), 1)
        self.assertEqual(self.case._count_table("accounting_core.general_journal"), 2)


if __name__ == "__main__":
    unittest.main()
