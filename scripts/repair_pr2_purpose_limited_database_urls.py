"""Separate ordinary runtime SQL authority from close/adjust/reversal authority."""

from __future__ import annotations

from pathlib import Path


def _read(path: str) -> str:
    """Return one UTF-8 repository file."""
    return Path(path).read_text(encoding="utf-8")


def _write(path: str, text: str) -> None:
    """Replace one UTF-8 repository file."""
    Path(path).write_text(text, encoding="utf-8")


def narrow_runtime_database_grants() -> None:
    """Keep fiscal-period transitions off the ordinary runtime privilege role."""
    path = "database/migrations/0007_runtime_tenant_binding.sql"
    text = _read(path)
    old = '''GRANT UPDATE ON accounting_core.fiscal_period,
                accounting_integration.journal_proposal_record,
                accounting_integration.outbox_event
    TO accounting_runtime_user;
'''
    new = '''GRANT UPDATE ON accounting_integration.journal_proposal_record,
                accounting_integration.outbox_event
    TO accounting_runtime_user;

GRANT UPDATE ON accounting_core.fiscal_period
    TO accounting_closing_writer;
'''
    if new not in text:
        if old not in text:
            raise SystemExit("runtime UPDATE grant anchor drifted")
        text = text.replace(old, new, 1)
    _write(path, text)


def split_http_database_authority() -> None:
    """Route privileged accounting commands through a separately provisioned DB URL."""
    path = "src/accounting_information_platform/http_api.py"
    text = _read(path)

    server_old = '''    def __init__(
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
    server_new = '''    def __init__(
        self,
        server_address: tuple[str, int],
        database_url: str,
        tenant_reference: str,
        bearer_token_validator: Callable[[str], str] | None = None,
        closing_database_url: str | None = None,
    ) -> None:
        """Bind one tenant, ordinary DB authority, and optional privileged close authority."""
        self.database_url = database_url
        self.closing_database_url = closing_database_url
        self.tenant_reference = tenant_reference
        self.bearer_token_validator = (
            bearer_token_validator
            if bearer_token_validator is not None
            else _reject_unconfigured_bearer_token
        )
        super().__init__(server_address, JournalProposalHandler)
'''
    if server_new not in text:
        if server_old not in text:
            raise SystemExit("authenticated JournalProposalServer anchor drifted")
        text = text.replace(server_old, server_new, 1)

    create_old = '''def create_journal_proposal_server(
    database_url: str,
    tenant_reference: str,
    host: str = "127.0.0.1",
    port: int = 0,
    bearer_token_validator: Callable[[str], str] | None = None,
) -> JournalProposalServer:
'''
    create_new = '''def create_journal_proposal_server(
    database_url: str,
    tenant_reference: str,
    host: str = "127.0.0.1",
    port: int = 0,
    bearer_token_validator: Callable[[str], str] | None = None,
    closing_database_url: str | None = None,
) -> JournalProposalServer:
'''
    if create_new not in text:
        if create_old not in text:
            raise SystemExit("authenticated server factory signature drifted")
        text = text.replace(create_old, create_new, 1)

    create_return_old = '''    return JournalProposalServer(
        (host, port),
        database_url,
        tenant_reference,
        bearer_token_validator=bearer_token_validator,
    )
'''
    create_return_new = '''    return JournalProposalServer(
        (host, port),
        database_url,
        tenant_reference,
        bearer_token_validator=bearer_token_validator,
        closing_database_url=closing_database_url,
    )
'''
    if create_return_new not in text:
        if create_return_old not in text:
            raise SystemExit("authenticated server factory return drifted")
        text = text.replace(create_return_old, create_return_new, 1)

    run_old = '''def run_journal_proposal_server(
    database_url: str | None = None,
    tenant_reference: str | None = None,
    host: str | None = None,
    port: int | None = None,
    serve: Callable[[], None] | None = None,
    bearer_token_validator: Callable[[str], str] | None = None,
) -> JournalProposalServer:
'''
    run_new = '''def run_journal_proposal_server(
    database_url: str | None = None,
    tenant_reference: str | None = None,
    host: str | None = None,
    port: int | None = None,
    serve: Callable[[], None] | None = None,
    bearer_token_validator: Callable[[str], str] | None = None,
    closing_database_url: str | None = None,
) -> JournalProposalServer:
'''
    if run_new not in text:
        if run_old not in text:
            raise SystemExit("authenticated HTTP runner signature drifted")
        text = text.replace(run_old, run_new, 1)

    tenant_anchor = '''    resolved_tenant = (
        tenant_reference
        if tenant_reference is not None
        else os.environ.get("ACCOUNTING_TENANT_REFERENCE", "")
    )
'''
    tenant_replacement = tenant_anchor + '''    resolved_closing_url = (
        closing_database_url
        if closing_database_url is not None
        else os.environ.get("ACCOUNTING_CLOSING_DATABASE_URL", "")
    )
'''
    if "resolved_closing_url = (" not in text:
        if tenant_anchor not in text:
            raise SystemExit("closing database env anchor drifted")
        text = text.replace(tenant_anchor, tenant_replacement, 1)

    run_create_old = '''    server = create_journal_proposal_server(
        resolved_url,
        resolved_tenant,
        resolved_host,
        resolved_port,
        bearer_token_validator=bearer_token_validator,
    )
'''
    run_create_new = '''    server = create_journal_proposal_server(
        resolved_url,
        resolved_tenant,
        resolved_host,
        resolved_port,
        bearer_token_validator=bearer_token_validator,
        closing_database_url=resolved_closing_url or None,
    )
'''
    if run_create_new not in text:
        if run_create_old not in text:
            raise SystemExit("closing database runner call drifted")
        text = text.replace(run_create_old, run_create_new, 1)

    helper_marker = '''    def _bound_tenant_header(self, mismatch_action: str) -> str | None:
'''
    helper = '''    def _closing_database_url(self, operation_name: str) -> str | None:
        """Return the purpose-limited DB URL or fail closed before a privileged command."""
        database_url = self.server.closing_database_url
        if database_url:
            return database_url
        self._write_error(
            503,
            "purpose-limited accounting database authority is not configured. "
            f"Configure ACCOUNTING_CLOSING_DATABASE_URL before {operation_name}, then retry.",
        )
        return None

'''
    if "def _closing_database_url(" not in text:
        if helper_marker not in text:
            raise SystemExit("closing database helper insertion anchor drifted")
        text = text.replace(helper_marker, helper + helper_marker, 1)

    command_replacements = (
        (
            '''        try:\n            document = accept_adjusting_journal(\n                payload, self.server.database_url, tenant_header\n            )\n''',
            '''        closing_database_url = self._closing_database_url("posting an adjusting journal")\n        if closing_database_url is None:\n            return\n        try:\n            document = accept_adjusting_journal(\n                payload, closing_database_url, tenant_header\n            )\n''',
        ),
        (
            '''        try:\n            document = accept_journal_reversal(\n                payload, self.server.database_url, tenant_header\n            )\n''',
            '''        closing_database_url = self._closing_database_url("posting a journal reversal")\n        if closing_database_url is None:\n            return\n        try:\n            document = accept_journal_reversal(\n                payload, closing_database_url, tenant_header\n            )\n''',
        ),
        (
            '''        try:\n            document = accept_period_close(\n                payload, self.server.database_url, tenant_header\n            )\n''',
            '''        closing_database_url = self._closing_database_url("closing a fiscal period")\n        if closing_database_url is None:\n            return\n        try:\n            document = accept_period_close(\n                payload, closing_database_url, tenant_header\n            )\n''',
        ),
    )
    for old, new in command_replacements:
        if new in text:
            continue
        if old not in text:
            raise SystemExit("privileged HTTP command database anchor drifted")
        text = text.replace(old, new, 1)

    _write(path, text)


def adapt_http_tests() -> None:
    """Keep existing privileged-path tests explicit and prove missing authority fails closed."""
    path = "tests/test_postgres_posting.py"
    text = _read(path)

    helper_old = '''        server = create_journal_proposal_server(
            DATABASE_URL,
            bound_tenant,
            "127.0.0.1",
            0,
            bearer_token_validator=validate_test_token,
        )
'''
    helper_new = '''        server = create_journal_proposal_server(
            DATABASE_URL,
            bound_tenant,
            "127.0.0.1",
            0,
            bearer_token_validator=validate_test_token,
            closing_database_url=DATABASE_URL,
        )
'''
    if helper_new not in text:
        if helper_old not in text:
            raise SystemExit("authenticated PostgreSQL HTTP test server anchor drifted")
        text = text.replace(helper_old, helper_new, 1)

    mismatch_old = '''        server = create_journal_proposal_server(
            DATABASE_URL,
            self.policy.tenant_reference,
            "127.0.0.1",
            0,
            bearer_token_validator=other_tenant_validator,
        )
'''
    mismatch_new = '''        server = create_journal_proposal_server(
            DATABASE_URL,
            self.policy.tenant_reference,
            "127.0.0.1",
            0,
            bearer_token_validator=other_tenant_validator,
            closing_database_url=DATABASE_URL,
        )
'''
    if mismatch_new not in text:
        if mismatch_old not in text:
            raise SystemExit("authenticated tenant-mismatch server anchor drifted")
        text = text.replace(mismatch_old, mismatch_new, 1)

    test_marker = '''    def test_http_reverses_posted_journal_and_preserves_original_receipt(self) -> None:
'''
    regression = '''    def test_http_privileged_commands_fail_without_closing_database_authority(self) -> None:
        """Authenticated tenant access alone cannot exercise close/adjust/reversal DB authority."""
        def validate_test_token(token: str) -> str:
            if token != "ais-test-token":
                raise AccountingValidationError("test bearer token is invalid")
            return self.policy.tenant_reference

        server = create_journal_proposal_server(
            DATABASE_URL,
            self.policy.tenant_reference,
            "127.0.0.1",
            0,
            bearer_token_validator=validate_test_token,
        )
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        previous_server = getattr(self, "_http_server", None)
        self._http_server = server
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        try:
            status, body = self._http_json(
                "POST",
                "/period-closes",
                {
                    "tenant_reference": self.policy.tenant_reference,
                    "legal_entity_reference": self.policy.legal_entity_reference,
                    "book_reference": self.policy.accounting_book_reference,
                    "fiscal_period_reference": "2026-08",
                    "snapshot_currency_code": "KRW",
                    "idempotency_key": f"missing-close-authority:{uuid.uuid4()}",
                },
            )
        finally:
            if previous_server is not None:
                self._http_server = previous_server

        self.assertEqual(status, 503)
        self.assertIn("ACCOUNTING_CLOSING_DATABASE_URL", str(body["error_message"]))
        self.assertEqual(self._count_table("accounting_reporting.trial_balance_snapshot"), 0)

'''
    if "test_http_privileged_commands_fail_without_closing_database_authority" not in text:
        if test_marker not in text:
            raise SystemExit("privileged HTTP authority test insertion marker drifted")
        text = text.replace(test_marker, regression + test_marker, 1)

    runtime_anchor = '''        self._set_period_status("soft_closed")
        with psycopg.connect(runtime_url) as connection:
'''
    runtime_replacement = '''        self._set_period_status("soft_closed")
        with psycopg.connect(runtime_url) as connection:
            with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                connection.execute(
                    """
                    UPDATE accounting_core.fiscal_period
                       SET period_status_code = 'open', period_closed_at = NULL
                     WHERE tenant_account_id = %s
                       AND period_code = '2026-08'
                    """,
                    (self.tenant_id,),
                )
            connection.rollback()

        with psycopg.connect(runtime_url) as connection:
'''
    if "SET period_status_code = 'open', period_closed_at = NULL" not in text:
        if runtime_anchor not in text:
            raise SystemExit("runtime direct-period-transition regression anchor drifted")
        text = text.replace(runtime_anchor, runtime_replacement, 1)

    _write(path, text)


def update_docs() -> None:
    """Document ordinary versus closing database authority as an operational boundary."""
    security_path = "docs/SECURITY.md"
    security = _read(security_path).rstrip()
    section = '''

## Purpose-limited accounting database authority

The ordinary `ACCOUNTING_DATABASE_URL` runtime login is a member of `accounting_runtime_user` and cannot update fiscal-period state. Adjusting-journal, reversal, and period-close HTTP commands require the separately configured `ACCOUNTING_CLOSING_DATABASE_URL`. That login is provisioned for the same tenant as a non-owner, non-superuser, non-`BYPASSRLS` member of both `accounting_runtime_user` and the NOLOGIN `accounting_closing_writer` role. The latter role owns the narrow fiscal-period transition permission and is also required by the soft-close journal trigger. Do not grant `accounting_closing_writer` to ordinary posting/read service identities. Missing closing authority fails closed before the privileged command reaches persistence.
'''
    if "## Purpose-limited accounting database authority" not in security:
        security += section
    _write(security_path, security.rstrip() + "\n")

    operability_path = "docs/OPERABILITY.md"
    operability = _read(operability_path).rstrip()
    section = '''

## Purpose-limited close connection

Configure `ACCOUNTING_DATABASE_URL` with the ordinary tenant-bound runtime login. Configure `ACCOUNTING_CLOSING_DATABASE_URL` with a different tenant-bound runtime login that additionally belongs to `accounting_closing_writer`; use it only for adjusting-journal, reversal, and period-close command paths. The ordinary runtime role has no `UPDATE` privilege on `accounting_core.fiscal_period`, so a compromised posting/read connection cannot reopen or close a period with direct SQL. Rotate the two credentials independently and audit membership changes. If the close connection is absent, the privileged HTTP routes return a next-action error instead of borrowing ordinary or administrative database authority.
'''
    if "## Purpose-limited close connection" not in operability:
        operability += section
    _write(operability_path, operability.rstrip() + "\n")

    changelog_path = "CHANGELOG.md"
    changelog = _read(changelog_path)
    entry = "- Separated ordinary runtime SQL authority from adjusting/reversal/period-close database authority; fiscal-period transitions now require a purpose-limited closing login instead of broad `accounting_runtime_user` update privilege.\n"
    if entry not in changelog:
        marker = "### Security\n"
        if marker in changelog:
            changelog = changelog.replace(marker, marker + "\n" + entry, 1)
        else:
            changelog = changelog.rstrip() + "\n\n### Security\n\n" + entry
    _write(changelog_path, changelog)


def main() -> None:
    """Apply purpose-limited DB grants, route separation, tests, and operator docs."""
    narrow_runtime_database_grants()
    split_http_database_authority()
    adapt_http_tests()
    update_docs()


if __name__ == "__main__":
    main()
