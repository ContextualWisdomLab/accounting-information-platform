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

    def test_ci_retains_diagnostics_before_enforcing_exact_coverage(self) -> None:
        """A failing 100% gate must still publish the exact missing-line evidence."""
        workflow = WORKFLOW.read_text(encoding="utf-8")
        foundation_job = workflow.split("  accounting-foundation:", 1)[1].split(
            "  integrated-attestations:", 1
        )[0]
        diagnostics_marker = "      - name: Publish coverage diagnostics"
        upload_marker = "      - name: Upload coverage diagnostics"
        enforcement_marker = "      - name: Enforce complete coverage and publish denominator evidence"

        self.assertIn(diagnostics_marker, foundation_job)
        self.assertIn(upload_marker, foundation_job)
        self.assertIn(enforcement_marker, foundation_job)
        self.assertLess(foundation_job.index(diagnostics_marker), foundation_job.index(upload_marker))
        self.assertLess(foundation_job.index(upload_marker), foundation_job.index(enforcement_marker))

        diagnostics_section = foundation_job.split(diagnostics_marker, 1)[1].split(
            upload_marker, 1
        )[0]
        upload_section = foundation_job.split(upload_marker, 1)[1].split(
            enforcement_marker, 1
        )[0]
        enforcement_section = foundation_job.split(enforcement_marker, 1)[1]

        self.assertIn(
            "python -m coverage report --fail-under=0 --show-missing | tee coverage.txt",
            diagnostics_section,
        )
        self.assertIn("python -m coverage json -o coverage.json", diagnostics_section)
        self.assertIn("python -m coverage xml -o coverage.xml", diagnostics_section)
        self.assertIn("        if: always()\n", upload_section)
        self.assertIn(
            "          name: accounting-coverage-${{ github.event.pull_request.head.sha || github.sha }}\n",
            upload_section,
        )
        self.assertIn("            coverage.txt\n", upload_section)
        self.assertIn("            coverage.json\n", upload_section)
        self.assertIn("            coverage.xml\n", upload_section)
        self.assertIn(
            "python -m coverage report --fail-under=100 --show-missing",
            enforcement_section,
        )

    def test_ci_binds_coverage_json_to_exact_head_package_evidence(self) -> None:
        """The SHA-named evidence artifact must retain the machine coverage document."""
        workflow = WORKFLOW.read_text(encoding="utf-8")
        foundation_job = workflow.split("  accounting-foundation:", 1)[1].split(
            "  integrated-attestations:", 1
        )[0]
        staging_section = foundation_job.split(
            "      - name: Stage package evidence with explicit artifact root", 1
        )[1].split("      - name: Upload package evidence", 1)[0]
        upload_section = foundation_job.split(
            "      - name: Upload package evidence", 1
        )[1]

        self.assertIn(
            'cp "coverage.json" "$evidence_root/coverage.json"',
            staging_section,
        )
        self.assertIn(
            "          path: ${{ runner.temp }}/accounting-package-evidence/\n",
            upload_section,
        )


if __name__ == "__main__":
    unittest.main()
