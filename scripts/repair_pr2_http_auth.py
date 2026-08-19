"""Harden the PR 2 HTTP boundary with fail-closed bearer authentication."""

from __future__ import annotations

from pathlib import Path


def _read(path: str) -> str:
    """Return one UTF-8 repository file."""
    return Path(path).read_text(encoding="utf-8")


def _write(path: str, text: str) -> None:
    """Replace one UTF-8 repository file."""
    Path(path).write_text(text, encoding="utf-8")


def harden_http_boundary() -> None:
    """Require a configured signature-validating bearer verifier on every accounting route."""
    path = "src/accounting_information_platform/http_api.py"
    text = _read(path)

    import_anchor = "import json\nimport os\nimport re\n"
    import_replacement = "import importlib\nimport json\nimport os\nimport re\n"
    if import_replacement not in text:
        if import_anchor not in text:
            raise SystemExit("HTTP auth import anchor drifted")
        text = text.replace(import_anchor, import_replacement, 1)

    constant_anchor = '''TENANT_HEADER = "X-CWL-Tenant-Reference"\nHEALTHZ_PATH = "/healthz"\n'''
    constant_replacement = '''TENANT_HEADER = "X-CWL-Tenant-Reference"\nAUTHORIZATION_HEADER = "Authorization"\nJWT_VERIFIER_ENV = "ACCOUNTING_JWT_VERIFIER"\nJWT_ISSUER_ENV = "ACCOUNTING_JWT_ISSUER"\nJWT_AUDIENCE_ENV = "ACCOUNTING_JWT_AUDIENCE"\nBearerTokenVerifier = Callable[[str, str, str], str]\nHEALTHZ_PATH = "/healthz"\n'''
    if constant_replacement not in text:
        if constant_anchor not in text:
            raise SystemExit("HTTP auth constant anchor drifted")
        text = text.replace(constant_anchor, constant_replacement, 1)

    server_anchor = '''    def __init__(\n        self,\n        server_address: tuple[str, int],\n        database_url: str,\n        tenant_reference: str,\n    ) -> None:\n        """Bind *server_address* to one tenant's posting endpoint."""\n        self.database_url = database_url\n        self.tenant_reference = tenant_reference\n        super().__init__(server_address, JournalProposalHandler)\n'''
    server_replacement = '''    def __init__(\n        self,\n        server_address: tuple[str, int],\n        database_url: str,\n        tenant_reference: str,\n        token_verifier: BearerTokenVerifier,\n        token_issuer: str,\n        token_audience: str,\n    ) -> None:\n        """Bind one tenant endpoint to a signature-validating bearer verifier."""\n        self.database_url = database_url\n        self.tenant_reference = tenant_reference\n        self.token_verifier = token_verifier\n        self.token_issuer = token_issuer\n        self.token_audience = token_audience\n        super().__init__(server_address, JournalProposalHandler)\n'''
    if server_replacement not in text:
        if server_anchor not in text:
            raise SystemExit("HTTP server auth anchor drifted")
        text = text.replace(server_anchor, server_replacement, 1)

    get_anchor = '''        if parsed.path == HEALTHZ_PATH:\n            self._write_json(200, {"status": "ok"})\n            return\n        if parsed.path == BILLING_PROPOSAL_PULL_PATH:\n'''
    get_replacement = '''        if parsed.path == HEALTHZ_PATH:\n            self._write_json(200, {"status": "ok"})\n            return\n        if self._authenticated_tenant_reference() is None:\n            return\n        if parsed.path == BILLING_PROPOSAL_PULL_PATH:\n'''
    if get_replacement not in text:
        if get_anchor not in text:
            raise SystemExit("HTTP GET auth anchor drifted")
        text = text.replace(get_anchor, get_replacement, 1)

    post_anchor = '''    def do_POST(self) -> None:\n        """Route journal-proposal accept, adjusting journal, reverse, Billing pull, close, outbox publish, audit-history 405, and GET-only POST 405s."""\n        raw_body = self._read_body()\n'''
    post_replacement = '''    def do_POST(self) -> None:\n        """Authenticate, then route state-changing and POST-only accounting commands."""\n        if self._authenticated_tenant_reference() is None:\n            return\n        raw_body = self._read_body()\n'''
    if post_replacement not in text:
        if post_anchor not in text:
            raise SystemExit("HTTP POST auth anchor drifted")
        text = text.replace(post_anchor, post_replacement, 1)

    bound_anchor = '''    def _bound_tenant_header(self, mismatch_action: str) -> str | None:\n        tenant_header = self.headers.get(TENANT_HEADER)\n'''
    auth_method = '''    def _authenticated_tenant_reference(self) -> str | None:\n        """Return the cryptographically authenticated tenant or fail closed."""\n        authorization = self.headers.get(AUTHORIZATION_HEADER, "")\n        parts = authorization.split()\n        if len(parts) != 2 or parts[0].lower() != "bearer":\n            self._write_error(\n                401,\n                "Bearer authentication is required. Supply a Keyverse-issued bearer token, then retry.",\n            )\n            return None\n        try:\n            tenant_reference = self.server.token_verifier(\n                parts[1],\n                self.server.token_issuer,\n                self.server.token_audience,\n            )\n        except Exception:\n            self._write_error(\n                401,\n                "Bearer token validation failed. Obtain a fresh authorized token, then retry.",\n            )\n            return None\n        try:\n            _require_reference(tenant_reference, "authenticated tenant reference")\n        except AccountingValidationError:\n            self._write_error(\n                401,\n                "Bearer token validation returned an invalid tenant. Obtain a fresh authorized token, then retry.",\n            )\n            return None\n        if tenant_reference != self.server.tenant_reference:\n            self._write_error(\n                403,\n                "authenticated tenant does not match this AIS tenant binding. Send the request to that tenant's endpoint, then retry.",\n            )\n            return None\n        return tenant_reference\n\n    def _bound_tenant_header(self, mismatch_action: str) -> str | None:\n        tenant_header = self.headers.get(TENANT_HEADER)\n'''
    if auth_method not in text:
        if bound_anchor not in text:
            raise SystemExit("HTTP tenant-header auth insertion anchor drifted")
        text = text.replace(bound_anchor, auth_method, 1)

    create_anchor = '''def create_journal_proposal_server(\n    database_url: str,\n    tenant_reference: str,\n    host: str = "127.0.0.1",\n    port: int = 0,\n) -> JournalProposalServer:\n    """Create a stdlib HTTP server that posts Billing proposals, AIS adjusting journals, pulls, closes, opens periods, and reads TB, statements, journals, reversals, receivable aging, payable aging, outbox, and audit history."""\n    if not database_url:\n        raise AccountingValidationError(\n            "ACCOUNTING_DATABASE_URL is empty. Set a PostgreSQL 18 URL and retry posting."\n        )\n    _require_reference(tenant_reference, "tenant reference")\n    return JournalProposalServer((host, port), database_url, tenant_reference)\n\n\ndef run_journal_proposal_server(\n    database_url: str | None = None,\n    tenant_reference: str | None = None,\n    host: str | None = None,\n    port: int | None = None,\n    serve: Callable[[], None] | None = None,\n) -> JournalProposalServer:\n'''
    create_replacement = '''def _load_configured_bearer_verifier(specification: str) -> BearerTokenVerifier:\n    """Load the operator-configured verifier that validates JWT signatures and claims."""\n    module_name, separator, attribute_name = specification.strip().partition(":")\n    if not separator or not module_name or not attribute_name:\n        raise AccountingValidationError(\n            "ACCOUNTING_JWT_VERIFIER must be module.path:callable. Configure a Keyverse JWT verifier, then retry."\n        )\n    try:\n        module = importlib.import_module(module_name)\n    except (ImportError, ValueError) as error:\n        raise AccountingValidationError(\n            "ACCOUNTING_JWT_VERIFIER module cannot be loaded. Fix the verifier module, then retry."\n        ) from error\n    verifier = getattr(module, attribute_name, None)\n    if not callable(verifier):\n        raise AccountingValidationError(\n            "ACCOUNTING_JWT_VERIFIER target must be callable. Configure a JWT verifier, then retry."\n        )\n    return verifier\n\n\ndef _resolve_bearer_authentication(\n    token_verifier: BearerTokenVerifier | None,\n    token_issuer: str | None,\n    token_audience: str | None,\n) -> tuple[BearerTokenVerifier, str, str]:\n    """Resolve a fail-closed signature/issuer/audience/expiry verification contract."""\n    issuer = token_issuer if token_issuer is not None else os.environ.get(JWT_ISSUER_ENV, "")\n    audience = (\n        token_audience\n        if token_audience is not None\n        else os.environ.get(JWT_AUDIENCE_ENV, "")\n    )\n    if not issuer.strip():\n        raise AccountingValidationError(\n            "ACCOUNTING_JWT_ISSUER is required. Configure the trusted Keyverse issuer, then retry."\n        )\n    if not audience.strip():\n        raise AccountingValidationError(\n            "ACCOUNTING_JWT_AUDIENCE is required. Configure the AIS audience, then retry."\n        )\n    verifier = token_verifier\n    if verifier is None:\n        verifier_specification = os.environ.get(JWT_VERIFIER_ENV, "")\n        if not verifier_specification.strip():\n            raise AccountingValidationError(\n                "ACCOUNTING_JWT_VERIFIER is required. Configure a signature-validating Keyverse verifier, then retry."\n            )\n        verifier = _load_configured_bearer_verifier(verifier_specification)\n    return verifier, issuer.strip(), audience.strip()\n\n\ndef create_journal_proposal_server(\n    database_url: str,\n    tenant_reference: str,\n    host: str = "127.0.0.1",\n    port: int = 0,\n    *,\n    token_verifier: BearerTokenVerifier | None = None,\n    token_issuer: str | None = None,\n    token_audience: str | None = None,\n) -> JournalProposalServer:\n    """Create one tenant-bound HTTP server with mandatory bearer authentication."""\n    if not database_url:\n        raise AccountingValidationError(\n            "ACCOUNTING_DATABASE_URL is empty. Set a PostgreSQL 18 URL and retry posting."\n        )\n    _require_reference(tenant_reference, "tenant reference")\n    verifier, issuer, audience = _resolve_bearer_authentication(\n        token_verifier, token_issuer, token_audience\n    )\n    return JournalProposalServer(\n        (host, port),\n        database_url,\n        tenant_reference,\n        verifier,\n        issuer,\n        audience,\n    )\n\n\ndef run_journal_proposal_server(\n    database_url: str | None = None,\n    tenant_reference: str | None = None,\n    host: str | None = None,\n    port: int | None = None,\n    serve: Callable[[], None] | None = None,\n    *,\n    token_verifier: BearerTokenVerifier | None = None,\n    token_issuer: str | None = None,\n    token_audience: str | None = None,\n) -> JournalProposalServer:\n'''
    if create_replacement not in text:
        if create_anchor not in text:
            raise SystemExit("HTTP server factory auth anchor drifted")
        text = text.replace(create_anchor, create_replacement, 1)

    run_call_anchor = '''    server = create_journal_proposal_server(\n        resolved_url, resolved_tenant, resolved_host, resolved_port\n    )\n'''
    run_call_replacement = '''    server = create_journal_proposal_server(\n        resolved_url,\n        resolved_tenant,\n        resolved_host,\n        resolved_port,\n        token_verifier=token_verifier,\n        token_issuer=token_issuer,\n        token_audience=token_audience,\n    )\n'''
    if run_call_replacement not in text:
        if run_call_anchor not in text:
            raise SystemExit("HTTP run-server auth call anchor drifted")
        text = text.replace(run_call_anchor, run_call_replacement, 1)

    _write(path, text)


def update_postgres_http_fixtures() -> None:
    """Make existing HTTP integration fixtures explicitly authenticate every accounting request."""
    path = "tests/test_postgres_posting.py"
    text = _read(path)

    start_anchor = '''    def _start_http_server(self, tenant_reference: str | None = None):\n        server = create_journal_proposal_server(\n            DATABASE_URL,\n            self.policy.tenant_reference if tenant_reference is None else tenant_reference,\n            "127.0.0.1",\n            0,\n        )\n'''
    start_replacement = '''    def _start_http_server(self, tenant_reference: str | None = None):\n        bound_tenant = (\n            self.policy.tenant_reference if tenant_reference is None else tenant_reference\n        )\n\n        def verify_test_token(token: str, issuer: str, audience: str) -> str:\n            if issuer != "https://keyverse.example.test":\n                raise ValueError("issuer mismatch")\n            if audience != "accounting-information-platform":\n                raise ValueError("audience mismatch")\n            if token == "test-valid-token":\n                return bound_tenant\n            if token == "test-other-tenant":\n                return "urn:cwl:tenant_other"\n            if token == "test-invalid-tenant":\n                return "not-a-reference"\n            raise ValueError("signature, issuer, audience, or expiry validation failed")\n\n        server = create_journal_proposal_server(\n            DATABASE_URL,\n            bound_tenant,\n            "127.0.0.1",\n            0,\n            token_verifier=verify_test_token,\n            token_issuer="https://keyverse.example.test",\n            token_audience="accounting-information-platform",\n        )\n'''
    if start_replacement not in text:
        if start_anchor not in text:
            raise SystemExit("PostgreSQL HTTP server fixture auth anchor drifted")
        text = text.replace(start_anchor, start_replacement, 1)

    json_header_anchor = '''        headers = {"Content-Type": "application/json"}\n        if tenant_header is None:\n'''
    json_header_replacement = '''        headers = {\n            "Content-Type": "application/json",\n            "Authorization": "Bearer test-valid-token",\n        }\n        if tenant_header is None:\n'''
    if json_header_replacement not in text:
        if json_header_anchor not in text:
            raise SystemExit("HTTP JSON fixture authorization anchor drifted")
        text = text.replace(json_header_anchor, json_header_replacement, 1)

    raw_header_anchor = '''                headers={\n                    "Content-Type": "application/json",\n                    "X-CWL-Tenant-Reference": tenant_reference,\n                },\n'''
    raw_header_replacement = '''                headers={\n                    "Content-Type": "application/json",\n                    "Authorization": "Bearer test-valid-token",\n                    "X-CWL-Tenant-Reference": tenant_reference,\n                },\n'''
    if raw_header_replacement not in text:
        if raw_header_anchor not in text:
            raise SystemExit("HTTP raw fixture authorization anchor drifted")
        text = text.replace(raw_header_anchor, raw_header_replacement, 1)

    length_anchor = '''            connection.putheader("Content-Type", "application/json")\n            connection.putheader("X-CWL-Tenant-Reference", self.policy.tenant_reference)\n            connection.putheader("Content-Length", "abc")\n'''
    length_replacement = '''            connection.putheader("Content-Type", "application/json")\n            connection.putheader("Authorization", "Bearer test-valid-token")\n            connection.putheader("X-CWL-Tenant-Reference", self.policy.tenant_reference)\n            connection.putheader("Content-Length", "abc")\n'''
    if length_replacement not in text:
        if length_anchor not in text:
            raise SystemExit("invalid Content-Length auth fixture anchor drifted")
        text = text.replace(length_anchor, length_replacement, 1)

    marker = "    def test_post_proposal_catalog_misses_write_zero_rows(self) -> None:\n"
    if "def test_http_bearer_authentication_fails_closed_before_accounting_routes" not in text:
        regression = '''    def test_http_bearer_authentication_fails_closed_before_accounting_routes(self) -> None:\n        """Every accounting route requires verified bearer identity before tenant headers."""\n        server = self._start_http_server()\n        self.assertEqual(self._http_unauthenticated("GET", "/legal-entities", None)[0], 401)\n        self.assertEqual(\n            self._http_with_authorization(\n                "GET", "/legal-entities", None, "Basic not-a-bearer"\n            )[0],\n            401,\n        )\n        self.assertEqual(\n            self._http_with_authorization(\n                "GET", "/legal-entities", None, "Bearer invalid-token"\n            )[0],\n            401,\n        )\n        self.assertEqual(\n            self._http_with_authorization(\n                "GET", "/legal-entities", None, "Bearer test-invalid-tenant"\n            )[0],\n            401,\n        )\n        self.assertEqual(\n            self._http_with_authorization(\n                "GET", "/legal-entities", None, "Bearer test-other-tenant"\n            )[0],\n            403,\n        )\n        self.assertEqual(\n            self._http_with_authorization(\n                "GET", "/legal-entities", None, "Bearer test-valid-token", tenant_header=None\n            )[0],\n            400,\n        )\n        self.assertEqual(self._http_unauthenticated("GET", "/healthz", None)[0], 200)\n        server.shutdown()\n\n    def test_http_authentication_configuration_fails_closed(self) -> None:\n        """The server never starts without issuer, audience, and a callable verifier."""\n        with mock.patch.dict(os.environ, {}, clear=True):\n            with self.assertRaisesRegex(AccountingValidationError, "ACCOUNTING_JWT_ISSUER"):\n                create_journal_proposal_server(DATABASE_URL, self.policy.tenant_reference)\n        with mock.patch.dict(\n            os.environ,\n            {"ACCOUNTING_JWT_ISSUER": "https://keyverse.example.test"},\n            clear=True,\n        ):\n            with self.assertRaisesRegex(AccountingValidationError, "ACCOUNTING_JWT_AUDIENCE"):\n                create_journal_proposal_server(DATABASE_URL, self.policy.tenant_reference)\n        with mock.patch.dict(\n            os.environ,\n            {\n                "ACCOUNTING_JWT_ISSUER": "https://keyverse.example.test",\n                "ACCOUNTING_JWT_AUDIENCE": "accounting-information-platform",\n            },\n            clear=True,\n        ):\n            with self.assertRaisesRegex(AccountingValidationError, "ACCOUNTING_JWT_VERIFIER"):\n                create_journal_proposal_server(DATABASE_URL, self.policy.tenant_reference)\n        with mock.patch.dict(\n            os.environ,\n            {\n                "ACCOUNTING_JWT_ISSUER": "https://keyverse.example.test",\n                "ACCOUNTING_JWT_AUDIENCE": "accounting-information-platform",\n                "ACCOUNTING_JWT_VERIFIER": "bad-specification",\n            },\n            clear=True,\n        ):\n            with self.assertRaisesRegex(AccountingValidationError, "module.path:callable"):\n                create_journal_proposal_server(DATABASE_URL, self.policy.tenant_reference)\n        with mock.patch.dict(\n            os.environ,\n            {\n                "ACCOUNTING_JWT_ISSUER": "https://keyverse.example.test",\n                "ACCOUNTING_JWT_AUDIENCE": "accounting-information-platform",\n                "ACCOUNTING_JWT_VERIFIER": "missing.accounting.module:verify",\n            },\n            clear=True,\n        ):\n            with self.assertRaisesRegex(AccountingValidationError, "module cannot be loaded"):\n                create_journal_proposal_server(DATABASE_URL, self.policy.tenant_reference)\n        with mock.patch.dict(\n            os.environ,\n            {\n                "ACCOUNTING_JWT_ISSUER": "https://keyverse.example.test",\n                "ACCOUNTING_JWT_AUDIENCE": "accounting-information-platform",\n                "ACCOUNTING_JWT_VERIFIER": "tests.test_postgres_posting:DATABASE_URL",\n            },\n            clear=True,\n        ):\n            with self.assertRaisesRegex(AccountingValidationError, "target must be callable"):\n                create_journal_proposal_server(DATABASE_URL, self.policy.tenant_reference)\n\n'''
        if marker not in text:
            raise SystemExit("HTTP auth regression insertion marker drifted")
        text = text.replace(marker, regression + marker, 1)

    port_anchor = '''    def _http_port(self) -> int:\n        return self._http_server.server_address[1]\n'''
    if "def _http_with_authorization(" not in text:
        helpers = '''    def _http_with_authorization(\n        self,\n        method: str,\n        path: str,\n        payload: dict[str, object] | None,\n        authorization: str | None,\n        *,\n        tenant_header: str | None = "",\n    ) -> tuple[int, dict[str, object]]:\n        """Send one HTTP request with an explicitly controlled Authorization header."""\n        body = None if payload is None else json.dumps(payload).encode("utf-8")\n        headers = {"Content-Type": "application/json"}\n        if authorization is not None:\n            headers["Authorization"] = authorization\n        if tenant_header is not None:\n            headers["X-CWL-Tenant-Reference"] = (\n                self.policy.tenant_reference if tenant_header == "" else tenant_header\n            )\n        request = urllib.request.Request(\n            f"http://127.0.0.1:{self._http_port()}{path}",\n            data=body,\n            method=method,\n            headers=headers,\n        )\n        try:\n            with urllib.request.urlopen(request) as response:\n                return response.status, json.loads(response.read().decode("utf-8"))\n        except urllib.error.HTTPError as error:\n            return error.code, json.loads(error.read().decode("utf-8"))\n\n    def _http_unauthenticated(\n        self, method: str, path: str, payload: dict[str, object] | None\n    ) -> tuple[int, dict[str, object]]:\n        """Send one request without Authorization for fail-closed regression coverage."""\n        return self._http_with_authorization(method, path, payload, None)\n\n'''
        if port_anchor not in text:
            raise SystemExit("HTTP auth helper insertion marker drifted")
        text = text.replace(port_anchor, helpers + port_anchor, 1)

    _write(path, text)


def update_docs() -> None:
    """Make security, architecture, operability, and standards evidence match authentication."""
    security_path = "docs/SECURITY.md"
    security = _read(security_path)
    old = "- OIDC/JWT audience and signature validation through Keyverse in the API milestone.\n"
    new = (
        "- Every accounting HTTP route except `/healthz` requires `Authorization: Bearer`. "
        "The configured `ACCOUNTING_JWT_VERIFIER` is a trusted adapter contract that MUST "
        "cryptographically verify the JWT signature with an explicit algorithm allowlist, exact "
        "`ACCOUNTING_JWT_ISSUER`, `ACCOUNTING_JWT_AUDIENCE`, and expiry before returning the "
        "authenticated tenant reference. Missing verifier configuration or verification failure "
        "fails closed. `X-CWL-Tenant-Reference` is only a routing/scope assertion and must exactly "
        "match both the authenticated tenant and the process tenant binding.\n"
    )
    if new not in security:
        if old not in security:
            raise SystemExit("SECURITY authentication documentation anchor drifted")
        security = security.replace(old, new, 1)
        _write(security_path, security)

    architecture_path = "docs/ARCHITECTURE.md"
    architecture = _read(architecture_path)
    old = (
        "`GET /healthz` is an ops probe. The HTTP server binds `0.0.0.0:$PORT` and accepts only "
        "the purpose-limited `X-CWL-Tenant-Reference` header on accounting routes."
    )
    new = (
        "`GET /healthz` is an unauthenticated ops probe. Every other HTTP route requires a bearer "
        "token whose configured verifier has validated signature, issuer, audience, and expiry and "
        "returned the tenant identity; `X-CWL-Tenant-Reference` remains only a purpose-limited "
        "scope assertion and must equal the authenticated/process tenant. The HTTP server binds "
        "`0.0.0.0:$PORT`."
    )
    if new not in architecture:
        if old not in architecture:
            raise SystemExit("ARCHITECTURE authentication documentation anchor drifted")
        architecture = architecture.replace(old, new, 1)
        _write(architecture_path, architecture)

    operability_path = "docs/OPERABILITY.md"
    operability = _read(operability_path)
    old = (
        "For HTTP, set `ACCOUNTING_TENANT_REFERENCE` and bind `0.0.0.0:$PORT` with "
        "`run_journal_proposal_server`. POST `/journal-proposals` with `X-CWL-Tenant-Reference` only; "
        "that header scopes tenant identity and is not a general credential."
    )
    new = (
        "For HTTP, set `ACCOUNTING_TENANT_REFERENCE`, `ACCOUNTING_JWT_ISSUER`, "
        "`ACCOUNTING_JWT_AUDIENCE`, and `ACCOUNTING_JWT_VERIFIER=module.path:callable`, then bind "
        "`0.0.0.0:$PORT` with `run_journal_proposal_server`. The verifier is the trusted Keyverse "
        "adapter boundary and MUST verify an allowed JWT signing algorithm and signature, exact issuer, "
        "audience, and expiry before returning the token's tenant reference. Every accounting route "
        "except `/healthz` requires `Authorization: Bearer`; POST `/journal-proposals` also sends "
        "`X-CWL-Tenant-Reference`, which is a scope assertion rather than a credential and must match "
        "the authenticated/process tenant."
    )
    if new not in operability:
        if old not in operability:
            raise SystemExit("OPERABILITY authentication documentation anchor drifted")
        operability = operability.replace(old, new, 1)
        _write(operability_path, operability)

    trace_path = "docs/doctoring/STANDARD_TRACEABILITY.md"
    trace = _read(trace_path)
    row = (
        "| OpenID Connect Core 1.0 + RFC 8725 | All accounting HTTP routes except health require a "
        "bearer token validated for allowed signing algorithm/signature, exact issuer, audience, and "
        "expiry before tenant authority is accepted; the tenant header cannot substitute for "
        "authentication | `ACCOUNTING_JWT_VERIFIER` boundary, HTTP auth regressions, security and "
        "operability docs |\n"
    )
    marker = "| RFC 9562 | New persistence identifiers use UUIDv7 | Initial migration |\n"
    if row not in trace:
        if marker not in trace:
            raise SystemExit("standard traceability auth insertion anchor drifted")
        trace = trace.replace(marker, marker + row, 1)
        _write(trace_path, trace)

    references_path = "docs/doctoring/REFERENCES.md"
    references = _read(references_path)
    additions = (
        "OpenID Foundation. (2023). *OpenID Connect Core 1.0 incorporating errata set 2*. https://openid.net/specs/openid-connect-core-1_0.html\n",
        "Sheffer, Y., Hardt, D., & Jones, M. (2020). *JSON Web Token best current practices* (RFC 8725). RFC Editor. https://www.rfc-editor.org/rfc/rfc8725.html\n",
    )
    changed = False
    for addition in additions:
        if addition not in references:
            references = references.rstrip() + "\n\n" + addition
            changed = True
    if changed:
        _write(references_path, references.rstrip() + "\n")

    changelog_path = "CHANGELOG.md"
    changelog = _read(changelog_path)
    entry = (
        "- Required signature-validating bearer authentication on every accounting HTTP route except "
        "`/healthz`; issuer, audience, expiry, and tenant binding now fail closed before request routing, "
        "and `X-CWL-Tenant-Reference` can no longer act as caller identity.\n"
    )
    marker = "### Changed\n"
    if entry not in changelog:
        if marker not in changelog:
            raise SystemExit("CHANGELOG auth insertion anchor drifted")
        changelog = changelog.replace(marker, marker + "\n" + entry, 1)
        _write(changelog_path, changelog)


def main() -> None:
    """Apply HTTP authentication source, regressions, and documentation together."""
    harden_http_boundary()
    update_postgres_http_fixtures()
    update_docs()


if __name__ == "__main__":
    main()
