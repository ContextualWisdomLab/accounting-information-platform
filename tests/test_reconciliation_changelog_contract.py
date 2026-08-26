"""CHANGELOG contract for the deterministic bank-reconciliation slice."""

from __future__ import annotations

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class ReconciliationChangelogContractTests(unittest.TestCase):
    """Keep the buyer-visible reconciliation behavior recorded under Unreleased."""

    def test_unreleased_records_deterministic_reconciliation_boundary(self) -> None:
        """The slice must record its deterministic, proposal-only authority boundary."""
        changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
        unreleased = changelog.split("## [Unreleased]", maxsplit=1)[1]
        entries = [line for line in unreleased.splitlines() if line.startswith("- ")]
        matching_entries = [
            line
            for line in entries
            if "deterministic bank-reconciliation proposal engine" in line
        ]
        self.assertEqual(
            len(matching_entries),
            1,
            "[Unreleased] must contain one deterministic bank-reconciliation proposal entry",
        )
        entry = matching_entries[0]
        for required_phrase in (
            "exact decimal",
            "explicit abstention",
            "no automatic journal posting",
            "ADR 0054",
        ):
            with self.subTest(required_phrase=required_phrase):
                self.assertIn(required_phrase, entry)


if __name__ == "__main__":
    unittest.main()
