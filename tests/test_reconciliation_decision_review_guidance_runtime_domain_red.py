"""RED contracts for reconciliation review-guidance runtime domains.

Rule provenance and operator guidance are durable reconciliation evidence. Direct
construction must reject caller-defined string behavior before validating blank
content while preserving ordinary non-empty built-in strings.
"""

from __future__ import annotations

import unittest
from decimal import Decimal

from accounting_information_platform.reconciliation import ReconciliationDecision


class _ExplodingStripStr(str):
    def strip(self, chars: str | None = None) -> str:
        """Prove admission rejects subclasses before caller string logic runs."""
        raise RuntimeError("caller-controlled review-guidance strip must not execute")


class ReconciliationDecisionReviewGuidanceRuntimeDomainRedTests(unittest.TestCase):
    """Require repository-owned string behavior for review provenance and guidance."""

    @staticmethod
    def _match(
        *,
        rule_code: object = "provider_reference",
        next_action: object = "Review this deterministic reconciliation proposal.",
    ) -> ReconciliationDecision:
        """Build otherwise-valid match evidence while varying review-guidance fields."""
        return ReconciliationDecision(
            statement_entry_reference="statement-review-domain-1",
            decision_code="match",
            rule_code=rule_code,  # type: ignore[arg-type]
            matched_journal_references=("journal-review-domain-1",),
            allocated_amount=Decimal("1000.00"),
            exception_code=None,
            next_action=next_action,  # type: ignore[arg-type]
        )

    @staticmethod
    def _abstain(
        *, next_action: object = "Review the unresolved reconciliation evidence."
    ) -> ReconciliationDecision:
        """Build otherwise-valid abstention evidence while varying operator guidance."""
        return ReconciliationDecision(
            statement_entry_reference="statement-review-domain-1",
            decision_code="abstain",
            rule_code=None,
            matched_journal_references=(),
            allocated_amount=Decimal("0"),
            exception_code="no_candidate",
            next_action=next_action,  # type: ignore[arg-type]
        )

    def test_rule_code_subclass_fails_before_custom_strip(self) -> None:
        """A caller-defined string cannot become durable matching-rule provenance."""
        with self.assertRaisesRegex(
            ValueError,
            "match decision requires a non-empty rule_code",
        ):
            self._match(rule_code=_ExplodingStripStr("provider_reference"))

    def test_match_next_action_subclass_fails_before_custom_strip(self) -> None:
        """Match guidance cannot execute caller-defined string normalization."""
        with self.assertRaisesRegex(
            ValueError,
            "next_action must be a non-empty review instruction",
        ):
            self._match(next_action=_ExplodingStripStr("Review this proposal."))

    def test_abstain_next_action_subclass_fails_before_custom_strip(self) -> None:
        """Abstention guidance uses the same exact-string admission boundary."""
        with self.assertRaisesRegex(
            ValueError,
            "next_action must be a non-empty review instruction",
        ):
            self._abstain(next_action=_ExplodingStripStr("Review this exception."))

    def test_builtin_review_guidance_remains_valid(self) -> None:
        """Ordinary built-in provenance and guidance preserve existing semantics."""
        match = self._match()
        abstain = self._abstain()

        self.assertEqual(match.rule_code, "provider_reference")
        self.assertEqual(
            match.next_action,
            "Review this deterministic reconciliation proposal.",
        )
        self.assertEqual(
            abstain.next_action,
            "Review the unresolved reconciliation evidence.",
        )


if __name__ == "__main__":
    unittest.main()
