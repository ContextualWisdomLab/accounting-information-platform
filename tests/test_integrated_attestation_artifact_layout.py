"""Regression contracts for integrated-head package-evidence attestation repair."""

from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class IntegratedAttestationArtifactLayoutTests(unittest.TestCase):
    """Keep downloaded evidence and its release-history record reviewable."""

    def test_package_evidence_keeps_workspace_root_anchor(self) -> None:
        """The upload must keep a root member so the retained dist prefix is stable."""
        workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(
            encoding="utf-8"
        )
        foundation_job = workflow.split("  accounting-foundation:", 1)[1].split(
            "  integrated-attestations:", 1
        )[0]
        upload_step = foundation_job.split(
            "      - name: Upload package evidence", 1
        )[1]

        # actions/upload-artifact preserves paths from the least common ancestor.
        # coverage.json intentionally anchors that ancestor at the workspace root;
        # removing it would flatten dist/* and invalidate the integrated download.
        for required_member in (
            "            dist/*.whl\n",
            "            dist/SHA256SUMS\n",
            "            dist/sbom.spdx.json\n",
            "            dist/source-provenance.json\n",
            "            coverage.json\n",
        ):
            self.assertIn(required_member, upload_step)
        self.assertLess(upload_step.index("dist/*.whl"), upload_step.index("coverage.json"))

    def test_download_preserves_uploaded_dist_prefix(self) -> None:
        """Artifact extraction must not add a second dist directory before verification."""
        workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(
            encoding="utf-8"
        )
        attestation_job = workflow.split("  integrated-attestations:", 1)[1]
        download_step = attestation_job.split(
            "      - name: Download exact-head package evidence", 1
        )[1].split("      - name: Verify integrated-head evidence identity", 1)[0]

        self.assertIn("          path: .\n", download_step)
        self.assertNotIn("          path: dist\n", download_step)
        self.assertIn("(cd dist && sha256sum -c SHA256SUMS)", attestation_job)
        self.assertIn(
            'Path("dist/source-provenance.json")',
            attestation_job,
        )
        self.assertIn(
            "subject-path: ${{ github.workspace }}/dist/*.whl",
            attestation_job,
        )
        self.assertIn(
            "sbom-path: ${{ github.workspace }}/dist/sbom.spdx.json",
            attestation_job,
        )

    def test_integrated_attestation_repair_is_recorded_in_changelog(self) -> None:
        """The exact repair entry must retain its operational verification details."""
        changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
        repair_entry = next(
            (
                line
                for line in changelog.splitlines()
                if line.startswith("- `Integrated-head attestations` ")
            ),
            "",
        )

        self.assertTrue(repair_entry, "missing Integrated-head attestations changelog entry")
        for required_detail in (
            "workspace root",
            "`dist/`",
            "SHA256",
            "source-provenance",
            "SPDX SBOM",
        ):
            self.assertIn(required_detail, repair_entry)


if __name__ == "__main__":
    unittest.main()
