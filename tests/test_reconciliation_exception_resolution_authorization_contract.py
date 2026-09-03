"""Authorization contracts for the reconciliation-exception review command."""

from __future__ import annotations

import unittest

from accounting_information_platform.authorization import (
    AUTHORIZATION_POLICY_VERSION,
    AuthenticatedPrincipal,
    authorize,
    permission_for_operation,
    require_authorization,
)


_TENANT = "urn:cwl:tenant:reconciliation-exception-auth"
_OPERATION = "resolve_reconciliation_exception"
_PERMISSION = "accounting.resolve_reconciliation_exception"


def _principal(*permissions: str, principal_kind: str = "human") -> AuthenticatedPrincipal:
    """Return one trusted-adapter principal for focused exception-review tests."""
    return AuthenticatedPrincipal(
        principal_reference="urn:cwl:principal:independent_reviewer",
        tenant_reference=_TENANT,
        authentication_context_reference="urn:cwl:authentication:oidc-session",
        granted_permission_codes=frozenset(permissions),
        purpose_code="bank_reconciliation_exception_review",
        credential_evidence_reference="urn:cwl:evidence:credential-session",
        principal_kind=principal_kind,
    )


class ReconciliationExceptionResolutionAuthorizationContractTests(unittest.TestCase):
    """Reserve a distinct high-impact permission before exposing resolution transport."""

    def test_exception_resolution_has_one_explicit_versioned_permission(self) -> None:
        """Exception review must not inherit completion, posting, close, or tenant authority."""
        self.assertEqual(AUTHORIZATION_POLICY_VERSION, "accounting-authorization-v3")
        self.assertEqual(permission_for_operation(_OPERATION), _PERMISSION)
        decision = require_authorization(_principal(_PERMISSION), _TENANT, _OPERATION)
        self.assertTrue(decision.allowed)
        self.assertEqual(decision.permission_code, _PERMISSION)
        self.assertEqual(decision.purpose_code, "bank_reconciliation_exception_review")
        self.assertEqual(decision.policy_version, AUTHORIZATION_POLICY_VERSION)

    def test_other_accounting_permissions_do_not_resolve_exception(self) -> None:
        """Completion, posting, close, and read grants remain non-equivalent authorities."""
        for permission in (
            "accounting.complete_reconciliation",
            "accounting.post_proposal",
            "accounting.hard_close_period",
            "accounting.read_close",
        ):
            with self.subTest(permission=permission):
                decision = authorize(_principal(permission), _TENANT, _OPERATION)
                self.assertFalse(decision.allowed)
                self.assertEqual(decision.permission_code, _PERMISSION)

    def test_agent_origin_is_denied_resolution_even_with_copied_permission(self) -> None:
        """Model/agent identity cannot promote itself into exception-review authority."""
        decision = authorize(
            _principal(_PERMISSION, principal_kind="agent"),
            _TENANT,
            _OPERATION,
        )
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.permission_code, _PERMISSION)


if __name__ == "__main__":  # pragma: no cover - direct invocation convenience
    unittest.main()
