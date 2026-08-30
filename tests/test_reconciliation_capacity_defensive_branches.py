"""Exercise fail-closed reconciliation capacity branches required by exact coverage."""

from __future__ import annotations

import unittest
import unittest.mock as mock
from decimal import Decimal

import accounting_information_platform.reconciliation_close_package as close_package
from accounting_information_platform.reconciliation import ReconciliationDecision
from accounting_information_platform.reconciliation_bridge import (
    BookToBankBridgeInput,
    compute_book_to_bank_bridge,
)
from accounting_information_platform.reconciliation_read_model import (
    ReconciliationAllocationEvidence,
    ReconciliationCloseReviewInput,
    ReconciliationCloseReviewProjection,
    ReconciliationCloseReviewScope,
    ReconciliationReviewedMatch,
    _RECONCILED_CLOSE_REVIEW_NEXT_ACTION,
    _source_capacity_map,
    _validate_reviewed_allocation_conservation,
    _validate_reviewed_match_population,
    _validate_reviewed_population_source_capacity,
)


class ReconciliationCapacityDefensiveBranchTests(unittest.TestCase):
    """Keep every source-capacity failure mode explicit and fail closed."""

    @staticmethod
    def _allocation(
        reference: str,
        source: str,
        amount: str,
        capacity: str | None = None,
    ) -> ReconciliationAllocationEvidence:
        """Build one exact-Decimal allocation fixture."""
        return ReconciliationAllocationEvidence(
            allocation_reference=reference,
            source_reference=source,
            allocated_amount=Decimal(amount),
            source_capacity=Decimal(capacity) if capacity is not None else None,
        )

    @classmethod
    def _match(
        cls,
        *,
        match_reference: str = "match-capacity",
        candidate_statement_reference: str = "statement-anchor",
        candidate_journal_reference: str = "journal-anchor",
        statement_amount: str = "10.00",
        journal_amount: str = "10.00",
        statement_allocations: tuple[ReconciliationAllocationEvidence, ...] | None = None,
        journal_allocations: tuple[ReconciliationAllocationEvidence, ...] | None = None,
    ) -> ReconciliationReviewedMatch:
        """Build one reviewed-match fixture with overridable allocation evidence."""
        return ReconciliationReviewedMatch(
            reconciliation_match_reference=match_reference,
            candidate_reference=f"candidate-{match_reference}",
            candidate_statement_reference=candidate_statement_reference,
            candidate_journal_reference=candidate_journal_reference,
            statement_amount=Decimal(statement_amount),
            journal_amount=Decimal(journal_amount),
            rule_code="provider_reference",
            statement_allocations=(
                cls._allocation("statement-allocation", candidate_statement_reference, "10.00")
                if statement_allocations is None
                else statement_allocations
            ),
            journal_allocations=(
                cls._allocation("journal-allocation", candidate_journal_reference, "10.00")
                if journal_allocations is None
                else journal_allocations
            ),
        )

    @staticmethod
    def _bridge():
        """Build bridge evidence for the reviewed-match population contract."""
        return compute_book_to_bank_bridge(
            BookToBankBridgeInput(
                reconciliation_run_reference="run-capacity",
                statement_population_reference="statement-population-capacity",
                book_population_reference="book-population-capacity",
                currency_code="KRW",
                statement_opening_balance=Decimal("0.00"),
                statement_period_movements=Decimal("10.00"),
                statement_closing_balance=Decimal("10.00"),
                book_opening_balance=Decimal("0.00"),
                posted_cash_book_movements=Decimal("10.00"),
                book_closing_balance=Decimal("10.00"),
                reconciled_book_balance=Decimal("10.00"),
                outstanding_book_items=Decimal("0.00"),
                outstanding_bank_items=Decimal("0.00"),
                tenant_account_reference="tenant-capacity",
                legal_entity_reference="entity-capacity",
                accounting_book_reference="book-capacity",
                bank_account_assignment_reference="bank-capacity",
            )
        )

    @staticmethod
    def _scope() -> ReconciliationCloseReviewScope:
        """Build the accounting scope bound to the bridge fixture."""
        return ReconciliationCloseReviewScope(
            tenant_account_reference="tenant-capacity",
            legal_entity_reference="entity-capacity",
            accounting_book_reference="book-capacity",
            bank_account_assignment_reference="bank-capacity",
            currency_code="KRW",
        )

    def test_source_capacity_map_rejects_each_invalid_capacity_shape(self) -> None:
        """Missing, non-finite, inconsistent, and overspent capacities are distinct failures."""
        with self.subTest("required capacity missing"):
            with self.assertRaisesRegex(ValueError, "requires authoritative source capacity"):
                _source_capacity_map(
                    (self._allocation("a-1", "source-a", "1.00"),),
                    require_capacities=True,
                    label="statement",
                )

        with self.subTest("capacity non-finite"):
            allocation = ReconciliationAllocationEvidence(
                allocation_reference="a-2",
                source_reference="source-a",
                allocated_amount=Decimal("1.00"),
                source_capacity=Decimal("NaN"),
            )
            with self.assertRaisesRegex(ValueError, "positive exact Decimal"):
                _source_capacity_map(
                    (allocation,),
                    require_capacities=False,
                    label="statement",
                )

        with self.subTest("repeated source capacity inconsistent"):
            with self.assertRaisesRegex(ValueError, "consistent for each source"):
                _source_capacity_map(
                    (
                        self._allocation("a-3", "source-a", "1.00", "10.00"),
                        self._allocation("a-4", "source-a", "1.00", "11.00"),
                    ),
                    require_capacities=False,
                    label="statement",
                )

        with self.subTest("source overspent"):
            with self.assertRaisesRegex(ValueError, "exceeds authoritative source capacity"):
                _source_capacity_map(
                    (
                        self._allocation("a-5", "source-a", "6.00", "10.00"),
                        self._allocation("a-6", "source-a", "5.00", "10.00"),
                    ),
                    require_capacities=False,
                    label="statement",
                )

    def test_per_match_conservation_rejects_all_authoritative_capacity_drifts(self) -> None:
        """Per-match evidence cannot exceed or relabel either authoritative anchor capacity."""
        cases = (
            (
                "statement total exceeds candidate",
                self._match(
                    statement_amount="5.00",
                    journal_amount="6.00",
                    statement_allocations=(
                        self._allocation("s-over", "statement-anchor", "6.00"),
                    ),
                    journal_allocations=(
                        self._allocation("j-over", "journal-anchor", "6.00"),
                    ),
                ),
                "statement allocation exceeds",
            ),
            (
                "journal total exceeds candidate",
                self._match(
                    statement_amount="6.00",
                    journal_amount="5.00",
                    statement_allocations=(
                        self._allocation("s-over", "statement-anchor", "6.00"),
                    ),
                    journal_allocations=(
                        self._allocation("j-over", "journal-anchor", "6.00"),
                    ),
                ),
                "journal allocation exceeds",
            ),
            (
                "statement explicit capacity drifts",
                self._match(
                    statement_amount="10.00",
                    journal_amount="10.00",
                    statement_allocations=(
                        self._allocation("s-drift", "statement-anchor", "10.00", "11.00"),
                    ),
                    journal_allocations=(
                        self._allocation("j-ok", "journal-anchor", "10.00", "10.00"),
                    ),
                ),
                "statement source capacity must match",
            ),
            (
                "journal explicit capacity drifts",
                self._match(
                    statement_amount="10.00",
                    journal_amount="10.00",
                    statement_allocations=(
                        self._allocation("s-ok", "statement-anchor", "10.00", "10.00"),
                    ),
                    journal_allocations=(
                        self._allocation("j-drift", "journal-anchor", "10.00", "11.00"),
                    ),
                ),
                "journal source capacity must match",
            ),
            (
                "multi-source anchor capacity drifts",
                self._match(
                    statement_amount="10.00",
                    journal_amount="10.00",
                    statement_allocations=(
                        self._allocation("s-a", "statement-anchor", "5.00", "9.00"),
                        self._allocation("s-b", "statement-other", "5.00", "5.00"),
                    ),
                    journal_allocations=(
                        self._allocation("j-a", "journal-anchor", "10.00", "10.00"),
                    ),
                ),
                "anchor source capacity must match",
            ),
        )
        for label, reviewed_match, pattern in cases:
            with self.subTest(label):
                with self.assertRaisesRegex(ValueError, pattern):
                    _validate_reviewed_allocation_conservation(reviewed_match)

    def test_population_capacity_rejects_anchor_missing_and_cross_match_drift(self) -> None:
        """Complete-population evidence must bind every source to one authoritative capacity."""
        with self.subTest("explicit anchor capacity differs from candidate"):
            match = self._match(
                statement_allocations=(
                    self._allocation("s-anchor", "statement-anchor", "10.00", "11.00"),
                ),
            )
            with self.assertRaisesRegex(ValueError, "source capacity must match"):
                _validate_reviewed_population_source_capacity((match,))

        with self.subTest("non-anchor source omits capacity"):
            match = self._match(
                statement_allocations=(
                    self._allocation("s-anchor", "statement-anchor", "5.00", "10.00"),
                    self._allocation("s-other", "statement-other", "5.00"),
                ),
            )
            with self.assertRaisesRegex(ValueError, "requires authoritative source capacity"):
                _validate_reviewed_population_source_capacity((match,))

        with self.subTest("shared source changes capacity across matches"):
            first = self._match(
                match_reference="match-a",
                journal_amount="10.00",
                journal_allocations=(
                    self._allocation("j-a", "journal-shared", "5.00", "10.00"),
                ),
                candidate_journal_reference="journal-shared",
            )
            second = self._match(
                match_reference="match-b",
                journal_amount="11.00",
                journal_allocations=(
                    self._allocation("j-b", "journal-shared", "5.00", "11.00"),
                ),
                candidate_journal_reference="journal-shared",
            )
            with self.assertRaisesRegex(ValueError, "consistent across reviewed matches"):
                _validate_reviewed_population_source_capacity((first, second))

    def test_reviewed_match_population_rejects_missing_candidate_source_identity(self) -> None:
        """A reviewed match cannot name a candidate source absent from its allocations."""
        reviewed_match = self._match(
            statement_allocations=(
                self._allocation("s-other", "statement-other", "10.00"),
            ),
        )
        decision = ReconciliationDecision(
            statement_entry_reference="statement-other",
            decision_code="match",
            rule_code="provider_reference",
            matched_journal_references=("journal-anchor",),
            allocated_amount=Decimal("10.00"),
            exception_code=None,
            next_action="Review the deterministic proposal; do not post a journal.",
            reconciliation_match_reference="match-capacity",
        )
        projection_input = ReconciliationCloseReviewInput(
            bridge_result=self._bridge(),
            decisions=(decision,),
            expected_statement_entry_references=("statement-other",),
            reviewed_matches=(reviewed_match,),
            scope=self._scope(),
        )
        with self.assertRaisesRegex(ValueError, "represented in their allocations"):
            _validate_reviewed_match_population(
                projection_input,
                match_decisions=(decision,),
            )

    def test_close_package_projection_rejects_missing_candidate_source_identity(self) -> None:
        """Caller-shaped close-package evidence must re-prove candidate source identity."""
        reviewed_match = self._match(
            statement_allocations=(
                self._allocation("s-other", "statement-other", "10.00"),
            ),
        )
        projection = ReconciliationCloseReviewProjection(
            tenant_account_reference="tenant-capacity",
            legal_entity_reference="entity-capacity",
            accounting_book_reference="book-capacity",
            bank_account_assignment_reference="bank-capacity",
            reconciliation_run_reference="run-capacity",
            statement_population_reference="statement-population-capacity",
            book_population_reference="book-population-capacity",
            currency_code="KRW",
            bank_closing_balance=Decimal("10.00"),
            posted_book_cash_balance=Decimal("10.00"),
            reconciled_balance=Decimal("10.00"),
            outstanding_bank_items=Decimal("0.00"),
            outstanding_book_items=Decimal("0.00"),
            unexplained_difference=Decimal("0.00"),
            safely_matchable_candidate_count=1,
            reviewed_match_references=("match-capacity",),
            exception_count=0,
            exception_statement_entry_references=(),
            unexplained_difference_change=None,
            outstanding_bank_items_change=None,
            outstanding_book_items_change=None,
            suitable_for_period_close_review=True,
            next_action=_RECONCILED_CLOSE_REVIEW_NEXT_ACTION,
            reviewed_match_evidence=(reviewed_match,),
        )
        with self.assertRaisesRegex(ValueError, "candidate source identities"):
            close_package._validate_projection(projection)

    def test_snapshot_serializer_fails_closed_if_v2_capacity_is_absent(self) -> None:
        """The serializer itself rejects missing v2 capacity even if its precheck is isolated."""
        reviewed_match = self._match(
            statement_allocations=(
                self._allocation("s-a", "statement-anchor", "5.00", "10.00"),
                self._allocation("s-b", "statement-other", "5.00"),
            ),
        )
        with mock.patch.object(
            close_package,
            "_validate_reviewed_allocation_conservation",
            return_value=None,
        ):
            with self.assertRaisesRegex(ValueError, "version-2 reconciliation snapshot"):
                close_package._reconciliation_match_snapshot_sha256(
                    "tenant-capacity",
                    "run-capacity",
                    reviewed_match,
                )


if __name__ == "__main__":
    unittest.main()
