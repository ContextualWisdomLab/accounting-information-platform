"""One-shot repair making HomeTax idempotency identity mandatory at command entry."""

from __future__ import annotations

from pathlib import Path


def _read(path: str) -> str:
    """Return one UTF-8 repository file."""
    return Path(path).read_text(encoding="utf-8")


def _write(path: str, text: str) -> None:
    """Replace one UTF-8 repository file."""
    Path(path).write_text(text, encoding="utf-8")


def require_command_key_before_scope_validation() -> None:
    """Reject a HomeTax write command without identity before any scope-derived outcome."""
    path = "src/accounting_information_platform/accept.py"
    text = _read(path)
    early_key = '''    submission_idempotency_key = str(payload.get("idempotency_key") or "").strip()
    if not submission_idempotency_key:
        raise AccountingValidationError(
            "idempotency_key is required. "
            "Supply the home-tax-submission command idempotency key, then retry."
        )
'''
    late = '''    submission_idempotency_key = str(payload.get("idempotency_key") or "").strip()
    if not submission_idempotency_key:
        raise AccountingValidationError(
            "idempotency_key is required. "
            "Supply the home-tax-submission command idempotency key, then retry."
        )
    ledger = PostgresPostingLedger(database_url, tenant_reference)
'''
    start_marker = "def accept_home_tax_submission(\n"
    end_marker = "\ndef lookup_home_tax_submissions(\n"
    if start_marker not in text:
        raise SystemExit("HomeTax command method start drifted")
    start = text.index(start_marker)
    try:
        end = text.index(end_marker, start)
    except ValueError as error:
        raise SystemExit("HomeTax command method end drifted") from error
    method = text[start:end]

    if late in method:
        method = method.replace(
            late,
            "    ledger = PostgresPostingLedger(database_url, tenant_reference)\n",
            1,
        )

    anchor = '''    legal_entity_reference = str(payload.get("legal_entity_reference") or "")
'''
    if anchor not in method:
        raise SystemExit("HomeTax command entry anchor drifted")
    anchor_index = method.index(anchor)
    if (
        "submission_idempotency_key = payload.get(" not in method[:anchor_index]
        and early_key not in method[:anchor_index]
    ):
        method = method.replace(anchor, early_key + anchor, 1)

    _write(path, text[:start] + method + text[end:])


def strengthen_http_regression() -> None:
    """Prove missing command identity wins even when required accounting scope is absent."""
    path = "tests/test_postgres_posting.py"
    text = _read(path)
    old = '''        missing_key_status, _missing_key = self._http_json(
            "POST",
            "/home-tax-submissions",
            {
                "tenant_reference": self.policy.tenant_reference,
                "legal_entity_reference": self.policy.legal_entity_reference,
                "book_reference": self.policy.accounting_book_reference,
                "fiscal_period_reference": "urn:cwl:accounting:fiscal_period:2026-08",
            },
        )
        self.assertEqual(missing_key_status, 404)
'''
    new = '''        missing_key_status, missing_key = self._http_json(
            "POST",
            "/home-tax-submissions",
            {"tenant_reference": self.policy.tenant_reference},
        )
        self.assertEqual(missing_key_status, 404)
        self.assertIn("idempotency_key", str(missing_key))
'''
    if new not in text:
        if old not in text:
            raise SystemExit("HomeTax missing-key regression anchor drifted")
        text = text.replace(old, new, 1)
    _write(path, text)


def update_contract_docs() -> None:
    """Record that a write-command identity is required before scope-specific processing."""
    path = "docs/adr/0003-append-only-journals.md"
    text = _read(path)
    sentence = (
        "HomeTax submission commands require their tenant-scoped idempotency key at command "
        "entry, before scope completeness or register availability is evaluated, so even "
        "fail-closed write attempts have an explicit command identity."
    )
    if sentence not in text:
        marker = "\n## Consequences\n"
        if marker not in text:
            raise SystemExit("HomeTax command identity ADR anchor drifted")
        text = text.replace(marker, "\n" + sentence + "\n" + marker, 1)
    _write(path, text)


def main() -> None:
    """Apply HomeTax entry identity, regression, and ADR repair."""
    require_command_key_before_scope_validation()
    strengthen_http_regression()
    update_contract_docs()


if __name__ == "__main__":
    main()
