"""Runtime contracts for protected-head supply-chain attestations."""

from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class IntegratedAttestationRuntimeContractTests(unittest.TestCase):
    """Keep push-only attestation verification executable on the declared runner."""

    def test_integrated_attestation_uses_explicit_python3_runtime(self) -> None:
        """The job must not depend on an unprovisioned `python` command alias."""
        workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(
            encoding="utf-8"
        )
        attestation_job = workflow.split("  integrated-attestations:", 1)[1]

        self.assertIn("python3 - <<'PY'", attestation_job)
        self.assertNotIn("\n          python - <<'PY'", attestation_job)


if __name__ == "__main__":
    unittest.main()
