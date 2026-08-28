"""Tamper-evident reconciliation evidence packages for period-close review.

The package binds an already read-only close-review projection to a complete
structured approval-evidence population and source-evidence hashes. It is an evidence manifest only: creating,
verifying, or rendering it cannot approve reconciliation, mutate accounting
facts, close a period, or post a journal.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from .reconciliation_read_model import (
    ReconciliationCloseReviewProjection,
    render_reconciliation_close_review_json,
)

_SHA256_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
_UTC_SECOND_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
_REQUIRED_EVIDENCE_KINDS = frozenset(
    {
        "reconciliation_run",
        "statement_artifact",
        "statement_population",
        "book_population",
    }
)
_PROJECTION_IDENTITY_FIELDS = (
    "tenant_account_reference",
    "legal_entity_reference",
    "accounting_book_reference",
    "bank_account_assignment_reference",
    "reconciliation_run_reference",
    "statement_population_reference",
    "book_population_reference",
    "currency_code",
    "next_action",
)
_PROJECTION_MONEY_FIELDS = (
    "bank_closing_balance",
    "posted_book_cash_balance",
    "reconciled_balance",
    "outstanding_bank_items",
    "outstanding_book_items",
    "unexplained_difference",
)
_PROJECTION_OPTIONAL_MONEY_FIELDS = (
    "unexplained_difference_change",
    "outstanding_bank_items_change",
    "outstanding_book_items_change",
)
_NEXT_ACTION = (
    "Archive this tamper-evident package with the period-close review record; "
    "obtain the separately authorized reconciliation/close decision before any accounting action."
)


def _require_identifier(value: object, *, field_name: str) -> str:
    """Return one canonical non-empty identifier or fail closed."""
    if not isinstance(value, str) or not value or value.strip() != value:
        raise ValueError(f"{field_name} must be a non-empty canonical string")
    return value


def _require_sha256(value: object, *, field_name: str) -> str:
    """Return a lowercase prefixed SHA-256 digest or fail closed."""
    if not isinstance(value, str) or _SHA256_PATTERN.fullmatch(value) is None:
        raise ValueError(f"{field_name} must match sha256:<64 lowercase hex characters>")
    return value


def _require_knowledge_cutoff(value: object) -> str:
    """Require a canonical UTC RFC 3339 second precision knowledge cutoff."""
    if not isinstance(value, str) or _UTC_SECOND_PATTERN.fullmatch(value) is None:
        raise ValueError("knowledge_cutoff must be canonical UTC RFC 3339 at second precision")
    try:
        datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError as exc:
        raise ValueError("knowledge_cutoff must name a real UTC calendar instant") from exc
    return value


@dataclass(frozen=True, slots=True)
class ReconciliationEvidenceReference:
    """Immutable source-evidence identity, digest, and optional run cutoff."""

    evidence_kind_code: str
    evidence_reference: str
    sha256_digest: str
    knowledge_cutoff: str | None = None

    def __post_init__(self) -> None:
        """Reject ambiguous identities, digests, and cutoff provenance."""
        _require_identifier(self.evidence_kind_code, field_name="evidence_kind_code")
        _require_identifier(self.evidence_reference, field_name="evidence_reference")
        _require_sha256(self.sha256_digest, field_name="sha256_digest")
        if self.evidence_kind_code == "reconciliation_run":
            _require_knowledge_cutoff(self.knowledge_cutoff)
        elif self.knowledge_cutoff is not None:
            raise ValueError(
                "knowledge_cutoff is permitted only on reconciliation_run evidence"
            )


@dataclass(frozen=True, slots=True)
class ReconciliationApprovalEvidence:
    """Immutable database-owned approval evidence for one reconciliation match."""

    tenant_account_reference: str
    reconciliation_run_reference: str
    reconciliation_match_reference: str
    approval_decision_code: str
    reconciliation_snapshot_sha256: str
    evidence_reference: str

    def __post_init__(self) -> None:
        """Reject non-canonical approval scope, decision, digest, or evidence identity."""
        _require_identifier(
            self.tenant_account_reference,
            field_name="approval tenant_account_reference",
        )
        _require_identifier(
            self.reconciliation_run_reference,
            field_name="approval reconciliation_run_reference",
        )
        _require_identifier(
            self.reconciliation_match_reference,
            field_name="approval reconciliation_match_reference",
        )
        if self.approval_decision_code not in {"approved", "rejected"}:
            raise ValueError(
                "approval_decision_code must be approved or rejected"
            )
        _require_sha256(
            self.reconciliation_snapshot_sha256,
            field_name="reconciliation_snapshot_sha256",
        )
        _require_identifier(self.evidence_reference, field_name="approval evidence_reference")


@dataclass(frozen=True, slots=True)
class ReconciliationClosePackageInput:
    """Evidence required to bind one eligible close-review projection into a package."""

    projection: ReconciliationCloseReviewProjection
    approval_evidence: tuple[ReconciliationApprovalEvidence, ...]
    knowledge_cutoff: str
    evidence_references: tuple[ReconciliationEvidenceReference, ...]


@dataclass(frozen=True, slots=True)
class ReconciliationClosePackage:
    """Canonical tamper-evident period-close reconciliation evidence package."""

    projection: ReconciliationCloseReviewProjection
    approval_evidence: tuple[ReconciliationApprovalEvidence, ...]
    knowledge_cutoff: str
    evidence_references: tuple[ReconciliationEvidenceReference, ...]
    package_sha256: str
    next_action: str


def _validate_projection(projection: object) -> ReconciliationCloseReviewProjection:
    """Revalidate one public close projection before treating it as evidence."""
    if not isinstance(projection, ReconciliationCloseReviewProjection):
        raise ValueError("projection must be a ReconciliationCloseReviewProjection")

    for field_name in _PROJECTION_IDENTITY_FIELDS:
        _require_identifier(getattr(projection, field_name), field_name=field_name)

    for field_name in _PROJECTION_MONEY_FIELDS:
        value = getattr(projection, field_name)
        if not isinstance(value, Decimal) or not value.is_finite():
            raise ValueError(f"{field_name} must be a finite Decimal")

    for field_name in _PROJECTION_OPTIONAL_MONEY_FIELDS:
        value = getattr(projection, field_name)
        if value is not None and (
            not isinstance(value, Decimal) or not value.is_finite()
        ):
            raise ValueError(f"{field_name} must be None or a finite Decimal")

    if (
        not isinstance(projection.safely_matchable_candidate_count, int)
        or isinstance(projection.safely_matchable_candidate_count, bool)
        or projection.safely_matchable_candidate_count < 0
    ):
        raise ValueError("safely_matchable_candidate_count must be a non-negative integer")
    if not isinstance(projection.reviewed_match_references, tuple):
        raise ValueError("reviewed match identities must be a tuple")
    if any(
        not isinstance(reference, str) or not reference or reference.strip() != reference
        for reference in projection.reviewed_match_references
    ):
        raise ValueError("reviewed match identities must be canonical strings")
    if len(set(projection.reviewed_match_references)) != len(
        projection.reviewed_match_references
    ):
        raise ValueError("reviewed match identities must be unique")
    if len(projection.reviewed_match_references) != projection.safely_matchable_candidate_count:
        raise ValueError(
            "reviewed match identities must exactly cover the safely matchable proposals"
        )
    if (
        not isinstance(projection.exception_count, int)
        or isinstance(projection.exception_count, bool)
        or projection.exception_count < 0
    ):
        raise ValueError("exception_count must be a non-negative integer")
    if not isinstance(projection.exception_statement_entry_references, tuple) or any(
        not isinstance(reference, str)
        or not reference
        or reference.strip() != reference
        for reference in projection.exception_statement_entry_references
    ):
        raise ValueError(
            "exception_statement_entry_references must contain canonical string identities"
        )
    if len(set(projection.exception_statement_entry_references)) != len(
        projection.exception_statement_entry_references
    ):
        raise ValueError("exception_statement_entry_references must be unique")

    if (
        projection.suitable_for_period_close_review is not True
        or projection.exception_count != 0
        or projection.exception_statement_entry_references
        or projection.unexplained_difference != Decimal("0")
    ):
        raise ValueError(
            "reconciliation projection is not suitable for period-close review; "
            "resolve the exact bridge or reconciliation exceptions first"
        )

    bridge_unexplained_difference = (
        projection.reconciled_balance
        + projection.outstanding_book_items
        - projection.outstanding_bank_items
        - projection.bank_closing_balance
    )
    if bridge_unexplained_difference != projection.unexplained_difference:
        raise ValueError(
            "reconciliation projection must preserve the exact book-to-bank bridge equation"
        )
    return projection


def _validate_and_order_evidence(
    evidence_references: object,
    *,
    projection: ReconciliationCloseReviewProjection,
) -> tuple[ReconciliationEvidenceReference, ...]:
    """Bind canonical source evidence to run and projection population identities."""
    if not isinstance(evidence_references, tuple) or not evidence_references:
        raise ValueError(
            "evidence_references must include immutable reconciliation run, statement, and book evidence"
        )
    if any(
        not isinstance(evidence, ReconciliationEvidenceReference)
        for evidence in evidence_references
    ):
        raise ValueError("evidence_references must contain evidence reference objects")

    identities = tuple(
        (evidence.evidence_kind_code, evidence.evidence_reference)
        for evidence in evidence_references
    )
    if len(set(identities)) != len(identities):
        raise ValueError("evidence_references identities must be unique")

    evidence_kinds = {evidence.evidence_kind_code for evidence in evidence_references}
    if not _REQUIRED_EVIDENCE_KINDS.issubset(evidence_kinds):
        raise ValueError(
            "evidence_references must include reconciliation_run, statement_artifact, "
            "statement_population, and book_population evidence"
        )

    reconciliation_runs = tuple(
        evidence
        for evidence in evidence_references
        if evidence.evidence_kind_code == "reconciliation_run"
    )
    statement_populations = tuple(
        evidence
        for evidence in evidence_references
        if evidence.evidence_kind_code == "statement_population"
    )
    book_populations = tuple(
        evidence
        for evidence in evidence_references
        if evidence.evidence_kind_code == "book_population"
    )
    if len(reconciliation_runs) != 1:
        raise ValueError("evidence_references must include exactly one reconciliation_run evidence")
    if len(statement_populations) != 1:
        raise ValueError("evidence_references must include exactly one statement_population evidence")
    if len(book_populations) != 1:
        raise ValueError("evidence_references must include exactly one book_population evidence")
    if reconciliation_runs[0].evidence_reference != projection.reconciliation_run_reference:
        raise ValueError(
            "reconciliation_run evidence must bind projection.reconciliation_run_reference"
        )
    if (
        statement_populations[0].evidence_reference
        != projection.statement_population_reference
    ):
        raise ValueError(
            "statement_population evidence must bind projection.statement_population_reference"
        )
    if book_populations[0].evidence_reference != projection.book_population_reference:
        raise ValueError("book_population evidence must bind projection.book_population_reference")

    return tuple(
        sorted(
            evidence_references,
            key=lambda evidence: (
                evidence.evidence_kind_code,
                evidence.evidence_reference,
                evidence.sha256_digest,
            ),
        )
    )


def _approval_mapping(
    approval: ReconciliationApprovalEvidence,
) -> dict[str, str]:
    """Return one deterministic structured approval-evidence mapping."""
    return {
        "tenant_account_reference": approval.tenant_account_reference,
        "reconciliation_run_reference": approval.reconciliation_run_reference,
        "reconciliation_match_reference": approval.reconciliation_match_reference,
        "approval_decision_code": approval.approval_decision_code,
        "reconciliation_snapshot_sha256": approval.reconciliation_snapshot_sha256,
        "evidence_reference": approval.evidence_reference,
    }


def _validate_approval_evidence(
    approval_evidence: object,
    *,
    projection: ReconciliationCloseReviewProjection,
) -> tuple[ReconciliationApprovalEvidence, ...]:
    """Bind complete approved match evidence to the projection's immutable scope."""
    if not isinstance(approval_evidence, tuple) or not approval_evidence:
        raise ValueError("approval evidence must be a non-empty tuple")
    if any(
        not isinstance(approval, ReconciliationApprovalEvidence)
        for approval in approval_evidence
    ):
        raise ValueError("approval evidence must contain structured evidence objects")
    if len(approval_evidence) != projection.safely_matchable_candidate_count:
        raise ValueError(
            "approval evidence must exactly cover the safely matchable reviewed population"
        )
    match_references = tuple(
        approval.reconciliation_match_reference for approval in approval_evidence
    )
    if len(set(match_references)) != len(match_references):
        raise ValueError("approval evidence match identities must be unique")
    if set(match_references) != set(projection.reviewed_match_references):
        raise ValueError(
            "approval evidence must exactly cover the projection's reviewed match population"
        )
    for approval in approval_evidence:
        if (
            approval.tenant_account_reference != projection.tenant_account_reference
            or approval.reconciliation_run_reference
            != projection.reconciliation_run_reference
        ):
            raise ValueError("approval evidence must remain in the same tenant and run scope")
        if approval.approval_decision_code != "approved":
            raise ValueError("close-package approval evidence must be approved")
    return tuple(
        sorted(
            approval_evidence,
            key=lambda approval: (
                approval.tenant_account_reference,
                approval.reconciliation_run_reference,
                approval.reconciliation_match_reference,
                approval.evidence_reference,
                approval.reconciliation_snapshot_sha256,
            ),
        )
    )


def _evidence_mapping(
    evidence: ReconciliationEvidenceReference,
) -> dict[str, object]:
    """Return one deterministic evidence-reference mapping."""
    mapping: dict[str, object] = {
        "evidence_kind_code": evidence.evidence_kind_code,
        "evidence_reference": evidence.evidence_reference,
        "sha256_digest": evidence.sha256_digest,
    }
    if evidence.knowledge_cutoff is not None:
        mapping["knowledge_cutoff"] = evidence.knowledge_cutoff
    return mapping


def _package_unsigned_mapping(
    *,
    projection: ReconciliationCloseReviewProjection,
    approval_evidence: tuple[ReconciliationApprovalEvidence, ...],
    knowledge_cutoff: str,
    evidence_references: tuple[ReconciliationEvidenceReference, ...],
) -> dict[str, object]:
    """Return the canonical payload committed by ``package_sha256``."""
    return {
        "schema_version": 3,
        "projection": json.loads(render_reconciliation_close_review_json(projection)),
        "approval_evidence": [
            _approval_mapping(approval) for approval in approval_evidence
        ],
        "knowledge_cutoff": knowledge_cutoff,
        "evidence_references": [
            _evidence_mapping(evidence) for evidence in evidence_references
        ],
        "next_action": _NEXT_ACTION,
    }


def _canonical_json_bytes(payload: dict[str, object]) -> bytes:
    """Serialize one package payload with deterministic UTF-8 JSON encoding."""
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def build_reconciliation_close_package(
    package_input: ReconciliationClosePackageInput,
) -> ReconciliationClosePackage:
    """Build a deterministic package only from close-review-eligible evidence."""
    projection = _validate_projection(package_input.projection)
    approval_evidence = _validate_approval_evidence(
        package_input.approval_evidence,
        projection=projection,
    )
    knowledge_cutoff = _require_knowledge_cutoff(package_input.knowledge_cutoff)
    ordered_evidence = _validate_and_order_evidence(
        package_input.evidence_references,
        projection=projection,
    )
    run_evidence = next(
        evidence
        for evidence in ordered_evidence
        if evidence.evidence_kind_code == "reconciliation_run"
    )
    if run_evidence.knowledge_cutoff != knowledge_cutoff:
        raise ValueError(
            "knowledge_cutoff must match immutable reconciliation_run evidence"
        )

    unsigned_payload = _package_unsigned_mapping(
        projection=projection,
        approval_evidence=approval_evidence,
        knowledge_cutoff=knowledge_cutoff,
        evidence_references=ordered_evidence,
    )
    package_sha256 = "sha256:" + hashlib.sha256(
        _canonical_json_bytes(unsigned_payload)
    ).hexdigest()
    return ReconciliationClosePackage(
        projection=projection,
        approval_evidence=approval_evidence,
        knowledge_cutoff=knowledge_cutoff,
        evidence_references=ordered_evidence,
        package_sha256=package_sha256,
        next_action=_NEXT_ACTION,
    )


def verify_reconciliation_close_package(package: ReconciliationClosePackage) -> None:
    """Fail closed unless a package is canonical and matches its committed SHA-256."""
    if not isinstance(package, ReconciliationClosePackage):
        raise ValueError("package must be a ReconciliationClosePackage")
    _require_sha256(package.package_sha256, field_name="package_sha256")
    try:
        rebuilt = build_reconciliation_close_package(
            ReconciliationClosePackageInput(
                projection=package.projection,
                approval_evidence=package.approval_evidence,
                knowledge_cutoff=package.knowledge_cutoff,
                evidence_references=package.evidence_references,
            )
        )
    except ValueError as exc:
        if "exact book-to-bank bridge equation" in str(exc):
            raise ValueError(
                "package_sha256 does not match the canonical close-package payload"
            ) from exc
        raise
    if (
        package.next_action != rebuilt.next_action
        or package.evidence_references != rebuilt.evidence_references
        or not hmac.compare_digest(package.package_sha256, rebuilt.package_sha256)
    ):
        raise ValueError("package_sha256 does not match the canonical close-package payload")


def render_reconciliation_close_package_json(
    package: ReconciliationClosePackage,
) -> str:
    """Verify and render one deterministic exact-value close package as canonical JSON."""
    verify_reconciliation_close_package(package)
    payload = _package_unsigned_mapping(
        projection=package.projection,
        approval_evidence=package.approval_evidence,
        knowledge_cutoff=package.knowledge_cutoff,
        evidence_references=package.evidence_references,
    )
    payload["package_sha256"] = package.package_sha256
    return _canonical_json_bytes(payload).decode("utf-8")
