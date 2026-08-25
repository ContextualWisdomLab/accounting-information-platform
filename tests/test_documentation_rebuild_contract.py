"""Contracts for rebuilding the documentation successor on integrated develop."""

from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class DocumentationRebuildContractTests(unittest.TestCase):
    """Keep contributor, sequencing, and reporting-taxonomy docs code-current."""

    def test_root_contributor_entry_exists(self) -> None:
        """GitHub's conventional contributor entry must point to repository guidance."""
        contributor = ROOT / "CONTRIBUTING.md"
        self.assertTrue(contributor.is_file())
        text = contributor.read_text(encoding="utf-8")
        self.assertIn("docs/CONTRIBUTING.md", text)
        self.assertIn("docs/doctoring/IMPLEMENTATION_SEQUENCE.md", text)

    def test_implementation_sequence_describes_integrated_foundation(self) -> None:
        """The durable sequence cannot describe persistence or HTTP as unimplemented."""
        sequence = ROOT / "docs" / "doctoring" / "IMPLEMENTATION_SEQUENCE.md"
        self.assertTrue(sequence.is_file())
        text = sequence.read_text(encoding="utf-8")
        self.assertNotIn("does not yet run a live persistence adapter or HTTP service", text)
        self.assertIn("PostgresPostingLedger", text)
        self.assertIn("immutable bank-statement evidence", text)
        self.assertIn("deterministic reconciliation", text)
        self.assertIn("no automatic posting", text)

    def test_reporting_taxonomy_adr_does_not_reuse_runtime_binding_number(self) -> None:
        """Reporting projection must not collide with integrated ADR 0049."""
        runtime_binding = ROOT / "docs" / "adr" / "0049-runtime-tenant-database-binding.md"
        reporting_projection = ROOT / "docs" / "adr" / "0053-reporting-taxonomy-projection.md"
        stale_projection = ROOT / "docs" / "adr" / "0049-reporting-taxonomy-projection.md"
        self.assertTrue(runtime_binding.is_file())
        self.assertTrue(reporting_projection.is_file())
        self.assertFalse(stale_projection.exists())
        text = reporting_projection.read_text(encoding="utf-8")
        self.assertIn("versioned projection", text)
        self.assertIn("does not claim", text)

    def test_contributor_operations_do_not_describe_bootstrap_stack(self) -> None:
        """Repository operations must describe the integrated branch, not predecessor PRs."""
        text = (ROOT / "docs" / "CONTRIBUTING.md").read_text(encoding="utf-8")
        self.assertNotIn("protected default branch may remain a bootstrap commit", text)
        self.assertNotIn("open draft against `main`", text)
        self.assertIn("live ruleset", text)
        self.assertIn("predecessor evidence", text)


if __name__ == "__main__":
    unittest.main()
