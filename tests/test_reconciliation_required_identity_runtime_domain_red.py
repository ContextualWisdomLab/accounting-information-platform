"""RED contract for exact built-in required reconciliation identities."""

from __future__ import annotations

import unittest
from datetime import date
from decimal import Decimal

from accounting_information_platform.reconciliation import BookJournalEvidence
from accounting_information_platform.reconciliation import ReconciliationDecision
from accounting_information_platform.reconciliation import StatementEntryEvidence


class _UnhashableStr(str):
    __hash__ = None


class _ExplodingStripStr(str):
    def strip(self, *args: object, **kwargs: object) -> str:
        raise RuntimeError("caller-controlled strip must not execute")


class ReconciliationRequiredIdentityRuntimeDomainRedTests(unittest.TestCase):
    """Keep required reconciliation source identities inside the built-in string domain."""

    @staticmethod
    def _statement(reference: object) -> StatementEntryEvidence:
        """Build otherwise-valid statement evidence while varying only its identity."""
        return StatementEntryEvidence(
            statement_entry_reference=reference,  # type: ignore[arg-type]
            provider_reference=None,
            end_to_end_reference=None,
            account_servicer_reference=None,
            amount=Decimal("1000.00"),
            currency_code="KRW",
            credit_debit_code="CRDT",
            booking_date=date(2026, 9, 22),
            value_date=date(2026, 9, 22),
        )

    @staticmethod
    def _journal(reference: object) -> BookJournalEvidence:
        """Build otherwise-valid book evidence while varying only its identity."""
        return BookJournalEvidence(
            journal_reference=reference,  # type: ignore[arg-type]
            provider_reference=None,
            end_to_end_reference=None,
            account_servicer_reference=None,
            amount=Decimal("1000.00"),
            currency_code="KRW",
            credit_debit_code="CRDT",
            accounting_date=date(2026, 9, 22),
        )

    @staticmethod
    def _decision(reference: object) -> ReconciliationDecision:
        """Build otherwise-valid reviewed evidence while varying only statement identity."""
        return ReconciliationDecision(
            statement_entry_reference=reference,  # type: ignore[arg-type]
            decision_code="match",
            rule_code="provider_reference",
            matched_journal_references=("journal-required-runtime-1",),
            allocated_amount=Decimal("1000.00"),
            exception_code=None,
            next_action="Review the reconciliation proposal; do not post a journal.",
        )

    def test_unhashable_string_subclass_is_rejected_for_every_required_identity(self) -> None:
        """Hash-hostile subclasses must fail at admission before downstream set usage."""
        cases = (
            (self._statement, "statement_entry_reference"),
            (self._journal, "journal_reference"),
            (self._decision, "statement_entry_reference"),
        )
        for factory, field_name in cases:
            with self.subTest(field_name=field_name):
                with self.assertRaisesRegex(ValueError, field_name):
                    factory(_UnhashableStr(f"{field_name}-runtime-1"))

    def test_required_identity_rejects_subclass_before_custom_strip_executes(self) -> None:
        """Admission must not invoke caller-defined string methods on durable identities."""
        cases = (
            (self._statement, "statement_entry_reference"),
            (self._journal, "journal_reference"),
            (self._decision, "statement_entry_reference"),
        )
        for factory, field_name in cases:
            with self.subTest(field_name=field_name):
                with self.assertRaisesRegex(ValueError, field_name):
                    factory(_ExplodingStripStr(f"{field_name}-runtime-2"))

    def test_exact_builtin_required_identities_remain_valid(self) -> None:
        """Canonical built-in strings preserve statement, journal, and decision behavior."""
        statement = self._statement("statement-required-runtime-1")
        journal = self._journal("journal-required-runtime-1")
        decision = self._decision("statement-required-runtime-1")

        self.assertEqual(statement.statement_entry_reference, "statement-required-runtime-1")
        self.assertEqual(journal.journal_reference, "journal-required-runtime-1")
        self.assertEqual(decision.statement_entry_reference, "statement-required-runtime-1")
        self.assertEqual(decision.allocated_amount, Decimal("1000.00"))


if __name__ == "__main__":
    unittest.main()
