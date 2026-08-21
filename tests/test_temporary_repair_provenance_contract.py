"""Regression contracts for temporary repair-workflow provenance boundaries."""

from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
NORMALIZATION_WORKFLOW = ROOT / ".github/workflows/normalize-home-tax-provenance.yml"


class TemporaryRepairProvenanceContractTests(unittest.TestCase):
    """Keep temporary repair machinery from manufacturing exact-head provenance."""

    def test_repair_workflow_does_not_claim_provenance_for_uncommitted_repairs(self) -> None:
        """Only canonical exact-head CI may emit SBOM/provenance for a committed source head."""
        if not NORMALIZATION_WORKFLOW.exists():
            return
        workflow = NORMALIZATION_WORKFLOW.read_text(encoding="utf-8")
        self.assertNotIn("generate_supply_chain_evidence.py", workflow)
        self.assertNotIn("actions/attest@", workflow)
        self.assertIn("pip wheel --no-deps --no-build-isolation", workflow)
        self.assertIn("sha256sum", workflow)

    def test_repair_workflow_uses_the_pr_exact_head_not_a_synthetic_merge_ref(self) -> None:
        """Temporary normalization is observable on the same PR head it is allowed to publish."""
        if not NORMALIZATION_WORKFLOW.exists():
            return
        workflow = NORMALIZATION_WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("pull_request:", workflow)
        self.assertNotIn("\n  push:\n", workflow)
        self.assertIn("github.event.pull_request.head.sha", workflow)
        self.assertNotIn("TRIGGER_SHA: ${{ github.sha }}", workflow)
        self.assertIn(
            "github.event.pull_request.head.repo.full_name == github.repository",
            workflow,
        )

    def test_repair_workflow_removes_temporary_machinery_before_production_coverage(self) -> None:
        """The 100% production gate evaluates the publishable tree, not one-shot helpers."""
        if not NORMALIZATION_WORKFLOW.exists():
            return
        workflow = NORMALIZATION_WORKFLOW.read_text(encoding="utf-8")
        cleanup_marker = "Remove temporary normalization machinery before final validation"
        coverage_marker = "Run complete production branch coverage"
        self.assertIn(cleanup_marker, workflow)
        self.assertIn(coverage_marker, workflow)
        self.assertLess(workflow.index(cleanup_marker), workflow.index(coverage_marker))
        cleanup_section = workflow.split(cleanup_marker, 1)[1].split("- name:", 1)[0]
        self.assertIn("rm .github/workflows/normalize-home-tax-provenance.yml", cleanup_section)
        self.assertIn("rm scripts/repair_home_tax_provenance.py", cleanup_section)
        self.assertIn("rm scripts/repair_append_only_ddl.py", cleanup_section)


if __name__ == "__main__":
    unittest.main()
