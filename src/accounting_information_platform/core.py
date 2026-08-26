"""Deterministic accounting proposal validation and posting reference core.

The module intentionally contains no database or network integration.  It
provides the executable accounting invariants that PostgreSQL repositories and
HTTP adapters must preserve: exact decimals, balanced journals, idempotent
posting, append-only reversal, tenant scope, open-period control, and
source-to-posting provenance.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from types import MappingProxyType
from typing import Mapping, Sequence


_CODE_PATTERN = re.compile(r"^[a-z][a-z0-9_]{1,63}$")
_CURRENCY_PATTERN = re.compile(r"^[A-Z]{3}$")
_DECIMAL_PATTERN = re.compile(r"^(0|[1-9][0-9]*)(\.[0-9]{1,6})?$")
_HASH_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
_PROPOSAL_ID_PATTERN = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)
_REFERENCE_PATTERN = re.compile(r"^urn:cwl:[A-Za-z0-9_:.\-]+$")
_CUSTOMER_ERROR_REPLACEMENTS = (
    (
        "the database session is not provisioned for this tenant",
        "this deployment is not provisioned for this tenant",
    ),
    (
        "Repair the fiscal-period control data for this book, then retry the close.",
        "Ask the platform operator to restore the fiscal-period control data for this book, then retry the close.",
    ),
    (
        "requires a stored trial_balance_snapshot",
        "requires stored close evidence",
    ),
    (
        "without a trial-balance snapshot. Restore the trial_balance_snapshot for this book from the journal population, then retry the trial-balance read.",
        "without stored close evidence. Ask the platform operator to restore the close evidence for this book from the authoritative journal history, then retry the trial-balance read.",
    ),
    (
        "reversal date belongs to a closed fiscal period. Reverse into an open or soft-closed period, then retry reversal.",
        "reversal_date is outside the permitted accounting policy date range. Supply a reversal_date within the policy range, then retry reversal.",
    ),
)


def _customer_safe_error_message(message: str) -> str:
    """Remove known storage boundaries from caller-visible validation guidance."""
    for internal_text, customer_text in _CUSTOMER_ERROR_REPLACEMENTS:
        message = message.replace(internal_text, customer_text)
    return message


class AccountingValidationError(ValueError):
    """Raised when an accounting fact violates a deterministic invariant."""

    def __init__(self, message: str) -> None:
        """Store only caller-safe guidance while retaining the validation category."""
        super().__init__(_customer_safe_error_message(message))


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
            raise AccountingValidationError("line_number must be positive. Supply a line_number starting at 1, then retry ingest.")
        _require_code(self.account_role_code, "account role code")
        debit_amount = _parse_amount(self.debit_amount)
        credit_amount = _parse_amount(self.credit_amount)
        if (debit_amount > 0) == (credit_amount > 0):
            raise AccountingValidationError(
                "journal line must contain exactly one positive debit or credit amount. Supply exactly one positive debit_amount or credit_amount per line, then retry ingest."
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
            raise AccountingValidationError("proposal identity and contract version are required. Supply proposal_id and proposal_contract_version, then retry ingest.")
        _require_proposal_id(self.proposal_id)
        if not self.idempotency_key:
            raise AccountingValidationError("idempotency_key is required. Supply the source-system idempotency_key, then retry ingest.")
        _require_reference(self.tenant_reference, "tenant reference")
        _require_reference(self.legal_entity_reference, "legal entity reference")
        _require_code(self.intended_book_role_code, "intended book role code")
        _require_currency(self.transaction_currency)
        if _HASH_PATTERN.fullmatch(self.source_payload_hash) is None:
            raise AccountingValidationError("source_payload_hash must be canonical sha256. Supply sha256: plus 64 hex characters, then retry ingest.")
        if not self.source_event_references:
            raise AccountingValidationError("at least one source event reference is required. Supply at least one source_event_reference, then retry ingest.")
        for reference in self.source_event_references:
            _require_reference(reference, "source event reference")
        if len(self.lines) < 2:
            raise AccountingValidationError("journal proposal requires at least two lines. Supply at least two journal lines, then retry ingest.")
        line_numbers = tuple(line.line_number for line in self.lines)
        if len(set(line_numbers)) != len(line_numbers):
            raise AccountingValidationError("journal line numbers must be unique. Supply unique line numbers, then retry ingest.")
        debit_total = sum((line.debit_amount for line in self.lines), Decimal("0"))
        credit_total = sum((line.credit_amount for line in self.lines), Decimal("0"))
        if debit_total != credit_total:
            raise AccountingValidationError("journal proposal must balance. Correct the line amounts so debit totals equal credit totals, then retry ingest.")

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
            raise AccountingValidationError("open fiscal period start must not exceed end. Correct open_period_start/open_period_end in the policy manifest, then retry policy load.")
        if not self.accounting_policy_version or not self.posting_rule_version:
            raise AccountingValidationError("accounting policy and posting rule versions are required. Supply accounting_policy_version and posting_rule_version, then retry policy load.")
        normalized_mapping: dict[str, str] = {}
        for role_code, account_code in self.chart_account_mapping.items():
            _require_code(role_code, "account role code")
            if not account_code or not account_code.isascii() or not account_code.isalnum():
                raise AccountingValidationError("chart account code must be non-empty ASCII alphanumeric. Supply a non-empty alphanumeric chart_account_code, then retry policy load.")
            normalized_mapping[role_code] = account_code
        object.__setattr__(self, "chart_account_mapping", MappingProxyType(normalized_mapping))

    def permits(self, accounting_date: date) -> bool:
        """Return whether *accounting_date* belongs to the open period."""
        return self.open_period_start <= accounting_date <= self.open_period_end


def load_chart_account_mapping(
    account_mappings: Sequence[Mapping[str, object]],
) -> dict[str, str]:
    """Require at most one chart account per semantic account role."""
    if not account_mappings:
        raise AccountingValidationError(
            "account mappings must include at least one role. "
            "Supply a unique role-to-chart mapping, then retry policy load."
        )
    mapping: dict[str, str] = {}
    for item in account_mappings:
        if not isinstance(item, Mapping):
            raise AccountingValidationError(
                "account mapping must be an object with account_role_code and "
                "chart_account_code. Correct the policy manifest, then retry policy load."
            )
        role_code = item.get("account_role_code")
        account_code = item.get("chart_account_code")
        if not isinstance(role_code, str) or not isinstance(account_code, str):
            raise AccountingValidationError(
                "account mapping must include account_role_code and chart_account_code. "
                "Correct the policy manifest, then retry policy load."
            )
        if role_code in mapping:
            raise AccountingValidationError(
                f"account role {role_code} is mapped more than once. "
                "Keep one chart_account_code per account_role_code, then retry policy load."
            )
        mapping[role_code] = account_code
    return mapping


def load_accounting_policy(manifest: Mapping[str, object]) -> AccountingPolicy:
    """Build AccountingPolicy from a published manifest after unique role mapping."""
    raw_mappings = manifest.get("account_mappings")
    if not isinstance(raw_mappings, Sequence) or isinstance(raw_mappings, (str, bytes)):
        raise AccountingValidationError(
            "policy manifest account_mappings must be an array. "
            "Correct the policy manifest, then retry policy load."
        )
    try:
        open_period_start = date.fromisoformat(str(manifest.get("open_period_start", "")))
        open_period_end = date.fromisoformat(str(manifest.get("open_period_end", "")))
    except ValueError as error:
        raise AccountingValidationError(
            "policy manifest open period must be an ISO date. "
            "Correct the policy manifest, then retry policy load."
        ) from error
    return AccountingPolicy(
        tenant_reference=str(manifest.get("tenant_reference", "")),
        legal_entity_reference=str(manifest.get("legal_entity_reference", "")),
        accounting_book_reference=str(manifest.get("accounting_book_reference", "")),
        intended_book_role_code=str(manifest.get("intended_book_role_code", "")),
        transaction_currency=str(manifest.get("transaction_currency", "")),
        functional_currency=str(manifest.get("functional_currency", "")),
        open_period_start=open_period_start,
        open_period_end=open_period_end,
        chart_account_mapping=load_chart_account_mapping(raw_mappings),
        accounting_policy_version=str(manifest.get("accounting_policy_version", "")),
        posting_rule_version=str(manifest.get("posting_rule_version", "")),
    )


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
    reversal_idempotency_key: str | None = None


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
class PeriodCloseReceipt:
    """Authoritative acknowledgement that one fiscal period was closed."""

    tenant_reference: str
    legal_entity_reference: str
    accounting_book_reference: str
    period_code: str
    period_status_code: str
    snapshot_record_id: str
    snapshot_generated_at: datetime
    source_journal_count: int
    source_payload_hash: str
    replayed: bool


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
        self._journals: dict[tuple[str, str], PostedJournal] = {}
        self._receipts_by_idempotency: dict[tuple[str, str], tuple[str, PostingReceipt]] = {}
        self._reversal_receipts: dict[tuple[str, str], PostingReceipt] = {}
        self._reversal_command_evidence: dict[
            tuple[str, str], tuple[str, str, str]
        ] = {}

    @property
    def journal_count(self) -> int:
        """Return the number of original and reversal journals retained."""
        return len(self._journals)

    def post(self, proposal: JournalProposal, policy: AccountingPolicy) -> PostingReceipt:
        """Resolve and append *proposal* or return its prior idempotent receipt."""
        cached_receipt = self._cached_idempotency_receipt(
            proposal.tenant_reference,
            proposal.idempotency_key,
            proposal.source_payload_hash,
        )
        if cached_receipt is not None:
            return cached_receipt
        self._validate_policy_scope(proposal, policy)
        resolved_lines = tuple(self._resolve_line(line, policy) for line in proposal.lines)
        journal_reference = f"urn:cwl:accounting:general_journal:{proposal.proposal_id}"
        receipt_reference = f"urn:cwl:accounting:posting_receipt:{proposal.proposal_id}"
        journal_key = self._tenant_cache_key(proposal.tenant_reference, journal_reference)
        existing = self._journals.get(journal_key)
        if existing is not None:
            raise AccountingValidationError(
                "posted journal is immutable. Reverse the existing journal, then post a replacement."
            )
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
        self._journals[journal_key] = journal
        self._receipts_by_idempotency[
            self._tenant_cache_key(proposal.tenant_reference, proposal.idempotency_key)
        ] = (
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
        *,
        reversal_idempotency_key: str | None = None,
    ) -> PostingReceipt:
        """Append or exactly replay the opposite of one original journal."""
        _require_code(reversal_reason_code, "reversal reason code")
        command_key = (
            f"reversal:{journal_reference}"
            if reversal_idempotency_key is None
            else reversal_idempotency_key.strip()
        )
        if not command_key:
            raise AccountingValidationError("reversal idempotency key must not be empty. Supply the reversal command identity, then retry reversal.")
        command_hash = _reversal_command_hash(
            tenant_reference=policy.tenant_reference,
            reversal_idempotency_key=command_key,
            original_journal_reference=journal_reference,
            reversal_date=reversal_date,
            reversal_reason_code=reversal_reason_code,
        )
        reversal_key = self._tenant_cache_key(policy.tenant_reference, journal_reference)
        prior_receipt = self._cached_reversal_receipt(
            policy.tenant_reference,
            journal_reference,
            command_key,
            command_hash,
        )
        if prior_receipt is not None:
            return prior_receipt
        original = self._journals.get(reversal_key)
        if original is None:
            raise AccountingValidationError("journal does not exist. Supply a posted journal reference, then retry reversal.")
        if original.reversal_of_journal_reference is not None:
            raise AccountingValidationError("a reversal journal cannot itself be reversed. Reverse the original journal, or post a replacement.")
        if (
            original.legal_entity_reference != policy.legal_entity_reference
            or original.accounting_book_reference != policy.accounting_book_reference
        ):
            raise AccountingValidationError("reversal policy scope does not match original journal. Supply the reversal policy for the original journal's legal entity and book, then retry reversal.")
        reversal_reference = f"{journal_reference}:reversal"
        occupant = self._journals.get(
            self._tenant_cache_key(original.tenant_reference, reversal_reference)
        )
        if occupant is not None:
            if occupant.reversal_of_journal_reference != journal_reference:
                raise AccountingValidationError(
                    "posted journal is immutable. Reverse the existing journal, then post a replacement."
                )
            if occupant.reversal_idempotency_key != command_key:
                raise AccountingValidationError(
                    "journal is already reversed. Use the existing reversal receipt, then retry."
                )
            if occupant.source_payload_hash != command_hash:
                raise IdempotencyConflictError(
                    "reversal idempotency key was already used with a different payload"
                )
            receipt = self._receipt_for_posted_journal(occupant)
            self._reversal_receipts[reversal_key] = receipt
            self._reversal_command_evidence[reversal_key] = (
                command_key,
                journal_reference,
                command_hash,
            )
            return receipt
        if reversal_date < original.accounting_date:
            raise AccountingValidationError(
                "reversal date must not precede the original journal accounting date. Supply a reversal_date on or after the original accounting date, then retry reversal."
            )
        if not policy.permits(reversal_date):
            raise AccountingValidationError(
                "reversal_date is outside the permitted accounting policy date range. "
                "Supply a reversal_date within the policy range, then retry reversal."
            )
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
            source_payload_hash=command_hash,
            accounting_policy_version=policy.accounting_policy_version,
            posting_rule_version=policy.posting_rule_version,
            lines=reversal_lines,
            reversal_of_journal_reference=journal_reference,
            reversal_reason_code=reversal_reason_code,
            reversal_idempotency_key=command_key,
        )
        receipt = PostingReceipt(
            receipt_reference=f"{reversal_reference}:receipt",
            journal_reference=reversal_reference,
            posting_status_code="posted",
            source_proposal_id=original.source_proposal_id,
            source_payload_hash=command_hash,
            tenant_reference=original.tenant_reference,
            legal_entity_reference=original.legal_entity_reference,
            accounting_book_reference=original.accounting_book_reference,
            accounting_policy_version=policy.accounting_policy_version,
            posting_rule_version=policy.posting_rule_version,
            line_count=len(reversal_lines),
            reversal_of_journal_reference=journal_reference,
        )
        self._journals[self._tenant_cache_key(original.tenant_reference, reversal_reference)] = (
            reversal
        )
        self._reversal_receipts[reversal_key] = receipt
        self._reversal_command_evidence[reversal_key] = (
            command_key,
            journal_reference,
            command_hash,
        )
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
    def _tenant_cache_key(tenant_reference: str, scoped_key: str) -> tuple[str, str]:
        """Return the tenant-scoped cache identity used by the in-memory oracle."""
        return (tenant_reference, scoped_key)

    def _cached_idempotency_receipt(
        self,
        tenant_reference: str,
        idempotency_key: str,
        source_payload_hash: str,
    ) -> PostingReceipt | None:
        """Return the prior receipt only when tenant, key, and payload hash all match."""
        prior = self._receipts_by_idempotency.get(
            self._tenant_cache_key(tenant_reference, idempotency_key)
        )
        if prior is None:
            return None
        prior_hash, prior_receipt = prior
        if prior_receipt.tenant_reference != tenant_reference:
            return None
        if prior_hash != source_payload_hash:
            raise IdempotencyConflictError(
                "idempotency key was already used with a different payload"
            )
        return prior_receipt

    def _cached_reversal_receipt(
        self,
        tenant_reference: str,
        journal_reference: str,
        reversal_idempotency_key: str,
        command_hash: str,
    ) -> PostingReceipt | None:
        """Return a reversal receipt only when its immutable command evidence matches."""
        cache_key = self._tenant_cache_key(tenant_reference, journal_reference)
        prior_receipt = self._reversal_receipts.get(cache_key)
        if prior_receipt is None or prior_receipt.tenant_reference != tenant_reference:
            return None
        evidence = self._reversal_command_evidence.get(cache_key)
        if evidence is None:
            return None
        prior_key, prior_original_reference, prior_hash = evidence
        if prior_key != reversal_idempotency_key or prior_original_reference != journal_reference:
            return None
        if prior_hash != command_hash:
            raise IdempotencyConflictError(
                "reversal idempotency key was already used with a different payload"
            )
        return prior_receipt

    @staticmethod
    def _receipt_for_posted_journal(journal: PostedJournal) -> PostingReceipt:
        """Rebuild the posting receipt for an already-retained journal."""
        return PostingReceipt(
            receipt_reference=f"{journal.journal_reference}:receipt",
            journal_reference=journal.journal_reference,
            posting_status_code="posted",
            source_proposal_id=journal.source_proposal_id,
            source_payload_hash=journal.source_payload_hash,
            tenant_reference=journal.tenant_reference,
            legal_entity_reference=journal.legal_entity_reference,
            accounting_book_reference=journal.accounting_book_reference,
            accounting_policy_version=journal.accounting_policy_version,
            posting_rule_version=journal.posting_rule_version,
            line_count=len(journal.lines),
            reversal_of_journal_reference=journal.reversal_of_journal_reference,
        )

    @staticmethod
    def _resolve_line(
        line: JournalLineProposal, policy: AccountingPolicy
    ) -> PostedJournalLine:
        """Map one semantic account role to the policy's chart account."""
        chart_account_code = policy.chart_account_mapping.get(line.account_role_code)
        if chart_account_code is None:
            raise AccountingValidationError(
                f"account role {line.account_role_code} is not mapped on this book. Map that role in the policy manifest, then retry posting."
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
            raise AccountingValidationError("proposal tenant scope does not match policy. Send the proposal under the matching tenant scope, then retry posting.")
        if proposal.legal_entity_reference != policy.legal_entity_reference:
            raise AccountingValidationError("proposal legal entity scope does not match policy. Send the proposal under the matching legal-entity scope, then retry posting.")
        if proposal.intended_book_role_code != policy.intended_book_role_code:
            raise AccountingValidationError("proposal book role does not match policy. Send the proposal under the matching book role, then retry posting.")
        if not policy.permits(proposal.accounting_date):
            raise AccountingValidationError("accounting date belongs to a closed fiscal period. Post into an open period for this book, then retry posting.")
        if proposal.transaction_currency != policy.transaction_currency:
            raise AccountingValidationError("proposal transaction currency does not match policy. Supply the policy transaction currency, then retry posting.")
        if policy.transaction_currency != policy.functional_currency:
            raise AccountingValidationError(
                "foreign exchange accounting is outside the initial posting milestone. Post in the book's functional currency, then retry posting."
            )


def _reversal_command_hash(
    *,
    tenant_reference: str,
    reversal_idempotency_key: str,
    original_journal_reference: str,
    reversal_date: date,
    reversal_reason_code: str,
) -> str:
    """Return the canonical immutable hash for one reversal command."""
    payload = {
        "original_journal_reference": original_journal_reference,
        "reversal_date": reversal_date.isoformat(),
        "reversal_idempotency_key": reversal_idempotency_key,
        "reversal_reason_code": reversal_reason_code,
        "tenant_reference": tenant_reference,
    }
    digest = hashlib.sha256(
        json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    ).hexdigest()
    return f"sha256:{digest}"


def _parse_amount(value: Decimal | str) -> Decimal:
    """Parse a canonical non-negative decimal with at most six fractional digits."""
    text = str(value)
    if _DECIMAL_PATTERN.fullmatch(text) is None:
        raise AccountingValidationError(
            "amount must be a canonical non-negative decimal with at most six fractional digits. "
            "Supply a non-negative decimal string with no more than six fractional digits, then retry ingest."
        )
    try:
        amount = Decimal(text)
    except InvalidOperation as error:  # pragma: no cover - guarded by the regex.
        raise AccountingValidationError(
            "amount is not a valid decimal. Supply a canonical non-negative decimal string, then retry ingest."
        ) from error
    return amount


def _require_proposal_id(proposal_id: str) -> str:
    """Require a hyphenated UUID so a commercial id cannot form a reversal key."""
    if _PROPOSAL_ID_PATTERN.fullmatch(proposal_id) is None:
        raise AccountingValidationError(
            "proposal_id must be a UUID. Supply the Billing published proposal_id, "
            "then retry posting."
        )
    return proposal_id


def _require_code(value: str, label: str) -> None:
    """Require a lower snake-case semantic code."""
    if _CODE_PATTERN.fullmatch(value) is None:
        raise AccountingValidationError(f"{label} must be lower snake_case. Supply a valid {label}, then retry.")


def _require_currency(value: str) -> None:
    """Require a three-letter uppercase currency code."""
    if _CURRENCY_PATTERN.fullmatch(value) is None:
        raise AccountingValidationError("currency code must contain three uppercase letters. Supply a three-letter uppercase ISO currency code, then retry.")


def _require_reference(value: str, label: str) -> None:
    """Require an opaque CWL URN reference rather than embedded business data."""
    if _REFERENCE_PATTERN.fullmatch(value) is None:
        raise AccountingValidationError(f"{label} must be a CWL URN. Supply an opaque urn:cwl: reference, then retry.")
