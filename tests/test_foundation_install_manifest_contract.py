"""Contracts for the complete foundation install and reviewable evidence manifest."""

from __future__ import annotations

import unittest
from pathlib import Path

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


if __name__ == "__main__":
    unittest.main()
