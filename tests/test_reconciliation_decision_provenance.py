"""RED contracts for reconciliation-decision provenance and structural integrity.

A reviewable deterministic match or abstention must carry the immutable statement
entry identity that produced it. Call-site context is not durable audit evidence;
the decision object itself must remain attributable when it is logged, exported,
or later persisted by the authoritative reconciliation workflow. Decision objects
must also fail closed if a caller attempts to construct success-shaped evidence
that violates the deterministic reconciliation contract.
"""

from __future__ import annotations

import unittest
from datetime import date
from decimal import Decimal

from accounting_information_platform.reconciliation import (
    BookJournalEvidence,
    DeterministicMatchPolicy,
    ReconciliationDecision,
    StatementEntryEvidence,
    propose_deterministic_match,
)


class ReconciliationDecisionProvenanceTests(unittest.TestCase):
    """Require proposal decisions to retain source identity and valid structure."""

    def _statement(self) -> StatementEntryEvidence:
        return StatementEntryEvidence(
            statement_entry_reference="statement-provenance-1",
            provider_reference="provider-provenance-1",
            end_to_end_reference=None,
            account_servicer_reference=None,
            amount=Decimal("125000.00"),
            currency_code="KRW",
            credit_debit_code="CRDT",
            booking_date=date(2026, 8, 25),
            value_date=date(2026, 8, 25),
        )

    def _journal(self, *, credit_debit_code: str = "CRDT") -> BookJournalEvidence:
        return BookJournalEvidence(
            journal_reference="journal-provenance-1",
            provider_reference="provider-provenance-1",
            end_to_end_reference=None,
            account_servicer_reference=None,
            amount=Decimal("125000.00"),
            currency_code="KRW",
            credit_debit_code=credit_debit_code,
            accounting_date=date(2026, 8, 25),
        )

    def test_match_decision_retains_statement_entry_identity(self) -> None:
        """A positive proposal remains attributable to immutable statement evidence."""
        decision = propose_deterministic_match(
            self._statement(),
            (self._journal(),),
            DeterministicMatchPolicy(date_window_days=2),
        )

        self.assertEqual(decision.decision_code, "match")
        self.assertEqual(decision.statement_entry_reference, "statement-provenance-1")
        self.assertEqual(decision.matched_journal_references, ("journal-provenance-1",))
        self.assertEqual(decision.contract_version, "reconciliation-decision/v1")

    def test_abstention_retains_statement_entry_identity(self) -> None:
        """An exception remains attributable even when no monetary evidence is consumed."""
        decision = propose_deterministic_match(
            self._statement(),
            (self._journal(credit_debit_code="DBIT"),),
            DeterministicMatchPolicy(date_window_days=2),
        )

        self.assertEqual(decision.decision_code, "abstain")
        self.assertEqual(decision.exception_code, "direction_mismatch")
        self.assertEqual(decision.statement_entry_reference, "statement-provenance-1")
        self.assertEqual(decision.matched_journal_references, ())
        self.assertEqual(decision.allocated_amount, Decimal("0"))
        self.assertEqual(decision.contract_version, "reconciliation-decision/v1")

    def test_default_contract_rejects_multi_journal_match(self) -> None:
        """Existing direct consumers retain the historical singleton match contract."""
        with self.assertRaisesRegex(ValueError, "v1 match decision must reference exactly one journal"):
            ReconciliationDecision(
                statement_entry_reference="statement-provenance-1",
                decision_code="match",
                rule_code="provider_reference",
                matched_journal_references=("journal-provenance-1", "journal-provenance-2"),
                allocated_amount=Decimal("125000.00"),
                exception_code=None,
                next_action="Review the deterministic proposal; do not post a journal.",
            )

    def test_versioned_review_contract_explicitly_allows_multi_journal_match(self) -> None:
        """Reviewed split evidence opts into the version that permits multiple journals."""
        decision = ReconciliationDecision(
            statement_entry_reference="statement-provenance-1",
            decision_code="match",
            rule_code="provider_reference",
            matched_journal_references=("journal-provenance-1", "journal-provenance-2"),
            allocated_amount=Decimal("125000.00"),
            exception_code=None,
            next_action="Review the approved split evidence; do not post a journal.",
            contract_version="reconciliation-decision/v2",
        )
        self.assertEqual(
            decision.matched_journal_references,
            ("journal-provenance-1", "journal-provenance-2"),
        )
        self.assertEqual(decision.contract_version, "reconciliation-decision/v2")

    def test_versioned_review_contract_rejects_empty_journal_population(self) -> None:
        """Reviewed v2 match evidence still requires at least one journal."""
        with self.assertRaisesRegex(
            ValueError,
            "match decision must reference at least one journal",
        ):
            ReconciliationDecision(
                statement_entry_reference="statement-provenance-1",
                decision_code="match",
                rule_code="provider_reference",
                matched_journal_references=(),
                allocated_amount=Decimal("125000.00"),
                exception_code=None,
                next_action="Review the approved split evidence; do not post a journal.",
                contract_version="reconciliation-decision/v2",
            )

    def test_unknown_decision_contract_version_fails_closed(self) -> None:
        """Callers cannot silently invent an incompatible reconciliation decision shape."""
        with self.assertRaisesRegex(ValueError, "contract_version"):
            ReconciliationDecision(
                statement_entry_reference="statement-provenance-1",
                decision_code="match",
                rule_code="provider_reference",
                matched_journal_references=("journal-provenance-1",),
                allocated_amount=Decimal("125000.00"),
                exception_code=None,
                next_action="Review the deterministic proposal; do not post a journal.",
                contract_version="reconciliation-decision/v99",
            )

    def test_direct_match_construction_cannot_forge_success_shaped_evidence(self) -> None:
        """A match must carry one journal, positive exact allocation, and no exception."""
        invalid_matches = (
            {
                "matched_journal_references": (),
                "allocated_amount": Decimal("125000.00"),
                "exception_code": None,
            },
            {
                "matched_journal_references": ("journal-provenance-1",),
                "allocated_amount": Decimal("0"),
                "exception_code": None,
            },
            {
                "matched_journal_references": ("journal-provenance-1",),
                "allocated_amount": Decimal("NaN"),
                "exception_code": None,
            },
            {
                "matched_journal_references": ("journal-provenance-1",),
                "allocated_amount": Decimal("125000.00"),
                "exception_code": "fabricated_exception",
            },
        )

        for fields in invalid_matches:
            with self.subTest(fields=fields):
                with self.assertRaisesRegex(ValueError, "match decision"):
                    ReconciliationDecision(
                        statement_entry_reference="statement-provenance-1",
                        decision_code="match",
                        rule_code="provider_reference",
                        matched_journal_references=fields["matched_journal_references"],
                        allocated_amount=fields["allocated_amount"],
                        exception_code=fields["exception_code"],
                        next_action=(
                            "Review and record this deterministic reconciliation proposal; "
                            "do not post a journal from it."
                        ),
                    )

    def test_direct_abstention_construction_cannot_forge_exception_evidence(self) -> None:
        """An abstention must consume no journal or money and must name its exception."""
        invalid_abstentions = (
            {
                "matched_journal_references": ("journal-provenance-1",),
                "allocated_amount": Decimal("0"),
                "exception_code": "no_candidate",
            },
            {
                "matched_journal_references": (),
                "allocated_amount": Decimal("1"),
                "exception_code": "no_candidate",
            },
            {
                "matched_journal_references": (),
                "allocated_amount": Decimal("0"),
                "exception_code": None,
            },
        )

        for fields in invalid_abstentions:
            with self.subTest(fields=fields):
                with self.assertRaisesRegex(ValueError, "abstain decision"):
                    ReconciliationDecision(
                        statement_entry_reference="statement-provenance-1",
                        decision_code="abstain",
                        rule_code=None,
                        matched_journal_references=fields["matched_journal_references"],
                        allocated_amount=fields["allocated_amount"],
                        exception_code=fields["exception_code"],
                        next_action="Review unmatched evidence and record an explicit exception.",
                    )

    def test_direct_construction_rejects_unknown_decision_code(self) -> None:
        """Only the closed match/abstain decision domain may reach close-review evidence."""
        with self.assertRaisesRegex(ValueError, "decision_code"):
            ReconciliationDecision(
                statement_entry_reference="statement-provenance-1",
                decision_code="approved",
                rule_code=None,
                matched_journal_references=(),
                allocated_amount=Decimal("0"),
                exception_code=None,
                next_action="Do not accept an unknown reconciliation decision state.",
            )


if __name__ == "__main__":
    unittest.main()
