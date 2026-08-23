"""README migration-chain documentation contracts."""

from __future__ import annotations

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class ReadmeMigrationContractTests(unittest.TestCase):
    """Keep operator install guidance aligned with the checked-in schema chain."""

    def test_readme_names_latest_checked_in_migration(self) -> None:
        """README installation guidance must name the latest ordered migration."""
        migration_names = sorted(
            path.name for path in (ROOT / "database" / "migrations").glob("*.sql")
        )
        self.assertTrue(migration_names, "expected at least one accounting migration")
        latest_migration = migration_names[-1]
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn(
            f"migration chain through `database/migrations/{latest_migration}`",
            readme,
        )


if __name__ == "__main__":
    unittest.main()
