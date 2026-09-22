"""Exact split/aggregate reconciliation allocation proposals.

This module extends deterministic reconciliation matching with many-to-many
allocation planning while keeping every monetary value in exact ``Decimal``.
It returns immutable, tenant- and run-scoped allocations for an operator to
review; it has no authority to post, reverse, or approve a journal (ADR 0054).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Iterable, overload

from .core import AccountingValidationError, _require_currency
from .reconciliation import BookJournalEvidence, StatementEntryEvidence


def _require_exact_positive(value: object, field_name: str) -> None:
    """Reject money that is not a finite, positive exact Decimal."""
    if type(value) is not Decimal or not value.is_finite() or value <= 0:
        raise ValueError(
            f"{field_name} must be a positive exact Decimal. Supply a finite "
            "Decimal greater than zero before reconciliation."
        )


def _require_identity(value: object, field_name: str) -> None:
    """Reject blank identity bindings on reconciliation evidence."""
    if type(value) is not str or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty identity")


def _require_allocation_currency(value: object) -> None:
    """Require allocation currency to use the accounting core's canonical syntax."""
    if type(value) is not str:
        raise ValueError("currency_code must be a three-letter uppercase currency code")
    try:
        _require_currency(value)
    except AccountingValidationError as exc:
        raise ValueError(
            "currency_code must be a three-letter uppercase currency code"
        ) from exc


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
        ):
            _require_identity(getattr(self, field_name), field_name)
        _require_exact_positive(self.allocated_amount, "allocated_amount")
        _require_allocation_currency(self.currency_code)


def propose_split_allocations(
    *,
    statement_entry_reference: str,
    statement_amount: Decimal,
    candidate_journals: Iterable[BookJournalEvidence],
    reconciliation_run_reference: str,
    tenant_account_reference: str,
) -> tuple[ReconciliationAllocation, ...]:
    """Propose one allocation per distinct candidate journal with exact conservation.

    Every candidate must be exact repository-owned ``BookJournalEvidence`` so
    split planning cannot bypass source-evidence admission or execute
    caller-defined subclass behavior. Every candidate journal contributes a
    positive exact Decimal amount, every journal identity appears at most once,
    and all candidates share one currency. The returned allocations sum exactly
    to ``statement_amount``; a duplicate or malformed source population, or a
    candidate set whose total is not exactly that amount, fails closed rather
    than returning reviewable allocation evidence.
    """

    _require_identity(statement_entry_reference, "statement_entry_reference")
    _require_exact_positive(statement_amount, "statement_amount")

    journal_tuple = tuple(candidate_journals)
    if not journal_tuple:
        raise ValueError("at least one candidate journal is required for a split allocation")
    if any(type(journal) is not BookJournalEvidence for journal in journal_tuple):
        raise ValueError(
            "split candidates must be exact BookJournalEvidence. Rebuild candidate "
            "journals from repository-owned posted-journal evidence before planning."
        )
    currency_code = journal_tuple[0].currency_code

    allocations: list[ReconciliationAllocation] = []
    planned_total = Decimal("0")
    seen_journal_references: set[str] = set()
    for journal in journal_tuple:
        _require_identity(journal.journal_reference, "journal_reference")
        if journal.journal_reference in seen_journal_references:
            raise ValueError(
                "split candidates must use distinct journal identities. Remove "
                "duplicate journal evidence before planning a split."
            )
        seen_journal_references.add(journal.journal_reference)
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


@overload
def aggregate_allocations(
    *,
    statement_items: tuple[StatementEntryEvidence],
    reconciliation_run_reference: str,
    tenant_account_reference: str,
    journal_evidence: BookJournalEvidence,
) -> tuple[ReconciliationAllocation]: ...


@overload
def aggregate_allocations(
    *,
    statement_items: tuple[StatementEntryEvidence, ...],
    reconciliation_run_reference: str,
    tenant_account_reference: str,
    journal_evidence: BookJournalEvidence,
) -> tuple[ReconciliationAllocation, ...]: ...


def aggregate_allocations(
    *,
    statement_items: tuple[StatementEntryEvidence, ...],
    reconciliation_run_reference: str,
    tenant_account_reference: str,
    journal_evidence: BookJournalEvidence | None = None,
    journal_total: object | None = None,
    journal_reference: object | None = None,
    currency_code: object | None = None,
) -> tuple[ReconciliationAllocation, ...]:
    """Allocate admitted statement evidence to one admitted journal source.

    Typed callers supply exact repository-owned ``StatementEntryEvidence`` values
    and one exact repository-owned ``BookJournalEvidence``. Aggregate planning
    derives statement identity/amount/currency and journal identity/amount/currency
    only after those source-evidence boundaries, so caller-assembled scalar pairs
    cannot become reviewable allocation evidence merely because their numbers add
    up. All statement sources must use the admitted journal currency.

    The outer statement population must itself be an exact built-in tuple. Each
    statement identity appears at most once, and the statement-side exact Decimal
    total must equal the admitted journal amount exactly. The legacy journal scalar
    keyword names remain runtime-only sentinels so older calls fail through a
    repository-owned domain error rather than silently producing evidence.
    """

    if type(journal_evidence) is not BookJournalEvidence:
        raise ValueError(
            "journal_evidence must be exact BookJournalEvidence. Rebuild the "
            "aggregate from repository-owned posted-journal evidence before planning."
        )
    if any(
        value is not None
        for value in (journal_total, journal_reference, currency_code)
    ):
        raise ValueError(
            "aggregate journal provenance must come from BookJournalEvidence; "
            "journal_total, journal_reference, and currency_code are not accepted "
            "as independent source evidence"
        )

    book_total = journal_evidence.amount
    book_reference = journal_evidence.journal_reference
    book_currency = journal_evidence.currency_code

    if type(statement_items) is not tuple:
        raise ValueError(
            "statement_items must be an immutable built-in tuple. Snapshot the "
            "statement evidence population before planning an aggregate."
        )
    if not statement_items:
        raise ValueError("at least one statement item is required for an aggregate allocation")

    allocations: list[ReconciliationAllocation] = []
    statement_total = Decimal("0")
    seen_statement_references: set[str] = set()
    for statement_item in statement_items:
        if type(statement_item) is not StatementEntryEvidence:
            raise ValueError(
                "each statement item must be exact StatementEntryEvidence. Rebuild "
                "aggregate sources from repository-owned statement evidence before planning."
            )
        statement_reference = statement_item.statement_entry_reference
        amount = statement_item.amount
        if statement_item.currency_code != book_currency:
            raise ValueError(
                "aggregate statement and journal evidence must share one currency. "
                "Reconcile same-currency source evidence before planning allocations."
            )
        if statement_reference in seen_statement_references:
            raise ValueError(
                "aggregate items must use distinct statement identities. Remove "
                "duplicate statement evidence before planning an aggregate."
            )
        seen_statement_references.add(statement_reference)
        _require_exact_positive(amount, f"statement {statement_reference} amount")
        statement_total += amount
        allocations.append(
            ReconciliationAllocation(
                tenant_account_reference=tenant_account_reference,
                reconciliation_run_reference=reconciliation_run_reference,
                statement_entry_reference=statement_reference,
                journal_reference=book_reference,
                allocated_amount=amount,
                currency_code=book_currency,
            )
        )

    if statement_total != book_total:
        raise ValueError(
            "aggregation sides must agree: the statement-side total must equal "
            "the journal-side total exactly. Investigate the unmatched evidence "
            "before recording allocations."
        )

    return tuple(allocations)
