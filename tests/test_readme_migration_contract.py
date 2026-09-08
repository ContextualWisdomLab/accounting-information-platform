"""README migration-chain documentation contracts."""

from __future__ import annotations

from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]


class ReadmeMigrationContractTests(unittest.TestCase):
    """Keep operator install guidance aligned with the canonical foundation manifest."""

    def test_readme_names_canonical_foundation_install_endpoint(self) -> None:
        """Branch-local provisional migrations must not redefine the foundation endpoint."""
        validator = (ROOT / "scripts" / "validate_repository.py").read_text(
            encoding="utf-8"
        )
        migration_names = re.findall(
            r'"database/migrations/(\d{4}_[^"]+\.sql)"',
            validator,
        )
        self.assertTrue(
            migration_names,
            "expected canonical accounting migrations in REQUIRED_FILES",
        )
        latest_migration = migration_names[-1]
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn(
            f"migration chain through `database/migrations/{latest_migration}`",
            readme,
        )


if __name__ == "__main__":
    unittest.main()
