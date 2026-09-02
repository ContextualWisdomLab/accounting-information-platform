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

    def test_baseline_keeps_branch_governance_as_durable_policy(self) -> None:
        """Mutable ruleset/workflow state must be queried live, not fossilized in the baseline."""
        text = BASELINE.read_text(encoding="utf-8")
        self.assertIn(
            "Both integration and release branches require ordinary branch/ruleset protection",
            text,
        )
        self.assertIn(
            "must be queried rather than copied into this durable baseline",
            text,
        )
        self.assertIn(
            "Neither branch policy nor a passing predecessor head authorizes a protection bypass",
            text,
        )
        volatile_claim_patterns = (
            r"`main`\s+(?:is|has)\s+[^\n]{0,120}\b(?:active|applied|protected)\b",
            r"\bcentral\s+required\s+workflows?\b[^\n]{0,120}\b(?:is|are|remain)\b[^\n]{0,80}\b(?:active|applied)\b",
            r"\bactive\s+(?:repository|organization)?-?scoped\s+(?:gate|ruleset)\b",
        )
        for pattern in volatile_claim_patterns:
            with self.subTest(pattern=pattern):
                self.assertNotRegex(text, re.compile(pattern, re.IGNORECASE))

    def test_database_isolation_reference_names_one_exact_publication(self) -> None:
        """The PVLDB citation must not be mixed with the separate extended-version title."""
        text = BASELINE.read_text(encoding="utf-8")
        self.assertIn(
            "Fast verification of strong database isolation. *Proceedings of the VLDB Endowment, 19*(4), 563–575.",
            text,
        )
        self.assertIn("https://doi.org/10.14778/3785297.3785300", text)
        self.assertNotIn("Fast verification of strong database isolation (Extended Version)", text)

    def test_baseline_requires_reconciliation_permissions_before_routes(self) -> None:
        """The durable gap must require both lifecycle permissions before buyer mutation routes."""
        text = BASELINE.read_text(encoding="utf-8")
        required = (
            "add explicit `complete_reconciliation` → `accounting.complete_reconciliation` "
            "and exception-resolution operation/permission mapping before exposing "
            "buyer-facing lifecycle routes"
        )
        self.assertIn(required, text)
        self.assertNotIn(
            "preserve the explicit `complete_reconciliation` → `accounting.complete_reconciliation` permission",
            text,
        )

    def test_changelog_does_not_claim_an_unpublished_tagged_release(self) -> None:
        """Changelog history must not claim a tag or release absent from GitHub."""
        changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
        self.assertNotIn("First tagged release", changelog)
        self.assertIn("not a published release or tag", changelog)

    def test_readme_describes_the_integrated_reconciliation_foundation(self) -> None:
        """The buyer-facing README must describe integrated reconciliation without leaking migration internals."""
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("immutable camt.053.001.14 bank-statement evidence registry", readme)
        self.assertIn("deterministic reconciliation proposal engine", readme)
        self.assertIn("exact book-to-bank bridge", readme)
        self.assertIn("durable reconciliation runs, exceptions, and evidence", readme)
        self.assertRegex(readme, re.compile(r"full\s+cross-run\s+many-to-many\s+allocation"))
        self.assertNotIn("0014_reconciliation_candidate_allocation.sql", readme)
        self.assertNotIn("bank-statement ingestion and reconciliation", readme)
        self.assertNotIn("deepwiki.com", readme.lower())


if __name__ == "__main__":
    unittest.main()