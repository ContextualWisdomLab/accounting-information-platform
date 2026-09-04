"""Release-note contracts for reconciliation accounting-control migrations."""

from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class ReconciliationAuthorityChangelogContractTests(unittest.TestCase):
    """Keep unreleased reconciliation authority changes visible to operators."""

    def test_unreleased_changelog_names_complete_reconciliation_authority_chain(self) -> None:
        """Migrations 0021 through 0025 must be release-visible in dependency order."""
        changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
        unreleased = changelog.split("## [Unreleased]", 1)[1].split("## [0.1.0]", 1)[0]
        migrations = (
            "0021_reconciliation_exception_resolution_outbox_pair.sql",
            "0022_reconciliation_authority_outbox_retention.sql",
            "0023_reconciliation_authority_outbox_orphan_guard.sql",
            "0024_reconciliation_control_recording_time_authority.sql",
            "0025_reconciliation_lifecycle_recording_time_authority.sql",
        )
        positions = []
        for migration in migrations:
            with self.subTest(migration=migration):
                self.assertIn(migration, unreleased)
                positions.append(unreleased.index(migration))
        self.assertEqual(positions, sorted(positions))

    def test_unreleased_changelog_preserves_control_meaning(self) -> None:
        """Release notes must retain the buyer-relevant authority semantics, not filenames only."""
        changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
        unreleased = changelog.split("## [Unreleased]", 1)[1].split("## [0.1.0]", 1)[0]
        for phrase in (
            "command/status/outbox",
            "exactly one matching reconciliation authority outbox event",
            "reserved reconciliation authority event types",
            "legacy_unverified",
            "database_clock",
            "reconciliation_lifecycle_legacy_recording_time_preflight",
            "effective_at",
            "recorded_at",
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, unreleased)


if __name__ == "__main__":
    unittest.main()
