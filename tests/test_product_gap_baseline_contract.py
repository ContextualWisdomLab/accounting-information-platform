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


if __name__ == "__main__":
    unittest.main()
