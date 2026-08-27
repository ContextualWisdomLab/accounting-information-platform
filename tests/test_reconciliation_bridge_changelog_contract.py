"""CHANGELOG contract for the exact book-to-bank reconciliation bridge slice."""

from __future__ import annotations

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


def _unreleased_entries(changelog: str) -> list[str]:
    """Return bullet entries from only the current ``[Unreleased]`` section."""
    unreleased = changelog.split("## [Unreleased]", maxsplit=1)[1]
    unreleased = unreleased.split("\n## ", maxsplit=1)[0]
    return [line for line in unreleased.splitlines() if line.startswith("- ")]


class ReconciliationBridgeChangelogContractTests(unittest.TestCase):
    """Keep the buyer-visible book-to-bank close control in release history."""

    def test_unreleased_records_exact_book_to_bank_bridge_boundary(self) -> None:
        """The bridge must record exact equations, provenance, and no-posting authority."""
        changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
        entries = _unreleased_entries(changelog)
        matching_entries = [
            line
            for line in entries
            if "exact book-to-bank reconciliation bridge" in line
        ]
        self.assertEqual(
            len(matching_entries),
            1,
            "[Unreleased] must contain one exact book-to-bank reconciliation bridge entry",
        )
        entry = matching_entries[0]
        for required_phrase in (
            "exact Decimal",
            "one minor unit",
            "statement-population",
            "book-population",
            "no automatic journal posting",
            "ADR 0054",
        ):
            with self.subTest(required_phrase=required_phrase):
                self.assertIn(required_phrase, entry)

    def test_unreleased_records_finite_decimal_bridge_validation(self) -> None:
        """The current release notes must preserve the bridge monetary-domain hardening."""
        changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
        entries = _unreleased_entries(changelog)
        matching_entries = [
            line
            for line in entries
            if "book-to-bank bridge monetary inputs" in line
        ]
        self.assertEqual(
            len(matching_entries),
            1,
            "[Unreleased] must contain one book-to-bank bridge monetary-input validation entry",
        )
        entry = matching_entries[0]
        for required_phrase in (
            "finite `Decimal`",
            "binary float",
            "NaN",
            "infinities",
            "signed",
            "ADR 0054",
        ):
            with self.subTest(required_phrase=required_phrase):
                self.assertIn(required_phrase, entry)

    def test_released_history_does_not_count_as_unreleased(self) -> None:
        """A historical bridge entry must not satisfy the live Unreleased contract."""
        changelog = """# Changelog

## [Unreleased]

- Added another accounting change.

## [0.1.0] - 2026-08-26

- Added the exact book-to-bank reconciliation bridge with exact Decimal arithmetic, one minor unit fail-closed handling, statement-population and book-population provenance, no automatic journal posting, and ADR 0054.
"""
        entries = _unreleased_entries(changelog)
        matching_entries = [
            line
            for line in entries
            if "exact book-to-bank reconciliation bridge" in line
        ]
        self.assertEqual(len(matching_entries), 0)


if __name__ == "__main__":
    unittest.main()
