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
    _RECONCILED_CLOSE_REVIEW_NEXT_ACTION,
    _validate_reviewed_allocation_conservation,
    _validate_reviewed_population_source_capacity,
    ReconciliationCloseReviewProjection,
    ReconciliationAllocationEvidence,
    ReconciliationReviewedMatch,
    render_reconciliation_close_review_json,
)
from .reconciliation_bridge import _exact_decimal_sum
from .persistence import PostgresPostingLedger, _format_timestamp

_SHA256_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
_UTC_CANONICAL_PATTERN = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{6})?Z$"
)
_REQUIRED_EVIDENCE_KINDS = frozenset(
    {
        "reconciliation_run",
        "statement_artifact",
        "statement_population",
        "book_population",
    }
)
_SNAPSHOT_TENANT_EVIDENCE_KIND = "reconciliation_snapshot_tenant"
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


def _snapshot_value(value: str) -> str:
    """Encode one snapshot text value like PostgreSQL's byte-length framing."""
    return f"{len(value.encode('utf-8'))}:{value}"


def _reconciliation_match_snapshot_sha256(
    tenant_account_reference: str,
    reconciliation_run_reference: str,
    reviewed_match: ReconciliationReviewedMatch,
) -> str:
    """Reproduce database-owned v1/v2 reconciliation match snapshot digests exactly."""
    _validate_reviewed_allocation_conservation(reviewed_match)
    statement_sources = {
        allocation.source_reference for allocation in reviewed_match.statement_allocations
    }
    journal_sources = {
        allocation.source_reference for allocation in reviewed_match.journal_allocations
    }
    snapshot_version = 2 if len(statement_sources) > 1 or len(journal_sources) > 1 else 1

    def allocation_row(
        allocation: ReconciliationAllocationEvidence,
        *,
        row_kind: str,
    ) -> str:
        """Serialize one allocation row for the database-compatible snapshot digest."""
        values = [
            row_kind,
            _snapshot_value(allocation.allocation_reference),
            _snapshot_value(allocation.source_reference),
            _snapshot_value(str(allocation.allocated_amount)),
        ]
        if snapshot_version == 2:
            if allocation.source_capacity is None:
                raise ValueError(
                    "version-2 reconciliation snapshot requires authoritative source capacity"
                )
            values.append(_snapshot_value(str(allocation.source_capacity)))
        return "|".join(values)

    statement_rows = "\n".join(
        allocation_row(allocation, row_kind="statement")
        for allocation in reviewed_match.statement_allocations
    )
    journal_rows = "\n".join(
        allocation_row(allocation, row_kind="journal")
        for allocation in reviewed_match.journal_allocations
    )
    snapshot = "\n".join(
        (
            f"reconciliation_snapshot_version={snapshot_version}",
            "tenant=" + _snapshot_value(tenant_account_reference),
            "run=" + _snapshot_value(reconciliation_run_reference),
            "match=" + _snapshot_value(reviewed_match.reconciliation_match_reference),
            "candidate=" + _snapshot_value(reviewed_match.candidate_reference),
            "statement_reference="
            + _snapshot_value(reviewed_match.candidate_statement_reference),
            "journal_reference="
            + _snapshot_value(reviewed_match.candidate_journal_reference),
            "statement_amount=" + _snapshot_value(str(reviewed_match.statement_amount)),
            "journal_amount=" + _snapshot_value(str(reviewed_match.journal_amount)),
            "rule=" + _snapshot_value(reviewed_match.rule_code),
            "statement_allocations=" + statement_rows,
            "journal_allocations=" + journal_rows,
        )
    )
    return "sha256:" + hashlib.sha256(snapshot.encode("utf-8")).hexdigest()


def _snapshot_tenant_identity_sha256(
    tenant_reference: str,
    tenant_account_id: str,
) -> str:
    """Bind the public tenant reference to PostgreSQL's internal snapshot identity."""
    payload = "\n".join(
        (
            "reconciliation_snapshot_tenant_version=1",
            "tenant_reference=" + _snapshot_value(tenant_reference),
            "tenant_account_id=" + _snapshot_value(tenant_account_id),
        )
    )
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


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
    """Require the canonical UTC precision emitted by persisted reconciliation runs."""
    if not isinstance(value, str) or _UTC_CANONICAL_PATTERN.fullmatch(value) is None:
        raise ValueError(
            "knowledge_cutoff must be canonical UTC RFC 3339 at whole-second or "
            "six-digit microsecond precision"
        )
    try:
        datetime.fromisoformat(value[:-1] + "+00:00")
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
    source_payload_hash: str
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
        _require_sha256(self.source_payload_hash, field_name="approval source_payload_hash")
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


def _snapshot_tenant_identity_evidence(
    *,
    tenant_reference: str,
    tenant_account_id: object,
) -> ReconciliationEvidenceReference:
    """Create package evidence for PostgreSQL's internal approval-snapshot tenant identity."""
    tenant_account_id_text = _require_identifier(
        str(tenant_account_id),
        field_name="snapshot tenant_account_id",
    )
    return ReconciliationEvidenceReference(
        evidence_kind_code=_SNAPSHOT_TENANT_EVIDENCE_KIND,
        evidence_reference=tenant_account_id_text,
        sha256_digest=_snapshot_tenant_identity_sha256(
            tenant_reference,
            tenant_account_id_text,
        ),
    )


def _snapshot_tenant_identity_from_evidence(
    evidence_references: object,
    *,
    projection: ReconciliationCloseReviewProjection,
) -> str:
    """Resolve the database snapshot tenant identity while preserving legacy pure fixtures."""
    if not isinstance(evidence_references, tuple):
        return projection.tenant_account_reference
    identity_evidence = tuple(
        evidence
        for evidence in evidence_references
        if isinstance(evidence, ReconciliationEvidenceReference)
        and evidence.evidence_kind_code == _SNAPSHOT_TENANT_EVIDENCE_KIND
    )
    if not identity_evidence:
        return projection.tenant_account_reference
    if len(identity_evidence) != 1:
        raise ValueError(
            "evidence_references must include at most one reconciliation_snapshot_tenant evidence"
        )
    evidence = identity_evidence[0]
    expected_digest = _snapshot_tenant_identity_sha256(
        projection.tenant_account_reference,
        evidence.evidence_reference,
    )
    if not hmac.compare_digest(evidence.sha256_digest, expected_digest):
        raise ValueError(
            "reconciliation_snapshot_tenant evidence must bind the public tenant reference "
            "to the database snapshot identity"
        )
    return evidence.evidence_reference


def _validate_projection(projection: object) -> ReconciliationCloseReviewProjection:
    """Revalidate one public close projection before treating it as evidence."""
    if not isinstance(projection, ReconciliationCloseReviewProjection):
        raise ValueError("projection must be a ReconciliationCloseReviewProjection")

    for field_name in _PROJECTION_IDENTITY_FIELDS:
        _require_identifier(getattr(projection, field_name), field_name=field_name)
    if projection.next_action != _RECONCILED_CLOSE_REVIEW_NEXT_ACTION:
        raise ValueError("next action must use the canonical close-review guidance")

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
    if not isinstance(projection.reviewed_match_evidence, tuple):
        raise ValueError("reviewed match evidence must be a tuple")
    if any(
        not isinstance(reviewed_match, ReconciliationReviewedMatch)
        for reviewed_match in projection.reviewed_match_evidence
    ):
        raise ValueError("reviewed match evidence must contain structured evidence objects")
    for reviewed_match in projection.reviewed_match_evidence:
        _require_identifier(
            reviewed_match.reconciliation_match_reference,
            field_name="reviewed match reconciliation_match_reference",
        )
        _require_identifier(
            reviewed_match.candidate_reference,
            field_name="reviewed match candidate_reference",
        )
        _require_identifier(
            reviewed_match.candidate_statement_reference,
            field_name="reviewed match candidate_statement_reference",
        )
        _require_identifier(
            reviewed_match.candidate_journal_reference,
            field_name="reviewed match candidate_journal_reference",
        )
        _require_identifier(reviewed_match.rule_code, field_name="reviewed match rule_code")
        for field_name in ("statement_amount", "journal_amount"):
            value = getattr(reviewed_match, field_name)
            if not isinstance(value, Decimal) or not value.is_finite() or value <= 0:
                raise ValueError(
                    "reviewed match candidate amounts must be positive exact Decimals"
                )
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
                _require_identifier(
                    allocation.allocation_reference,
                    field_name=f"reviewed {field_name} allocation_reference",
                )
                _require_identifier(
                    allocation.source_reference,
                    field_name=f"reviewed {field_name} source_reference",
                )
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
            if tuple(
                allocation.allocation_reference for allocation in allocations
            ) != tuple(
                sorted(
                    allocation.allocation_reference for allocation in allocations
                )
            ):
                raise ValueError("reviewed allocation evidence must use deterministic ordering")
            if len({allocation.allocation_reference for allocation in allocations}) != len(
                allocations
            ):
                raise ValueError("reviewed allocation identities must be unique")
        _validate_reviewed_allocation_conservation(reviewed_match)
    if len(projection.reviewed_match_evidence) != projection.safely_matchable_candidate_count:
        raise ValueError(
            "reviewed match evidence must exactly cover the safely matchable proposals"
        )
    if tuple(
        reviewed_match.reconciliation_match_reference
        for reviewed_match in projection.reviewed_match_evidence
    ) != projection.reviewed_match_references:
        raise ValueError("reviewed match evidence must bind projection match identities")
    for reviewed_match in projection.reviewed_match_evidence:
        if reviewed_match.candidate_statement_reference not in {
            allocation.source_reference
            for allocation in reviewed_match.statement_allocations
        } or reviewed_match.candidate_journal_reference not in {
            allocation.source_reference
            for allocation in reviewed_match.journal_allocations
        }:
            raise ValueError(
                "reviewed match candidate source identities must be present in allocation populations"
            )
    _validate_reviewed_population_source_capacity(projection.reviewed_match_evidence)
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

    bridge_unexplained_difference = _exact_decimal_sum(
        projection.reconciled_balance,
        projection.outstanding_book_items,
        projection.outstanding_bank_items.copy_negate(),
        projection.bank_closing_balance.copy_negate(),
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
    statement_artifacts = tuple(
        evidence
        for evidence in evidence_references
        if evidence.evidence_kind_code == "statement_artifact"
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
    if len(statement_artifacts) != 1:
        raise ValueError("evidence_references must include exactly one statement_artifact evidence")
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
        "source_payload_hash": approval.source_payload_hash,
        "reconciliation_snapshot_sha256": approval.reconciliation_snapshot_sha256,
        "evidence_reference": approval.evidence_reference,
    }


def _validate_approval_evidence(
    approval_evidence: object,
    *,
    projection: ReconciliationCloseReviewProjection,
    snapshot_tenant_identity: str | None = None,
) -> tuple[ReconciliationApprovalEvidence, ...]:
    """Bind complete approved match evidence to the projection's immutable scope."""
    if not isinstance(approval_evidence, tuple):
        raise ValueError("approval evidence must be a tuple")
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
    reviewed_matches = {
        reviewed_match.reconciliation_match_reference: reviewed_match
        for reviewed_match in projection.reviewed_match_evidence
    }
    snapshot_tenant_identity = (
        snapshot_tenant_identity or projection.tenant_account_reference
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
        expected_snapshot = _reconciliation_match_snapshot_sha256(
            snapshot_tenant_identity,
            projection.reconciliation_run_reference,
            reviewed_matches[approval.reconciliation_match_reference],
        )
        if not hmac.compare_digest(
            approval.reconciliation_snapshot_sha256,
            expected_snapshot,
        ):
            raise ValueError(
                "approval evidence snapshot must match complete reviewed match facts"
            )
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


def _validate_approval_payload_provenance(
    approval_evidence: tuple[ReconciliationApprovalEvidence, ...],
    evidence_references: tuple[ReconciliationEvidenceReference, ...],
) -> None:
    """Bind every approval command hash to separately retained immutable evidence."""
    payload_evidence = tuple(
        evidence
        for evidence in evidence_references
        if evidence.evidence_kind_code == "reconciliation_approval_payload"
    )
    expected_references = {
        approval.evidence_reference for approval in approval_evidence
    }
    actual_references = {
        evidence.evidence_reference for evidence in payload_evidence
    }
    if actual_references != expected_references:
        raise ValueError(
            "approval source payload evidence must exactly cover every packaged approval"
        )
    payload_by_reference = {
        evidence.evidence_reference: evidence.sha256_digest
        for evidence in payload_evidence
    }
    for approval in approval_evidence:
        if not hmac.compare_digest(
            payload_by_reference[approval.evidence_reference],
            approval.source_payload_hash,
        ):
            raise ValueError(
                "approval source payload evidence digest must match the retained immutable payload"
            )


def _validate_active_match_state_evidence(
    approval_evidence: tuple[ReconciliationApprovalEvidence, ...],
    evidence_references: tuple[ReconciliationEvidenceReference, ...],
) -> None:
    """Require retained evidence that every packaged match remains currently approved."""
    state_evidence = tuple(
        evidence
        for evidence in evidence_references
        if evidence.evidence_kind_code == "reconciliation_match_state"
    )
    expected_references = {
        f"{approval.reconciliation_match_reference}:approved"
        for approval in approval_evidence
    }
    actual_references = {
        evidence.evidence_reference for evidence in state_evidence
    }
    if actual_references != expected_references:
        raise ValueError(
            "current match state evidence must exactly prove every packaged approval remains approved"
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
        "schema_version": 4,
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


def _build_reconciliation_close_package_from_verified_state(
    package_input: ReconciliationClosePackageInput,
) -> ReconciliationClosePackage:
    """Build a deterministic package from already database-verified state evidence."""
    if not isinstance(package_input, ReconciliationClosePackageInput):
        raise ValueError("package_input must be a ReconciliationClosePackageInput")
    projection = _validate_projection(package_input.projection)
    snapshot_tenant_identity = _snapshot_tenant_identity_from_evidence(
        package_input.evidence_references,
        projection=projection,
    )
    approval_evidence = _validate_approval_evidence(
        package_input.approval_evidence,
        projection=projection,
        snapshot_tenant_identity=snapshot_tenant_identity,
    )
    knowledge_cutoff = _require_knowledge_cutoff(package_input.knowledge_cutoff)
    ordered_evidence = _validate_and_order_evidence(
        package_input.evidence_references,
        projection=projection,
    )
    _validate_approval_payload_provenance(approval_evidence, ordered_evidence)
    _validate_active_match_state_evidence(approval_evidence, ordered_evidence)
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


def _database_owned_match_state_evidence(
    connection: object,
    tenant_account_id: object,
    *,
    tenant_reference: str,
    reconciliation_run_reference: str,
    approval_evidence: tuple[ReconciliationApprovalEvidence, ...],
) -> tuple[ReconciliationEvidenceReference, ...]:
    """Read and bind the complete active-approved database state for the run."""
    match_references = [
        approval.reconciliation_match_reference for approval in approval_evidence
    ]
    rows = connection.execute(
        """
        SELECT match.reconciliation_match_id::text,
               match.match_status_code,
               approval.approval_decision_code,
               approval.source_payload_hash,
               approval.source_payload_reference,
               approval.reconciliation_snapshot_hash
        FROM accounting_core.reconciliation_match AS match
        LEFT JOIN accounting_core.reconciliation_approval AS approval
          ON approval.tenant_account_id = match.tenant_account_id
         AND approval.reconciliation_run_id = match.reconciliation_run_id
         AND approval.reconciliation_match_id = match.reconciliation_match_id
        WHERE match.tenant_account_id = %s
          AND match.reconciliation_run_id::text = %s
        FOR SHARE OF match
        """,
        (tenant_account_id, reconciliation_run_reference),
    ).fetchall()
    rows_by_match = {str(row[0]): row for row in rows}
    active_approved_match_references = {
        str(row[0]) for row in rows if row[1] == "approved"
    }
    if active_approved_match_references != set(match_references):
        raise ValueError(
            "database-owned match state must exactly match the active approved match population"
        )

    state_evidence: list[ReconciliationEvidenceReference] = []
    for approval in approval_evidence:
        row = rows_by_match[approval.reconciliation_match_reference]
        (
            _match_reference,
            match_status_code,
            approval_decision_code,
            source_payload_hash,
            source_payload_reference,
            reconciliation_snapshot_hash,
        ) = row
        if match_status_code != "approved" or approval_decision_code != "approved":
            raise ValueError(
                "database-owned match state must remain approved when the close package is built"
            )
        if not hmac.compare_digest(str(source_payload_hash), approval.source_payload_hash):
            raise ValueError(
                "database-owned approval payload hash must match packaged approval evidence"
            )
        if str(source_payload_reference) != approval.evidence_reference:
            raise ValueError(
                "database-owned approval payload reference must match packaged approval evidence"
            )
        if not hmac.compare_digest(
            str(reconciliation_snapshot_hash),
            approval.reconciliation_snapshot_sha256,
        ):
            raise ValueError(
                "database-owned approval snapshot must match packaged approval evidence"
            )
        state_payload = "\n".join(
            (
                "reconciliation_match_state_version=1",
                "tenant=" + _snapshot_value(tenant_reference),
                "run=" + _snapshot_value(reconciliation_run_reference),
                "match=" + _snapshot_value(approval.reconciliation_match_reference),
                "status=" + _snapshot_value(str(match_status_code)),
                "approval_snapshot=" + _snapshot_value(str(reconciliation_snapshot_hash)),
            )
        )
        state_evidence.append(
            ReconciliationEvidenceReference(
                evidence_kind_code="reconciliation_match_state",
                evidence_reference=(
                    f"{approval.reconciliation_match_reference}:approved"
                ),
                sha256_digest="sha256:"
                + hashlib.sha256(state_payload.encode("utf-8")).hexdigest(),
            )
        )
    return tuple(
        sorted(state_evidence, key=lambda evidence: evidence.evidence_reference)
    )


def _database_owned_run_source_evidence(
    connection: object,
    tenant_account_id: object,
    *,
    tenant_reference: str,
    reconciliation_run_reference: str,
) -> tuple[ReconciliationEvidenceReference, ReconciliationEvidenceReference]:
    """Load the immutable run cutoff, command digest, and retained statement artifact."""
    rows = connection.execute(
        """
        SELECT run_record.knowledge_cutoff_at,
               run_command.reconciliation_command_hash,
               run_command.source_payload_hash,
               run_command.source_payload_reference,
               statement_record.source_artifact_hash,
               statement_artifact.source_artifact_hash,
               statement_artifact.artifact_store_reference
        FROM accounting_core.reconciliation_run AS run_record
        JOIN accounting_core.reconciliation_run_command AS run_command
          ON run_command.tenant_account_id = run_record.tenant_account_id
         AND run_command.reconciliation_run_id = run_record.reconciliation_run_id
        JOIN accounting_integration.bank_statement_record AS statement_record
          ON statement_record.tenant_account_id = run_command.tenant_account_id
         AND statement_record.bank_statement_record_id = run_command.bank_statement_record_id
        JOIN accounting_integration.bank_statement_artifact AS statement_artifact
          ON statement_artifact.tenant_account_id = statement_record.tenant_account_id
         AND statement_artifact.bank_statement_artifact_id =
             statement_record.bank_statement_artifact_id
        WHERE run_record.tenant_account_id = %s
          AND run_record.reconciliation_run_id::text = %s
        FOR SHARE OF run_record, run_command, statement_record, statement_artifact
        """,
        (tenant_account_id, reconciliation_run_reference),
    ).fetchall()
    if len(rows) != 1:
        raise ValueError(
            "database-owned reconciliation run evidence must resolve exactly one "
            "run command and statement artifact"
        )
    (
        knowledge_cutoff_at,
        reconciliation_command_hash,
        command_source_hash,
        command_source_reference,
        statement_source_hash,
        artifact_source_hash,
        artifact_store_reference,
    ) = rows[0]
    if not hmac.compare_digest(str(command_source_hash), str(statement_source_hash)):
        raise ValueError(
            "database-owned reconciliation run command source hash must match "
            "the retained statement record"
        )
    if not hmac.compare_digest(str(statement_source_hash), str(artifact_source_hash)):
        raise ValueError(
            "database-owned retained statement artifact hash must match "
            "the statement record"
        )
    if str(command_source_reference) != str(artifact_store_reference):
        raise ValueError(
            "database-owned retained statement artifact reference must match "
            "the reconciliation run command"
        )
    run_evidence = ReconciliationEvidenceReference(
        evidence_kind_code="reconciliation_run",
        evidence_reference=reconciliation_run_reference,
        sha256_digest=str(reconciliation_command_hash),
        knowledge_cutoff=_format_timestamp(knowledge_cutoff_at),
    )
    statement_artifact_evidence = ReconciliationEvidenceReference(
        evidence_kind_code="statement_artifact",
        evidence_reference=str(artifact_store_reference),
        sha256_digest=str(artifact_source_hash),
    )
    return run_evidence, statement_artifact_evidence


def build_reconciliation_close_package(
    package_input: ReconciliationClosePackageInput,
    *,
    database_url: str | None = None,
    tenant_reference: str | None = None,
) -> ReconciliationClosePackage:
    """Build close evidence only while its reviewed matches remain approved in PostgreSQL."""
    if not isinstance(package_input, ReconciliationClosePackageInput):
        raise ValueError("package_input must be a ReconciliationClosePackageInput")
    if not database_url or not tenant_reference:
        raise ValueError(
            "database-owned match state verification is required to build a close package"
        )
    if tenant_reference != package_input.projection.tenant_account_reference:
        raise ValueError(
            "database-owned match state tenant must match the close-package projection"
        )
    ledger = PostgresPostingLedger(database_url, tenant_reference)
    with ledger._session() as connection:
        tenant_account_id = ledger._require_tenant(connection)
        authoritative_snapshot_tenant = _snapshot_tenant_identity_evidence(
            tenant_reference=tenant_reference,
            tenant_account_id=tenant_account_id,
        )
        authoritative_state = _database_owned_match_state_evidence(
            connection,
            tenant_account_id,
            tenant_reference=tenant_reference,
            reconciliation_run_reference=package_input.projection.reconciliation_run_reference,
            approval_evidence=package_input.approval_evidence,
        )
        authoritative_run, authoritative_artifact = _database_owned_run_source_evidence(
            connection,
            tenant_account_id,
            tenant_reference=tenant_reference,
            reconciliation_run_reference=package_input.projection.reconciliation_run_reference,
        )
        packaged_runs = tuple(
            evidence
            for evidence in package_input.evidence_references
            if evidence.evidence_kind_code == "reconciliation_run"
        )
        packaged_artifacts = tuple(
            evidence
            for evidence in package_input.evidence_references
            if evidence.evidence_kind_code == "statement_artifact"
        )
        if package_input.knowledge_cutoff != authoritative_run.knowledge_cutoff:
            raise ValueError(
                "knowledge_cutoff must match the database-owned reconciliation run cutoff"
            )
        if len(packaged_runs) != 1 or packaged_runs[0] != authoritative_run:
            raise ValueError(
                "database-owned reconciliation run evidence must match the packaged run evidence"
            )
        if len(packaged_artifacts) != 1 or packaged_artifacts[0] != authoritative_artifact:
            raise ValueError(
                "database-owned statement artifact evidence must match the packaged artifact evidence"
            )
        retained_evidence = tuple(
            evidence
            for evidence in package_input.evidence_references
            if evidence.evidence_kind_code
            not in {
                "reconciliation_run",
                "statement_artifact",
                "reconciliation_match_state",
                _SNAPSHOT_TENANT_EVIDENCE_KIND,
            }
        )
        verified_input = ReconciliationClosePackageInput(
            projection=package_input.projection,
            approval_evidence=package_input.approval_evidence,
            knowledge_cutoff=authoritative_run.knowledge_cutoff,
            evidence_references=(
                retained_evidence
                + (
                    authoritative_run,
                    authoritative_artifact,
                    authoritative_snapshot_tenant,
                )
                + authoritative_state
            ),
        )
        return _build_reconciliation_close_package_from_verified_state(verified_input)


def verify_reconciliation_close_package(package: ReconciliationClosePackage) -> None:
    """Fail closed unless a package is canonical and matches its committed SHA-256."""
    if not isinstance(package, ReconciliationClosePackage):
        raise ValueError("package must be a ReconciliationClosePackage")
    _require_sha256(package.package_sha256, field_name="package_sha256")
    try:
        rebuilt = _build_reconciliation_close_package_from_verified_state(
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
        or package.approval_evidence != rebuilt.approval_evidence
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
