"""Exact split/aggregate reconciliation allocation proposals.

This module extends deterministic reconciliation matching with many-to-many
allocation planning while keeping every monetary value in exact ``Decimal``.
It returns immutable, tenant- and run-scoped allocations for an operator to
review; it has no authority to post, reverse, or approve a journal (ADR 0054).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Iterable

from .reconciliation import BookJournalEvidence


def _require_exact_positive(value: object, field_name: str) -> None:
    """Reject money that is not a finite, positive exact Decimal."""
    if not isinstance(value, Decimal) or not value.is_finite() or value <= 0:
        raise ValueError(
            f"{field_name} must be a positive exact Decimal. Supply a finite "
            "Decimal greater than zero before reconciliation."
        )


def _require_identity(value: object, field_name: str) -> None:
    """Reject blank identity bindings on reconciliation evidence."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty identity")


@dataclass(frozen=True, slots=True)
class ReconciliationAllocation:
    """One immutable, tenant- and run-scoped statement-to-journal allocation."""

    tenant_account_reference: str
    reconciliation_run_reference: str
    statement_entry_reference: str
    journal_reference: str
    allocated_amount: Decimal
    currency_code: str

    def __post_init__(self) -> None:
        """Reject non-identity bindings and non-exact monetary evidence."""
        for field_name in (
            "tenant_account_reference",
            "reconciliation_run_reference",
            "statement_entry_reference",
            "journal_reference",
            "currency_code",
        ):
            _require_identity(getattr(self, field_name), field_name)
        _require_exact_positive(self.allocated_amount, "allocated_amount")


def propose_split_allocations(
    *,
    statement_entry_reference: str,
    statement_amount: Decimal,
    candidate_journals: Iterable[BookJournalEvidence],
    reconciliation_run_reference: str,
    tenant_account_reference: str,
) -> tuple[ReconciliationAllocation, ...]:
    """Propose one allocation per candidate that conserves the statement amount.

    Every candidate journal contributes a positive exact Decimal amount and all
    candidates share one currency. The returned allocations sum exactly to
    ``statement_amount``; a candidate set whose total is not exactly that amount
    fails closed rather than returning partial conservation evidence.
    """

    _require_identity(statement_entry_reference, "statement_entry_reference")
    _require_exact_positive(statement_amount, "statement_amount")

    journal_tuple = tuple(candidate_journals)
    if not journal_tuple:
        raise ValueError("at least one candidate journal is required for a split allocation")
    currency_code = journal_tuple[0].currency_code

    allocations: list[ReconciliationAllocation] = []
    planned_total = Decimal("0")
    for journal in journal_tuple:
        _require_exact_positive(journal.amount, f"candidate {journal.journal_reference} amount")
        if journal.currency_code != currency_code:
            raise ValueError(
                "split candidates must share one currency. Supply same-currency "
                "journal evidence before planning a split."
            )
        planned_total += journal.amount
        allocations.append(
            ReconciliationAllocation(
                tenant_account_reference=tenant_account_reference,
                reconciliation_run_reference=reconciliation_run_reference,
                statement_entry_reference=statement_entry_reference,
                journal_reference=journal.journal_reference,
                allocated_amount=journal.amount,
                currency_code=currency_code,
            )
        )

    if planned_total != statement_amount:
        raise ValueError(
            "split allocations must conserve the exact statement amount: the "
            "candidate total may not exceed the statement amount. Re-select "
            "candidates whose exact total equals the statement, then retry."
        )

    return tuple(allocations)


def aggregate_allocations(
    *,
    statement_items: tuple[tuple[str, Decimal], ...],
    journal_total: Decimal,
    reconciliation_run_reference: str,
    tenant_account_reference: str,
    journal_reference: str = "journal-aggregate",
    currency_code: str = "KRW",
) -> tuple[ReconciliationAllocation, ...]:
    """Allocate several statement entries to a journal total conserving both sides.

    Each statement item contributes its own allocation and the returned total
    equals ``journal_total`` exactly. Sides that disagree fail closed instead
    of emitting partial aggregate evidence.
    """

    _require_exact_positive(journal_total, "journal_total")
    _require_identity(journal_reference, "journal_reference")
    _require_identity(currency_code, "currency_code")
    if not statement_items:
        raise ValueError("at least one statement item is required for an aggregate allocation")

    allocations: list[ReconciliationAllocation] = []
    statement_total = Decimal("0")
    for statement_reference, amount in statement_items:
        _require_identity(statement_reference, "statement_entry_reference")
        _require_exact_positive(amount, f"statement {statement_reference} amount")
        statement_total += amount
        allocations.append(
            ReconciliationAllocation(
                tenant_account_reference=tenant_account_reference,
                reconciliation_run_reference=reconciliation_run_reference,
                statement_entry_reference=statement_reference,
                journal_reference=journal_reference,
                allocated_amount=amount,
                currency_code=currency_code,
            )
        )

    if statement_total != journal_total:
        raise ValueError(
            "aggregation sides must agree: the statement-side total must equal "
            "the journal-side total exactly. Investigate the unmatched evidence "
            "before recording allocations."
        )

    return tuple(allocations)