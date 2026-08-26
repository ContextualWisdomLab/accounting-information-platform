"""CHANGELOG contract for the deterministic bank-reconciliation slice."""

from __future__ import annotations

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


def _unreleased_entries(changelog: str) -> list[str]:
    """Return bullet entries from only the current ``[Unreleased]`` section."""
    unreleased = changelog.split("## [Unreleased]", maxsplit=1)[1]
    unreleased = unreleased.split("\n## ", maxsplit=1)[0]
    return [line for line in unreleased.splitlines() if line.startswith("- ")]


class ReconciliationChangelogContractTests(unittest.TestCase):
    """Keep the buyer-visible reconciliation behavior recorded under Unreleased."""

    def test_unreleased_records_deterministic_reconciliation_boundary(self) -> None:
        """The slice must record its deterministic, proposal-only authority boundary."""
        changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
        entries = _unreleased_entries(changelog)
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

    def test_released_history_does_not_count_as_unreleased(self) -> None:
        """A historical duplicate must not make the live Unreleased contract look duplicated."""
        changelog = """# Changelog

## [Unreleased]

- Added the deterministic bank-reconciliation proposal engine with exact decimal, explicit abstention, no automatic journal posting, and ADR 0054.

## [0.1.0] - 2026-08-26

- Added the deterministic bank-reconciliation proposal engine in historical release notes.
"""
        entries = _unreleased_entries(changelog)
        matching_entries = [
            line
            for line in entries
            if "deterministic bank-reconciliation proposal engine" in line
        ]
        self.assertEqual(len(matching_entries), 1)


if __name__ == "__main__":
    unittest.main()
