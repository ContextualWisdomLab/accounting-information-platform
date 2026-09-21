"""RED contracts for distinct journal identities in reviewed reconciliation decisions.

A versioned close-review decision may carry several journal identities, but repeating
one immutable journal identity does not create a second source of accounting
evidence. The decision boundary must fail closed before duplicated source identity
can be serialized as reviewable split evidence.
"""

from __future__ import annotations

import unittest
from decimal import Decimal

from accounting_information_platform.reconciliation import ReconciliationDecision


class ReconciliationDecisionDistinctSourceRedTests(unittest.TestCase):
    """Require reviewed multi-journal decisions to carry a source-identity set."""

    @staticmethod
    def _decision(journal_references: tuple[str, ...]) -> ReconciliationDecision:
        return ReconciliationDecision(
            statement_entry_reference="statement-distinct-source-1",
            decision_code="match",
            rule_code="reviewed_split",
            matched_journal_references=journal_references,
            allocated_amount=Decimal("1000.00"),
            exception_code=None,
            next_action="Review the persisted split evidence; do not post a journal.",
            contract_version="reconciliation-decision/v2",
        )

    def test_v2_rejects_duplicate_journal_identity(self) -> None:
        """One immutable journal cannot appear twice in reviewed split evidence."""
        with self.assertRaisesRegex(ValueError, "distinct journal identities"):
            self._decision(("journal-source-1", "journal-source-1"))

    def test_v2_preserves_distinct_multi_journal_identity_set(self) -> None:
        """The duplicate guard must not collapse a legitimate reviewed split."""
        decision = self._decision(("journal-source-1", "journal-source-2"))

        self.assertEqual(
            decision.matched_journal_references,
            ("journal-source-1", "journal-source-2"),
        )
        self.assertEqual(decision.allocated_amount, Decimal("1000.00"))
        self.assertEqual(decision.contract_version, "reconciliation-decision/v2")


if __name__ == "__main__":
    unittest.main()
