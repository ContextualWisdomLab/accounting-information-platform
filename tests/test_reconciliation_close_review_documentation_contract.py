"""Repository contracts for reconciliation close-review authority documentation."""

from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class ReconciliationCloseReviewDocumentationContractTests(unittest.TestCase):
    """Keep the buyer-facing reconciliation read model aligned with its authority docs."""

    def test_changelog_records_exact_read_only_close_review_surface(self) -> None:
        """The unreleased changelog names exact values, exports, and non-authority."""
        changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
        unreleased = changelog.split("## [0.1.0]", 1)[0]
        self.assertIn("reconciliation close-review projection", unreleased)
        self.assertIn("decimal strings", unreleased)
        self.assertIn("suitable_for_period_close_review", unreleased)
        self.assertIn("cannot approve reconciliation", unreleased)
        self.assertIn("post a journal", unreleased)

    def test_adr_separates_evidence_eligibility_from_approval_and_posting(self) -> None:
        """ADR 0054 keeps the projection read-only and fail-closed."""
        adr = (
            ROOT / "docs" / "adr" / "0054-deterministic-bank-reconciliation-proposals.md"
        ).read_text(encoding="utf-8")
        self.assertIn("### Buyer close-review projection", adr)
        self.assertIn("Suitable for period-close review", adr)
        self.assertIn("not a reconciliation approval", adr)
        self.assertIn("decimal strings", adr)
        self.assertIn("customer-facing next action", adr)

    def test_standard_traceability_names_close_review_as_internal_ais_control(self) -> None:
        """ISO 20022 traceability does not claim that the close-review logic is ISO-defined."""
        traceability = (
            ROOT / "docs" / "doctoring" / "STANDARD_TRACEABILITY.md"
        ).read_text(encoding="utf-8")
        self.assertIn("buyer close-review projection", traceability)
        self.assertIn("read-only AIS presentation", traceability)
        self.assertIn("evidence eligibility only", traceability)
        self.assertIn("close-review projection and exact-value export regressions", traceability)


if __name__ == "__main__":
    unittest.main()
