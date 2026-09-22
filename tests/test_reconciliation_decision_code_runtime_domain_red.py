"""RED contracts for the reconciliation decision-code runtime domain.

The decision code is durable control evidence, not a trusted annotation. Admission
must reject non-repository string behavior before branching into match/abstain
semantics, while preserving both supported built-in states.
"""

from __future__ import annotations

import unittest
from decimal import Decimal

from accounting_information_platform.reconciliation import ReconciliationDecision


class _ExplodingEqualityStr(str):
    def __eq__(self, other: object) -> bool:
        """Prove admission does not execute caller equality while choosing a state."""
        raise RuntimeError("caller-controlled decision-code equality must not execute")


class ReconciliationDecisionCodeRuntimeDomainRedTests(unittest.TestCase):
    """Require exact built-in match/abstain state evidence before decision branching."""

    @staticmethod
    def _match(decision_code: object = "match") -> ReconciliationDecision:
        return ReconciliationDecision(
            statement_entry_reference="statement-decision-domain-1",
            decision_code=decision_code,  # type: ignore[arg-type]
            rule_code="provider_reference",
            matched_journal_references=("journal-decision-domain-1",),
            allocated_amount=Decimal("1000.00"),
            exception_code=None,
            next_action="Review this deterministic reconciliation proposal.",
        )

    @staticmethod
    def _abstain(decision_code: object = "abstain") -> ReconciliationDecision:
        return ReconciliationDecision(
            statement_entry_reference="statement-decision-domain-1",
            decision_code=decision_code,  # type: ignore[arg-type]
            rule_code=None,
            matched_journal_references=(),
            allocated_amount=Decimal("0"),
            exception_code="no_candidate",
            next_action="Review the unresolved reconciliation evidence.",
        )

    def test_decision_code_subclass_fails_before_custom_equality(self) -> None:
        """A caller-defined str cannot choose durable match/abstain semantics."""
        for builder, value in (
            (self._match, _ExplodingEqualityStr("match")),
            (self._abstain, _ExplodingEqualityStr("abstain")),
        ):
            with self.subTest(value=value):
                with self.assertRaisesRegex(
                    ValueError,
                    "decision_code must be match or abstain",
                ):
                    builder(value)

    def test_unknown_exact_builtin_decision_code_remains_rejected(self) -> None:
        """The runtime guard retains the existing closed two-state contract."""
        with self.assertRaisesRegex(ValueError, "decision_code must be match or abstain"):
            self._abstain("approved")

    def test_supported_builtin_decision_codes_remain_valid(self) -> None:
        """Exact built-in match and abstain states keep their existing semantics."""
        match = self._match()
        abstain = self._abstain()

        self.assertEqual(match.decision_code, "match")
        self.assertEqual(match.matched_journal_references, ("journal-decision-domain-1",))
        self.assertEqual(abstain.decision_code, "abstain")
        self.assertEqual(abstain.exception_code, "no_candidate")


if __name__ == "__main__":
    unittest.main()
