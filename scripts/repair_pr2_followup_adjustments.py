"""One-shot normalization after PR 2 accounting repair scripts run."""

from __future__ import annotations

from pathlib import Path


def read(path: str) -> str:
    """Return one UTF-8 repository file."""
    return Path(path).read_text(encoding="utf-8")


def write(path: str, text: str) -> None:
    """Replace one UTF-8 repository file."""
    Path(path).write_text(text, encoding="utf-8")


def fix_generated_python_source() -> None:
    """Keep generated regression fixtures syntactically valid."""
    path = "tests/test_repository_contracts.py"
    text = read(path)
    invalid = '''            'UPDATE "accounting_core"."general_journal" SET journal_status_code = 'posted';',
'''
    valid = '''            "UPDATE \\"accounting_core\\".\\"general_journal\\" SET journal_status_code = 'posted';",
'''
    if invalid not in text:
        raise SystemExit("quoted append-only regression fixture did not match")
    write(path, text.replace(invalid, valid, 1))


def retain_tenant_defense_on_reversal_cache() -> None:
    """Do not return a foreign-tenant receipt from a corrupted cache row."""
    path = "src/accounting_information_platform/core.py"
    text = read(path)
    desired = '''        prior_key, prior_original, prior_hash, prior_receipt = prior
        if prior_receipt.tenant_reference != tenant_reference:
            return None
        if (
            prior_key != reversal_idempotency_key
'''
    if desired in text:
        return
    anchor = '''        prior_key, prior_original, prior_hash, prior_receipt = prior
        if (
            prior_key != reversal_idempotency_key
'''
    if anchor not in text:
        raise SystemExit("reversal cache tenant-defense anchor drifted")
    write(path, text.replace(anchor, desired, 1))


def harden_home_tax_projection() -> None:
    """Index the scoped list and derive HTTP status from the stored document state."""
    migration_path = "database/migrations/0003_home_tax_submission.sql"
    migration = read(migration_path)
    anchor = '''ALTER TABLE accounting_integration.home_tax_submission ENABLE ROW LEVEL SECURITY;
'''
    index_sql = '''CREATE INDEX home_tax_submission_scope_index
    ON accounting_integration.home_tax_submission (
        tenant_account_id,
        legal_entity_id,
        accounting_book_id,
        fiscal_period_id,
        created_at,
        home_tax_submission_id
    );

ALTER TABLE accounting_integration.home_tax_submission ENABLE ROW LEVEL SECURITY;
'''
    if anchor not in migration:
        raise SystemExit("HomeTax index anchor drifted")
    write(migration_path, migration.replace(anchor, index_sql, 1))

    http_path = "src/accounting_information_platform/http_api.py"
    http = read(http_path)
    anchor = '''        self._write_json(422, document)

    def _read_body(self) -> bytes | None:
'''
    replacement = '''        response_status = (
            422 if document.get("submission_status_code") == "rejected" else 200
        )
        self._write_json(response_status, document)

    def _read_body(self) -> bytes | None:
'''
    if anchor not in http:
        raise SystemExit("HomeTax HTTP response anchor drifted")
    write(http_path, http.replace(anchor, replacement, 1))


def normalize_trigger_and_test_cleanup() -> None:
    """Make the ordinary trigger replayable and clean up the added HTTP test."""
    migration_path = "database/migrations/0005_closed_period_guard.sql"
    migration = read(migration_path)
    migration = migration.replace(
        "CREATE TRIGGER closed_period_guard\n",
        "CREATE OR REPLACE TRIGGER closed_period_guard\n",
        1,
    )
    write(migration_path, migration)

    test_path = "tests/test_postgres_posting.py"
    tests = read(test_path)
    anchor = '''        server = self._start_http_server()
        command_key = f"{self.policy.tenant_reference}:home_tax_submission:august:v1"
'''
    replacement = '''        server = self._start_http_server()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        command_key = f"{self.policy.tenant_reference}:home_tax_submission:august:v1"
'''
    if anchor not in tests:
        raise SystemExit("HomeTax replay HTTP cleanup anchor drifted")
    tests = tests.replace(anchor, replacement, 1)
    tests = tests.replace(
        '''        self.assertEqual(missing_key_status, 404)
        server.shutdown()

''',
        '''        self.assertEqual(missing_key_status, 404)

''',
        1,
    )
    write(test_path, tests)


def main() -> None:
    """Normalize every generated repair artifact before validation."""
    fix_generated_python_source()
    retain_tenant_defense_on_reversal_cache()
    harden_home_tax_projection()
    normalize_trigger_and_test_cleanup()


if __name__ == "__main__":
    main()
