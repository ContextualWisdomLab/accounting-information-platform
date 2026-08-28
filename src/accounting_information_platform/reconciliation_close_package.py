"""Tamper-evident reconciliation evidence packages for period-close review.

The package binds an already read-only close-review projection to immutable
approval and source-evidence hashes. It is an evidence manifest only: creating or
rendering it cannot approve reconciliation, mutate accounting facts, close a
period, or post a journal.
"""

from __future__ import annotations

import hashlib
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
_REQUIRED_EVIDENCE_KINDS = frozenset({"statement_artifact", "book_population"})
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
    """Immutable source-evidence identity and SHA-256 digest included in a package."""

    evidence_kind_code: str
    evidence_reference: str
    sha256_digest: str

    def __post_init__(self) -> None:
        """Reject ambiguous identities and non-canonical evidence digests."""
        _require_identifier(self.evidence_kind_code, field_name="evidence_kind_code")
        _require_identifier(self.evidence_reference, field_name="evidence_reference")
        _require_sha256(self.sha256_digest, field_name="sha256_digest")


@dataclass(frozen=True, slots=True)
class ReconciliationClosePackageInput:
    """Evidence required to bind one eligible close-review projection into a package."""

    projection: ReconciliationCloseReviewProjection
    approval_evidence_reference: str
    approval_snapshot_sha256: str
    knowledge_cutoff: str
    evidence_references: tuple[ReconciliationEvidenceReference, ...]


@dataclass(frozen=True, slots=True)
class ReconciliationClosePackage:
    """Canonical tamper-evident period-close reconciliation evidence package."""

    projection: ReconciliationCloseReviewProjection
    approval_evidence_reference: str
    approval_snapshot_sha256: str
    knowledge_cutoff: str
    evidence_references: tuple[ReconciliationEvidenceReference, ...]
    package_sha256: str
    next_action: str


def _evidence_mapping(
    evidence: ReconciliationEvidenceReference,
) -> dict[str, str]:
    """Return one deterministic evidence-reference mapping."""
    return {
        "evidence_kind_code": evidence.evidence_kind_code,
        "evidence_reference": evidence.evidence_reference,
        "sha256_digest": evidence.sha256_digest,
    }


def _package_unsigned_mapping(
    *,
    projection: ReconciliationCloseReviewProjection,
    approval_evidence_reference: str,
    approval_snapshot_sha256: str,
    knowledge_cutoff: str,
    evidence_references: tuple[ReconciliationEvidenceReference, ...],
) -> dict[str, object]:
    """Return the canonical payload committed by ``package_sha256``."""
    return {
        "schema_version": 1,
        "projection": json.loads(render_reconciliation_close_review_json(projection)),
        "approval_evidence_reference": approval_evidence_reference,
        "approval_snapshot_sha256": approval_snapshot_sha256,
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
    projection = package_input.projection
    if (
        not projection.suitable_for_period_close_review
        or projection.exception_count != 0
        or projection.unexplained_difference != Decimal("0")
    ):
        raise ValueError(
            "reconciliation projection is not suitable for period-close review; "
            "resolve the exact bridge or reconciliation exceptions first"
        )

    approval_reference = _require_identifier(
        package_input.approval_evidence_reference,
        field_name="approval_evidence_reference",
    )
    approval_hash = _require_sha256(
        package_input.approval_snapshot_sha256,
        field_name="approval_snapshot_sha256",
    )
    knowledge_cutoff = _require_knowledge_cutoff(package_input.knowledge_cutoff)

    evidence_references = package_input.evidence_references
    if not evidence_references:
        raise ValueError(
            "evidence_references must include immutable statement and book populations"
        )
    identities = tuple(
        (evidence.evidence_kind_code, evidence.evidence_reference)
        for evidence in evidence_references
    )
    if len(set(identities)) != len(identities):
        raise ValueError("evidence_references identities must be unique")
    evidence_kinds = {evidence.evidence_kind_code for evidence in evidence_references}
    if not _REQUIRED_EVIDENCE_KINDS.issubset(evidence_kinds):
        raise ValueError(
            "evidence_references must include statement_artifact and book_population evidence"
        )

    ordered_evidence = tuple(
        sorted(
            evidence_references,
            key=lambda evidence: (
                evidence.evidence_kind_code,
                evidence.evidence_reference,
                evidence.sha256_digest,
            ),
        )
    )
    unsigned_payload = _package_unsigned_mapping(
        projection=projection,
        approval_evidence_reference=approval_reference,
        approval_snapshot_sha256=approval_hash,
        knowledge_cutoff=knowledge_cutoff,
        evidence_references=ordered_evidence,
    )
    package_sha256 = "sha256:" + hashlib.sha256(
        _canonical_json_bytes(unsigned_payload)
    ).hexdigest()
    return ReconciliationClosePackage(
        projection=projection,
        approval_evidence_reference=approval_reference,
        approval_snapshot_sha256=approval_hash,
        knowledge_cutoff=knowledge_cutoff,
        evidence_references=ordered_evidence,
        package_sha256=package_sha256,
        next_action=_NEXT_ACTION,
    )


def render_reconciliation_close_package_json(
    package: ReconciliationClosePackage,
) -> str:
    """Render one deterministic exact-value close package as canonical JSON."""
    payload = _package_unsigned_mapping(
        projection=package.projection,
        approval_evidence_reference=package.approval_evidence_reference,
        approval_snapshot_sha256=package.approval_snapshot_sha256,
        knowledge_cutoff=package.knowledge_cutoff,
        evidence_references=package.evidence_references,
    )
    payload["package_sha256"] = package.package_sha256
    return _canonical_json_bytes(payload).decode("utf-8")
