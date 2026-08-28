"""Contracts keeping the product-gap baseline durable instead of stale live evidence."""

from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BASELINE = ROOT / "docs/product-technical-gap-baseline.md"


class ProductGapBaselineContractTests(unittest.TestCase):
    """Keep durable product-gap documentation separate from volatile GitHub state."""

    def test_baseline_does_not_embed_mutable_exact_head_evidence(self) -> None:
        """PR heads, run IDs, and queue state belong in live PR/issue evidence, not this file."""
        text = BASELINE.read_text(encoding="utf-8")

        self.assertRegex(
            text,
            re.compile(
                r"Live\s+PR/check evidence is intentionally not duplicated here",
                re.IGNORECASE,
            ),
        )
        self.assertNotRegex(text, re.compile(r"\b[0-9a-f]{40}\b"))
        self.assertNotRegex(text, re.compile(r"\bworkflow run\s+\d{6,}\b", re.IGNORECASE))
        self.assertNotIn("checks are currently queued", text.lower())
        self.assertNotIn("production candidate is exact commit", text.lower())
        self.assertNotRegex(text, re.compile(r"(?<!\w)#\d+\b"))

    def test_postgres_reference_is_consistent_across_bibliographies(self) -> None:
        """The README and doctoring bibliography must cite the same release notes."""
        expected = "PostgreSQL 18.6 release notes"
        expected_url = "https://www.postgresql.org/docs/release/18.6/"
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        references = (ROOT / "docs" / "doctoring" / "REFERENCES.md").read_text(encoding="utf-8")
        self.assertIn(expected, readme)
        self.assertIn(expected_url, readme)
        self.assertIn(expected, references)
        self.assertIn(expected_url, references)


if __name__ == "__main__":
    unittest.main()
