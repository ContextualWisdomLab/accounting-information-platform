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

    def test_ci_runs_pinned_semgrep_on_the_verified_exact_head(self) -> None:
        """Repository-owned SAST must scan the exact PR head, not a synthetic merge ref."""
        workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn("exact-head-sast:", workflow)
        self.assertIn("name: Exact-head SAST", workflow)
        self.assertIn(
            "semgrep/semgrep@sha256:2b33f46ba66cf8cc2ad59ccfa7d22951fd00c632c38f1339e84ec8e6e641a942",
            workflow,
        )
        sast_job = workflow.split("  exact-head-sast:", 1)[1].split(
            "  exact-head-security:", 1
        )[0]
        self.assertIn(
            "ref: ${{ github.event.pull_request.head.sha || github.sha }}",
            sast_job,
        )
        self.assertIn(
            "EXPECTED_SHA: ${{ github.event.pull_request.head.sha || github.sha }}",
            sast_job,
        )
        self.assertIn('test "$(git rev-parse HEAD)" = "$EXPECTED_SHA"', sast_job)
        self.assertIn("--config=p/default", sast_job)
        self.assertIn("--severity=WARNING", sast_job)
        self.assertIn("--severity=ERROR", sast_job)
        self.assertIn("--error", sast_job)
        self.assertIn("--metrics=off", sast_job)

    def test_ci_runs_non_vacuous_trivy_secret_gate_on_the_verified_exact_head(self) -> None:
        """Trivy must prove its applicable secret-scan boundary without unsupported-scan claims."""
        workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn("exact-head-security:", workflow)
        self.assertIn("name: Exact-head security", workflow)
        security_job = workflow.split("  exact-head-security:", 1)[1].split(
            "  exact-head-dependency-diff:", 1
        )[0]
        self.assertIn(
            "ref: ${{ github.event.pull_request.head.sha || github.sha }}",
            security_job,
        )
        self.assertIn(
            "EXPECTED_SHA: ${{ github.event.pull_request.head.sha || github.sha }}",
            security_job,
        )
        self.assertIn('test "$(git rev-parse HEAD)" = "$EXPECTED_SHA"', security_job)
        self.assertIn(
            "aquasecurity/trivy-action@a9c7b0f06e461e9d4b4d1711f154ee024b8d7ab8",
            security_job,
        )
        self.assertIn("version: v0.70.0", security_job)
        self.assertIn("scanners: secret", security_job)
        self.assertNotIn("scanners: vuln,secret,misconfig", security_job)
        self.assertIn("exit-code: '1'", security_job)
        self.assertIn(
            "Prove Trivy secret scanner detects a deterministic canary",
            security_job,
        )
        self.assertIn("aws-access-key-id", security_job)
        self.assertIn("canary_status", security_job)
        self.assertIn('test "$canary_status" -eq 1', security_job)
        self.assertIn("Exact-head dependency diff", workflow)
        self.assertIn("Scan exact-head locked Python dependencies with OSV", workflow)

    def test_ci_reproducible_timestamp_uses_exact_head_and_fails_closed(self) -> None:
        """The verified exact head supplies a mandatory non-empty SOURCE_DATE_EPOCH."""
        workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn(
            'source_date_epoch="$(git show -s --format=%ct "$EXPECTED_SHA")"',
            workflow,
        )
        self.assertIn('test -n "$source_date_epoch"', workflow)
        self.assertIn("printf 'SOURCE_DATE_EPOCH=%s\\n'", workflow)
        self.assertNotIn('\\"$EXPECTED_SHA\\"', workflow)

    def test_signed_attestations_run_only_for_integrated_branch_pushes(self) -> None:
        """PR jobs cannot receive or exercise signed-attestation authority."""
        workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(
            encoding="utf-8"
        )
        foundation_job, attestation_job = workflow.split(
            "  integrated-attestations:", 1
        )
        self.assertNotIn(
            "actions/attest@508db95dd578ae2727ebd6217d5ba78e4fbda05d",
            foundation_job,
        )
        self.assertIn("if: github.event_name == 'push'", attestation_job)
        self.assertIn("needs: accounting-foundation", attestation_job)
        self.assertEqual(
            attestation_job.count(
                "actions/attest@508db95dd578ae2727ebd6217d5ba78e4fbda05d"
            ),
            2,
        )

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
        self.assertIn("dist/source-provenance.json", workflow)
        self.assertIn(
            "actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a",
            workflow,
        )
        self.assertIn(
            "actions/download-artifact@3e5f45b2cfb9172054b4087a40e8e0b5a5461e7c",
            workflow,
        )


if __name__ == "__main__":
    unittest.main()
