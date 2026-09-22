"""RED contracts for the bounded reconciliation exception-code domain.

ADR 0054 defines a closed set of abstention exception codes. Direct construction
must preserve that domain instead of accepting arbitrary caller labels that later
buyer/control logic cannot interpret as repository-owned reconciliation evidence.
"""

from __future__ import annotations

import unittest
from decimal import Decimal

from accounting_information_platform.reconciliation import ReconciliationDecision


_ALLOWED_EXCEPTION_CODES = (
    "ambiguous_reference",
    "amount_mismatch",
    "currency_mismatch",
    "direction_mismatch",
    "date_window_mismatch",
    "no_candidate",
)


class _ExplodingStripStr(str):
    def strip(self, chars: str | None = None) -> str:
        """Prove admission rejects subclasses before caller string logic runs."""
        raise RuntimeError("caller-controlled exception-code strip must not execute")


class ReconciliationDecisionExceptionCodeDomainRedTests(unittest.TestCase):
    """Keep abstention reasons inside the repository-owned exception vocabulary."""

    @staticmethod
    def _abstain(exception_code: object) -> ReconciliationDecision:
        """Build otherwise-valid abstention evidence while varying only its reason."""
        return ReconciliationDecision(
            statement_entry_reference="statement-exception-domain-1",
            decision_code="abstain",
            rule_code=None,
            matched_journal_references=(),
            allocated_amount=Decimal("0"),
            exception_code=exception_code,  # type: ignore[arg-type]
            next_action="Review the unresolved reconciliation evidence.",
        )

    def test_unknown_exact_builtin_exception_code_fails_closed(self) -> None:
        """Callers cannot invent exception categories outside ADR 0054."""
        for value in ("manual_override", "approved", "unexplained_difference"):
            with self.subTest(value=value):
                with self.assertRaisesRegex(
                    ValueError,
                    "exception_code must be a supported reconciliation exception code",
                ):
                    self._abstain(value)

    def test_exception_code_subclass_fails_before_custom_string_behavior(self) -> None:
        """Repository-owned reason semantics cannot delegate to a str subclass."""
        with self.assertRaisesRegex(
            ValueError,
            "exception_code must be a supported reconciliation exception code",
        ):
            self._abstain(_ExplodingStripStr("no_candidate"))

    def test_all_bounded_exception_codes_remain_valid(self) -> None:
        """The guard preserves every exception category currently owned by ADR 0054."""
        for value in _ALLOWED_EXCEPTION_CODES:
            with self.subTest(value=value):
                decision = self._abstain(value)
                self.assertEqual(decision.exception_code, value)
                self.assertEqual(decision.decision_code, "abstain")
                self.assertEqual(decision.allocated_amount, Decimal("0"))


if __name__ == "__main__":
    unittest.main()
