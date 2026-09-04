"""Repository contract for the supported PostgreSQL security baseline."""

from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CI_WORKFLOW = ROOT / ".github/workflows/ci.yml"
POSTGRES_IMAGE = (
    "postgres:18.6@sha256:4ef4dbc939d61acea57712655ddb4b4ab27419c913f94cca0cd57cb3ea3c2280"
)


class PostgreSQLRuntimeBaselineTests(unittest.TestCase):
    """Keep real accounting regressions on the current supported PostgreSQL minor."""

    def test_exact_head_ci_uses_postgresql_18_6_by_immutable_digest(self) -> None:
        """The PostgreSQL service must include the August 2026 security update."""
        workflow = CI_WORKFLOW.read_text(encoding="utf-8")
        self.assertIn(POSTGRES_IMAGE, workflow)
        self.assertNotIn("postgres:18.4", workflow)


if __name__ == "__main__":
    unittest.main()
