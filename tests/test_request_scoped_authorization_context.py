"""Regression contracts for request-scoped authenticated principal resolution."""

from __future__ import annotations

import inspect
import unittest
import unittest.mock as mock
from email.message import Message
from types import SimpleNamespace

from accounting_information_platform.authorization import AuthenticatedPrincipal
from accounting_information_platform.http_api import (
    JournalProposalHandler,
    create_journal_proposal_server,
)


_TENANT = "urn:cwl:tenant:request-auth"


def _principal(reference: str, *permissions: str) -> AuthenticatedPrincipal:
    """Build one already-validated host identity for a transport-boundary test."""
    return AuthenticatedPrincipal(
        principal_reference=f"urn:cwl:principal:{reference}",
        tenant_reference=_TENANT,
        authentication_context_reference=f"urn:cwl:authentication:{reference}",
        granted_permission_codes=frozenset(permissions),
        purpose_code="request_scope_test",
        credential_evidence_reference=f"urn:cwl:evidence:{reference}",
        principal_kind="human",
    )


class RequestScopedAuthorizationContextTests(unittest.TestCase):
    """Prevent one server-wide principal from becoming every caller's identity."""

    def _handler(self, resolver: object, identity: str) -> JournalProposalHandler:
        """Construct a handler with one transport identity marker and resolver."""
        handler = object.__new__(JournalProposalHandler)
        headers = Message()
        headers.add_header("X-CWL-Tenant-Reference", _TENANT)
        headers.add_header("X-Test-Validated-Identity", identity)
        handler.headers = headers
        handler.server = SimpleNamespace(
            database_url="postgresql://unused",
            tenant_reference=_TENANT,
            request_principal_resolver=resolver,
        )
        handler._write_error = mock.Mock()  # type: ignore[method-assign]
        return handler

    def test_public_server_factory_requires_a_request_principal_resolver(self) -> None:
        """The production factory must not accept one static principal for all requests."""
        parameters = inspect.signature(create_journal_proposal_server).parameters

        self.assertIn("request_principal_resolver", parameters)
        self.assertNotIn("authorization_context", parameters)

    def test_each_request_resolves_its_own_validated_principal(self) -> None:
        """Two requests on one server cannot silently inherit the same caller authority."""
        reader = _principal("reader", "accounting.read_catalog")
        observer = _principal("observer")
        seen: list[str] = []

        def resolver(request: JournalProposalHandler) -> AuthenticatedPrincipal | None:
            identity = request.headers.get("X-Test-Validated-Identity", "")
            seen.append(identity)
            return {"reader": reader, "observer": observer}.get(identity)

        reader_handler = self._handler(resolver, "reader")
        observer_handler = self._handler(resolver, "observer")
        with mock.patch(
            "accounting_information_platform.http_api.record_authorization_decision"
        ) as record:
            self.assertTrue(
                JournalProposalHandler._authorize_request(
                    reader_handler, "read_catalog", "/legal-entities"
                )
            )
            self.assertFalse(
                JournalProposalHandler._authorize_request(
                    observer_handler, "read_catalog", "/legal-entities"
                )
            )

        self.assertEqual(seen, ["reader", "observer"])
        self.assertEqual(record.call_count, 2)
        observer_handler._write_error.assert_called_once()
        self.assertEqual(observer_handler._write_error.call_args.args[0], 403)

    def test_identity_adapter_failure_is_fail_closed_before_audit_allow(self) -> None:
        """An unavailable trusted identity adapter cannot fall back to shared authority."""
        resolver = mock.Mock(side_effect=RuntimeError("identity provider unavailable"))
        handler = self._handler(resolver, "reader")

        with mock.patch(
            "accounting_information_platform.http_api.record_authorization_decision"
        ) as record:
            self.assertFalse(
                JournalProposalHandler._authorize_request(
                    handler, "read_catalog", "/legal-entities"
                )
            )

        record.assert_not_called()
        handler._write_error.assert_called_once()
        self.assertEqual(handler._write_error.call_args.args[0], 503)
        self.assertNotIn(
            "identity provider unavailable",
            handler._write_error.call_args.args[1],
        )

    def test_malformed_identity_adapter_output_is_fail_closed_before_audit_allow(self) -> None:
        """Malformed trusted-adapter output must return 503 instead of dropping the request."""
        resolver = mock.Mock(return_value=object())
        handler = self._handler(resolver, "reader")

        with mock.patch(
            "accounting_information_platform.http_api.record_authorization_decision"
        ) as record:
            self.assertFalse(
                JournalProposalHandler._authorize_request(
                    handler, "read_catalog", "/legal-entities"
                )
            )

        record.assert_not_called()
        handler._write_error.assert_called_once()
        self.assertEqual(handler._write_error.call_args.args[0], 503)
        self.assertIn(
            "trusted identity adapter",
            handler._write_error.call_args.args[1],
        )


if __name__ == "__main__":
    unittest.main()
