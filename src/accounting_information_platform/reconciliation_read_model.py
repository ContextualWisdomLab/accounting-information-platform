"""Read-only reconciliation close-review projection and exact-value exports.

This module converts deterministic reconciliation decisions and the exact
book-to-bank bridge into buyer-facing close-review evidence. It has no authority
to approve reconciliation, mutate statement evidence, or post accounting facts.
"""

from __future__ import annotations

import csv
import io
import json
from dataclasses import dataclass
from decimal import Decimal

from .reconciliation import ReconciliationDecision
from .reconciliation_bridge import BookToBankBridgeResult


@dataclass(frozen=True)
class ReconciliationCloseReviewScope:
    """Immutable accounting and bank-account scope for one close-review run."""

    tenant_account_reference: str
    legal_entity_reference: str
    accounting_book_reference: str
    bank_account_assignment_reference: str
    currency_code: str


@dataclass(frozen=True)
class ReconciliationCloseReviewInput:
    """Inputs required to build one read-only reconciliation close-review view."""

    bridge_result: BookToBankBridgeResult
    decisions: tuple[ReconciliationDecision, ...]
    expected_statement_entry_references: tuple[str, ...]
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
    exception_count: int
    exception_statement_entry_references: tuple[str, ...]
    unexplained_difference_change: Decimal | None
    outstanding_bank_items_change: Decimal | None
    outstanding_book_items_change: Decimal | None
    suitable_for_period_close_review: bool
    next_action: str


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
    match_count = sum(
        1 for decision in projection_input.decisions if decision.decision_code == "match"
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
        unexplained_change = (
            bridge.unexplained_difference - preceding.unexplained_difference
        )
        outstanding_bank_change = (
            bridge.outstanding_bank_items - preceding.outstanding_bank_items
        )
        outstanding_book_change = (
            bridge.outstanding_book_items - preceding.outstanding_book_items
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
        next_action = (
            "Attach this exact reconciliation evidence to the period-close review; "
            "the authorized reconciliation review remains a separate control."
        )

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
        safely_matchable_candidate_count=match_count,
        exception_count=len(exceptions),
        exception_statement_entry_references=tuple(
            decision.statement_entry_reference for decision in exceptions
        ),
        unexplained_difference_change=unexplained_change,
        outstanding_bank_items_change=outstanding_bank_change,
        outstanding_book_items_change=outstanding_book_change,
        suitable_for_period_close_review=suitable,
        next_action=next_action,
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


def _projection_mapping(
    projection: ReconciliationCloseReviewProjection,
) -> dict[str, object]:
    """Return a serialization-safe mapping with monetary values as exact strings."""

    return {
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
