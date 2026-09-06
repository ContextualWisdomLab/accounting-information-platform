"""Contracts for rebuilding the documentation successor on integrated develop."""

from __future__ import annotations

import re
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
        """The sequence must recognize integrated bank evidence and move to reconciliation."""
        sequence = ROOT / "docs" / "doctoring" / "IMPLEMENTATION_SEQUENCE.md"
        self.assertTrue(sequence.is_file())
        text = sequence.read_text(encoding="utf-8")
        self.assertNotIn("does not yet run a live persistence adapter or HTTP service", text)
        self.assertIn("PostgresPostingLedger", text)
        self.assertIn("bank-statement evidence registry", text)
        self.assertIn("already contains", text)
        self.assertIn("deterministic reconciliation", text)
        self.assertIn("book-to-bank bridge", text)
        self.assertIn("without automatically posting", text)
        self.assertTrue((ROOT / "src" / "accounting_information_platform" / "bank_statement.py").is_file())

    def test_gap_baseline_does_not_call_integrated_registry_a_candidate(self) -> None:
        """Durable gap docs must not regress the integrated bank-evidence registry."""
        text = (ROOT / "docs" / "product-technical-gap-baseline.md").read_text(encoding="utf-8")
        self.assertNotIn("statement-registry candidate is not yet an integrated protected-branch fact", text)
        self.assertNotIn("awaiting the same lawful protected integration", text)
        self.assertIn("Deterministic reconciliation", text)

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
        self.assertIn("already integrated", text)

    def test_standard_traceability_uses_the_real_ci_postgres_minor(self) -> None:
        """Standards traceability must name the exact PostgreSQL minor exercised in CI."""
        workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(
            encoding="utf-8"
        )
        service_image = re.search(r"(?m)^\s+image: postgres:(18\.\d+)@sha256:", workflow)
        self.assertIsNotNone(service_image)
        postgres_minor = service_image.group(1)
        traceability = (
            ROOT / "docs" / "doctoring" / "STANDARD_TRACEABILITY.md"
        ).read_text(encoding="utf-8")
        self.assertIn(
            f"| PostgreSQL {postgres_minor} test environment |",
            traceability,
        )
        self.assertIn(
            f"The real regression environment uses PostgreSQL {postgres_minor}",
            traceability,
        )


if __name__ == "__main__":
    unittest.main()
