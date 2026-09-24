"""Real PostgreSQL RED for reversal idempotency-key runtime admission."""

from __future__ import annotations

from datetime import date
import unittest
from unittest.mock import patch

from accounting_information_platform import AccountingValidationError

import tests.test_postgres_posting as postgres_posting


class _HostileIdempotencyKey(str):
    """Expose caller-owned normalization if key type admission comes too late."""

    def strip(self, chars: str | None = None) -> str:
        """Fail if PostgreSQL reversal normalizes this subclass before admission."""
        raise AssertionError("caller-defined reversal idempotency strip executed")


class _HostileNonStringKey:
    """Expose arbitrary caller behavior if non-string keys reach normalization."""

    def strip(self, chars: str | None = None) -> str:
        """Fail if a non-string command identity is treated as string-like."""
        raise AssertionError("non-string reversal idempotency strip executed")


class PostgresReversalIdempotencyRuntimeDomainTests(unittest.TestCase):
    """Keep PostgreSQL reversal behind exact built-in command-key admission."""

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

    def test_first_reversal_rejects_non_string_key_before_normalization_or_session(self) -> None:
        """Caller-defined command-key behavior cannot run before repository admission."""
        original = self._post_original()
        baseline = self._authority_counts()
        invalid_keys = (
            _HostileIdempotencyKey("reversal-idempotency-command-v1"),
            _HostileNonStringKey(),
        )

        for invalid_key in invalid_keys:
            with self.subTest(invalid_type=type(invalid_key).__name__):
                with patch.object(
                    self.case.ledger,
                    "_session",
                    side_effect=AssertionError(
                        "PostgreSQL session opened before reversal-idempotency admission"
                    ),
                ) as session:
                    with self.assertRaisesRegex(
                        AccountingValidationError,
                        "reversal idempotency key must be a string",
                    ):
                        self.case.ledger.reverse(
                            original.journal_reference,
                            date(2026, 8, 31),
                            "billing_correction",
                            self.case.policy,
                            reversal_idempotency_key=invalid_key,  # type: ignore[arg-type]
                        )

                session.assert_not_called()
                self.assertEqual(self._authority_counts(), baseline)

    def test_replay_revalidates_key_type_before_normalization_or_session(self) -> None:
        """Retained reversal evidence must not hide a hostile current key type."""
        original = self._post_original()
        command_key = "reversal-idempotency-command-v1"
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
                "PostgreSQL session opened before replay reversal-idempotency admission"
            ),
        ) as session:
            with self.assertRaisesRegex(
                AccountingValidationError,
                "reversal idempotency key must be a string",
            ):
                self.case.ledger.reverse(
                    original.journal_reference,
                    date(2026, 8, 31),
                    "billing_correction",
                    self.case.policy,
                    reversal_idempotency_key=_HostileIdempotencyKey(command_key),
                )

        session.assert_not_called()
        self.assertEqual(self._authority_counts(), baseline)

    def test_exact_builtin_key_preserves_reversal_and_replay(self) -> None:
        """A canonical built-in command key retains the supported PostgreSQL contract."""
        original = self._post_original()
        command_key = "reversal-idempotency-command-v1"

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
