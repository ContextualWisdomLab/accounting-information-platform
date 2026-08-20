"""One-shot repair requiring reversals to follow the original accounting date."""

from __future__ import annotations

from pathlib import Path


def _read(path: str) -> str:
    """Return one UTF-8 repository file."""
    return Path(path).read_text(encoding="utf-8")


def _write(path: str, text: str) -> None:
    """Replace one UTF-8 repository file."""
    Path(path).write_text(text, encoding="utf-8")


def harden_reference_oracle() -> None:
    """Reject an in-memory reversal dated before its original journal."""
    path = "src/accounting_information_platform/core.py"
    text = _read(path)
    if "reversal date precedes original accounting date" in text:
        return
    anchor = '''        if original.reversal_of_journal_reference is not None:\n            raise AccountingValidationError("a reversal journal cannot itself be reversed")\n        if not policy.permits(reversal_date):\n'''
    replacement = '''        if original.reversal_of_journal_reference is not None:\n            raise AccountingValidationError("a reversal journal cannot itself be reversed")\n        if reversal_date < original.accounting_date:\n            raise AccountingValidationError(\n                "reversal date precedes original accounting date"\n            )\n        if not policy.permits(reversal_date):\n'''
    if anchor not in text:
        raise SystemExit("reference reversal temporal-order anchor drifted")
    _write(path, text.replace(anchor, replacement, 1))


def harden_postgres_reversal() -> None:
    """Load the original accounting date and reject backdated reversals."""
    path = "src/accounting_information_platform/persistence.py"
    text = _read(path)
    if "reversal date precedes original accounting date" in text:
        return
    select_anchor = '''                SELECT general_journal_id, legal_entity_id, accounting_book_id,\n                       transaction_currency_code, functional_currency_code,\n                       source_proposal_record_id, transaction_date\n'''
    select_replacement = '''                SELECT general_journal_id, legal_entity_id, accounting_book_id,\n                       transaction_currency_code, functional_currency_code,\n                       source_proposal_record_id, transaction_date, accounting_date\n'''
    if select_anchor not in text:
        raise SystemExit("PostgreSQL reversal accounting-date select anchor drifted")
    text = text.replace(select_anchor, select_replacement, 1)
    validation_anchor = '''            if already_reversal is not None:\n                raise AccountingValidationError(\n                    "a reversal journal cannot itself be reversed. Reverse the original journal, or post a replacement."\n                )\n            if not policy.permits(reversal_date):\n'''
    validation_replacement = '''            if already_reversal is not None:\n                raise AccountingValidationError(\n                    "a reversal journal cannot itself be reversed. Reverse the original journal, or post a replacement."\n                )\n            if reversal_date < original[7]:\n                raise AccountingValidationError(\n                    "reversal date precedes original accounting date"\n                )\n            if not policy.permits(reversal_date):\n'''
    if validation_anchor not in text:
        raise SystemExit("PostgreSQL reversal temporal-order validation anchor drifted")
    _write(path, text.replace(validation_anchor, validation_replacement, 1))


def add_regressions() -> None:
    """Add reference and real-PostgreSQL tests for reversal temporal order."""
    core_path = "tests/test_accounting_core.py"
    core = _read(core_path)
    marker = "    def test_same_proposal_id_posts_independently_per_tenant(self) -> None:\n"
    regression = '''    def test_reversal_cannot_precede_original_accounting_date(self) -> None:\n        """A reversal cannot appear in a trial balance before its original journal."""\n        original = self.ledger.post(self._invoice_proposal(), self.policy)\n\n        with self.assertRaisesRegex(\n            AccountingValidationError, "precedes original accounting date"\n        ):\n            self.ledger.reverse(\n                original.journal_reference,\n                date(2026, 8, 30),\n                "billing_correction",\n                self.policy,\n                reversal_idempotency_key="reversal:invoice-1:backdated:v1",\n            )\n\n        self.assertEqual(self.ledger.journal_count, 1)\n\n'''
    if "test_reversal_cannot_precede_original_accounting_date" not in core:
        if marker not in core:
            raise SystemExit("reference reversal temporal regression marker drifted")
        core = core.replace(marker, regression + marker, 1)
        _write(core_path, core)

    postgres_path = "tests/test_postgres_posting.py"
    postgres = _read(postgres_path)
    marker = "    def test_closed_period_posts_zero_rows(self) -> None:\n"
    regression = '''    def test_postgres_reversal_cannot_precede_original_accounting_date(self) -> None:\n        """The durable reversal path rejects a date before the original accounting date."""\n        original = self.ledger.post(self._two_line_proposal(), self.policy)\n\n        with self.assertRaisesRegex(\n            AccountingValidationError, "precedes original accounting date"\n        ):\n            self.ledger.reverse(\n                original.journal_reference,\n                date(2026, 8, 30),\n                "billing_correction",\n                self.policy,\n                reversal_idempotency_key=(\n                    f"{self.policy.tenant_reference}:reversal:backdated:v1"\n                ),\n            )\n\n        self.assertEqual(self._count_table("accounting_core.general_journal"), 1)\n        self.assertEqual(self._count_table("accounting_core.journal_reversal"), 0)\n\n'''
    if "test_postgres_reversal_cannot_precede_original_accounting_date" not in postgres:
        if marker not in postgres:
            raise SystemExit("PostgreSQL reversal temporal regression marker drifted")
        postgres = postgres.replace(marker, regression + marker, 1)
        _write(postgres_path, postgres)


def add_authority_edge_regression() -> None:
    """Cover the new command, migration, and HTTP denial edges in one real DB test."""
    path = "tests/test_postgres_posting.py"
    tests = _read(path)
    if "test_repair_authority_edge_cases" in tests:
        return
    marker = "    def test_closed_period_posts_zero_rows(self) -> None:\n"
    regression = '''    def test_repair_authority_edge_cases(self) -> None:
        """Exercise empty command identities, changed reversal evidence, and missing authority files."""
        original = self.ledger.post(
            self._two_line_proposal(
                proposal_id=str(uuid.uuid4()),
                idempotency_key=f"{self.policy.tenant_reference}:edge-post:v1",
                source_payload_hash="sha256:" + "b" * 64,
            )
        )
        reverse_payload = {
            "tenant_reference": self.policy.tenant_reference,
            "journal_reference": original.journal_reference,
            "reversal_date": "2026-08-31",
            "reversal_reason_code": "billing_correction",
            "reversal_idempotency_key": "",
        }
        with self.assertRaisesRegex(
            AccountingValidationError, "reversal_idempotency_key"
        ):
            accept_journal_reversal(reverse_payload, DATABASE_URL, self.policy.tenant_reference)
        with self.assertRaisesRegex(AccountingValidationError, "complete VAT register"):
            self.ledger.persist_home_tax_submission(
                legal_entity_reference=self.policy.legal_entity_reference,
                accounting_book_reference=self.policy.accounting_book_reference,
                period_code="2026-08",
                register_document={},
                rejection_reason_code="register_unavailable",
                submission_idempotency_key="edge:missing-register:v1",
            )
        with self.assertRaisesRegex(
            AccountingValidationError, "reversal_idempotency_key"
        ):
            self.ledger.reverse(
                original.journal_reference,
                date(2026, 8, 31),
                "billing_correction",
                self.policy,
                reversal_idempotency_key="",
            )

        server = self._start_http_server()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        command = {
            "tenant_reference": self.policy.tenant_reference,
            "journal_reference": original.journal_reference,
            "reversal_date": "2026-08-31",
            "reversal_reason_code": "billing_correction",
            "reversal_idempotency_key": f"{self.policy.tenant_reference}:edge-reversal:v1",
        }
        first_status, _first = self._http_json("POST", "/journal-reversals", command)
        conflict_status, conflict = self._http_json(
            "POST",
            "/journal-reversals",
            {**command, "reversal_reason_code": "duplicate_charge"},
        )
        self.assertEqual(first_status, 200)
        self.assertEqual(conflict_status, 409)
        self.assertIn("reversal_idempotency_key", str(conflict))

        migration_names = (
            "0001_accounting_foundation.sql",
            "0002_chart_account_class.sql",
            "0003_home_tax_submission.sql",
            "0004_close_idempotency_key.sql",
            "0005_closed_period_guard.sql",
        )
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary_root = Path(temporary_directory)
            for name in migration_names:
                (temporary_root / name).write_text("-- authority-path test\n", encoding="utf-8")
            with self.assertRaisesRegex(AccountingValidationError, "0006_force_tenant_rls"):
                apply_foundation_migration(
                    DATABASE_URL, temporary_root / migration_names[0]
                )
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary_root = Path(temporary_directory)
            for name in migration_names + ("0006_force_tenant_rls.sql",):
                (temporary_root / name).write_text("-- authority-path test\n", encoding="utf-8")
            with self.assertRaisesRegex(AccountingValidationError, "0007_runtime_tenant_binding"):
                apply_foundation_migration(
                    DATABASE_URL, temporary_root / migration_names[0]
                )

'''
    if marker not in tests:
        raise SystemExit("authority edge regression insertion marker drifted")
    _write(path, tests.replace(marker, regression + marker, 1))


def update_documentation() -> None:
    """Record the no-backdating reversal invariant in the canonical ADR."""
    path = "docs/adr/0003-append-only-journals.md"
    text = _read(path)
    sentence = (
        "If the occupant is the existing reversing journal for that original, "
        "the request replays only when tenant_reference, reversal idempotency key, "
        "original journal_reference, and immutable canonical source-payload hash all "
        "match; any mismatch fails closed."
    )
    replacement = sentence + (
        " A reversal accounting date must also be on or after the original "
        "journal accounting date, so a reversal cannot appear in a trial balance "
        "before the fact it corrects."
    )
    if replacement in text:
        return
    if sentence not in text:
        raise SystemExit("ADR reversal temporal-order anchor drifted")
    _write(path, text.replace(sentence, replacement, 1))


def main() -> None:
    """Apply reversal temporal-order production, test, and documentation repairs."""
    harden_reference_oracle()
    harden_postgres_reversal()
    add_regressions()
    add_authority_edge_regression()
    update_documentation()


if __name__ == "__main__":
    main()
