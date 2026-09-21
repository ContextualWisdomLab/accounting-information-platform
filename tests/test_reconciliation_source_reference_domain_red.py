"""RED contracts for reconciliation source-reference identity domains.

Strong reconciliation references are source identities, not arbitrary truthy text.
Required statement/journal identities must be non-empty strings, while optional
provider/end-to-end/account-servicer references are either absent (`None`) or a
non-empty string. Malformed whitespace/non-string values must fail before strong
reference precedence can manufacture a high-confidence match or block fallback.
"""

from __future__ import annotations

import unittest
from datetime import date
from decimal import Decimal

from accounting_information_platform.reconciliation import (
    BookJournalEvidence,
    DeterministicMatchPolicy,
    StatementEntryEvidence,
    propose_deterministic_match,
)


class ReconciliationSourceReferenceDomainRedTests(unittest.TestCase):
    """Require source identities to enter matching in one canonical runtime domain."""

    @staticmethod
    def _statement(**overrides: object) -> StatementEntryEvidence:
        values: dict[str, object] = {
            "statement_entry_reference": "statement-source-domain-1",
            "provider_reference": "provider-source-domain-1",
            "end_to_end_reference": None,
            "account_servicer_reference": None,
            "amount": Decimal("1000.00"),
            "currency_code": "KRW",
            "credit_debit_code": "CRDT",
            "booking_date": date(2026, 9, 21),
            "value_date": date(2026, 9, 21),
        }
        values.update(overrides)
        return StatementEntryEvidence(**values)  # type: ignore[arg-type]

    @staticmethod
    def _journal(**overrides: object) -> BookJournalEvidence:
        values: dict[str, object] = {
            "journal_reference": "journal-source-domain-1",
            "provider_reference": "provider-source-domain-1",
            "end_to_end_reference": None,
            "account_servicer_reference": None,
            "amount": Decimal("1000.00"),
            "currency_code": "KRW",
            "credit_debit_code": "CRDT",
            "accounting_date": date(2026, 9, 21),
        }
        values.update(overrides)
        return BookJournalEvidence(**values)  # type: ignore[arg-type]

    def test_statement_requires_non_empty_source_identity(self) -> None:
        for value in ("", "   ", None, 7):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, "statement_entry_reference must be a non-empty identity"):
                    self._statement(statement_entry_reference=value)

    def test_journal_requires_non_empty_source_identity(self) -> None:
        for value in ("", "\t", None, 7):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, "journal_reference must be a non-empty identity"):
                    self._journal(journal_reference=value)

    def test_statement_rejects_malformed_optional_strong_reference(self) -> None:
        for field_name in (
            "provider_reference",
            "end_to_end_reference",
            "account_servicer_reference",
        ):
            for value in ("", "   ", 7):
                with self.subTest(field_name=field_name, value=value):
                    with self.assertRaisesRegex(ValueError, rf"{field_name} must be None or a non-empty identity"):
                        self._statement(**{field_name: value})

    def test_journal_rejects_malformed_optional_strong_reference(self) -> None:
        for field_name in (
            "provider_reference",
            "end_to_end_reference",
            "account_servicer_reference",
        ):
            for value in ("", "\t", 7):
                with self.subTest(field_name=field_name, value=value):
                    with self.assertRaisesRegex(ValueError, rf"{field_name} must be None or a non-empty identity"):
                        self._journal(**{field_name: value})

    def test_absent_optional_references_still_use_exact_money_date_fallback(self) -> None:
        statement = self._statement(
            provider_reference=None,
            end_to_end_reference=None,
            account_servicer_reference=None,
        )
        journal = self._journal(
            provider_reference=None,
            end_to_end_reference=None,
            account_servicer_reference=None,
        )

        decision = propose_deterministic_match(
            statement,
            (journal,),
            DeterministicMatchPolicy(date_window_days=0),
        )

        self.assertEqual(decision.decision_code, "match")
        self.assertEqual(decision.rule_code, "exact_money_bounded_date")
        self.assertEqual(decision.matched_journal_references, ("journal-source-domain-1",))

    def test_valid_strong_reference_still_wins_over_fallback(self) -> None:
        decision = propose_deterministic_match(
            self._statement(),
            (self._journal(accounting_date=date(2026, 9, 30)),),
            DeterministicMatchPolicy(date_window_days=0),
        )

        self.assertEqual(decision.decision_code, "match")
        self.assertEqual(decision.rule_code, "provider_reference")


if __name__ == "__main__":
    unittest.main()
