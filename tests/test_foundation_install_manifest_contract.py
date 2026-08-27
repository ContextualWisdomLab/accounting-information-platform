"""Contracts for the complete foundation install and reviewable evidence manifest."""

from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

from accounting_information_platform import AccountingValidationError, apply_foundation_migration
from scripts.validate_repository import REQUIRED_FILES


ROOT = Path(__file__).resolve().parents[1]


class FoundationInstallManifestContractTests(unittest.TestCase):
    """Keep required-file and migration-install documentation aligned with the schema."""

    def test_required_files_include_runtime_tenant_binding_and_its_adrs(self) -> None:
        """CI must fail when database-owned runtime tenant binding evidence is absent."""
        required = set(REQUIRED_FILES)
        self.assertTrue(
            {
                "database/migrations/0007_runtime_tenant_binding.sql",
                "docs/adr/0048-reproducible-package-evidence.md",
                "docs/adr/0049-runtime-tenant-database-binding.md",
            }
            <= required
        )

    def test_install_docs_include_runtime_tenant_binding_after_concurrency_migration(self) -> None:
        """Operators must install runtime tenant binding before granting runtime access."""
        migration_six = "database/migrations/0006_concurrency_hot_partition.sql"
        migration_seven = "database/migrations/0007_runtime_tenant_binding.sql"
        for relative_path in ("docs/OPERABILITY.md", "docs/ARCHITECTURE.md"):
            with self.subTest(relative_path=relative_path):
                text = (ROOT / relative_path).read_text(encoding="utf-8")
                self.assertIn(migration_six, text)
                self.assertIn(migration_seven, text)
                self.assertLess(text.index(migration_six), text.index(migration_seven))

    def test_required_files_and_install_docs_include_soft_close_command_evidence(self) -> None:
        """Soft-close evidence migration follows book-period control in operator docs."""
        migration_nine = "database/migrations/0009_accounting_book_period_control.sql"
        migration_ten = "database/migrations/0010_soft_close_command_evidence.sql"
        self.assertIn(migration_ten, set(REQUIRED_FILES))
        for relative_path in ("docs/OPERABILITY.md", "docs/ARCHITECTURE.md"):
            with self.subTest(relative_path=relative_path):
                text = (ROOT / relative_path).read_text(encoding="utf-8")
                self.assertIn(migration_nine, text)
                self.assertIn(migration_ten, text)
                self.assertLess(text.index(migration_nine), text.index(migration_ten))

    def test_required_files_and_install_docs_include_bank_statement_evidence(self) -> None:
        """Bank-statement evidence follows soft-close command evidence in operator docs."""
        migration_ten = "database/migrations/0010_soft_close_command_evidence.sql"
        migration_eleven = "database/migrations/0011_bank_statement_evidence.sql"
        self.assertIn(migration_eleven, set(REQUIRED_FILES))
        for relative_path in ("docs/OPERABILITY.md", "docs/ARCHITECTURE.md"):
            with self.subTest(relative_path=relative_path):
                text = (ROOT / relative_path).read_text(encoding="utf-8")
                self.assertIn(migration_ten, text)
                self.assertIn(migration_eleven, text)
                self.assertLess(text.index(migration_ten), text.index(migration_eleven))

    def test_required_files_and_install_docs_include_assignment_identity(self) -> None:
        """Assignment command identity follows bank-statement evidence in operator docs."""
        migration_eleven = "database/migrations/0011_bank_statement_evidence.sql"
        migration_twelve = "database/migrations/0012_bank_assignment_command_identity.sql"
        self.assertIn(migration_twelve, set(REQUIRED_FILES))
        for relative_path in ("docs/OPERABILITY.md", "docs/ARCHITECTURE.md"):
            with self.subTest(relative_path=relative_path):
                text = (ROOT / relative_path).read_text(encoding="utf-8")
                self.assertIn(migration_eleven, text)
                self.assertIn(migration_twelve, text)
                self.assertLess(text.index(migration_eleven), text.index(migration_twelve))

    def test_required_files_and_install_docs_include_reconciliation_control(self) -> None:
        """Reconciliation control follows assignment identity in operator/install contracts."""
        migration_twelve = "database/migrations/0012_bank_assignment_command_identity.sql"
        migration_thirteen = "database/migrations/0013_reconciliation_run_exception_evidence.sql"
        self.assertIn(migration_thirteen, set(REQUIRED_FILES))
        for relative_path in ("docs/OPERABILITY.md", "docs/ARCHITECTURE.md"):
            with self.subTest(relative_path=relative_path):
                text = (ROOT / relative_path).read_text(encoding="utf-8")
                self.assertIn(migration_twelve, text)
                self.assertIn(migration_thirteen, text)
                self.assertLess(text.index(migration_twelve), text.index(migration_thirteen))

    def test_install_fails_closed_when_reconciliation_control_migration_is_missing(self) -> None:
        """The public foundation loader may not silently omit migration 0013."""
        original_is_file = Path.is_file

        def is_file(path: Path) -> bool:
            if path.name == "0013_reconciliation_run_exception_evidence.sql":
                return False
            return original_is_file(path)

        with patch.object(Path, "is_file", is_file):
            with self.assertRaises(AccountingValidationError):
                apply_foundation_migration(
                    "postgresql://unused",
                    ROOT / "database/migrations/0001_accounting_foundation.sql",
                )

    def test_install_fails_closed_when_reconciliation_control_apply_fails(self) -> None:
        """Applying migration 0013 inside the authoritative chain keeps the PostgreSQL cause."""
        failing_psycopg = type("FailingPsycopg", (), {
            "ClientCursor": object,
            "connect": staticmethod(
                lambda *args, **kwargs: (_ for _ in ()).throw(
                    RuntimeError("postgres connection refused")
                )
            ),
        })
        with patch(
            "accounting_information_platform.persistence._import_psycopg",
            return_value=failing_psycopg,
        ):
            with self.assertRaisesRegex(
                AccountingValidationError, "Foundation migration failed"
            ) as raised:
                apply_foundation_migration(
                    "postgresql://unused",
                    ROOT / "database/migrations/0001_accounting_foundation.sql",
                )
        self.assertIsInstance(raised.exception.__cause__, RuntimeError)
        self.assertEqual(str(raised.exception.__cause__), "postgres connection refused")

    def test_install_fails_closed_when_assignment_identity_migration_is_missing(self) -> None:
        """The foundation loader may not silently omit migration 0012."""
        original_is_file = Path.is_file

        def is_file(path: Path) -> bool:
            if path.name == "0012_bank_assignment_command_identity.sql":
                return False
            return original_is_file(path)

        with patch.object(Path, "is_file", is_file):
            with self.assertRaises(AccountingValidationError):
                apply_foundation_migration(
                    "postgresql://unused",
                    ROOT / "database/migrations/0001_accounting_foundation.sql",
                )

    def test_install_fails_closed_when_bank_statement_migration_is_missing(self) -> None:
        """The foundation loader may not silently omit migration 0011."""
        original_is_file = Path.is_file

        def is_file(path: Path) -> bool:
            if path.name == "0011_bank_statement_evidence.sql":
                return False
            return original_is_file(path)

        with patch.object(Path, "is_file", is_file):
            with self.assertRaises(AccountingValidationError):
                apply_foundation_migration(
                    "postgresql://unused",
                    ROOT / "database/migrations/0001_accounting_foundation.sql",
                )

    def test_install_fails_closed_when_soft_close_evidence_migration_is_missing(self) -> None:
        """The foundation loader may not silently omit migration 0010."""
        original_is_file = Path.is_file

        def is_file(path: Path) -> bool:
            if path.name == "0010_soft_close_command_evidence.sql":
                return False
            return original_is_file(path)

        with patch.object(Path, "is_file", is_file):
            with self.assertRaises(AccountingValidationError):
                apply_foundation_migration(
                    "postgresql://unused",
                    ROOT / "database/migrations/0001_accounting_foundation.sql",
                )


if __name__ == "__main__":
    unittest.main()
