"""Purpose-bound authorization contracts for the accounting HTTP boundary.

The host identity adapter is responsible for validating the credential. AIS receives only the
opaque, validated claims needed to make an operation decision; bearer material never enters this
module or the accounting domain.
"""

from __future__ import annotations

import hashlib
import hmac
import re
from dataclasses import dataclass, field
from secrets import token_bytes
from types import MappingProxyType
from typing import Mapping

from .core import _require_code, _require_reference


AUTHORIZATION_POLICY_VERSION = "accounting-authorization-v1"
_PERMISSION_PATTERN = re.compile(r"^[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*$")
_PRINCIPAL_KINDS = frozenset(("human", "service", "agent"))


_AUTHORIZATION_DECISION_SEAL_KEY = token_bytes(32)
_DECISION_VALUE_NAMES = (
    "principal_reference",
    "tenant_reference",
    "requested_tenant_reference",
    "authentication_context_reference",
    "credential_evidence_reference",
    "operation_code",
    "permission_code",
    "purpose_code",
    "policy_version",
    "decision_code",
    "allowed",
)

_OPERATION_PERMISSIONS: Mapping[str, str] = MappingProxyType(
    {
        "read_catalog": "accounting.read_catalog",
        "read_journal": "accounting.read_journal",
        "read_financial_statement": "accounting.read_financial_statement",
        "read_close": "accounting.read_close",
        "read_audit": "accounting.read_audit",
        "read_tax_artifact": "accounting.read_tax_artifact",
        "read_bank_statement": "accounting.read_bank_statement",
        "read_receipt": "accounting.read_receipt",
        "post_proposal": "accounting.post_proposal",
        "post_adjustment": "accounting.post_adjustment",
        "reverse_journal": "accounting.reverse_journal",
        "open_period": "accounting.open_period",
        "soft_close_period": "accounting.soft_close_period",
        "hard_close_period": "accounting.hard_close_period",
        "complete_reconciliation": "accounting.complete_reconciliation",
        "publish_outbox": "accounting.publish_outbox",
        "submit_tax_artifact": "accounting.submit_tax_artifact",
        "manage_bank_account": "accounting.manage_bank_account",
        "ingest_bank_statement": "accounting.ingest_bank_statement",
    }
)

_HIGH_IMPACT_OPERATIONS = frozenset(
    {
        "post_proposal",
        "post_adjustment",
        "reverse_journal",
        "open_period",
        "soft_close_period",
        "hard_close_period",
        "complete_reconciliation",
        "publish_outbox",
        "submit_tax_artifact",
        "manage_bank_account",
        "ingest_bank_statement",
    }
)


@dataclass(frozen=True, slots=True)
class AuthenticatedPrincipal:
    """Validated opaque identity claims with an explicit human, service, or agent kind."""

    principal_reference: str
    tenant_reference: str
    authentication_context_reference: str
    granted_permission_codes: frozenset[str]
    purpose_code: str
    credential_evidence_reference: str
    principal_kind: str

    def __post_init__(self) -> None:
        """Reject malformed identity evidence before it reaches route authorization."""
        for value, label in (
            (self.principal_reference, "principal reference"),
            (self.tenant_reference, "tenant reference"),
            (self.authentication_context_reference, "authentication context reference"),
            (self.credential_evidence_reference, "credential evidence reference"),
        ):
            _require_reference(value, label)
        _require_code(self.purpose_code, "purpose code")
        if self.principal_kind not in _PRINCIPAL_KINDS:
            raise ValueError("principal kind must be human, service, or agent")
        permissions = frozenset(self.granted_permission_codes)
        if any(_PERMISSION_PATTERN.fullmatch(permission) is None for permission in permissions):
            raise ValueError("permission codes must use domain.operation syntax")
        object.__setattr__(self, "granted_permission_codes", permissions)


@dataclass(frozen=True, slots=True)
class AuthorizationDecision:
    """Immutable decision evidence suitable for durable authorization audit storage."""

    principal_reference: str
    tenant_reference: str
    requested_tenant_reference: str
    authentication_context_reference: str
    credential_evidence_reference: str
    operation_code: str
    permission_code: str
    purpose_code: str
    policy_version: str
    decision_code: str
    allowed: bool
    _decision_fingerprint: str = field(default="", repr=False, compare=False)


def _decision_fingerprint(decision: AuthorizationDecision) -> str:
    """Fingerprint decision values so post-issuance mutation fails closed."""
    values = tuple(getattr(decision, name) for name in _DECISION_VALUE_NAMES)
    return hmac.new(
        _AUTHORIZATION_DECISION_SEAL_KEY,
        repr(values).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def _issue_authorization_decision(**values: object) -> AuthorizationDecision:
    """Create decision evidence only through the policy evaluator."""
    decision = AuthorizationDecision(**values)
    object.__setattr__(decision, "_decision_fingerprint", _decision_fingerprint(decision))
    return decision


def permission_for_operation(operation_code: str) -> str | None:
    """Return the exact permission required by *operation_code*, if it is registered."""
    return _OPERATION_PERMISSIONS.get(operation_code)


def authorize(
    principal: AuthenticatedPrincipal | None,
    requested_tenant_reference: str,
    operation_code: str,
) -> AuthorizationDecision:
    """Evaluate one operation without accepting authority from request data or model output."""
    _require_reference(requested_tenant_reference, "requested tenant reference")
    permission_code = permission_for_operation(operation_code) or ""
    if principal is None:
        return _issue_authorization_decision(
            principal_reference="urn:cwl:principal:unauthenticated",
            tenant_reference=requested_tenant_reference,
            requested_tenant_reference=requested_tenant_reference,
            authentication_context_reference="urn:cwl:authentication:none",
            credential_evidence_reference="urn:cwl:evidence:none",
            operation_code=operation_code,
            permission_code=permission_code,
            purpose_code="unauthenticated",
            policy_version=AUTHORIZATION_POLICY_VERSION,
            decision_code="denied",
            allowed=False,
        )
    tenant_matches = principal.tenant_reference == requested_tenant_reference
    agent_restricted = principal.principal_kind == "agent" and operation_code in _HIGH_IMPACT_OPERATIONS
    allowed = (
        bool(permission_code)
        and tenant_matches
        and not agent_restricted
        and permission_code in principal.granted_permission_codes
    )
    return _issue_authorization_decision(
        principal_reference=principal.principal_reference,
        tenant_reference=principal.tenant_reference,
        requested_tenant_reference=requested_tenant_reference,
        authentication_context_reference=principal.authentication_context_reference,
        credential_evidence_reference=principal.credential_evidence_reference,
        operation_code=operation_code,
        permission_code=permission_code,
        purpose_code=principal.purpose_code,
        policy_version=AUTHORIZATION_POLICY_VERSION,
        decision_code="allowed" if allowed else "denied",
        allowed=allowed,
    )


def require_authorization(
    principal: AuthenticatedPrincipal | None,
    requested_tenant_reference: str,
    operation_code: str,
) -> AuthorizationDecision:
    """Return allowed decision evidence or raise a caller-safe fail-closed error."""
    decision = authorize(principal, requested_tenant_reference, operation_code)
    if decision.allowed:
        return decision
    if not decision.permission_code:
        raise PermissionError(f"unknown accounting operation {operation_code}; authorization is denied")
    raise PermissionError(
        f"authorization denied for operation {operation_code}; obtain permission "
        f"{decision.permission_code} for purpose {decision.purpose_code}, then retry"
    )


def period_close_operation(period_status_code: object) -> str:
    """Map a period-close status to its independent authorization operation."""
    if period_status_code == "soft_closed":
        return "soft_close_period"
    if period_status_code in (None, "", "hard_closed"):
        return "hard_close_period"
    return "unknown_period_close_operation"


def record_authorization_decision(
    database_url: str,
    tenant_reference: str,
    decision: AuthorizationDecision,
    correlation_reference: str,
) -> None:
    """Append one decision to the tenant-scoped PostgreSQL authorization evidence table."""
    if not correlation_reference or len(correlation_reference) > 512:
        raise ValueError("authorization correlation reference must contain 1 to 512 characters")
    if decision._decision_fingerprint != _decision_fingerprint(decision):
        raise ValueError("authorization decision must be issued by authorize and remain unchanged")
    if decision.requested_tenant_reference != tenant_reference:
        raise ValueError("authorization decision tenant scope must match requested tenant reference")
    from .persistence import PostgresPostingLedger

    ledger = PostgresPostingLedger(database_url, tenant_reference)
    with ledger._session() as connection:
        tenant_id = ledger._require_tenant(connection)
        connection.execute(
            """
            INSERT INTO accounting_integration.authorization_decision_record (
                tenant_account_id, principal_reference, principal_tenant_reference,
                requested_tenant_reference,
                authentication_context_reference, credential_evidence_reference,
                operation_code, permission_code, purpose_code, policy_version,
                decision_code, correlation_reference
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                tenant_id,
                decision.principal_reference,
                decision.tenant_reference,
                decision.requested_tenant_reference,
                decision.authentication_context_reference,
                decision.credential_evidence_reference,
                decision.operation_code,
                decision.permission_code,
                decision.purpose_code,
                decision.policy_version,
                decision.decision_code,
                correlation_reference,
            ),
        )