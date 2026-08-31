"""Documentation contracts for authoritative reconciliation close-package provenance."""

from pathlib import Path
import unittest


_REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
_ADR_PATH = _REPOSITORY_ROOT / "docs/adr/0056-reconciliation-close-package-provenance.md"
_CHANGELOG_PATH = _REPOSITORY_ROOT / "CHANGELOG.md"


class ReconciliationClosePackageAuthoritativeDocumentationTests(unittest.TestCase):
    """Keep operator-facing provenance documentation aligned with the builder."""

    def test_adr_binds_run_cutoff_digest_and_statement_artifact_to_postgresql(self) -> None:
        adr = _ADR_PATH.read_text(encoding="utf-8")
        self.assertIn("whole-second or six-digit microsecond precision", adr)
        self.assertIn("exactly one `statement_artifact`", adr)
        self.assertIn("database-owned `reconciliation_run`", adr)
        self.assertIn("`reconciliation_run_command`", adr)
        self.assertIn("retained bank-statement artifact", adr)
        self.assertNotIn("second-precision `knowledge_cutoff`", adr)
        self.assertNotIn("at least one immutable `statement_artifact`", adr)

    def test_changelog_records_database_owned_run_and_artifact_binding(self) -> None:
        changelog = _CHANGELOG_PATH.read_text(encoding="utf-8")
        self.assertIn("database-owned reconciliation-run provenance", changelog)
        self.assertIn("retained statement artifact", changelog)
        self.assertIn("six-digit microsecond", changelog)


if __name__ == "__main__":
    unittest.main()
