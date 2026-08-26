"""RED contracts for deterministic bank-to-book reconciliation.

These tests deliberately define the first bounded behavior for issue #8 before
production reconciliation code exists. They use exact decimal evidence and
require abstention whenever stable evidence does not identify one safe match.
"""

from __future__ import annotations

import importlib
import importlib.util
import unittest
from datetime import date
from decimal import Decimal


class DeterministicReconciliationRedTests(unittest.TestCase):
    """Pin deterministic matching before persistence or review workflow is added."""

    def _module(self):
        """Load the future reconciliation module only after proving it is product code."""
        spec = importlib.util.find_spec("accounting_information_platform.reconciliation")
        self.assertIsNotNone(
            spec,
            "deterministic reconciliation is not implemented; add the bounded product module",
        )
        return importlib.import_module("accounting_information_platform.reconciliation")

    def _statement(self, module, **overrides):
        values = {
            "statement_entry_reference": "stmt-entry-1",
            "provider_reference": None,
            "end_to_end_reference": None,
            "account_servicer_reference": "ASR-2026-0001",
            "amount": Decimal("25000.00"),
            "currency_code": "KRW",
            "booking_date": date(2026, 8, 24),
            "value_date": date(2026, 8, 24),
        }
        values.update(overrides)
        return module.StatementEntryEvidence(**values)

    def _journal(self, module, reference, **overrides):
        values = {
            "journal_reference": reference,
            "provider_reference": None,
            "end_to_end_reference": None,
            "account_servicer_reference": "ASR-2026-0001",
            "amount": Decimal("25000.00"),
            "currency_code": "KRW",
            "accounting_date": date(2026, 8, 24),
        }
        values.update(overrides)
        return module.BookJournalEvidence(**values)

    def test_exact_servicer_reference_and_exact_money_yield_one_match(self) -> None:
        """A stable servicer identity may match only when amount and currency also agree."""
        module = self._module()
        decision = module.propose_deterministic_match(
            self._statement(module),
            (self._journal(module, "journal-1"),),
            module.DeterministicMatchPolicy(date_window_days=2),
        )
        self.assertEqual(decision.decision_code, "match")
        self.assertEqual(decision.rule_code, "account_servicer_reference")
        self.assertEqual(decision.matched_journal_references, ("journal-1",))
        self.assertEqual(decision.allocated_amount, Decimal("25000.00"))

    def test_duplicate_equal_amount_candidates_without_stable_identity_abstain(self) -> None:
        """Equal-money/date proximity is not enough to choose arbitrarily between duplicates."""
        module = self._module()
        statement = self._statement(
            module,
            account_servicer_reference=None,
            amount=Decimal("10000.00"),
        )
        candidates = (
            self._journal(
                module,
                "journal-a",
                account_servicer_reference=None,
                amount=Decimal("10000.00"),
            ),
            self._journal(
                module,
                "journal-b",
                account_servicer_reference=None,
                amount=Decimal("10000.00"),
            ),
        )
        decision = module.propose_deterministic_match(
            statement,
            candidates,
            module.DeterministicMatchPolicy(date_window_days=2),
        )
        self.assertEqual(decision.decision_code, "abstain")
        self.assertEqual(decision.exception_code, "ambiguous_reference")
        self.assertEqual(
            decision.next_action,
            "Review the competing book candidates and record an explicit reconciliation decision.",
        )
        self.assertEqual(decision.matched_journal_references, ())

    def test_same_stable_reference_with_changed_amount_fails_closed(self) -> None:
        """A stable identity conflict must not fall through to a weaker date/amount rule."""
        module = self._module()
        decision = module.propose_deterministic_match(
            self._statement(module, amount=Decimal("25000.00")),
            (
                self._journal(
                    module,
                    "journal-1",
                    amount=Decimal("24999.00"),
                ),
            ),
            module.DeterministicMatchPolicy(date_window_days=2),
        )
        self.assertEqual(decision.decision_code, "abstain")
        self.assertEqual(decision.exception_code, "amount_mismatch")
        self.assertEqual(decision.matched_journal_references, ())

    def test_currency_mismatch_never_consumes_monetary_evidence(self) -> None:
        """Reference equality cannot override an exact currency mismatch."""
        module = self._module()
        decision = module.propose_deterministic_match(
            self._statement(module, currency_code="KRW"),
            (self._journal(module, "journal-1", currency_code="USD"),),
            module.DeterministicMatchPolicy(date_window_days=2),
        )
        self.assertEqual(decision.decision_code, "abstain")
        self.assertEqual(decision.exception_code, "currency_mismatch")
        self.assertEqual(decision.allocated_amount, Decimal("0"))

    def test_no_stable_reference_can_use_exact_money_and_bounded_date_only_when_unique(self) -> None:
        """The weaker exact-money/date rule is permitted only for one unambiguous candidate."""
        module = self._module()
        statement = self._statement(module, account_servicer_reference=None)
        candidate = self._journal(
            module,
            "journal-unique",
            account_servicer_reference=None,
            accounting_date=date(2026, 8, 25),
        )
        decision = module.propose_deterministic_match(
            statement,
            (candidate,),
            module.DeterministicMatchPolicy(date_window_days=2),
        )
        self.assertEqual(decision.decision_code, "match")
        self.assertEqual(decision.rule_code, "exact_money_bounded_date")
        self.assertEqual(decision.matched_journal_references, ("journal-unique",))

    def test_out_of_window_candidate_abstains_with_next_action(self) -> None:
        """A money-equal candidate beyond policy cutoff becomes an explicit review exception."""
        module = self._module()
        statement = self._statement(module, account_servicer_reference=None)
        candidate = self._journal(
            module,
            "journal-late",
            account_servicer_reference=None,
            accounting_date=date(2026, 9, 1),
        )
        decision = module.propose_deterministic_match(
            statement,
            (candidate,),
            module.DeterministicMatchPolicy(date_window_days=2),
        )
        self.assertEqual(decision.decision_code, "abstain")
        self.assertEqual(decision.exception_code, "date_window_mismatch")
        self.assertTrue(decision.next_action)
        self.assertEqual(decision.matched_journal_references, ())

    def test_duplicate_stable_reference_abstains_instead_of_choosing_a_journal(self) -> None:
        """A duplicated strong identity remains an exception even when exact money agrees."""
        module = self._module()
        decision = module.propose_deterministic_match(
            self._statement(module),
            (
                self._journal(module, "journal-a"),
                self._journal(module, "journal-b"),
            ),
            module.DeterministicMatchPolicy(date_window_days=2),
        )
        self.assertEqual(decision.decision_code, "abstain")
        self.assertEqual(decision.exception_code, "ambiguous_reference")
        self.assertEqual(decision.matched_journal_references, ())

    def test_no_exact_money_candidate_abstains_with_review_action(self) -> None:
        """Absence of an exact-money candidate never becomes an inferred match."""
        module = self._module()
        statement = self._statement(module, account_servicer_reference=None)
        candidate = self._journal(
            module,
            "journal-other",
            account_servicer_reference=None,
            amount=Decimal("24999.00"),
        )
        decision = module.propose_deterministic_match(
            statement,
            (candidate,),
            module.DeterministicMatchPolicy(date_window_days=2),
        )
        self.assertEqual(decision.decision_code, "abstain")
        self.assertEqual(decision.exception_code, "no_candidate")
        self.assertTrue(decision.next_action)
        self.assertEqual(decision.allocated_amount, Decimal("0"))


if __name__ == "__main__":
    unittest.main()
