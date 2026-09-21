"""RED contracts for immutable matched-journal populations on decisions.

A frozen reconciliation decision is not immutable evidence when a caller can inject a mutable
container or a mutable-behavior tuple subclass into ``matched_journal_references`` and change
the source population after construction. The runtime boundary therefore requires the exact
built-in tuple container for the journal population.
"""

from __future__ import annotations

import unittest
from decimal import Decimal

from accounting_information_platform.reconciliation import ReconciliationDecision


class _MutableTuplePopulation(tuple[str, ...]):
    """Tuple subclass whose visible population is backed by mutable state."""

    def __new__(cls, values: tuple[str, ...]) -> _MutableTuplePopulation:
        instance = super().__new__(cls, ())
        instance.values = list(values)
        return instance

    def __len__(self) -> int:
        return len(self.values)

    def __iter__(self):  # type: ignore[override]
        return iter(self.values)

    def __bool__(self) -> bool:
        return bool(self.values)

    def __getitem__(self, index):  # type: ignore[override]
        return self.values[index]


class ReconciliationDecisionJournalPopulationDomainRedTests(unittest.TestCase):
    """Reject mutable or malformed decision journal-population containers."""

    @staticmethod
    def _decision(
        *,
        decision_code: str = "match",
        matched_journal_references: object = ("journal-population-1",),
        allocated_amount: Decimal = Decimal("1000.00"),
        exception_code: str | None = None,
        contract_version: str = "reconciliation-decision/v1",
    ) -> ReconciliationDecision:
        return ReconciliationDecision(
            statement_entry_reference="statement-population-1",
            decision_code=decision_code,
            rule_code="provider_reference" if decision_code == "match" else None,
            matched_journal_references=matched_journal_references,  # type: ignore[arg-type]
            allocated_amount=allocated_amount,
            exception_code=exception_code,
            next_action="Review immutable reconciliation evidence; do not post a journal.",
            contract_version=contract_version,
        )

    def test_v1_match_rejects_mutable_list_population(self) -> None:
        """A singleton list cannot become mutable success-shaped decision evidence."""
        with self.assertRaisesRegex(ValueError, "matched_journal_references must be an immutable tuple"):
            self._decision(matched_journal_references=["journal-population-1"])

    def test_v2_match_rejects_mutable_list_population(self) -> None:
        """Reviewed split evidence cannot retain a mutable multi-journal population."""
        with self.assertRaisesRegex(ValueError, "matched_journal_references must be an immutable tuple"):
            self._decision(
                matched_journal_references=["journal-population-1", "journal-population-2"],
                contract_version="reconciliation-decision/v2",
            )

    def test_abstention_rejects_mutable_empty_list_population(self) -> None:
        """An empty mutable list cannot later be changed to attach journals to an abstention."""
        with self.assertRaisesRegex(ValueError, "matched_journal_references must be an immutable tuple"):
            self._decision(
                decision_code="abstain",
                matched_journal_references=[],
                allocated_amount=Decimal("0"),
                exception_code="no_candidate",
            )

    def test_v1_match_rejects_mutable_tuple_subclass_population(self) -> None:
        """Tuple inheritance cannot smuggle mutable backing state into a match."""
        population = _MutableTuplePopulation(("journal-population-1",))
        with self.assertRaisesRegex(ValueError, "matched_journal_references must be an immutable tuple"):
            self._decision(matched_journal_references=population)

    def test_abstention_rejects_mutable_empty_tuple_subclass_population(self) -> None:
        """An empty tuple subclass cannot later attach journals to an abstention."""
        population = _MutableTuplePopulation(())
        with self.assertRaisesRegex(ValueError, "matched_journal_references must be an immutable tuple"):
            self._decision(
                decision_code="abstain",
                matched_journal_references=population,
                allocated_amount=Decimal("0"),
                exception_code="no_candidate",
            )

    def test_tuple_populations_preserve_v1_v2_and_abstention_contracts(self) -> None:
        """The exact built-in tuple contract preserves all supported decision shapes."""
        v1 = self._decision()
        v2 = self._decision(
            matched_journal_references=("journal-population-1", "journal-population-2"),
            contract_version="reconciliation-decision/v2",
        )
        abstain = self._decision(
            decision_code="abstain",
            matched_journal_references=(),
            allocated_amount=Decimal("0"),
            exception_code="no_candidate",
        )

        self.assertEqual(v1.matched_journal_references, ("journal-population-1",))
        self.assertEqual(
            v2.matched_journal_references,
            ("journal-population-1", "journal-population-2"),
        )
        self.assertEqual(abstain.matched_journal_references, ())


if __name__ == "__main__":
    unittest.main()
