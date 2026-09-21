"""RED contracts for non-empty source identities on reconciliation decisions.

A reviewable reconciliation decision is durable evidence only when its statement
and matched-journal identities name real immutable sources. Python type hints are
not an accounting control: direct construction must fail closed on blank or
non-string identity values before evidence can be logged, exported, or persisted.
"""

from __future__ import annotations

import unittest
from decimal import Decimal

from accounting_information_platform.reconciliation import ReconciliationDecision


class ReconciliationDecisionSourceIdentityRedTests(unittest.TestCase):
    """Require decision provenance identities to be non-empty runtime values."""

    @staticmethod
    def _match(
        *,
        statement_entry_reference: object = "statement-source-1",
        matched_journal_references: tuple[object, ...] = ("journal-source-1",),
        contract_version: str = "reconciliation-decision/v1",
    ) -> ReconciliationDecision:
        return ReconciliationDecision(
            statement_entry_reference=statement_entry_reference,  # type: ignore[arg-type]
            decision_code="match",
            rule_code="reviewed_split",
            matched_journal_references=matched_journal_references,  # type: ignore[arg-type]
            allocated_amount=Decimal("1000.00"),
            exception_code=None,
            next_action="Review the persisted reconciliation evidence; do not post a journal.",
            contract_version=contract_version,
        )

    @staticmethod
    def _abstain(*, statement_entry_reference: object) -> ReconciliationDecision:
        return ReconciliationDecision(
            statement_entry_reference=statement_entry_reference,  # type: ignore[arg-type]
            decision_code="abstain",
            rule_code=None,
            matched_journal_references=(),
            allocated_amount=Decimal("0"),
            exception_code="no_candidate",
            next_action="Review unmatched evidence and record an explicit exception.",
        )

    def test_match_rejects_blank_or_non_string_statement_identity(self) -> None:
        """A successful proposal cannot exist without immutable statement provenance."""
        for value in ("", "   ", None):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, "statement_entry_reference must be a non-empty identity"):
                    self._match(statement_entry_reference=value)

    def test_abstention_rejects_blank_or_non_string_statement_identity(self) -> None:
        """Fail-closed evidence must remain attributable to a real statement source."""
        for value in ("", "\t", None):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, "statement_entry_reference must be a non-empty identity"):
                    self._abstain(statement_entry_reference=value)

    def test_v2_rejects_blank_non_string_or_unhashable_journal_identity(self) -> None:
        """Reviewed split members fail domain validation before any raw set/hash error."""
        for value in ("", "   ", None, []):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, "matched_journal_references must contain non-empty identities"):
                    self._match(
                        matched_journal_references=("journal-source-1", value),
                        contract_version="reconciliation-decision/v2",
                    )

    def test_valid_distinct_source_identities_remain_reviewable(self) -> None:
        """Identity validation must not narrow legitimate reviewed split evidence."""
        decision = self._match(
            matched_journal_references=("journal-source-1", "journal-source-2"),
            contract_version="reconciliation-decision/v2",
        )

        self.assertEqual(decision.statement_entry_reference, "statement-source-1")
        self.assertEqual(
            decision.matched_journal_references,
            ("journal-source-1", "journal-source-2"),
        )
        self.assertEqual(decision.allocated_amount, Decimal("1000.00"))


if __name__ == "__main__":
    unittest.main()
