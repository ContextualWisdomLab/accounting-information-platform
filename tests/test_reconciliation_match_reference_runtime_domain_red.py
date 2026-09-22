"""RED contracts for durable reconciliation-match reference admission.

A reviewed decision may bind to a persisted reconciliation match, but that optional
identity is durable control evidence rather than a trusted Python annotation. When
present it must be an exact built-in non-blank string before any caller-defined
string behavior can enter later close-package or persistence comparisons.
"""

from __future__ import annotations

from decimal import Decimal
import unittest

from accounting_information_platform.reconciliation import ReconciliationDecision


class _ExplodingMatchReference(str):
    """Expose caller-controlled whitespace behavior if a string subclass is admitted."""

    def strip(self, chars: str | None = None) -> str:
        """Fail if decision admission executes caller-owned string behavior."""
        raise RuntimeError("caller-controlled reconciliation-match strip must not execute")


class ReconciliationMatchReferenceRuntimeDomainRedTests(unittest.TestCase):
    """Require repository-owned identity semantics for an optional match reference."""

    @staticmethod
    def _match(reference: object) -> ReconciliationDecision:
        """Construct otherwise-valid reviewed match evidence with one varied reference."""
        return ReconciliationDecision(
            statement_entry_reference="statement-001",
            decision_code="match",
            rule_code="provider_reference",
            matched_journal_references=("journal-001",),
            allocated_amount=Decimal("1000.00"),
            exception_code=None,
            next_action="Review the deterministic reconciliation proposal.",
            reconciliation_match_reference=reference,  # type: ignore[arg-type]
        )

    def test_string_subclass_fails_before_custom_strip(self) -> None:
        """A caller-defined string cannot become durable reconciliation-match identity."""
        with self.assertRaisesRegex(ValueError, "reconciliation_match_reference"):
            self._match(_ExplodingMatchReference("match-001"))

    def test_blank_and_non_string_references_fail_closed(self) -> None:
        """Present match references must identify one real persisted match."""
        for value in ("", "   ", [], 123):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, "reconciliation_match_reference"):
                    self._match(value)

    def test_none_and_builtin_non_blank_string_remain_valid(self) -> None:
        """Deterministic proposals stay unbound while reviewed matches may bind explicitly."""
        self.assertIsNone(self._match(None).reconciliation_match_reference)
        self.assertEqual(
            self._match("match-001").reconciliation_match_reference,
            "match-001",
        )


if __name__ == "__main__":
    unittest.main()
