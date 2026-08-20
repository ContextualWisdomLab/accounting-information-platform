"""GitHub Actions trigger contracts for the accounting foundation."""

from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class AccountingCiContractTests(unittest.TestCase):
    """Keep exact-head PR and protected-branch acceptance evidence active."""

    def test_ci_runs_after_default_develop_and_release_main_pushes(self) -> None:
        """Integrated develop and release main revisions both receive foundation CI."""
        workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(
            encoding="utf-8"
        )
        self.assertRegex(
            workflow,
            re.compile(
                r"(?m)^  push:\n    branches:\n      - develop\n      - main$"
            ),
        )

    def test_ci_checks_out_exact_pull_request_head_or_push_sha(self) -> None:
        """PR evidence must execute the immutable PR head, never GitHub's merge ref."""
        workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn(
            "ref: ${{ github.event.pull_request.head.sha || github.sha }}",
            workflow,
        )
        self.assertIn(
            "EXPECTED_SHA: ${{ github.event.pull_request.head.sha || github.sha }}",
            workflow,
        )
        self.assertIn('test "$(git rev-parse HEAD)" = "$EXPECTED_SHA"', workflow)

    def test_ci_requires_reproducible_wheel_sbom_and_signed_attestations(self) -> None:
        """Package acceptance must bind reproducibility, SPDX SBOM, and provenance to the head."""
        workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(
            encoding="utf-8"
        )
        for permission in (
            "id-token: write",
            "attestations: write",
            "artifact-metadata: write",
        ):
            self.assertIn(permission, workflow)
        attest_action = "actions/attest@508db95dd578ae2727ebd6217d5ba78e4fbda05d"
        self.assertEqual(workflow.count(attest_action), 2)
        self.assertIn("Build reproducible wheel and evidence", workflow)
        self.assertIn("SOURCE_DATE_EPOCH", workflow)
        self.assertIn("generate_supply_chain_evidence.py", workflow)
        self.assertIn("(cd dist && sha256sum -c SHA256SUMS)", workflow)
        self.assertIn("sbom-path: ${{ github.workspace }}/dist/sbom.spdx.json", workflow)
        self.assertIn(
            "actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a",
            workflow,
        )


if __name__ == "__main__":
    unittest.main()
