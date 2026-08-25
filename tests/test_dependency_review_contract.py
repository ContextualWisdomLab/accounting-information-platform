"""Contracts for repository-owned dependency-difference security evidence."""

from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"
OSV_SCANNER_IMAGE = (
    "ghcr.io/google/osv-scanner@"
    "sha256:5116601dedc01c1c580eb92371883ec052fc4c13c3fbc109d621a63ac416d475"
)


class DependencyReviewContractTests(unittest.TestCase):
    """Keep the dependency-diff gate exact-head, live-base, and fail-closed."""

    def test_pull_requests_run_repository_owned_dependency_diff_gate(self) -> None:
        """A PR must execute an explicit dependency-diff job, not inherit aggregate status."""
        workflow = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("exact-head-dependency-diff:", workflow)
        job = workflow.split("  exact-head-dependency-diff:", 1)[1].split(
            "  accounting-foundation:", 1
        )[0]
        self.assertIn("name: Exact-head dependency diff", job)
        self.assertIn("if: github.event_name == 'pull_request'", job)
        self.assertIn("contents: read", job)
        self.assertNotIn("contents: write", job)
        self.assertNotIn("id-token: write", job)
        self.assertNotIn("attestations: write", job)

    def test_dependency_diff_resolves_live_base_and_verifies_exact_head(self) -> None:
        """Evidence must bind an independently fetched live base tip to the immutable PR head."""
        workflow = WORKFLOW.read_text(encoding="utf-8")
        job = workflow.split("  exact-head-dependency-diff:", 1)[1].split(
            "  accounting-foundation:", 1
        )[0]
        self.assertIn("BASE_REF: ${{ github.event.pull_request.base.ref }}", job)
        self.assertIn("HEAD_SHA: ${{ github.event.pull_request.head.sha }}", job)
        self.assertIn("fetch-depth: 0", job)
        self.assertIn("ref: ${{ github.event.pull_request.head.sha }}", job)
        self.assertIn('test "$(git rev-parse HEAD)" = "$HEAD_SHA"', job)
        self.assertIn(
            'git fetch --no-tags origin "refs/heads/${BASE_REF}:refs/remotes/origin/${BASE_REF}"',
            job,
        )
        self.assertIn(
            'LIVE_BASE_SHA="$(git rev-parse "refs/remotes/origin/${BASE_REF}")"',
            job,
        )
        self.assertIn('git merge-base --is-ancestor "$LIVE_BASE_SHA" "$HEAD_SHA"', job)
        self.assertIn("requirements-quality.txt", job)
        self.assertIn("pyproject.toml", job)

    def test_dependency_diff_uses_pinned_osv_and_fails_closed(self) -> None:
        """Known-vulnerability or scanner failures must fail the exact-head dependency gate."""
        workflow = WORKFLOW.read_text(encoding="utf-8")
        job = workflow.split("  exact-head-dependency-diff:", 1)[1].split(
            "  accounting-foundation:", 1
        )[0]
        self.assertIn(OSV_SCANNER_IMAGE, job)
        self.assertIn(
            "--lockfile=requirements.txt:requirements-quality.txt",
            job,
        )
        self.assertIn("--format=json", job)
        self.assertIn("osv-head.json", job)
        self.assertNotIn("continue-on-error", job)
        self.assertNotIn("|| true", job)
        self.assertIn("dependency-diff-evidence.txt", job)
        self.assertIn("osv-head.json", job)
        self.assertIn(
            "actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a",
            job,
        )
        self.assertIn("if-no-files-found: error", job)


if __name__ == "__main__":
    unittest.main()
