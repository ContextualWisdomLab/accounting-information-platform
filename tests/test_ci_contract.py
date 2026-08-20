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


if __name__ == "__main__":
    unittest.main()
