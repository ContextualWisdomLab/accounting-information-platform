"""Documentation contracts for current accounting operability evidence."""

from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class OperabilityDocumentationContractTests(unittest.TestCase):
    """Keep release-operability guidance aligned with verified foundation behavior."""

    def test_reversal_operability_status_matches_current_foundation(self) -> None:
        """Operability must not call an already-proven reversal contract pending."""
        operability = (ROOT / "docs" / "OPERABILITY.md").read_text(encoding="utf-8")

        self.assertNotIn(
            "Treat PR #2 as non-release-ready until that contract passes",
            operability,
        )
        self.assertIn(
            "Current PostgreSQL integration tests prove exact reversal replay",
            operability,
        )


if __name__ == "__main__":
    unittest.main()
