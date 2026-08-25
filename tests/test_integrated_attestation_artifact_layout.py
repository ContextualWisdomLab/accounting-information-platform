"""Regression contract for integrated-head package-evidence download layout."""

from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class IntegratedAttestationArtifactLayoutTests(unittest.TestCase):
    """Keep downloaded evidence at the paths consumed by attestation verification."""

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


if __name__ == "__main__":
    unittest.main()
