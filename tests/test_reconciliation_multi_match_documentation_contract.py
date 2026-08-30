"""Repository contracts for code-current reconciliation multi-match documentation."""

from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ADR = ROOT / "docs/adr/0054-deterministic-bank-reconciliation-proposals.md"
BASELINE = ROOT / "docs/product-technical-gap-baseline.md"
CHANGELOG = ROOT / "CHANGELOG.md"


class ReconciliationMultiMatchDocumentationContractTests(unittest.TestCase):
    """Keep public accounting-control docs aligned with migration 0015 invariants."""

    def test_adr_describes_current_multi_match_persistence_contract(self) -> None:
        """ADR 0054 must not describe removed single-approval or future-persistence limits."""
        text = ADR.read_text(encoding="utf-8")
        self.assertNotIn("at most one approved match per run", text)
        self.assertNotIn("multi-match persistence, explicit approval/exception records", text)
        self.assertIn("Migration 0015", text)
        self.assertIn("multiple", text)
        self.assertIn("approved", text)
        self.assertIn("active reconciliation runs", text)
        self.assertIn("Migration 0016 now adds", text)
        self.assertIn("durable reconciliation approval evidence", text)
        self.assertIn("non-empty", text)
        self.assertIn("statement", text)
        self.assertIn("journal", text)
        self.assertIn("equal", text)
        self.assertIn("superseded", text)
        self.assertIn("append-only", text)
        self.assertNotIn(
            "Remaining bounded work includes explicit durable approval evidence/state transitions",
            text,
        )

    def test_product_baseline_distinguishes_current_tree_from_remaining_close_package(self) -> None:
        """The product gap queue must not call delivered 0015-0017 controls future work."""
        text = BASELINE.read_text(encoding="utf-8")
        self.assertIn("from migrations `0015` through `0017`", text)
        self.assertIn("close-package provenance remains open", text)
        self.assertIn("[delivered in current tree; migration 0015]", text)
        self.assertNotRegex(
            text.lower(),
            r"candidate/match\s+allocation\s+conservation\s+\[open\s+m2\s+slice\]",
        )

    def test_unreleased_changelog_records_balanced_approval_invariant(self) -> None:
        """The current change record names both non-empty sides and exact total equality."""
        text = CHANGELOG.read_text(encoding="utf-8")
        unreleased = re.search(
            r"(?ms)^## \[Unreleased\]\s*(.*?)(?=^## )",
            text,
        )
        self.assertIsNotNone(unreleased)
        current = unreleased.group(1)
        entry = next(
            (
                line
                for line in current.splitlines()
                if line.startswith("- Added durable reconciliation candidate, match, and allocation rows")
            ),
            "",
        )
        self.assertTrue(entry, "keep the migration 0015 reconciliation entry in [Unreleased]")
        for phrase in (
            "non-empty statement",
            "non-empty journal",
            "exactly equal",
            "source pairings that an immutable candidate proposed",
            "approved",
        ):
            self.assertIn(phrase, entry)


if __name__ == "__main__":
    unittest.main()
