"""Require an authenticated bearer-token tenant at the accounting HTTP boundary."""

from __future__ import annotations

from pathlib import Path


def _read(path: str) -> str:
    """Return UTF-8 repository text."""
    return Path(path).read_text(encoding="utf-8")


def _write(path: str, text: str) -> None:
    """Replace UTF-8 repository text."""
    Path(path).write_text(text, encoding="utf-8")


def harden_http_boundary() -> None:
    """Make every non-health request authenticate before tenant binding."""
    path = "src/accounting_information_platform/http_api.py"
    text = _read(path)

    server_init = '''    def __init__(
        self,
        server_address: tuple[str, int],
        database_url: str,
        tenant_reference: str,
    ) -> None:
        """Bind *server_address* to one tenant's posting endpoint."""
        self.database_url = database_url
        self.tenant_reference = tenant_reference
        super().__init__(server_address, JournalProposalHandler)
'''
    server_replacement = '''    def __init__(
        self,
        server_address: tuple[str, int],
        database_url: str,
        tenant_reference: str,
        bearer_token_validator: Callable[[str], str] | None = None,
    ) -> None:
        """Bind one tenant and a fail-closed bearer-token validation port."""
        self.database_url = database_url
        self.tenant_reference = tenant_reference
        self.bearer_token_validator = (
            bearer_token_validator
            if bearer_token_validator is not None
            else _reject_unconfigured_bearer_token
        )
        super().__init__(server_address, JournalProposalHandler)
'''
    if server_replacement not in text:
        if server_init not in text:
            raise SystemExit("JournalProposalServer constructor anchor drifted")
        text = text.replace(server_init, server_replacement, 1)

    bound_method = '''    def _bound_tenant_header(self, mismatch_action: str) -> str | None:
        tenant_header = self.headers.get(TENANT_HEADER)
        if not tenant_header:
            self._write_error(
                400,
                f"{TENANT_HEADER} is required. Supply that tenant header, then retry.",
            )
            return None
        if tenant_header != self.server.tenant_reference:
            self._write_error(
                403,
                f"{TENANT_HEADER} does not match this AIS tenant binding. "
                f"Send the {mismatch_action} to that tenant's endpoint, then retry.",
            )
            return None
        return tenant_header
'''
    bound_replacement = '''    def _bound_tenant_header(self, mismatch_action: str) -> str | None:
        authorization = self.headers.get("Authorization", "")
        prefix = "Bearer "
        if not authorization.startswith(prefix) or not authorization[len(prefix) :].strip():
            self._write_error(
                401,
                "Authorization Bearer token is required. Authenticate with the configured "
                "identity provider, then retry.",
            )
            return None
        bearer_token = authorization[len(prefix) :].strip()
        try:
            authenticated_tenant = self.server.bearer_token_validator(bearer_token)
            _require_reference(authenticated_tenant, "authenticated tenant reference")
        except (AccountingValidationError, ValueError, TypeError):
            self._write_error(
                401,
                "Bearer token validation failed. Refresh the authenticated session, then retry.",
            )
            return None
        if authenticated_tenant != self.server.tenant_reference:
            self._write_error(
                403,
                "authenticated tenant does not match this AIS tenant binding. "
                f"Send the {mismatch_action} to the authenticated tenant's endpoint, then retry.",
            )
            return None
        tenant_header = self.headers.get(TENANT_HEADER)
        if not tenant_header:
            self._write_error(
                400,
                f"{TENANT_HEADER} is required. Supply that tenant header, then retry.",
            )
            return None
        if tenant_header != authenticated_tenant:
            self._write_error(
                403,
                f"{TENANT_HEADER} does not match the authenticated tenant. "
                f"Send the {mismatch_action} with the token tenant, then retry.",
            )
            return None
        return authenticated_tenant
'''
    if bound_replacement not in text:
        if bound_method not in text:
            raise SystemExit("tenant-binding method anchor drifted")
        text = text.replace(bound_method, bound_replacement, 1)

    create_signature = '''def create_journal_proposal_server(
    database_url: str,
    tenant_reference: str,
    host: str = "127.0.0.1",
    port: int = 0,
) -> JournalProposalServer:
'''
    create_replacement = '''def create_journal_proposal_server(
    database_url: str,
    tenant_reference: str,
    host: str = "127.0.0.1",
    port: int = 0,
    bearer_token_validator: Callable[[str], str] | None = None,
) -> JournalProposalServer:
'''
    if create_replacement not in text:
        if create_signature not in text:
            raise SystemExit("HTTP server factory signature anchor drifted")
        text = text.replace(create_signature, create_replacement, 1)

    create_return = '''    return JournalProposalServer((host, port), database_url, tenant_reference)
'''
    create_return_replacement = '''    return JournalProposalServer(
        (host, port),
        database_url,
        tenant_reference,
        bearer_token_validator=bearer_token_validator,
    )
'''
    if create_return_replacement not in text:
        if create_return not in text:
            raise SystemExit("HTTP server factory return anchor drifted")
        text = text.replace(create_return, create_return_replacement, 1)

    run_signature = '''def run_journal_proposal_server(
    database_url: str | None = None,
    tenant_reference: str | None = None,
    host: str | None = None,
    port: int | None = None,
    serve: Callable[[], None] | None = None,
) -> JournalProposalServer:
'''
    run_replacement = '''def run_journal_proposal_server(
    database_url: str | None = None,
    tenant_reference: str | None = None,
    host: str | None = None,
    port: int | None = None,
    serve: Callable[[], None] | None = None,
    bearer_token_validator: Callable[[str], str] | None = None,
) -> JournalProposalServer:
'''
    if run_replacement not in text:
        if run_signature not in text:
            raise SystemExit("HTTP runner signature anchor drifted")
        text = text.replace(run_signature, run_replacement, 1)

    run_create = '''    server = create_journal_proposal_server(
        resolved_url, resolved_tenant, resolved_host, resolved_port
    )
'''
    run_create_replacement = '''    server = create_journal_proposal_server(
        resolved_url,
        resolved_tenant,
        resolved_host,
        resolved_port,
        bearer_token_validator=bearer_token_validator,
    )
'''
    if run_create_replacement not in text:
        if run_create not in text:
            raise SystemExit("HTTP runner factory-call anchor drifted")
        text = text.replace(run_create, run_create_replacement, 1)

    helper_marker = '''def _adjusting_journal_status(error: AccountingValidationError) -> int:
'''
    helper = '''def _reject_unconfigured_bearer_token(_bearer_token: str) -> str:
    """Fail closed until a host injects a signature/issuer/audience/expiry validator."""
    raise AccountingValidationError(
        "bearer token validator is not configured. Configure the Keyverse/OIDC "
        "validation adapter, then retry."
    )


'''
    if "def _reject_unconfigured_bearer_token(" not in text:
        if helper_marker not in text:
            raise SystemExit("authentication helper insertion anchor drifted")
        text = text.replace(helper_marker, helper + helper_marker, 1)

    _write(path, text)


def adapt_postgres_http_tests() -> None:
    """Keep all existing HTTP tests authenticated and add fail-closed regressions."""
    path = "tests/test_postgres_posting.py"
    text = _read(path)

    start_server = '''    def _start_http_server(self, tenant_reference: str | None = None):
        server = create_journal_proposal_server(
            DATABASE_URL,
            self.policy.tenant_reference if tenant_reference is None else tenant_reference,
            "127.0.0.1",
            0,
        )
'''
    start_replacement = '''    def _start_http_server(self, tenant_reference: str | None = None):
        bound_tenant = (
            self.policy.tenant_reference if tenant_reference is None else tenant_reference
        )

        def validate_test_token(token: str) -> str:
            if token != "ais-test-token":
                raise AccountingValidationError("test bearer token is invalid")
            return bound_tenant

        server = create_journal_proposal_server(
            DATABASE_URL,
            bound_tenant,
            "127.0.0.1",
            0,
            bearer_token_validator=validate_test_token,
        )
'''
    if start_replacement not in text:
        if start_server not in text:
            raise SystemExit("test HTTP server helper anchor drifted")
        text = text.replace(start_server, start_replacement, 1)

    http_json_sig = '''    def _http_json(
        self,
        method: str,
        path: str,
        payload: dict[str, object] | None,
        tenant_header: str | None = "",
    ) -> tuple[int, dict[str, object]]:
'''
    http_json_replacement = '''    def _http_json(
        self,
        method: str,
        path: str,
        payload: dict[str, object] | None,
        tenant_header: str | None = "",
        authorization_header: str | None = "",
    ) -> tuple[int, dict[str, object]]:
'''
    if http_json_replacement not in text:
        if http_json_sig not in text:
            raise SystemExit("HTTP JSON helper signature anchor drifted")
        text = text.replace(http_json_sig, http_json_replacement, 1)

    headers_anchor = '''        headers = {"Content-Type": "application/json"}
        if tenant_header is None:
'''
    headers_replacement = '''        headers = {"Content-Type": "application/json"}
        if authorization_header is None:
            pass
        elif authorization_header == "":
            headers["Authorization"] = "Bearer ais-test-token"
        else:
            headers["Authorization"] = authorization_header
        if tenant_header is None:
'''
    if headers_replacement not in text:
        if headers_anchor not in text:
            raise SystemExit("HTTP JSON header anchor drifted")
        text = text.replace(headers_anchor, headers_replacement, 1)

    raw_headers = '''                headers={
                    "Content-Type": "application/json",
                    "X-CWL-Tenant-Reference": tenant_reference,
                },
'''
    raw_headers_replacement = '''                headers={
                    "Content-Type": "application/json",
                    "Authorization": "Bearer ais-test-token",
                    "X-CWL-Tenant-Reference": tenant_reference,
                },
'''
    if raw_headers_replacement not in text:
        if raw_headers not in text:
            raise SystemExit("HTTP raw helper auth header anchor drifted")
        text = text.replace(raw_headers, raw_headers_replacement, 1)

    invalid_length_anchor = '''            connection.putheader("Content-Type", "application/json")
            connection.putheader("X-CWL-Tenant-Reference", self.policy.tenant_reference)
'''
    invalid_length_replacement = '''            connection.putheader("Content-Type", "application/json")
            connection.putheader("Authorization", "Bearer ais-test-token")
            connection.putheader("X-CWL-Tenant-Reference", self.policy.tenant_reference)
'''
    if invalid_length_replacement not in text:
        if invalid_length_anchor not in text:
            raise SystemExit("invalid-length helper auth anchor drifted")
        text = text.replace(invalid_length_anchor, invalid_length_replacement, 1)

    test_marker = '''    def test_http_reverses_posted_journal_and_preserves_original_receipt(self) -> None:
'''
    auth_test = '''    def test_http_requires_validated_bearer_before_tenant_header(self) -> None:
        """Every non-health route rejects absent or invalid bearer authentication."""
        server = self._start_http_server()
        no_auth_status, no_auth = self._http_json(
            "GET",
            "/legal-entities",
            None,
            authorization_header=None,
        )
        bad_auth_status, bad_auth = self._http_json(
            "GET",
            "/legal-entities",
            None,
            authorization_header="Bearer wrong-token",
        )
        no_tenant_status, no_tenant = self._http_json(
            "GET",
            "/legal-entities",
            None,
            tenant_header=None,
        )
        health_status, health = self._http_json(
            "GET",
            "/healthz",
            None,
            tenant_header=None,
            authorization_header=None,
        )

        self.assertEqual(no_auth_status, 401)
        self.assertIn("Bearer token", str(no_auth["error_message"]))
        self.assertEqual(bad_auth_status, 401)
        self.assertIn("validation failed", str(bad_auth["error_message"]))
        self.assertEqual(no_tenant_status, 400)
        self.assertIn("X-CWL-Tenant-Reference", str(no_tenant["error_message"]))
        self.assertEqual(health_status, 200)
        self.assertEqual(health, {"status": "ok"})
        server.shutdown()

    def test_http_rejects_authenticated_tenant_mismatch(self) -> None:
        """A validated token claim cannot be substituted with another tenant header."""
        def other_tenant_validator(token: str) -> str:
            if token != "ais-test-token":
                raise AccountingValidationError("test bearer token is invalid")
            return "urn:cwl:tenant_authenticated_other"

        server = create_journal_proposal_server(
            DATABASE_URL,
            self.policy.tenant_reference,
            "127.0.0.1",
            0,
            bearer_token_validator=other_tenant_validator,
        )
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        previous_server = getattr(self, "_http_server", None)
        self._http_server = server
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        try:
            status, body = self._http_json("GET", "/legal-entities", None)
        finally:
            if previous_server is not None:
                self._http_server = previous_server

        self.assertEqual(status, 403)
        self.assertIn("authenticated tenant", str(body["error_message"]))

'''
    if "test_http_requires_validated_bearer_before_tenant_header" not in text:
        if test_marker not in text:
            raise SystemExit("HTTP authentication regression insertion marker drifted")
        text = text.replace(test_marker, auth_test + test_marker, 1)

    _write(path, text)


def update_security_docs() -> None:
    """State the real authentication port and its fail-closed production requirement."""
    security_path = "docs/SECURITY.md"
    security = _read(security_path)
    note = (
        "\n## HTTP caller authentication\n\n"
        "Every accounting read or write route except `/healthz` requires a Bearer token. "
        "`JournalProposalServer` delegates cryptographic OIDC/JWT validation to a host-injected "
        "`bearer_token_validator`; the adapter is responsible for signature, issuer, audience, "
        "and expiry validation and returns only the authenticated tenant reference. The server "
        "fails closed when no validator is configured. `X-CWL-Tenant-Reference` remains an "
        "explicit routing assertion and must exactly match both the validated token tenant and "
        "the server tenant binding.\n"
    )
    if "## HTTP caller authentication" not in security:
        security = security.rstrip() + note
        _write(security_path, security.rstrip() + "\n")

    architecture_path = "docs/ARCHITECTURE.md"
    architecture = _read(architecture_path)
    sentence = (
        "\nThe HTTP adapter is fail-closed behind a host-injected Bearer-token validation port; "
        "validated token tenant, explicit tenant header, and server tenant binding must agree "
        "before any accounting command or read executes.\n"
    )
    if "host-injected Bearer-token validation port" not in architecture:
        _write(architecture_path, architecture.rstrip() + sentence)

    operability_path = "docs/OPERABILITY.md"
    operability = _read(operability_path)
    op_note = (
        "\nA production host must inject a Keyverse/OIDC bearer-token validator that validates "
        "signature, issuer, audience, and expiry before returning the tenant claim. Starting the "
        "stdlib server without that validator leaves `/healthz` available but every accounting "
        "read/write request fails authentication.\n"
    )
    if "production host must inject a Keyverse/OIDC bearer-token validator" not in operability:
        _write(operability_path, operability.rstrip() + op_note)

    changelog_path = "CHANGELOG.md"
    changelog = _read(changelog_path)
    line = "- Made the HTTP accounting boundary fail closed behind validated Bearer-token tenant authentication.\n"
    if line not in changelog:
        marker = "### Security\n"
        if marker in changelog:
            changelog = changelog.replace(marker, marker + "\n" + line, 1)
        else:
            changelog = changelog.rstrip() + "\n\n### Security\n\n" + line
        _write(changelog_path, changelog)


def main() -> None:
    """Apply the HTTP authentication repair and its realistic regressions."""
    harden_http_boundary()
    adapt_postgres_http_tests()
    update_security_docs()


if __name__ == "__main__":
    main()
