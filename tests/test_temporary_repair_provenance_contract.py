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


if __name__ == "__main__":
    unittest.main()
