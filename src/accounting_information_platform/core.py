"""Deterministic accounting proposal validation and posting reference core.

The module intentionally contains no database or network integration.  It
provides the executable accounting invariants that PostgreSQL repositories and
HTTP adapters must preserve: exact decimals, balanced journals, idempotent
posting, append-only reversal, tenant scope, open-period control, and
source-to-posting provenance.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from types import MappingProxyType
from typing import Mapping


_CODE_PATTERN = re.compile(r"^[a-z][a-z0-9_]{1,63}$")
_CURRENCY_PATTERN = re.compile(r"^[A-Z]{3}$")
_DECIMAL_PATTERN = re.compile(r"^(0|[1-9][0-9]*)(\.[0-9]{1,6})?$")
_HASH_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
_REFERENCE_PATTERN = re.compile(r"^urn:cwl:[A-Za-z0-9_:.\-]+$")


class AccountingValidationError(ValueError):
    """Raised when an accounting fact violates a deterministic invariant."""


class IdempotencyConflictError(AccountingValidationError):
    """Raised when one idempotency key is reused with a different payload."""


@dataclass(frozen=True, slots=True)
class JournalLineProposal:
    """One non-negative debit or credit line proposed by an upstream system."""

    line_number: int
    account_role_code: str
    debit_amount: Decimal | str
    credit_amount: Decimal | str

    def __post_init__(self) -> None:
        """Normalize exact decimals and require exactly one positive side."""
        if self.line_number < 1:
            raise AccountingValidationError("line number must be positive")
        _require_code(self.account_role_code, "account role code")
        debit_amount = _parse_amount(self.debit_amount)
        credit_amount = _parse_amount(self.credit_amount)
        if (debit_amount > 0) == (credit_amount > 0):
            raise AccountingValidationError(
                "journal line must contain exactly one positive debit or credit amount"
            )
        object.__setattr__(self, "debit_amount", debit_amount)
        object.__setattr__(self, "credit_amount", credit_amount)


@dataclass(frozen=True, slots=True)
class JournalProposal:
    """Balanced, source-addressable journal proposal awaiting policy resolution."""

    proposal_id: str
    proposal_contract_version: int
    idempotency_key: str
    tenant_reference: str
    legal_entity_reference: str
    intended_book_role_code: str
    transaction_currency: str
    transaction_date: date
    accounting_date: date
    source_payload_hash: str
    source_event_references: tuple[str, ...]
    lines: tuple[JournalLineProposal, ...]

    def __post_init__(self) -> None:
        """Validate identity, provenance, exact balancing, and line uniqueness."""
        if not self.proposal_id or self.proposal_contract_version < 1:
            raise AccountingValidationError("proposal identity and contract version are required")
        if not self.idempotency_key:
            raise AccountingValidationError("idempotency key is required")
        _require_reference(self.tenant_reference, "tenant reference")
        _require_reference(self.legal_entity_reference, "legal entity reference")
        _require_code(self.intended_book_role_code, "intended book role code")
        _require_currency(self.transaction_currency)
        if _HASH_PATTERN.fullmatch(self.source_payload_hash) is None:
            raise AccountingValidationError("source payload hash must be canonical sha256")
        if not self.source_event_references:
            raise AccountingValidationError("at least one source event reference is required")
        for reference in self.source_event_references:
            _require_reference(reference, "source event reference")
        if len(self.lines) < 2:
            raise AccountingValidationError("journal proposal requires at least two lines")
        line_numbers = tuple(line.line_number for line in self.lines)
        if len(set(line_numbers)) != len(line_numbers):
            raise AccountingValidationError("journal line numbers must be unique")
        debit_total = sum((line.debit_amount for line in self.lines), Decimal("0"))
        credit_total = sum((line.credit_amount for line in self.lines), Decimal("0"))
        if debit_total != credit_total:
            raise AccountingValidationError("journal proposal must balance")

    @property
    def debit_total(self) -> Decimal:
        """Return the exact total proposed debit amount."""
        return sum((line.debit_amount for line in self.lines), Decimal("0"))

    @property
    def credit_total(self) -> Decimal:
        """Return the exact total proposed credit amount."""
        return sum((line.credit_amount for line in self.lines), Decimal("0"))


@dataclass(frozen=True, slots=True)
class AccountingPolicy:
    """Resolved policy context required before an upstream proposal may post."""

    tenant_reference: str
    legal_entity_reference: str
    accounting_book_reference: str
    intended_book_role_code: str
    transaction_currency: str
    functional_currency: str
    open_period_start: date
    open_period_end: date
    chart_account_mapping: Mapping[str, str]
    accounting_policy_version: str
    posting_rule_version: str

    def __post_init__(self) -> None:
        """Validate scope, open interval, codes, and immutable account mapping."""
        _require_reference(self.tenant_reference, "tenant reference")
        _require_reference(self.legal_entity_reference, "legal entity reference")
        _require_reference(self.accounting_book_reference, "accounting book reference")
        _require_code(self.intended_book_role_code, "intended book role code")
        _require_currency(self.transaction_currency)
        _require_currency(self.functional_currency)
        if self.open_period_start > self.open_period_end:
            raise AccountingValidationError("open fiscal period start must not exceed end")
        if not self.accounting_policy_version or not self.posting_rule_version:
            raise AccountingValidationError("accounting policy and posting rule versions are required")
        normalized_mapping: dict[str, str] = {}
        for role_code, account_code in self.chart_account_mapping.items():
            _require_code(role_code, "account role code")
            if not account_code or not account_code.isascii() or not account_code.isalnum():
                raise AccountingValidationError("chart account code must be non-empty ASCII alphanumeric")
            normalized_mapping[role_code] = account_code
        object.__setattr__(self, "chart_account_mapping", MappingProxyType(normalized_mapping))

    def permits(self, accounting_date: date) -> bool:
        """Return whether *accounting_date* belongs to the open period."""
        return self.open_period_start <= accounting_date <= self.open_period_end


@dataclass(frozen=True, slots=True)
class PostedJournalLine:
    """One resolved chart-account line in an immutable posted journal."""

    line_number: int
    chart_account_code: str
    account_role_code: str
    debit_amount: Decimal
    credit_amount: Decimal


@dataclass(frozen=True, slots=True)
class PostedJournal:
    """An immutable accounting journal created from a validated proposal or reversal."""

    journal_reference: str
    tenant_reference: str
    legal_entity_reference: str
    accounting_book_reference: str
    accounting_date: date
    transaction_currency: str
    functional_currency: str
    source_proposal_id: str
    source_payload_hash: str
    accounting_policy_version: str
    posting_rule_version: str
    lines: tuple[PostedJournalLine, ...]
    reversal_of_journal_reference: str | None = None
    reversal_reason_code: str | None = None


@dataclass(frozen=True, slots=True)
class PostingReceipt:
    """Authoritative acknowledgement that one proposal or reversal was posted."""

    receipt_reference: str
    journal_reference: str
    posting_status_code: str
    source_proposal_id: str
    source_payload_hash: str
    tenant_reference: str
    legal_entity_reference: str
    accounting_book_reference: str
    accounting_policy_version: str
    posting_rule_version: str
    line_count: int
    reversal_of_journal_reference: str | None = None


@dataclass(frozen=True, slots=True)
class AccountBalance:
    """Exact debit, credit, and net totals for one chart account."""

    chart_account_code: str
    debit_total: Decimal
    credit_total: Decimal

    @property
    def net_balance(self) -> Decimal:
        """Return debit minus credit for this account."""
        return self.debit_total - self.credit_total


class PostingLedger:
    """Deterministic in-memory reference for posting, reversal, and trial balance.

    Production deployments replace persistence with PostgreSQL while preserving
    this public behavior and its test fixtures.
    """

    def __init__(self) -> None:
        """Create an empty append-only reference ledger."""
        self._journals: dict[str, PostedJournal] = {}
        self._receipts_by_idempotency: dict[str, tuple[str, PostingReceipt]] = {}
        self._reversal_receipts: dict[str, PostingReceipt] = {}

    @property
    def journal_count(self) -> int:
        """Return the number of original and reversal journals retained."""
        return len(self._journals)

    def post(self, proposal: JournalProposal, policy: AccountingPolicy) -> PostingReceipt:
        """Resolve and append *proposal* or return its prior idempotent receipt."""
        prior = self._receipts_by_idempotency.get(proposal.idempotency_key)
        if prior is not None:
            prior_hash, prior_receipt = prior
            if prior_hash != proposal.source_payload_hash:
                raise IdempotencyConflictError(
                    "idempotency key was already used with a different payload"
                )
            return prior_receipt
        self._validate_policy_scope(proposal, policy)
        resolved_lines = tuple(self._resolve_line(line, policy) for line in proposal.lines)
        journal_reference = f"urn:cwl:accounting:general_journal:{proposal.proposal_id}"
        receipt_reference = f"urn:cwl:accounting:posting_receipt:{proposal.proposal_id}"
        journal = PostedJournal(
            journal_reference=journal_reference,
            tenant_reference=proposal.tenant_reference,
            legal_entity_reference=proposal.legal_entity_reference,
            accounting_book_reference=policy.accounting_book_reference,
            accounting_date=proposal.accounting_date,
            transaction_currency=proposal.transaction_currency,
            functional_currency=policy.functional_currency,
            source_proposal_id=proposal.proposal_id,
            source_payload_hash=proposal.source_payload_hash,
            accounting_policy_version=policy.accounting_policy_version,
            posting_rule_version=policy.posting_rule_version,
            lines=resolved_lines,
        )
        receipt = PostingReceipt(
            receipt_reference=receipt_reference,
            journal_reference=journal_reference,
            posting_status_code="posted",
            source_proposal_id=proposal.proposal_id,
            source_payload_hash=proposal.source_payload_hash,
            tenant_reference=proposal.tenant_reference,
            legal_entity_reference=proposal.legal_entity_reference,
            accounting_book_reference=policy.accounting_book_reference,
            accounting_policy_version=policy.accounting_policy_version,
            posting_rule_version=policy.posting_rule_version,
            line_count=len(resolved_lines),
        )
        self._journals[journal_reference] = journal
        self._receipts_by_idempotency[proposal.idempotency_key] = (
            proposal.source_payload_hash,
            receipt,
        )
        return receipt

    def reverse(
        self,
        journal_reference: str,
        reversal_date: date,
        reversal_reason_code: str,
        policy: AccountingPolicy,
    ) -> PostingReceipt:
        """Append the exact opposite of one original journal and preserve lineage."""
        prior_receipt = self._reversal_receipts.get(journal_reference)
        if prior_receipt is not None:
            return prior_receipt
        original = self._journals.get(journal_reference)
        if original is None:
            raise AccountingValidationError("journal does not exist")
        if original.reversal_of_journal_reference is not None:
            raise AccountingValidationError("a reversal journal cannot itself be reversed")
        if not policy.permits(reversal_date):
            raise AccountingValidationError("reversal date belongs to a closed fiscal period")
        if (
            original.tenant_reference != policy.tenant_reference
            or original.legal_entity_reference != policy.legal_entity_reference
            or original.accounting_book_reference != policy.accounting_book_reference
        ):
            raise AccountingValidationError("reversal policy scope does not match original journal")
        _require_code(reversal_reason_code, "reversal reason code")
        reversal_reference = f"{journal_reference}:reversal"
        reversal_lines = tuple(
            PostedJournalLine(
                line_number=line.line_number,
                chart_account_code=line.chart_account_code,
                account_role_code=line.account_role_code,
                debit_amount=line.credit_amount,
                credit_amount=line.debit_amount,
            )
            for line in original.lines
        )
        reversal = PostedJournal(
            journal_reference=reversal_reference,
            tenant_reference=original.tenant_reference,
            legal_entity_reference=original.legal_entity_reference,
            accounting_book_reference=original.accounting_book_reference,
            accounting_date=reversal_date,
            transaction_currency=original.transaction_currency,
            functional_currency=original.functional_currency,
            source_proposal_id=original.source_proposal_id,
            source_payload_hash=original.source_payload_hash,
            accounting_policy_version=policy.accounting_policy_version,
            posting_rule_version=policy.posting_rule_version,
            lines=reversal_lines,
            reversal_of_journal_reference=journal_reference,
            reversal_reason_code=reversal_reason_code,
        )
        receipt = PostingReceipt(
            receipt_reference=f"{reversal_reference}:receipt",
            journal_reference=reversal_reference,
            posting_status_code="posted",
            source_proposal_id=original.source_proposal_id,
            source_payload_hash=original.source_payload_hash,
            tenant_reference=original.tenant_reference,
            legal_entity_reference=original.legal_entity_reference,
            accounting_book_reference=original.accounting_book_reference,
            accounting_policy_version=policy.accounting_policy_version,
            posting_rule_version=policy.posting_rule_version,
            line_count=len(reversal_lines),
            reversal_of_journal_reference=journal_reference,
        )
        self._journals[reversal_reference] = reversal
        self._reversal_receipts[journal_reference] = receipt
        return receipt

    def trial_balance(
        self,
        tenant_reference: str,
        legal_entity_reference: str,
        accounting_book_reference: str,
        through_date: date,
    ) -> dict[str, AccountBalance]:
        """Aggregate posted lines in one tenant/entity/book scope through a date."""
        totals: dict[str, tuple[Decimal, Decimal]] = {}
        for journal in self._journals.values():
            if (
                journal.tenant_reference != tenant_reference
                or journal.legal_entity_reference != legal_entity_reference
                or journal.accounting_book_reference != accounting_book_reference
                or journal.accounting_date > through_date
            ):
                continue
            for line in journal.lines:
                debit_total, credit_total = totals.get(
                    line.chart_account_code, (Decimal("0"), Decimal("0"))
                )
                totals[line.chart_account_code] = (
                    debit_total + line.debit_amount,
                    credit_total + line.credit_amount,
                )
        return {
            account_code: AccountBalance(account_code, debit_total, credit_total)
            for account_code, (debit_total, credit_total) in sorted(totals.items())
        }

    @staticmethod
    def _resolve_line(
        line: JournalLineProposal, policy: AccountingPolicy
    ) -> PostedJournalLine:
        """Map one semantic account role to the policy's chart account."""
        chart_account_code = policy.chart_account_mapping.get(line.account_role_code)
        if chart_account_code is None:
            raise AccountingValidationError(
                f"unmapped account role: {line.account_role_code}"
            )
        return PostedJournalLine(
            line_number=line.line_number,
            chart_account_code=chart_account_code,
            account_role_code=line.account_role_code,
            debit_amount=line.debit_amount,
            credit_amount=line.credit_amount,
        )

    @staticmethod
    def _validate_policy_scope(
        proposal: JournalProposal, policy: AccountingPolicy
    ) -> None:
        """Require exact scope, book role, period, and supported currency policy."""
        if proposal.tenant_reference != policy.tenant_reference:
            raise AccountingValidationError("proposal tenant scope does not match policy")
        if proposal.legal_entity_reference != policy.legal_entity_reference:
            raise AccountingValidationError("proposal legal entity scope does not match policy")
        if proposal.intended_book_role_code != policy.intended_book_role_code:
            raise AccountingValidationError("proposal book role does not match policy")
        if not policy.permits(proposal.accounting_date):
            raise AccountingValidationError("accounting date belongs to a closed fiscal period")
        if proposal.transaction_currency != policy.transaction_currency:
            raise AccountingValidationError("proposal transaction currency does not match policy")
        if policy.transaction_currency != policy.functional_currency:
            raise AccountingValidationError(
                "foreign exchange accounting is outside the initial posting milestone"
            )


def _parse_amount(value: Decimal | str) -> Decimal:
    """Parse a canonical non-negative decimal with at most six fractional digits."""
    text = str(value)
    if _DECIMAL_PATTERN.fullmatch(text) is None:
        raise AccountingValidationError("amount must be a canonical non-negative decimal")
    try:
        amount = Decimal(text)
    except InvalidOperation as error:  # pragma: no cover - guarded by the regex.
        raise AccountingValidationError("amount is not a valid decimal") from error
    return amount


def _require_code(value: str, label: str) -> None:
    """Require a lower snake-case semantic code."""
    if _CODE_PATTERN.fullmatch(value) is None:
        raise AccountingValidationError(f"{label} must be lower snake_case")


def _require_currency(value: str) -> None:
    """Require a three-letter uppercase currency code."""
    if _CURRENCY_PATTERN.fullmatch(value) is None:
        raise AccountingValidationError("currency code must contain three uppercase letters")


def _require_reference(value: str, label: str) -> None:
    """Require an opaque CWL URN reference rather than embedded business data."""
    if _REFERENCE_PATTERN.fullmatch(value) is None:
        raise AccountingValidationError(f"{label} must be a CWL URN")
