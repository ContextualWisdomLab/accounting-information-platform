"""Machine-readable coverage evidence contracts for exact-head CI."""

from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"


class AccountingCoverageEvidenceContractTests(unittest.TestCase):
    """Keep exact-head coverage proof non-vacuous and machine-consumable."""

    def test_ci_publishes_non_vacuous_statement_and_branch_denominators(self) -> None:
        """Coverage must expose exact positive line and branch denominators to reviewers."""
        workflow = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("python -m coverage json -o coverage.json", workflow)
        self.assertIn('totals["num_statements"]', workflow)
        self.assertIn('totals["covered_lines"]', workflow)
        self.assertIn('totals["num_branches"]', workflow)
        self.assertIn('totals["covered_branches"]', workflow)
        self.assertIn("coverage lines:", workflow)
        self.assertIn("branches:", workflow)
        self.assertIn("covered_lines:", workflow)
        self.assertIn("covered_branches:", workflow)

    def test_ci_binds_coverage_json_to_exact_head_package_evidence(self) -> None:
        """The SHA-named evidence artifact must retain the machine coverage document."""
        workflow = WORKFLOW.read_text(encoding="utf-8")
        upload_section = workflow.split("- name: Upload package evidence", 1)[1]
        self.assertIn("coverage.json", upload_section)


if __name__ == "__main__":
    unittest.main()
