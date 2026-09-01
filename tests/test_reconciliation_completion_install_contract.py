"""Contracts keeping reconciliation completion in the public migration chain."""

from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

from accounting_information_platform import AccountingValidationError, apply_foundation_migration


ROOT = Path(__file__).resolve().parents[1]
MIGRATION_ROOT = ROOT / "database/migrations/0001_accounting_foundation.sql"
COMPLETION_MIGRATION = ROOT / "database/migrations/0020_reconciliation_completion_command.sql"
INSTALL_SOURCE = ROOT / "src/accounting_information_platform/migration_install.py"


class ReconciliationCompletionInstallContractTests(unittest.TestCase):
    """Require the public installer to fail closed before omitting migration 0020."""

    def test_public_installer_names_and_executes_completion_migration(self) -> None:
        """The exported install boundary must extend the canonical chain through 0020."""
        source = INSTALL_SOURCE.read_text(encoding="utf-8")
        self.assertIn('"0020_reconciliation_completion_command.sql"', source)
        self.assertIn("completion_migration_path.is_file()", source)
        self.assertIn("completion_migration_path.read_text(encoding=\"utf-8\")", source)
        self.assertIn("connection.execute", source)

    def test_public_installer_fails_before_partial_install_when_0020_is_missing(self) -> None:
        """A missing completion migration is rejected before the older chain is applied."""
        original_is_file = Path.is_file

        def is_file(path: Path) -> bool:
            if path.name == COMPLETION_MIGRATION.name:
                return False
            return original_is_file(path)

        with patch.object(Path, "is_file", is_file), patch(
            "accounting_information_platform.migration_install._apply_foundation_migration"
        ) as legacy_install:
            with self.assertRaisesRegex(
                AccountingValidationError,
                "Reconciliation completion-command migration is missing",
            ):
                apply_foundation_migration("postgresql://unused", MIGRATION_ROOT)
        legacy_install.assert_not_called()

    def test_operator_docs_list_0020_after_run_command_evidence(self) -> None:
        """Deployment documentation must not stop the accounting schema at 0019."""
        migration_nineteen = "database/migrations/0019_reconciliation_run_command_evidence.sql"
        migration_twenty = "database/migrations/0020_reconciliation_completion_command.sql"
        for relative_path in ("docs/OPERABILITY.md", "docs/ARCHITECTURE.md"):
            with self.subTest(relative_path=relative_path):
                text = (ROOT / relative_path).read_text(encoding="utf-8")
                self.assertIn(migration_nineteen, text)
                self.assertIn(migration_twenty, text)
                self.assertLess(text.index(migration_nineteen), text.index(migration_twenty))


if __name__ == "__main__":  # pragma: no cover - direct invocation convenience
    unittest.main()
