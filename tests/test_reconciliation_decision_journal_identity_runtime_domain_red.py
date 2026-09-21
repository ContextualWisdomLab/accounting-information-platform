"""RED contract for exact-string journal identities on reconciliation decisions."""

from __future__ import annotations

import unittest
from decimal import Decimal

from accounting_information_platform.reconciliation import ReconciliationDecision


class _UnhashableStr(str):
    __hash__ = None


class _ExplodingStripStr(str):
    def strip(self, *args: object, **kwargs: object) -> str:
        raise RuntimeError("caller-controlled strip must not execute")


class ReconciliationDecisionJournalIdentityRuntimeDomainRedTests(unittest.TestCase):
    """Keep reviewed journal populations inside the canonical identity runtime domain."""

    @staticmethod
    def _decision(*, journal_references: tuple[object, ...]) -> ReconciliationDecision:
        return ReconciliationDecision(
            statement_entry_reference="statement-journal-runtime-1",
            decision_code="match",
            rule_code="reviewed_split",
            matched_journal_references=journal_references,  # type: ignore[arg-type]
            allocated_amount=Decimal("1000.00"),
            exception_code=None,
            next_action="Review the persisted reconciliation evidence; do not post a journal.",
            contract_version="reconciliation-decision/v2",
        )

    def test_unhashable_string_subclass_fails_before_distinctness_hashing(self) -> None:
        """A hash-hostile string subclass must not leak a raw set-construction TypeError."""
        with self.assertRaisesRegex(
            ValueError,
            "matched_journal_references must contain non-empty identities",
        ):
            self._decision(
                journal_references=(
                    "journal-runtime-1",
                    _UnhashableStr("journal-runtime-2"),
                )
            )

    def test_string_subclass_cannot_execute_custom_strip_behavior(self) -> None:
        """Identity admission must reject subclasses before invoking caller-defined methods."""
        with self.assertRaisesRegex(
            ValueError,
            "matched_journal_references must contain non-empty identities",
        ):
            self._decision(
                journal_references=(
                    "journal-runtime-1",
                    _ExplodingStripStr("journal-runtime-2"),
                )
            )

    def test_distinct_exact_builtin_strings_remain_reviewable(self) -> None:
        """The runtime guard preserves a valid reviewed v2 journal population."""
        decision = self._decision(
            journal_references=("journal-runtime-1", "journal-runtime-2")
        )
        self.assertEqual(
            decision.matched_journal_references,
            ("journal-runtime-1", "journal-runtime-2"),
        )
        self.assertEqual(decision.allocated_amount, Decimal("1000.00"))


if __name__ == "__main__":
    unittest.main()
