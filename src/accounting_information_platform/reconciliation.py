"""Deterministic proposal-only bank-to-book reconciliation.

The functions in this module never mutate statement evidence or accounting facts.
They produce a deterministic match proposal or an explicit abstention for later
authorized review. Monetary comparisons use :class:`decimal.Decimal` values
without coercion or rounding.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Iterable


@dataclass(frozen=True, slots=True)
class StatementEntryEvidence:
    """Immutable normalized statement evidence considered for reconciliation."""

    statement_entry_reference: str
    provider_reference: str | None
    end_to_end_reference: str | None
    account_servicer_reference: str | None
    amount: Decimal
    currency_code: str
    booking_date: date
    value_date: date


@dataclass(frozen=True, slots=True)
class BookJournalEvidence:
    """Read-only posted-journal evidence eligible for deterministic matching."""

    journal_reference: str
    provider_reference: str | None
    end_to_end_reference: str | None
    account_servicer_reference: str | None
    amount: Decimal
    currency_code: str
    accounting_date: date


@dataclass(frozen=True, slots=True)
class DeterministicMatchPolicy:
    """Bound the weak exact-money/date rule used after strong identities fail."""

    date_window_days: int


@dataclass(frozen=True, slots=True)
class ReconciliationDecision:
    """Return a reviewable match proposal or an explicit fail-closed abstention."""

    decision_code: str
    rule_code: str | None
    matched_journal_references: tuple[str, ...]
    allocated_amount: Decimal
    exception_code: str | None
    next_action: str


_STRONG_REFERENCE_RULES = (
    ("provider_reference", "provider_reference"),
    ("end_to_end_reference", "end_to_end_reference"),
    ("account_servicer_reference", "account_servicer_reference"),
)


def _strong_reference(statement: StatementEntryEvidence) -> tuple[str, str, str] | None:
    for field_name, rule_code in _STRONG_REFERENCE_RULES:
        value = getattr(statement, field_name)
        if value:
            return field_name, rule_code, value
    return None


def _abstain(exception_code: str, next_action: str) -> ReconciliationDecision:
    return ReconciliationDecision(
        decision_code="abstain",
        rule_code=None,
        matched_journal_references=(),
        allocated_amount=Decimal("0"),
        exception_code=exception_code,
        next_action=next_action,
    )


def _match(
    statement: StatementEntryEvidence,
    candidate: BookJournalEvidence,
    rule_code: str,
) -> ReconciliationDecision:
    return ReconciliationDecision(
        decision_code="match",
        rule_code=rule_code,
        matched_journal_references=(candidate.journal_reference,),
        allocated_amount=statement.amount,
        exception_code=None,
        next_action="Review and record this deterministic reconciliation proposal; do not post a journal from it.",
    )


def propose_deterministic_match(
    statement: StatementEntryEvidence,
    candidates: Iterable[BookJournalEvidence],
    policy: DeterministicMatchPolicy,
) -> ReconciliationDecision:
    """Propose one deterministic match or abstain without mutating accounting facts.

    Strong statement identities take precedence over the weaker exact-money/date
    rule. A strong-identity conflict never falls through to a weaker rule. When no
    strong identity is available, exactly one same-currency, same-amount journal
    within ``date_window_days`` may be proposed. Every ambiguity or mismatch is an
    explicit exception with an operator next action.
    """

    candidate_tuple = tuple(candidates)
    strong_reference = _strong_reference(statement)
    if strong_reference is not None:
        field_name, rule_code, reference_value = strong_reference
        reference_candidates = tuple(
            candidate
            for candidate in candidate_tuple
            if getattr(candidate, field_name) == reference_value
        )
        if len(reference_candidates) != 1:
            return _abstain(
                "ambiguous_reference",
                "Review the competing book candidates and record an explicit reconciliation decision.",
            )
        candidate = reference_candidates[0]
        if candidate.currency_code != statement.currency_code:
            return _abstain(
                "currency_mismatch",
                "Verify the statement and book currencies before recording a reconciliation decision.",
            )
        if candidate.amount != statement.amount:
            return _abstain(
                "amount_mismatch",
                "Verify the exact statement and journal amounts before recording a reconciliation decision.",
            )
        return _match(statement, candidate, rule_code)

    exact_money_candidates = tuple(
        candidate
        for candidate in candidate_tuple
        if candidate.currency_code == statement.currency_code
        and candidate.amount == statement.amount
    )
    in_window_candidates = tuple(
        candidate
        for candidate in exact_money_candidates
        if abs((candidate.accounting_date - statement.booking_date).days)
        <= policy.date_window_days
    )
    if len(in_window_candidates) == 1:
        return _match(statement, in_window_candidates[0], "exact_money_bounded_date")
    if len(in_window_candidates) > 1:
        return _abstain(
            "ambiguous_reference",
            "Review the competing book candidates and record an explicit reconciliation decision.",
        )
    if exact_money_candidates:
        return _abstain(
            "date_window_mismatch",
            "Review the statement and journal dates or document an explicit reconciliation exception.",
        )
    return _abstain(
        "no_candidate",
        "Review unmatched statement evidence and create an authorized exception or adjusting-journal proposal if required.",
    )
