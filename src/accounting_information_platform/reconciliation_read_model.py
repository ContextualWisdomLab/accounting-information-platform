"""Read-only reconciliation close-review projection and exact-value exports.

This module converts deterministic reconciliation decisions and the exact
book-to-bank bridge into buyer-facing close-review evidence. It has no authority
to approve reconciliation, mutate statement evidence, or post accounting facts.
"""

from __future__ import annotations

import csv
import io
import json
from dataclasses import dataclass, replace
from decimal import Decimal

from .reconciliation import ReconciliationDecision
from .reconciliation_bridge import BookToBankBridgeResult
from .reconciliation_bridge import _exact_decimal_sum


_RECONCILED_CLOSE_REVIEW_NEXT_ACTION = (
    "Attach this exact reconciliation evidence to the period-close review; "
    "the authorized reconciliation review remains a separate control."
)


@dataclass(frozen=True)
class ReconciliationCloseReviewScope:
    """Immutable accounting and bank-account scope for one close-review run."""

    tenant_account_reference: str
    legal_entity_reference: str
    accounting_book_reference: str
    bank_account_assignment_reference: str
    currency_code: str


@dataclass(frozen=True, slots=True)
class ReconciliationAllocationEvidence:
    """One immutable normalized statement or journal allocation fact."""

    allocation_reference: str
    source_reference: str
    allocated_amount: Decimal
    source_capacity: Decimal | None = None


@dataclass(frozen=True, slots=True)
class ReconciliationReviewedMatch:
    """Complete candidate and normalized allocation facts for one durable match."""

    reconciliation_match_reference: str
    candidate_reference: str
    candidate_statement_reference: str
    candidate_journal_reference: str
    statement_amount: Decimal
    journal_amount: Decimal
    rule_code: str
    statement_allocations: tuple[ReconciliationAllocationEvidence, ...]
    journal_allocations: tuple[ReconciliationAllocationEvidence, ...]


@dataclass(frozen=True)
class ReconciliationCloseReviewInput:
    """Inputs required to build one read-only reconciliation close-review view."""

    bridge_result: BookToBankBridgeResult
    decisions: tuple[ReconciliationDecision, ...]
    expected_statement_entry_references: tuple[str, ...]
    reviewed_matches: tuple[ReconciliationReviewedMatch, ...]
    scope: ReconciliationCloseReviewScope
    preceding_bridge_result: BookToBankBridgeResult | None = None
    preceding_scope: ReconciliationCloseReviewScope | None = None


@dataclass(frozen=True)
class ReconciliationCloseReviewProjection:
    """Exact reconciliation evidence presented to a controller for close review."""

    tenant_account_reference: str
    legal_entity_reference: str
    accounting_book_reference: str
    bank_account_assignment_reference: str
    reconciliation_run_reference: str
    statement_population_reference: str
    book_population_reference: str
    currency_code: str
    bank_closing_balance: Decimal
    posted_book_cash_balance: Decimal
    reconciled_balance: Decimal
    outstanding_bank_items: Decimal
    outstanding_book_items: Decimal
    unexplained_difference: Decimal
    safely_matchable_candidate_count: int
    reviewed_match_references: tuple[str, ...]
    exception_count: int
    exception_statement_entry_references: tuple[str, ...]
    unexplained_difference_change: Decimal | None
    outstanding_bank_items_change: Decimal | None
    outstanding_book_items_change: Decimal | None
    suitable_for_period_close_review: bool
    next_action: str
    reviewed_match_evidence: tuple[ReconciliationReviewedMatch, ...] = ()


def _source_capacity_map(
    allocations: tuple[ReconciliationAllocationEvidence, ...],
    *,
    require_capacities: bool,
    label: str,
) -> dict[str, Decimal]:
    """Validate and return authoritative capacities for normalized allocation sources."""
    capacities: dict[str, Decimal] = {}
    for allocation in allocations:
        capacity = allocation.source_capacity
        if capacity is None:
            if require_capacities:
                raise ValueError(
                    "reviewed multi-source allocation evidence requires authoritative "
                    "source capacity for every allocated source"
                )
            continue
        if not isinstance(capacity, Decimal) or not capacity.is_finite() or capacity <= 0:
            raise ValueError("reviewed source capacity must be a positive exact Decimal")
        existing = capacities.get(allocation.source_reference)
        if existing is not None and existing != capacity:
            raise ValueError(
                f"reviewed {label} source capacity must be consistent for each source"
            )
        capacities[allocation.source_reference] = capacity

    for source_reference, capacity in capacities.items():
        allocated_total = _exact_decimal_sum(
            *(
                allocation.allocated_amount
                for allocation in allocations
                if allocation.source_reference == source_reference
            )
        )
        if allocated_total > capacity:
            raise ValueError(
                f"reviewed {label} allocation exceeds authoritative source capacity"
            )
    return capacities


def _validate_reviewed_allocation_conservation(
    reviewed_match: ReconciliationReviewedMatch,
) -> None:
    """Require exact equality and authoritative per-source allocation capacity."""
    statement_total = _exact_decimal_sum(
        *(allocation.allocated_amount for allocation in reviewed_match.statement_allocations)
    )
    journal_total = _exact_decimal_sum(
        *(allocation.allocated_amount for allocation in reviewed_match.journal_allocations)
    )
    if statement_total != journal_total:
        raise ValueError(
            "reviewed statement and journal allocation totals must match exactly"
        )

    statement_sources = {
        allocation.source_reference for allocation in reviewed_match.statement_allocations
    }
    journal_sources = {
        allocation.source_reference for allocation in reviewed_match.journal_allocations
    }
    require_capacities = len(statement_sources) > 1 or len(journal_sources) > 1
    statement_capacities = _source_capacity_map(
        reviewed_match.statement_allocations,
        require_capacities=require_capacities,
        label="statement",
    )
    journal_capacities = _source_capacity_map(
        reviewed_match.journal_allocations,
        require_capacities=require_capacities,
        label="journal",
    )

    if require_capacities:
        if (
            statement_capacities.get(reviewed_match.candidate_statement_reference)
            != reviewed_match.statement_amount
            or journal_capacities.get(reviewed_match.candidate_journal_reference)
            != reviewed_match.journal_amount
        ):
            raise ValueError(
                "reviewed anchor source capacity must match the database-owned candidate amount"
            )
    else:
        if statement_total > reviewed_match.statement_amount:
            raise ValueError(
                "reviewed statement allocation exceeds authoritative source capacity"
            )
        if journal_total > reviewed_match.journal_amount:
            raise ValueError(
                "reviewed journal allocation exceeds authoritative source capacity"
            )
        if statement_capacities and (
            statement_capacities.get(reviewed_match.candidate_statement_reference)
            != reviewed_match.statement_amount
        ):
            raise ValueError(
                "reviewed statement source capacity must match the database-owned candidate amount"
            )
        if journal_capacities and (
            journal_capacities.get(reviewed_match.candidate_journal_reference)
            != reviewed_match.journal_amount
        ):
            raise ValueError(
                "reviewed journal source capacity must match the database-owned candidate amount"
            )


def _validate_reviewed_population_source_capacity(
    reviewed_matches: tuple[ReconciliationReviewedMatch, ...],
) -> None:
    """Require source-capacity conservation across the complete reviewed population."""

    for label in ("statement", "journal"):
        capacities: dict[str, Decimal] = {}
        allocated_totals: dict[str, Decimal] = {}
        for reviewed_match in reviewed_matches:
            if label == "statement":
                allocations = reviewed_match.statement_allocations
                candidate_reference = reviewed_match.candidate_statement_reference
                candidate_capacity = reviewed_match.statement_amount
            else:
                allocations = reviewed_match.journal_allocations
                candidate_reference = reviewed_match.candidate_journal_reference
                candidate_capacity = reviewed_match.journal_amount

            for allocation in allocations:
                capacity = allocation.source_capacity
                if allocation.source_reference == candidate_reference:
                    if capacity is not None and capacity != candidate_capacity:
                        raise ValueError(
                            f"reviewed {label} source capacity must match the database-owned candidate amount"
                        )
                    capacity = candidate_capacity
                elif capacity is None:
                    raise ValueError(
                        "reviewed multi-source allocation evidence requires authoritative source capacity "
                        "for every allocated source"
                    )

                existing_capacity = capacities.get(allocation.source_reference)
                if existing_capacity is not None and existing_capacity != capacity:
                    raise ValueError(
                        f"reviewed {label} source capacity must be consistent across reviewed matches"
                    )
                capacities[allocation.source_reference] = capacity
                allocated_totals[allocation.source_reference] = _exact_decimal_sum(
                    allocated_totals.get(allocation.source_reference, Decimal("0")),
                    allocation.allocated_amount,
                )

        for source_reference, allocated_total in allocated_totals.items():
            if allocated_total > capacities[source_reference]:
                raise ValueError(
                    f"reviewed {label} allocation exceeds authoritative source capacity across reviewed matches"
                )


def _validate_scope(
    scope: ReconciliationCloseReviewScope,
    bridge: BookToBankBridgeResult,
    *,
    label: str,
) -> None:
    """Require complete scope identity that is immutably bound to bridge evidence."""

    identity_fields = (
        scope.tenant_account_reference,
        scope.legal_entity_reference,
        scope.accounting_book_reference,
        scope.bank_account_assignment_reference,
    )
    if any(not isinstance(value, str) or not value.strip() for value in identity_fields):
        raise ValueError(f"{label} reconciliation scope identity must be non-empty")
    if scope.currency_code != bridge.currency_code:
        raise ValueError(f"{label} reconciliation scope currency must match bridge currency")

    bridge_identity = (
        bridge.tenant_account_reference,
        bridge.legal_entity_reference,
        bridge.accounting_book_reference,
        bridge.bank_account_assignment_reference,
    )
    if any(not isinstance(value, str) or not value.strip() for value in bridge_identity):
        raise ValueError(f"{label} bridge scope identity must be bound")
    if bridge_identity != identity_fields:
        raise ValueError(f"{label} bridge scope must match reconciliation scope")


def _validate_statement_population(
    projection_input: ReconciliationCloseReviewInput,
) -> None:
    """Require exactly one decision for every immutable statement-entry identity."""

    expected = projection_input.expected_statement_entry_references
    if any(not isinstance(value, str) or not value.strip() for value in expected):
        raise ValueError("statement population identities must be non-empty")
    if len(set(expected)) != len(expected):
        raise ValueError("statement population identities must be unique")

    decision_references = tuple(
        decision.statement_entry_reference for decision in projection_input.decisions
    )
    if any(
        not isinstance(value, str) or not value.strip() for value in decision_references
    ):
        raise ValueError("statement population decision identities must be non-empty")
    if (
        len(decision_references) != len(expected)
        or len(set(decision_references)) != len(decision_references)
        or set(decision_references) != set(expected)
    ):
        raise ValueError(
            "statement population decisions must exactly cover the expected statement population"
        )


def _validate_reviewed_match_population(
    projection_input: ReconciliationCloseReviewInput,
    *,
    match_decisions: tuple[ReconciliationDecision, ...],
) -> tuple[ReconciliationReviewedMatch, ...]:
    """Require complete normalized match facts bound to durable decisions."""
    reviewed_matches = projection_input.reviewed_matches
    if not isinstance(reviewed_matches, tuple):
        raise ValueError("reviewed match evidence must be a tuple")
    if any(
        not isinstance(reviewed_match, ReconciliationReviewedMatch)
        for reviewed_match in reviewed_matches
    ):
        raise ValueError("reviewed match evidence must contain structured evidence objects")
    decision_references = tuple(
        decision.reconciliation_match_reference for decision in match_decisions
    )
    if any(
        not isinstance(reference, str)
        or not reference
        or reference.strip() != reference
        for reference in decision_references
    ):
        raise ValueError(
            "reviewed match evidence requires a canonical durable decision identity"
        )
    references = tuple(
        reviewed_match.reconciliation_match_reference for reviewed_match in reviewed_matches
    )
    if any(
        not isinstance(reference, str) or not reference or reference.strip() != reference
        for reference in references
    ):
        raise ValueError("reviewed match evidence identities must be canonical non-empty strings")
    if len(set(references)) != len(references):
        raise ValueError("reviewed match evidence identities must be unique")
    if set(references) != set(decision_references):
        raise ValueError(
            "reviewed match evidence must bind to matching decisions and exactly cover the durable match decision identities"
        )

    decisions_by_match: dict[str, tuple[ReconciliationDecision, ...]] = {}
    for decision in match_decisions:
        assert decision.reconciliation_match_reference is not None
        decisions_by_match.setdefault(decision.reconciliation_match_reference, ())
        decisions_by_match[decision.reconciliation_match_reference] += (decision,)

    normalized_matches: list[ReconciliationReviewedMatch] = []
    for reviewed_match in reviewed_matches:
        if any(
            not isinstance(value, str) or not value or value.strip() != value
            for value in (
                reviewed_match.candidate_reference,
                reviewed_match.candidate_statement_reference,
                reviewed_match.candidate_journal_reference,
                reviewed_match.rule_code,
            )
        ):
            raise ValueError("reviewed match candidate facts must be canonical non-empty strings")
        for field_name in ("statement_amount", "journal_amount"):
            value = getattr(reviewed_match, field_name)
            if not isinstance(value, Decimal) or not value.is_finite() or value <= 0:
                raise ValueError(
                    "reviewed match candidate amounts must be positive exact Decimals"
                )

        normalized_allocations: dict[str, tuple[ReconciliationAllocationEvidence, ...]] = {}
        for field_name in ("statement_allocations", "journal_allocations"):
            allocations = getattr(reviewed_match, field_name)
            if not isinstance(allocations, tuple) or not allocations:
                raise ValueError(
                    "reviewed match allocation populations must be non-empty tuples"
                )
            if any(
                not isinstance(allocation, ReconciliationAllocationEvidence)
                for allocation in allocations
            ):
                raise ValueError(
                    "reviewed match allocation populations must contain structured evidence objects"
                )
            for allocation in allocations:
                if any(
                    not isinstance(value, str) or not value or value.strip() != value
                    for value in (
                        allocation.allocation_reference,
                        allocation.source_reference,
                    )
                ):
                    raise ValueError("reviewed allocation identities must be canonical non-empty strings")
                if (
                    not isinstance(allocation.allocated_amount, Decimal)
                    or not allocation.allocated_amount.is_finite()
                    or allocation.allocated_amount <= 0
                ):
                    raise ValueError(
                        "reviewed allocation amount must be a positive exact Decimal"
                    )
                if allocation.source_capacity is not None and (
                    not isinstance(allocation.source_capacity, Decimal)
                    or not allocation.source_capacity.is_finite()
                    or allocation.source_capacity <= 0
                ):
                    raise ValueError(
                        "reviewed source capacity must be a positive exact Decimal"
                    )
            allocation_references = tuple(
                allocation.allocation_reference for allocation in allocations
            )
            if len(set(allocation_references)) != len(allocation_references):
                raise ValueError("reviewed allocation identities must be unique")
            normalized_allocations[field_name] = tuple(
                sorted(allocations, key=lambda allocation: allocation.allocation_reference)
            )

        statement_allocations = normalized_allocations["statement_allocations"]
        journal_allocations = normalized_allocations["journal_allocations"]
        normalized_match = replace(
            reviewed_match,
            statement_allocations=statement_allocations,
            journal_allocations=journal_allocations,
        )
        _validate_reviewed_allocation_conservation(normalized_match)
        decisions = decisions_by_match[reviewed_match.reconciliation_match_reference]
        statement_sources = {
            allocation.source_reference for allocation in statement_allocations
        }
        journal_sources = {
            allocation.source_reference for allocation in journal_allocations
        }
        if (
            reviewed_match.candidate_statement_reference not in statement_sources
            or reviewed_match.candidate_journal_reference not in journal_sources
        ):
            raise ValueError(
                "reviewed match candidate facts must be represented in their allocations"
            )
        covered_journal_sources: set[str] = set()
        for decision in decisions:
            matching_statement_amount = (
                _exact_decimal_sum(
                    *(
                        allocation.allocated_amount
                        for allocation in statement_allocations
                        if allocation.source_reference == decision.statement_entry_reference
                    ),
                )
                if decision.statement_entry_reference in statement_sources
                else Decimal("0")
            )
            if matching_statement_amount != decision.allocated_amount:
                raise ValueError(
                    "reviewed match evidence must bind every decision to statement allocations"
                )
            decision_journal_sources = set(decision.matched_journal_references)
            if not decision_journal_sources.issubset(journal_sources):
                raise ValueError(
                    "reviewed match evidence must bind every decision to journal allocations"
                )
            covered_journal_sources.update(decision_journal_sources)
        if covered_journal_sources != journal_sources:
            raise ValueError(
                "reviewed match evidence must cover every normalized journal allocation"
            )
        normalized_matches.append(normalized_match)
    normalized_population = tuple(
        sorted(
            normalized_matches,
            key=lambda reviewed_match: reviewed_match.reconciliation_match_reference,
        )
    )
    _validate_reviewed_population_source_capacity(normalized_population)
    return normalized_population


def build_reconciliation_close_review(
    projection_input: ReconciliationCloseReviewInput,
) -> ReconciliationCloseReviewProjection:
    """Build an exact, read-only close-review projection from reconciliation evidence."""

    bridge = projection_input.bridge_result
    _validate_scope(projection_input.scope, bridge, label="current")
    _validate_statement_population(projection_input)

    exceptions = tuple(
        decision
        for decision in projection_input.decisions
        if decision.decision_code != "match"
    )
    match_decisions = tuple(
        decision
        for decision in projection_input.decisions
        if decision.decision_code == "match"
    )
    reviewed_match_evidence = _validate_reviewed_match_population(
        projection_input,
        match_decisions=match_decisions,
    )
    reviewed_match_references = tuple(
        reviewed_match.reconciliation_match_reference
        for reviewed_match in reviewed_match_evidence
    )

    preceding = projection_input.preceding_bridge_result
    preceding_scope = projection_input.preceding_scope
    if preceding is None:
        if preceding_scope is not None:
            raise ValueError(
                "preceding reconciliation scope cannot exist without preceding bridge evidence"
            )
        unexplained_change = None
        outstanding_bank_change = None
        outstanding_book_change = None
    else:
        if preceding_scope is None:
            raise ValueError(
                "preceding reconciliation scope is required for preceding bridge evidence"
            )
        _validate_scope(preceding_scope, preceding, label="preceding")
        if preceding_scope != projection_input.scope:
            raise ValueError(
                "preceding reconciliation scope must match the current accounting and bank scope"
            )
        unexplained_change = _exact_decimal_sum(
            bridge.unexplained_difference,
            preceding.unexplained_difference.copy_negate(),
        )
        outstanding_bank_change = _exact_decimal_sum(
            bridge.outstanding_bank_items,
            preceding.outstanding_bank_items.copy_negate(),
        )
        outstanding_book_change = _exact_decimal_sum(
            bridge.outstanding_book_items,
            preceding.outstanding_book_items.copy_negate(),
        )

    suitable = bridge.status_code == "reconciled" and not exceptions
    if bridge.status_code != "reconciled":
        next_action = (
            "Resolve the exact book-to-bank bridge difference "
            f"{bridge.unexplained_difference} {bridge.currency_code}, then rerun close review."
        )
    elif exceptions:
        next_action = (
            f"Resolve {len(exceptions)} reconciliation exception(s) from the listed "
            "statement evidence, then rerun period-close review."
        )
    else:
        next_action = _RECONCILED_CLOSE_REVIEW_NEXT_ACTION

    return ReconciliationCloseReviewProjection(
        tenant_account_reference=projection_input.scope.tenant_account_reference,
        legal_entity_reference=projection_input.scope.legal_entity_reference,
        accounting_book_reference=projection_input.scope.accounting_book_reference,
        bank_account_assignment_reference=(
            projection_input.scope.bank_account_assignment_reference
        ),
        reconciliation_run_reference=bridge.reconciliation_run_reference,
        statement_population_reference=bridge.statement_population_reference,
        book_population_reference=bridge.book_population_reference,
        currency_code=bridge.currency_code,
        bank_closing_balance=bridge.statement_closing_balance,
        posted_book_cash_balance=bridge.book_closing_balance,
        reconciled_balance=bridge.reconciled_book_balance,
        outstanding_bank_items=bridge.outstanding_bank_items,
        outstanding_book_items=bridge.outstanding_book_items,
        unexplained_difference=bridge.unexplained_difference,
        safely_matchable_candidate_count=len(reviewed_match_evidence),
        reviewed_match_references=reviewed_match_references,
        exception_count=len(exceptions),
        exception_statement_entry_references=tuple(
            decision.statement_entry_reference for decision in exceptions
        ),
        unexplained_difference_change=unexplained_change,
        outstanding_bank_items_change=outstanding_bank_change,
        outstanding_book_items_change=outstanding_book_change,
        suitable_for_period_close_review=suitable,
        next_action=next_action,
        reviewed_match_evidence=reviewed_match_evidence,
    )


def render_reconciliation_close_review_json(
    projection: ReconciliationCloseReviewProjection,
) -> str:
    """Render one close-review projection as deterministic exact-value JSON."""

    return json.dumps(
        _projection_mapping(projection),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def render_reconciliation_close_review_csv(
    projection: ReconciliationCloseReviewProjection,
) -> str:
    """Render one close-review projection as a single-row exact-value CSV export."""

    output = io.StringIO(newline="")
    row = _projection_mapping(projection)
    writer = csv.DictWriter(output, fieldnames=tuple(row))
    writer.writeheader()
    writer.writerow(
        {
            key: (
                "true"
                if value is True
                else "false"
                if value is False
                else json.dumps(
                    value,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
                if key == "reviewed_match_evidence"
                else "|".join(value)
                if isinstance(value, tuple)
                else ""
                if value is None
                else value
            )
            for key, value in row.items()
        }
    )
    return output.getvalue()


def _allocation_mapping(allocation: ReconciliationAllocationEvidence) -> dict[str, str]:
    """Return one exact-value allocation mapping, including bound capacity when present."""
    mapping = {
        "allocation_reference": allocation.allocation_reference,
        "source_reference": allocation.source_reference,
        "allocated_amount": str(allocation.allocated_amount),
    }
    if allocation.source_capacity is not None:
        mapping["source_capacity"] = str(allocation.source_capacity)
    return mapping


def _projection_mapping(
    projection: ReconciliationCloseReviewProjection,
) -> dict[str, object]:
    """Return a serialization-safe mapping with monetary values as exact strings."""
    has_source_capacity = any(
        allocation.source_capacity is not None
        for reviewed_match in projection.reviewed_match_evidence
        for allocation in (
            *reviewed_match.statement_allocations,
            *reviewed_match.journal_allocations,
        )
    )

    return {
        "schema_version": 3 if has_source_capacity else 2,
        "tenant_account_reference": projection.tenant_account_reference,
        "legal_entity_reference": projection.legal_entity_reference,
        "accounting_book_reference": projection.accounting_book_reference,
        "bank_account_assignment_reference": projection.bank_account_assignment_reference,
        "reconciliation_run_reference": projection.reconciliation_run_reference,
        "statement_population_reference": projection.statement_population_reference,
        "book_population_reference": projection.book_population_reference,
        "currency_code": projection.currency_code,
        "bank_closing_balance": str(projection.bank_closing_balance),
        "posted_book_cash_balance": str(projection.posted_book_cash_balance),
        "reconciled_balance": str(projection.reconciled_balance),
        "outstanding_bank_items": str(projection.outstanding_bank_items),
        "outstanding_book_items": str(projection.outstanding_book_items),
        "unexplained_difference": str(projection.unexplained_difference),
        "safely_matchable_candidate_count": projection.safely_matchable_candidate_count,
        "reviewed_match_references": projection.reviewed_match_references,
        "reviewed_match_evidence": tuple(
            {
                "reconciliation_match_reference": reviewed_match.reconciliation_match_reference,
                "candidate_reference": reviewed_match.candidate_reference,
                "candidate_statement_reference": reviewed_match.candidate_statement_reference,
                "candidate_journal_reference": reviewed_match.candidate_journal_reference,
                "statement_amount": str(reviewed_match.statement_amount),
                "journal_amount": str(reviewed_match.journal_amount),
                "rule_code": reviewed_match.rule_code,
                "statement_allocations": tuple(
                    _allocation_mapping(allocation)
                    for allocation in reviewed_match.statement_allocations
                ),
                "journal_allocations": tuple(
                    _allocation_mapping(allocation)
                    for allocation in reviewed_match.journal_allocations
                ),
            }
            for reviewed_match in projection.reviewed_match_evidence
        ),
        "exception_count": projection.exception_count,
        "exception_statement_entry_references": (
            projection.exception_statement_entry_references
        ),
        "unexplained_difference_change": (
            None
            if projection.unexplained_difference_change is None
            else str(projection.unexplained_difference_change)
        ),
        "outstanding_bank_items_change": (
            None
            if projection.outstanding_bank_items_change is None
            else str(projection.outstanding_bank_items_change)
        ),
        "outstanding_book_items_change": (
            None
            if projection.outstanding_book_items_change is None
            else str(projection.outstanding_book_items_change)
        ),
        "suitable_for_period_close_review": projection.suitable_for_period_close_review,
        "next_action": projection.next_action,
    }
