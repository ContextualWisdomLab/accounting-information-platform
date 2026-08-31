"""Documentation contracts for authoritative reconciliation close-package provenance."""

from pathlib import Path
import unittest


_REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
_ADR_PATH = _REPOSITORY_ROOT / "docs/adr/0056-reconciliation-close-package-provenance.md"


class ReconciliationClosePackageAuthoritativeDocumentationTests(unittest.TestCase):
    """Keep operator-facing provenance documentation aligned with the builder."""

    def test_adr_binds_run_cutoff_digest_and_statement_artifact_to_postgresql(self) -> None:
        adr = _ADR_PATH.read_text(encoding="utf-8")
        self.assertIn("whole-second or six-digit microsecond precision", adr)
        self.assertIn("exactly one `statement_artifact`", adr)
        self.assertIn("database-owned `reconciliation_run`", adr)
        self.assertIn("`reconciliation_run_command`", adr)
        self.assertIn("retained bank-statement artifact", adr)
        self.assertIn("Caller-provided `knowledge_cutoff`", adr)
        self.assertNotIn("second-precision `knowledge_cutoff`", adr)
        self.assertNotIn("at least one immutable `statement_artifact`", adr)

    def test_adr_binds_postgresql_snapshot_identity_without_granting_authority(self) -> None:
        adr = _ADR_PATH.read_text(encoding="utf-8")
        self.assertIn("`reconciliation_snapshot_tenant`", adr)
        self.assertIn("internal `tenant_account_id`", adr)
        self.assertIn("Caller-supplied `reconciliation_snapshot_tenant` evidence", adr)
        self.assertIn("not accepted as a bearer secret", adr)
        self.assertIn("posting authority", adr)


if __name__ == "__main__":
    unittest.main()
