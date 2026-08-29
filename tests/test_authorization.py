"""Unit contracts for the purpose-bound application authorization port."""

from __future__ import annotations

import copy
import json
import unittest
from dataclasses import replace
from email.message import Message
from types import SimpleNamespace
import unittest.mock as mock

from accounting_information_platform import AccountingValidationError
from accounting_information_platform.authorization import (
    AUTHORIZATION_POLICY_VERSION,
    AuthenticatedPrincipal,
    AuthorizationDecision,
    authorize,
    period_close_operation,
    record_authorization_decision,
    require_authorization,
)
from accounting_information_platform.http_api import (
    JournalProposalHandler,
    _authorization_correlation,
    _post_authorization_operation,
)


TENANT = "urn:cwl:tenant_test"


def principal(*permissions: str, principal_kind: str = "human") -> AuthenticatedPrincipal:
    """Build a validated test principal without carrying a bearer token."""
    return AuthenticatedPrincipal(
        principal_reference="urn:cwl:principal:test",
        tenant_reference=TENANT,
        authentication_context_reference="urn:cwl:authentication:test",
        granted_permission_codes=frozenset(permissions),
        purpose_code="month_end_control",
        credential_evidence_reference="urn:cwl:auth-evidence:test",
        principal_kind=principal_kind,
    )


class AuthorizationContractTests(unittest.TestCase):
    """Keep authorization decisions explicit, versioned, and fail closed."""

    def test_allowed_decision_requires_exact_permission_and_preserves_evidence(self) -> None:
        """A matching tenant and permission produce a complete immutable decision."""
        decision = require_authorization(
            principal("accounting.read_catalog"), TENANT, "read_catalog"
        )

        self.assertTrue(decision.allowed)
        self.assertEqual(decision.decision_code, "allowed")
        self.assertEqual(decision.permission_code, "accounting.read_catalog")
        self.assertEqual(decision.policy_version, AUTHORIZATION_POLICY_VERSION)
        self.assertEqual(decision.authentication_context_reference, "urn:cwl:authentication:test")
        self.assertEqual(decision.credential_evidence_reference, "urn:cwl:auth-evidence:test")

    def test_missing_permission_is_denied_without_exposing_grants(self) -> None:
        """Tenant authentication alone cannot invoke a posting operation."""
        decision = authorize(principal("accounting.read_catalog"), TENANT, "post_proposal")

        self.assertFalse(decision.allowed)
        self.assertEqual(decision.decision_code, "denied")
        self.assertEqual(decision.permission_code, "accounting.post_proposal")
        with self.assertRaisesRegex(PermissionError, "accounting.post_proposal"):
            require_authorization(principal("accounting.read_catalog"), TENANT, "post_proposal")
        self.assertNotIn("accounting.read_catalog", str(decision))

    def test_tenant_mismatch_is_denied_before_permission_evaluation(self) -> None:
        """A forged tenant target cannot reuse a valid principal permission."""
        decision = authorize(principal("accounting.read_catalog"), "urn:cwl:tenant_other", "read_catalog")

        self.assertFalse(decision.allowed)
        self.assertEqual(decision.decision_code, "denied")
        self.assertEqual(decision.tenant_reference, TENANT)
        self.assertEqual(decision.requested_tenant_reference, "urn:cwl:tenant_other")

    def test_unknown_operation_is_denied_fail_closed(self) -> None:
        """Adding a route without a policy entry cannot inherit writer access."""
        decision = authorize(principal("accounting.post_proposal"), TENANT, "new_operation")

        self.assertFalse(decision.allowed)
        self.assertEqual(decision.permission_code, "")
        with self.assertRaisesRegex(PermissionError, "unknown accounting operation"):
            require_authorization(principal("accounting.post_proposal"), TENANT, "new_operation")

    def test_agent_principal_cannot_receive_high_impact_authority_by_default(self) -> None:
        """Model-originated context remains unable to post even with a copied grant."""
        decision = authorize(
            principal("accounting.post_proposal", principal_kind="agent"),
            TENANT,
            "post_proposal",
        )

        self.assertFalse(decision.allowed)

    def test_period_close_permissions_are_independent(self) -> None:
        """Soft-close and hard-close commands require distinct purpose-bound permissions."""
        self.assertEqual(period_close_operation("soft_closed"), "soft_close_period")
        self.assertEqual(period_close_operation("hard_closed"), "hard_close_period")
        self.assertEqual(period_close_operation(None), "hard_close_period")
        self.assertEqual(period_close_operation("open"), "unknown_period_close_operation")

    def test_missing_context_is_denied(self) -> None:
        """A tenant header without an authenticated principal is not authorization."""
        decision = authorize(None, TENANT, "read_catalog")

        self.assertFalse(decision.allowed)
        self.assertEqual(decision.principal_reference, "urn:cwl:principal:unauthenticated")

    def test_invalid_principal_claims_are_rejected(self) -> None:
        """The host adapter must provide opaque, bounded identity evidence."""
        with self.assertRaises(ValueError):
            principal("ACCOUNTING.READ_CATALOG")
        with self.assertRaises(ValueError):
            principal(principal_kind="robot")
        with self.assertRaises(ValueError):
            AuthenticatedPrincipal(
                principal_reference="",
                tenant_reference=TENANT,
                authentication_context_reference="urn:cwl:authentication:test",
                granted_permission_codes=frozenset(),
                purpose_code="month_end_control",
                credential_evidence_reference="urn:cwl:auth-evidence:test",
                principal_kind="human",
            )

    def test_principal_kind_must_be_explicit_at_the_trust_boundary(self) -> None:
        """Omitting caller kind cannot silently elevate an agent to a human principal."""
        with self.assertRaises(TypeError):
            AuthenticatedPrincipal(
                principal_reference="urn:cwl:principal:missing-kind",
                tenant_reference=TENANT,
                authentication_context_reference="urn:cwl:authentication:test",
                granted_permission_codes=frozenset(),
                purpose_code="month_end_control",
                credential_evidence_reference="urn:cwl:auth-evidence:test",
            )

    def test_http_authorization_evidence_failure_is_fail_closed(self) -> None:
        """A missing audit store cannot let an otherwise permitted request reach the domain."""
        handler = object.__new__(JournalProposalHandler)
        headers = Message()
        headers.add_header("X-CWL-Tenant-Reference", TENANT)
        handler.headers = headers
        handler.server = SimpleNamespace(
            database_url="postgresql://unused",
            tenant_reference=TENANT,
            authorization_context=principal("accounting.read_catalog"),
        )
        handler._write_error = mock.Mock()  # type: ignore[method-assign]
        with mock.patch(
            "accounting_information_platform.http_api.record_authorization_decision",
            side_effect=RuntimeError("audit store unavailable"),
        ):
            self.assertFalse(
                JournalProposalHandler._authorize_request(
                    handler, "read_catalog", "/account-role-mappings"
                )
            )
        handler._write_error.assert_called_once()
        self.assertEqual(handler._write_error.call_args.args[0], 503)
        handler._write_error.reset_mock()
        with mock.patch(
            "accounting_information_platform.http_api.record_authorization_decision",
            side_effect=AccountingValidationError(
                "Tenant urn:cwl:tenant_missing is not recorded. Create the tenant_account row, then retry posting."
            ),
        ):
            self.assertFalse(
                JournalProposalHandler._authorize_request(
                    handler, "read_catalog", "/account-role-mappings"
                )
            )
        handler._write_error.assert_called_once()
        self.assertEqual(handler._write_error.call_args.args[0], 503)
        self.assertIn("tenant_account row", handler._write_error.call_args.args[1])
        self.assertNotIn("audit store", handler._write_error.call_args.args[1])
        handler._write_error.reset_mock()
        with mock.patch(
            "accounting_information_platform.http_api.record_authorization_decision"
        ):
            self.assertFalse(
                JournalProposalHandler._authorize_request(
                    handler, "unknown_operation", "/unknown"
                )
            )
        handler._write_error.assert_called_once()
        self.assertEqual(handler._write_error.call_args.args[0], 403)

    def test_http_operation_and_correlation_helpers_are_bounded(self) -> None:
        """Route classification accepts only registered operations and bounded command identity."""
        outbox_path = "/outbox-events/019d7b92-1aa0-7a7f-b61c-962c0f4bf612/publish"
        self.assertEqual(
            _post_authorization_operation(outbox_path, b"{}"), "publish_outbox"
        )
        self.assertEqual(
            _post_authorization_operation("/period-closes", b'{"period_status_code":"soft_closed"}'),
            "soft_close_period",
        )
        self.assertEqual(
            _post_authorization_operation("/period-closes", b"not-json"),
            "hard_close_period",
        )
        self.assertEqual(
            _post_authorization_operation("/period-closes", b"[]"),
            "hard_close_period",
        )
        self.assertEqual(_post_authorization_operation("/unknown", b"{}"), None)
        self.assertEqual(
            _authorization_correlation(
                "/journal-proposals", b'{"idempotency_key":"command-1"}'
            ),
            "idempotency_key:command-1",
        )
        self.assertEqual(_authorization_correlation("/journal-proposals", b"[]"), "/journal-proposals")
        self.assertEqual(
            _authorization_correlation("/journal-proposals", b'{"idempotency_key":""}'),
            "/journal-proposals",
        )
        long_key = "x" * (512 - len("idempotency_key:"))
        self.assertEqual(
            _authorization_correlation(
                "/journal-proposals",
                json.dumps({"idempotency_key": long_key}).encode("utf-8"),
            ),
            f"idempotency_key:{long_key}",
        )
        too_long_key = "x" * (513 - len("idempotency_key:"))
        self.assertEqual(
            _authorization_correlation(
                "/journal-proposals",
                json.dumps({"idempotency_key": too_long_key}).encode("utf-8"),
            ),
            "/journal-proposals",
        )

    def test_authorization_correlation_is_required_before_database_work(self) -> None:
        """Authorization evidence rejects an absent correlation identity before opening PostgreSQL."""
        decision = authorize(principal("accounting.read_catalog"), TENANT, "read_catalog")
        with self.assertRaisesRegex(ValueError, "correlation reference"):
            record_authorization_decision("postgresql://unused", TENANT, decision, "")

    def test_authorization_evidence_rejects_requested_tenant_outside_storage_scope(self) -> None:
        """Audit evidence cannot claim a requested tenant different from its storage scope."""
        decision = authorize(principal("accounting.read_catalog"), "urn:cwl:tenant_other", "read_catalog")

        with self.assertRaisesRegex(ValueError, "tenant scope"):
            record_authorization_decision("postgresql://unused", TENANT, decision, "/account-role-mappings")

    def test_authorization_evidence_rejects_caller_constructed_allow(self) -> None:
        """Audit persistence cannot promote a caller-constructed decision to allowed evidence."""
        decision = AuthorizationDecision(
            principal_reference="urn:cwl:principal:forged",
            tenant_reference=TENANT,
            requested_tenant_reference=TENANT,
            authentication_context_reference="urn:cwl:authentication:forged",
            credential_evidence_reference="urn:cwl:auth-evidence:forged",
            operation_code="read_catalog",
            permission_code="accounting.read_catalog",
            purpose_code="catalog_read",
            policy_version=AUTHORIZATION_POLICY_VERSION,
            decision_code="allowed",
            allowed=True,
        )

        with self.assertRaisesRegex(ValueError, "issued by authorize"):
            record_authorization_decision(
                "postgresql://unused", TENANT, decision, "forged-decision"
            )

    def test_authorization_evidence_accepts_a_copied_policy_decision(self) -> None:
        """Copying evaluator evidence preserves its provenance for the persistence boundary."""
        decision = copy.deepcopy(
            authorize(principal("accounting.read_catalog"), TENANT, "read_catalog")
        )
        ledger = mock.Mock()
        session = mock.MagicMock()
        session.__enter__.return_value = mock.Mock()
        ledger._session.return_value = session
        ledger._require_tenant.return_value = "tenant-id"
        with mock.patch(
            "accounting_information_platform.persistence.PostgresPostingLedger",
            return_value=ledger,
        ):
            record_authorization_decision(
                "postgresql://unused", TENANT, decision, "copied-decision"
            )
        ledger._require_tenant.assert_called_once()

    def test_authorization_evidence_accepts_an_unchanged_replacement(self) -> None:
        """Replacing an evaluator decision without changing values preserves its provenance."""
        decision = replace(
            authorize(principal("accounting.read_catalog"), TENANT, "read_catalog")
        )
        ledger = mock.Mock()
        session = mock.MagicMock()
        session.__enter__.return_value = mock.Mock()
        ledger._session.return_value = session
        ledger._require_tenant.return_value = "tenant-id"
        with mock.patch(
            "accounting_information_platform.persistence.PostgresPostingLedger",
            return_value=ledger,
        ):
            record_authorization_decision(
                "postgresql://unused", TENANT, decision, "replacement-decision"
            )
        ledger._require_tenant.assert_called_once()

    def test_authorization_evidence_rejects_mutated_policy_decision(self) -> None:
        """Mutating an issued decision cannot change the evidence accepted for persistence."""
        decision = authorize(principal("accounting.read_catalog"), TENANT, "read_catalog")
        object.__setattr__(decision, "allowed", False)

        with self.assertRaisesRegex(ValueError, "issued by authorize"):
            record_authorization_decision(
                "postgresql://unused", TENANT, decision, "mutated-decision"
            )

    def test_internal_handlers_keep_their_missing_header_guard(self) -> None:
        """Direct handler dispatch remains fail-closed even outside the normal router."""
        handler_names = (
            "_get_posting_receipt",
            "_get_trial_balance",
            "_get_financial_statement",
            "_get_financial_statement_package",
            "_get_account_role_mappings",
            "_get_accounting_books",
            "_get_legal_entities",
            "_get_chart_accounts",
            "_get_account_rollforward",
            "_get_unapplied_cash_rollforward",
            "_get_vat_period_register",
            "_get_home_tax_submissions",
            "_get_account_balances",
            "_get_receivable_aging",
            "_get_payable_aging",
            "_get_period_close_package",
            "_get_account_ledger",
            "_get_journal_reversals",
            "_get_period_closes",
            "_get_posted_journal",
            "_get_outbox_events",
            "_get_audit_events",
            "_get_fiscal_period",
            "_get_bank_statements",
            "_get_bank_statement_entries",
            "_post_outbox_publish",
            "_post_fiscal_period",
            "_post_adjusting_journal",
            "_post_journal_proposal",
            "_post_journal_reversal",
            "_post_period_close",
            "_post_home_tax_submission",
            "_post_bank_account",
            "_post_bank_account_assignment",
            "_post_bank_statement",
            "_post_billing_proposal_pull",
        )
        for method_name in handler_names:
            with self.subTest(method_name=method_name):
                handler = object.__new__(JournalProposalHandler)
                handler.headers = Message()
                handler._write_error = mock.Mock()  # type: ignore[method-assign]
                method = getattr(JournalProposalHandler, method_name)
                if method_name == "_get_legal_entities":
                    method(handler)
                elif method_name == "_post_outbox_publish":
                    method(handler, "019d7b92-1aa0-7a7f-b61c-962c0f4bf612")
                elif method_name.startswith("_get_"):
                    method(handler, "")
                else:
                    method(handler, b"{}")
                handler._write_error.assert_called_once()
                self.assertEqual(handler._write_error.call_args.args[0], 400)

    def test_legal_entity_lookup_preserves_missing_catalog_error(self) -> None:
        """A validly authorized route still maps an absent tenant catalog to its client error."""
        handler = object.__new__(JournalProposalHandler)
        headers = Message()
        headers.add_header("X-CWL-Tenant-Reference", TENANT)
        handler.headers = headers
        handler.server = SimpleNamespace(
            database_url="postgresql://unused",
            tenant_reference=TENANT,
        )
        handler._write_error = mock.Mock()  # type: ignore[method-assign]
        handler._write_json = mock.Mock()  # type: ignore[method-assign]
        with mock.patch(
            "accounting_information_platform.http_api.lookup_legal_entities",
            side_effect=AccountingValidationError("tenant is not recorded"),
        ):
            JournalProposalHandler._get_legal_entities(handler)
        handler._write_error.assert_called_once_with(404, "tenant is not recorded")


if __name__ == "__main__":
    unittest.main()
