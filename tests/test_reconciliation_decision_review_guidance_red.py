"""RED contracts for review guidance on reconciliation decision evidence.

A reconciliation decision is reviewable evidence only when a positive match names
the rule that produced it and every decision tells the operator what to do next.
An abstention must not masquerade as a matched-rule result. Direct construction
therefore validates these fields instead of trusting Python annotations.
"""

from __future__ import annotations

import unittest
from decimal import Decimal

from accounting_information_platform.reconciliation import ReconciliationDecision


class ReconciliationDecisionReviewGuidanceRedTests(unittest.TestCase):
    """Require rule provenance and operator guidance to match decision semantics."""

    @staticmethod
    def _match(*, rule_code: object = "provider_reference", next_action: object = "Review this deterministic proposal.") -> ReconciliationDecision:
        return ReconciliationDecision(
            statement_entry_reference="statement-guidance-1",
            decision_code="match",
            rule_code=rule_code,  # type: ignore[arg-type]
            matched_journal_references=("journal-guidance-1",),
            allocated_amount=Decimal("1000.00"),
            exception_code=None,
            next_action=next_action,  # type: ignore[arg-type]
        )

    @staticmethod
    def _abstain(*, rule_code: object = None, next_action: object = "Review the unresolved evidence.") -> ReconciliationDecision:
        return ReconciliationDecision(
            statement_entry_reference="statement-guidance-1",
            decision_code="abstain",
            rule_code=rule_code,  # type: ignore[arg-type]
            matched_journal_references=(),
            allocated_amount=Decimal("0"),
            exception_code="no_candidate",
            next_action=next_action,  # type: ignore[arg-type]
        )

    def test_match_requires_non_empty_rule_provenance(self) -> None:
        """A success-shaped decision cannot omit the deterministic/reviewed rule."""
        for value in (None, "", "   ", 7):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, "match decision requires a non-empty rule_code"):
                    self._match(rule_code=value)

    def test_abstention_cannot_claim_match_rule_provenance(self) -> None:
        """An abstention cannot be exported as if a matching rule had succeeded."""
        for value in ("provider_reference", "exact_money_bounded_date", "   "):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, "abstain decision cannot carry a rule_code"):
                    self._abstain(rule_code=value)

    def test_match_requires_non_empty_operator_next_action(self) -> None:
        """A reviewable match must tell the operator what action remains permitted."""
        for value in (None, "", "   ", 7):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, "next_action must be a non-empty review instruction"):
                    self._match(next_action=value)

    def test_abstention_requires_non_empty_operator_next_action(self) -> None:
        """Fail-closed evidence must still give the operator a concrete next action."""
        for value in (None, "", "\t", 7):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, "next_action must be a non-empty review instruction"):
                    self._abstain(next_action=value)

    def test_valid_match_and_abstention_guidance_remain_reviewable(self) -> None:
        """The guard preserves valid match provenance and explicit exception guidance."""
        match = self._match()
        abstain = self._abstain()

        self.assertEqual(match.rule_code, "provider_reference")
        self.assertEqual(match.next_action, "Review this deterministic proposal.")
        self.assertIsNone(abstain.rule_code)
        self.assertEqual(abstain.next_action, "Review the unresolved evidence.")


if __name__ == "__main__":
    unittest.main()
