"""Repository contract for the supported PostgreSQL security baseline."""

from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CI_WORKFLOW = ROOT / ".github/workflows/ci.yml"
RUNTIME_BASELINE = ROOT / "docs/doctoring/POSTGRESQL_RUNTIME_BASELINE.md"
POSTGRES_IMAGE = (
    "postgres:18.6@sha256:4ef4dbc939d61acea57712655ddb4b4ab27419c913f94cca0cd57cb3ea3c2280"
)
POSTGRES_DIGEST = POSTGRES_IMAGE.partition("@")[2]


def _postgres_service_image(workflow: str) -> str:
    """Return the direct image field from the Accounting Foundation PostgreSQL service."""
    service_match = re.search(
        r"(?ms)^      postgres:\n(?P<body>(?:(?:^ {8,}\S.*|^\s*)\n?)*)",
        workflow,
    )
    if service_match is None:
        return ""
    image_match = re.search(
        r"(?m)^        image: (?P<image>\S+)$",
        service_match.group("body"),
    )
    return "" if image_match is None else image_match.group("image")


def _markdown_section(document: str, heading: str) -> str:
    """Return one level-two Markdown section without accepting another section's text."""
    section_marker = f"## {heading}\n"
    return document.partition(section_marker)[2].partition("\n## ")[0]


class PostgreSQLRuntimeBaselineTests(unittest.TestCase):
    """Keep real accounting regressions on the current supported PostgreSQL minor."""

    def test_exact_head_ci_uses_postgresql_18_6_by_immutable_digest(self) -> None:
        """The PostgreSQL service must include the August 2026 security update."""
        workflow = CI_WORKFLOW.read_text(encoding="utf-8")
        self.assertEqual(_postgres_service_image(workflow), POSTGRES_IMAGE)

    def test_runtime_baseline_records_18_6_existing_cluster_upgrade_checks(self) -> None:
        """An ephemeral CI image bump must not stand in for production upgrade evidence."""
        baseline = RUNTIME_BASELINE.read_text(encoding="utf-8")
        current_baseline = _markdown_section(baseline, "Current baseline")
        upgrade_acceptance = _markdown_section(
            baseline, "Existing-cluster upgrade acceptance"
        )
        self.assertIn("PostgreSQL **18.6**", current_baseline)
        self.assertIn(POSTGRES_DIGEST, current_baseline)
        self.assertNotIn("PostgreSQL **18.4**", current_baseline)
        for required_evidence in (
            "output_plugin_libraries",
            "pgcrypto",
            "COPY ... FROM STDIN",
            "GIN",
            "reltuples",
            "btree_gist",
            "ltree",
            "SHOW server_version",
        ):
            with self.subTest(required_evidence=required_evidence):
                self.assertIn(required_evidence, upgrade_acceptance)
        self.assertIn(
            "does not prove an existing database upgraded safely",
            upgrade_acceptance,
        )


if __name__ == "__main__":
    unittest.main()
