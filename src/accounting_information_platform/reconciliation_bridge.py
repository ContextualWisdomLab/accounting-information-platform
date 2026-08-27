"""Pure exact-value book-to-bank reconciliation bridge projection.

The bridge proves source equations and an exact book-to-bank difference.  It is
read-only evidence: it cannot post or reverse journals, approve reconciliation,
or mutate immutable statement evidence.
"""

from __future__ import annotations

from decimal import Decimal
from typing import NamedTuple


class BookToBankBridgeInput(NamedTuple):
    """Immutable source populations and exact values required by one bridge run."""

    reconciliation_run_reference: str
    statement_population_reference: str
    book_population_reference: str
    currency_code: str
    statement_opening_balance: Decimal
    statement_period_movements: Decimal
    statement_closing_balance: Decimal
    book_opening_balance: Decimal
    posted_cash_book_movements: Decimal
    book_closing_balance: Decimal
    reconciled_book_balance: Decimal
    outstanding_book_items: Decimal
    outstanding_bank_items: Decimal


class BookToBankBridgeResult(NamedTuple):
    """Exact bridge outcome with immutable population provenance and next action."""

    reconciliation_run_reference: str
    statement_population_reference: str
    book_population_reference: str
    currency_code: str
    statement_opening_balance: Decimal
    statement_period_movements: Decimal
    statement_closing_balance: Decimal
    book_opening_balance: Decimal
    posted_cash_book_movements: Decimal
    book_closing_balance: Decimal
    reconciled_book_balance: Decimal
    outstanding_book_items: Decimal
    outstanding_bank_items: Decimal
    bridge_balance: Decimal
    unexplained_difference: Decimal
    status_code: str
    exception_code: str | None
    next_action: str


_BRIDGE_MONEY_FIELDS = (
    "statement_opening_balance",
    "statement_period_movements",
    "statement_closing_balance",
    "book_opening_balance",
    "posted_cash_book_movements",
    "book_closing_balance",
    "reconciled_book_balance",
    "outstanding_book_items",
    "outstanding_bank_items",
)


def _validate_bridge_money(bridge_input: BookToBankBridgeInput) -> None:
    """Reject non-Decimal or non-finite monetary bridge evidence before arithmetic."""
    for field_name in _BRIDGE_MONEY_FIELDS:
        value = getattr(bridge_input, field_name)
        if not isinstance(value, Decimal) or not value.is_finite():
            raise ValueError(f"{field_name} must be a finite Decimal")


def _result(
    bridge_input: BookToBankBridgeInput,
    *,
    bridge_balance: Decimal,
    unexplained_difference: Decimal,
    status_code: str,
    exception_code: str | None,
    next_action: str,
) -> BookToBankBridgeResult:
    """Build one immutable result without changing accounting or statement facts."""
    return BookToBankBridgeResult(
        reconciliation_run_reference=bridge_input.reconciliation_run_reference,
        statement_population_reference=bridge_input.statement_population_reference,
        book_population_reference=bridge_input.book_population_reference,
        currency_code=bridge_input.currency_code,
        statement_opening_balance=bridge_input.statement_opening_balance,
        statement_period_movements=bridge_input.statement_period_movements,
        statement_closing_balance=bridge_input.statement_closing_balance,
        book_opening_balance=bridge_input.book_opening_balance,
        posted_cash_book_movements=bridge_input.posted_cash_book_movements,
        book_closing_balance=bridge_input.book_closing_balance,
        reconciled_book_balance=bridge_input.reconciled_book_balance,
        outstanding_book_items=bridge_input.outstanding_book_items,
        outstanding_bank_items=bridge_input.outstanding_bank_items,
        bridge_balance=bridge_balance,
        unexplained_difference=unexplained_difference,
        status_code=status_code,
        exception_code=exception_code,
        next_action=next_action,
    )


def compute_book_to_bank_bridge(
    bridge_input: BookToBankBridgeInput,
) -> BookToBankBridgeResult:
    """Prove statement, book, and bridge equations with exact ``Decimal`` values."""
    _validate_bridge_money(bridge_input)

    expected_statement_closing = (
        bridge_input.statement_opening_balance
        + bridge_input.statement_period_movements
    )
    expected_book_closing = (
        bridge_input.book_opening_balance + bridge_input.posted_cash_book_movements
    )
    bridge_balance = (
        bridge_input.reconciled_book_balance
        + bridge_input.outstanding_book_items
        - bridge_input.outstanding_bank_items
    )

    if expected_statement_closing != bridge_input.statement_closing_balance:
        difference = expected_statement_closing - bridge_input.statement_closing_balance
        return _result(
            bridge_input,
            bridge_balance=bridge_balance,
            unexplained_difference=difference,
            status_code="not_reconciled",
            exception_code="statement_balance_mismatch",
            next_action=(
                "Review the immutable statement population: opening balance plus "
                f"period movements differs from statement closing balance by {difference} "
                f"{bridge_input.currency_code}. Correct the source evidence or mapping, "
                "then rerun reconciliation."
            ),
        )

    if expected_book_closing != bridge_input.book_closing_balance:
        difference = expected_book_closing - bridge_input.book_closing_balance
        return _result(
            bridge_input,
            bridge_balance=bridge_balance,
            unexplained_difference=difference,
            status_code="not_reconciled",
            exception_code="book_balance_mismatch",
            next_action=(
                "Review the immutable posted-book population: opening balance plus "
                f"posted cash movements differs from book closing balance by {difference} "
                f"{bridge_input.currency_code}. Correct the book population selection, "
                "then rerun reconciliation."
            ),
        )

    unexplained_difference = bridge_balance - bridge_input.statement_closing_balance
    if unexplained_difference != Decimal("0"):
        return _result(
            bridge_input,
            bridge_balance=bridge_balance,
            unexplained_difference=unexplained_difference,
            status_code="not_reconciled",
            exception_code="bridge_difference",
            next_action=(
                "Review outstanding book and bank items: the exact book-to-bank bridge "
                f"differs by {unexplained_difference} {bridge_input.currency_code}. "
                "Resolve the unmatched evidence or record an explicit reviewed exception "
                "before period-close evidence is accepted."
            ),
        )

    return _result(
        bridge_input,
        bridge_balance=bridge_balance,
        unexplained_difference=Decimal("0"),
        status_code="reconciled",
        exception_code=None,
        next_action=(
            "Attach this exact book-to-bank bridge to period-close evidence. This "
            "projection does not post journals or approve reconciliation."
        ),
    )
