"""Repository contracts for code-current reversal release documentation."""

from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ARCHITECTURE = ROOT / "docs/ARCHITECTURE.md"
REVERSAL_ADR = ROOT / "docs/adr/0012-http-append-only-reversal.md"


class ReversalDocumentationContractTests(unittest.TestCase):
    """Keep reversal documentation aligned with the integrated posting foundation."""

    def test_canonical_docs_do_not_describe_merged_pr2_as_non_release_ready(self) -> None:
        """Merged foundation work must not remain described as an open PR #2 gate."""
        architecture = ARCHITECTURE.read_text(encoding="utf-8")
        reversal_adr = REVERSAL_ADR.read_text(encoding="utf-8")

        stale_markers = (
            "PR #2 remains non-release-ready",
            "before PR #2 can leave its non-release-ready state",
        )
        for marker in stale_markers:
            self.assertNotIn(marker, architecture)
            self.assertNotIn(marker, reversal_adr)

    def test_canonical_docs_retain_durable_reversal_release_contract(self) -> None:
        """Removing stale PR state must not erase the durable reversal evidence boundary."""
        architecture = ARCHITECTURE.read_text(encoding="utf-8")
        reversal_adr = REVERSAL_ADR.read_text(encoding="utf-8")

        for text in (architecture, reversal_adr):
            self.assertIn("reversal", text.lower())
            self.assertIn("immutable", text.lower())
            self.assertIn("idempotency", text.lower())

        self.assertIn("PostgreSQL", architecture)
        self.assertIn("PostgreSQL", reversal_adr)


if __name__ == "__main__":
    unittest.main()
