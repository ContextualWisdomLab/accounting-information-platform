"""Least-privilege GitHub Actions contracts for accounting package evidence."""

from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class AccountingCiLeastPrivilegeContractTests(unittest.TestCase):
    """Keep signing privileges out of pull-request-executed build code."""

    def test_attestation_write_permissions_exist_only_in_push_only_job(self) -> None:
        """PR build/test code must not receive OIDC or attestation write authority."""
        workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn("  integrated-attestations:", workflow)
        foundation_job = workflow.split("  accounting-foundation:", 1)[1].split(
            "  integrated-attestations:", 1
        )[0]
        for permission in (
            "id-token: write",
            "attestations: write",
            "artifact-metadata: write",
        ):
            self.assertNotIn(permission, foundation_job)

        attestation_job = workflow.split("  integrated-attestations:", 1)[1]
        self.assertIn("if: github.event_name == 'push'", attestation_job)
        self.assertIn("needs: accounting-foundation", attestation_job)
        self.assertIn("contents: read", attestation_job)
        self.assertIn("id-token: write", attestation_job)
        self.assertIn("attestations: write", attestation_job)
        self.assertIn("artifact-metadata: write", attestation_job)
        self.assertIn("actions/download-artifact@", attestation_job)
        self.assertEqual(
            attestation_job.count(
                "actions/attest@508db95dd578ae2727ebd6217d5ba78e4fbda05d"
            ),
            2,
        )


if __name__ == "__main__":
    unittest.main()
