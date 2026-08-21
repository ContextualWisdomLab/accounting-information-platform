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


if __name__ == "__main__":
    unittest.main()
