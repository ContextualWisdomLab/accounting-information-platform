"""Deterministic proposal-only bank-to-book reconciliation.

The functions in this module never mutate statement evidence or accounting facts.
They produce a deterministic match proposal or an explicit abstention for later
authorized review. Monetary comparisons use :class:`decimal.Decimal` values
without coercion or rounding, and bank-movement direction remains explicit.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Iterable


_CREDIT_DEBIT_CODES = frozenset({"CRDT", "DBIT"})


def _require_credit_debit_code(value: str) -> None:
    if value not in _CREDIT_DEBIT_CODES:
        raise ValueError(
            "credit_debit_code must be CRDT or DBIT. Normalize the source movement direction before reconciliation."
        )


def _require_positive_exact_decimal(value: object) -> None:
    """Reject monetary evidence that is not a finite, strictly positive Decimal."""
    if not isinstance(value, Decimal) or not value.is_finite() or value <= 0:
        raise ValueError(
            "amount must be a positive exact Decimal. Supply a finite Decimal greater than zero before reconciliation."
        )


@dataclass(frozen=True, slots=True)
class StatementEntryEvidence:
    """Immutable normalized statement evidence considered for reconciliation."""

    statement_entry_reference: str
    provider_reference: str | None
    end_to_end_reference: str | None
    account_servicer_reference: str | None
    amount: Decimal
    currency_code: str
    credit_debit_code: str
    booking_date: date
    value_date: date

    def __post_init__(self) -> None:
        """Reject non-canonical money and movement direction before matching."""
        _require_positive_exact_decimal(self.amount)
        _require_credit_debit_code(self.credit_debit_code)


@dataclass(frozen=True, slots=True)
class BookJournalEvidence:
    """Read-only posted-journal evidence eligible for deterministic matching."""

    journal_reference: str
    provider_reference: str | None
    end_to_end_reference: str | None
    account_servicer_reference: str | None
    amount: Decimal
    currency_code: str
    credit_debit_code: str
    accounting_date: date

    def __post_init__(self) -> None:
        """Reject non-canonical money and movement direction before matching."""
        _require_positive_exact_decimal(self.amount)
        _require_credit_debit_code(self.credit_debit_code)


@dataclass(frozen=True, slots=True)
class DeterministicMatchPolicy:
    """Bound the weak exact-money/date rule used after strong identities fail."""

    date_window_days: int

    def __post_init__(self) -> None:
        """Reject invalid date-window configuration before matching evidence."""
        if (
            isinstance(self.date_window_days, bool)
            or not isinstance(self.date_window_days, int)
            or self.date_window_days < 0
        ):
            raise ValueError(
                "date_window_days must be a non-negative integer. Supply zero or a whole number of days before reconciliation."
            )


@dataclass(frozen=True, slots=True)
class ReconciliationDecision:
    """Return a reviewable match proposal or an explicit fail-closed abstention."""

    statement_entry_reference: str
    decision_code: str
    rule_code: str | None
    matched_journal_references: tuple[str, ...]
    allocated_amount: Decimal
    exception_code: str | None
    next_action: str

    def __post_init__(self) -> None:
        """Reject forged success- or exception-shaped reconciliation evidence."""
        if self.decision_code == "match":
            if len(self.matched_journal_references) != 1:
                raise ValueError(
                    "match decision must reference exactly one journal. Rebuild the deterministic proposal from source evidence."
                )
            try:
                _require_positive_exact_decimal(self.allocated_amount)
            except ValueError as exc:
                raise ValueError(
                    "match decision allocated_amount must be a positive exact Decimal. Rebuild the deterministic proposal from source evidence."
                ) from exc
            if self.exception_code is not None:
                raise ValueError(
                    "match decision cannot carry an exception_code. Rebuild the deterministic proposal from source evidence."
                )
        elif self.decision_code == "abstain":
            if self.matched_journal_references:
                raise ValueError(
                    "abstain decision cannot reference a matched journal. Review unmatched evidence and record an explicit exception."
                )
            if (
                not isinstance(self.allocated_amount, Decimal)
                or not self.allocated_amount.is_finite()
                or self.allocated_amount != 0
            ):
                raise ValueError(
                    "abstain decision allocated_amount must be exactly zero Decimal. Review unmatched evidence and record an explicit exception."
                )
            if not isinstance(self.exception_code, str) or not self.exception_code.strip():
                raise ValueError(
                    "abstain decision requires an exception_code. Review unmatched evidence and record an explicit exception."
                )
        else:
            raise ValueError(
                "reconciliation decision_code must be match or abstain. Rebuild the deterministic proposal from source evidence."
            )


_STRONG_REFERENCE_RULES = (
    ("provider_reference", "provider_reference"),
    ("end_to_end_reference", "end_to_end_reference"),
    ("account_servicer_reference", "account_servicer_reference"),
)
_DIRECTION_MISMATCH_ACTION = (
    "Verify whether the bank movement is a credit or debit before recording a reconciliation decision."
)


def _strong_reference(statement: StatementEntryEvidence) -> tuple[str, str, str] | None:
    for field_name, rule_code in _STRONG_REFERENCE_RULES:
        value = getattr(statement, field_name)
        if value:
            return field_name, rule_code, value
    return None


def _abstain(
    statement: StatementEntryEvidence,
    exception_code: str,
    next_action: str,
) -> ReconciliationDecision:
    return ReconciliationDecision(
        statement_entry_reference=statement.statement_entry_reference,
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
        statement_entry_reference=statement.statement_entry_reference,
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
    rule. A strong-identity conflict never falls through to a weaker rule. Amount,
    currency, and credit/debit direction must agree before any match can be
    proposed. When no strong identity is available, exactly one same-direction,
    same-currency, same-amount journal within ``date_window_days`` may be proposed.
    Every ambiguity or mismatch is an explicit exception with an operator next
    action.
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
        if not reference_candidates:
            return _abstain(
                statement,
                "no_candidate",
                "Review unmatched statement evidence and create an authorized exception or adjusting-journal proposal if required.",
            )
        if len(reference_candidates) > 1:
            return _abstain(
                statement,
                "ambiguous_reference",
                "Review the competing book candidates and record an explicit reconciliation decision.",
            )
        candidate = reference_candidates[0]
        if candidate.currency_code != statement.currency_code:
            return _abstain(
                statement,
                "currency_mismatch",
                "Verify the statement and book currencies before recording a reconciliation decision.",
            )
        if candidate.amount != statement.amount:
            return _abstain(
                statement,
                "amount_mismatch",
                "Verify the exact statement and journal amounts before recording a reconciliation decision.",
            )
        if candidate.credit_debit_code != statement.credit_debit_code:
            return _abstain(statement, "direction_mismatch", _DIRECTION_MISMATCH_ACTION)
        return _match(statement, candidate, rule_code)

    exact_money_candidates = tuple(
        candidate
        for candidate in candidate_tuple
        if candidate.currency_code == statement.currency_code
        and candidate.amount == statement.amount
    )
    same_direction_candidates = tuple(
        candidate
        for candidate in exact_money_candidates
        if candidate.credit_debit_code == statement.credit_debit_code
    )
    in_window_candidates = tuple(
        candidate
        for candidate in same_direction_candidates
        if abs((candidate.accounting_date - statement.booking_date).days)
        <= policy.date_window_days
    )
    if len(in_window_candidates) == 1:
        return _match(statement, in_window_candidates[0], "exact_money_bounded_date")
    if len(in_window_candidates) > 1:
        return _abstain(
            statement,
            "ambiguous_reference",
            "Review the competing book candidates and record an explicit reconciliation decision.",
        )
    if same_direction_candidates:
        return _abstain(
            statement,
            "date_window_mismatch",
            "Review the statement and journal dates or document an explicit reconciliation exception.",
        )
    if exact_money_candidates:
        return _abstain(statement, "direction_mismatch", _DIRECTION_MISMATCH_ACTION)
    return _abstain(
        statement,
        "no_candidate",
        "Review unmatched statement evidence and create an authorized exception or adjusting-journal proposal if required.",
    )
