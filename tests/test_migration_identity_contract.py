"""Repository contract for canonical accounting migration identity."""

from __future__ import annotations

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = ROOT / "database" / "migrations"


class MigrationIdentityContractTests(unittest.TestCase):
    """Keep unreleased and released accounting migrations uniquely ordered."""

    def test_migration_numeric_identity_is_unique_and_contiguous(self) -> None:
        """Every SQL migration owns one four-digit position in the canonical chain."""
        migration_paths = sorted(MIGRATIONS.glob("*.sql"))
        self.assertTrue(migration_paths, "database/migrations must contain SQL migrations")

        numeric_identities: list[int] = []
        for migration_path in migration_paths:
            prefix, separator, _name = migration_path.name.partition("_")
            self.assertEqual(separator, "_", f"migration lacks numeric prefix: {migration_path.name}")
            self.assertEqual(len(prefix), 4, f"migration prefix must be four digits: {migration_path.name}")
            self.assertTrue(prefix.isdigit(), f"migration prefix must be numeric: {migration_path.name}")
            numeric_identities.append(int(prefix))

        self.assertEqual(
            len(numeric_identities),
            len(set(numeric_identities)),
            "each accounting migration number must identify exactly one SQL migration",
        )
        self.assertEqual(
            numeric_identities,
            list(range(1, max(numeric_identities) + 1)),
            "accounting migration identities must be monotonically contiguous",
        )


if __name__ == "__main__":
    unittest.main()
