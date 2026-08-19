"""Close coverage and test-fixture gaps after the PR 2 HTTP auth repair."""

from __future__ import annotations

from pathlib import Path


def _read(path: str) -> str:
    """Return one UTF-8 repository file."""
    return Path(path).read_text(encoding="utf-8")


def _write(path: str, text: str) -> None:
    """Replace one UTF-8 repository file."""
    Path(path).write_text(text, encoding="utf-8")


def harden_verifier_return_type() -> None:
    """Reject a misconfigured verifier that returns anything except a tenant string."""
    path = "src/accounting_information_platform/http_api.py"
    text = _read(path)
    anchor = '''        try:
            _require_reference(tenant_reference, "authenticated tenant reference")
        except AccountingValidationError:
'''
    replacement = '''        if not isinstance(tenant_reference, str):
            self._write_error(
                401,
                "Bearer token validation returned an invalid tenant. Obtain a fresh authorized token, then retry.",
            )
            return None
        try:
            _require_reference(tenant_reference, "authenticated tenant reference")
        except AccountingValidationError:
'''
    if replacement not in text:
        if anchor not in text:
            raise SystemExit("HTTP verifier return-type anchor drifted")
        text = text.replace(anchor, replacement, 1)
    _write(path, text)


def complete_postgres_auth_fixtures() -> None:
    """Configure env-loaded verifier paths and cover every fail-closed auth branch."""
    path = "tests/test_postgres_posting.py"
    text = _read(path)

    constants_anchor = '''VALID_FROM = datetime(2026, 1, 1, tzinfo=timezone.utc)


class FakeBillingServer(ThreadingHTTPServer):
'''
    constants_replacement = '''VALID_FROM = datetime(2026, 1, 1, tzinfo=timezone.utc)


def configured_test_bearer_verifier(token: str, issuer: str, audience: str) -> str:
    """Provide a callable fixture for env-based server configuration tests."""
    if not token or not issuer or not audience:
        raise ValueError("test verifier requires token, issuer, and audience")
    return "urn:cwl:tenant_test"


class FakeBillingServer(ThreadingHTTPServer):
'''
    if constants_replacement not in text:
        if constants_anchor not in text:
            raise SystemExit("test auth verifier fixture anchor drifted")
        text = text.replace(constants_anchor, constants_replacement, 1)

    setup_anchor = '''        self.ledger = PostgresPostingLedger(
            DATABASE_URL, tenant_reference=self.policy.tenant_reference
        )
        self._local_billing_origin_patch = mock.patch(
'''
    setup_replacement = '''        self.ledger = PostgresPostingLedger(
            DATABASE_URL, tenant_reference=self.policy.tenant_reference
        )
        self._jwt_environment_patch = mock.patch.dict(
            os.environ,
            {
                "ACCOUNTING_JWT_VERIFIER": (
                    "tests.test_postgres_posting:configured_test_bearer_verifier"
                ),
                "ACCOUNTING_JWT_ISSUER": "https://keyverse.example.test",
                "ACCOUNTING_JWT_AUDIENCE": "accounting-information-platform",
            },
            clear=False,
        )
        self._jwt_environment_patch.start()
        self.addCleanup(self._jwt_environment_patch.stop)
        self._local_billing_origin_patch = mock.patch(
'''
    if setup_replacement not in text:
        if setup_anchor not in text:
            raise SystemExit("test auth environment setup anchor drifted")
        text = text.replace(setup_anchor, setup_replacement, 1)

    verifier_anchor = '''            if token == "test-invalid-tenant":
                return "not-a-reference"
            raise ValueError("signature, issuer, audience, or expiry validation failed")
'''
    verifier_replacement = '''            if token == "test-invalid-tenant":
                return "not-a-reference"
            if token == "test-non-string-tenant":
                return 123  # type: ignore[return-value]
            raise ValueError("signature, issuer, audience, or expiry validation failed")
'''
    if verifier_replacement not in text:
        if verifier_anchor not in text:
            raise SystemExit("test token verifier branch anchor drifted")
        text = text.replace(verifier_anchor, verifier_replacement, 1)

    auth_test_anchor = '''        self.assertEqual(self._http_unauthenticated("GET", "/legal-entities", None)[0], 401)
        self.assertEqual(
            self._http_with_authorization(
                "GET", "/legal-entities", None, "Basic not-a-bearer"
            )[0],
            401,
        )
'''
    auth_test_replacement = '''        self.assertEqual(self._http_unauthenticated("GET", "/legal-entities", None)[0], 401)
        self.assertEqual(
            self._http_unauthenticated("POST", "/journal-proposals", {})[0],
            401,
        )
        self.assertEqual(
            self._http_with_authorization(
                "GET", "/legal-entities", None, "Basic not-a-bearer"
            )[0],
            401,
        )
'''
    if auth_test_replacement not in text:
        if auth_test_anchor not in text:
            raise SystemExit("HTTP auth POST regression anchor drifted")
        text = text.replace(auth_test_anchor, auth_test_replacement, 1)

    invalid_tenant_anchor = '''        self.assertEqual(
            self._http_with_authorization(
                "GET", "/legal-entities", None, "Bearer test-invalid-tenant"
            )[0],
            401,
        )
        self.assertEqual(
            self._http_with_authorization(
                "GET", "/legal-entities", None, "Bearer test-other-tenant"
            )[0],
            403,
        )
'''
    invalid_tenant_replacement = '''        self.assertEqual(
            self._http_with_authorization(
                "GET", "/legal-entities", None, "Bearer test-non-string-tenant"
            )[0],
            401,
        )
        self.assertEqual(
            self._http_with_authorization(
                "GET", "/legal-entities", None, "Bearer test-invalid-tenant"
            )[0],
            401,
        )
        self.assertEqual(
            self._http_with_authorization(
                "GET", "/legal-entities", None, "Bearer test-other-tenant"
            )[0],
            403,
        )
'''
    if invalid_tenant_replacement not in text:
        if invalid_tenant_anchor not in text:
            raise SystemExit("HTTP auth tenant-shape regression anchor drifted")
        text = text.replace(invalid_tenant_anchor, invalid_tenant_replacement, 1)

    malformed_anchor = '''        with mock.patch.dict(
            os.environ,
            {
                "ACCOUNTING_JWT_ISSUER": "https://keyverse.example.test",
                "ACCOUNTING_JWT_AUDIENCE": "accounting-information-platform",
                "ACCOUNTING_JWT_VERIFIER": "bad-specification",
            },
            clear=True,
        ):
            with self.assertRaisesRegex(AccountingValidationError, "module.path:callable"):
                create_journal_proposal_server(DATABASE_URL, self.policy.tenant_reference)
'''
    malformed_replacement = malformed_anchor + '''        for malformed_specification in (
            ":verify",
            "tests.test_postgres_posting:",
        ):
            with self.subTest(malformed_specification=malformed_specification):
                with mock.patch.dict(
                    os.environ,
                    {
                        "ACCOUNTING_JWT_ISSUER": "https://keyverse.example.test",
                        "ACCOUNTING_JWT_AUDIENCE": "accounting-information-platform",
                        "ACCOUNTING_JWT_VERIFIER": malformed_specification,
                    },
                    clear=True,
                ):
                    with self.assertRaisesRegex(
                        AccountingValidationError, "module.path:callable"
                    ):
                        create_journal_proposal_server(
                            DATABASE_URL, self.policy.tenant_reference
                        )
'''
    if "for malformed_specification in (" not in text:
        if malformed_anchor not in text:
            raise SystemExit("HTTP auth malformed-verifier regression anchor drifted")
        text = text.replace(malformed_anchor, malformed_replacement, 1)

    _write(path, text)


def main() -> None:
    """Finish fail-closed auth behavior and exact coverage fixtures."""
    harden_verifier_return_type()
    complete_postgres_auth_fixtures()


if __name__ == "__main__":
    main()
