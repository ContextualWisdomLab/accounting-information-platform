"""Regression contract for authority-bearing documentation acceptance in CI."""

from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PATH_FILTER = re.compile(r"(?m)^[ \t]+paths(?:-ignore)?:[ \t]*[^\r\n]*$")


class AccountingDocumentationCiAcceptanceTests(unittest.TestCase):
    """Keep documentation changes inside exact-head and integrated-head acceptance."""

    def test_path_filter_detection_rejects_inline_yaml_values(self) -> None:
        """Inline path filters must be detected as strongly as block-style filters."""
        for trigger_block in (
            "    paths: ['src/**']\n",
            "    paths-ignore: ['docs/**', '*.md']\n",
        ):
            with self.subTest(trigger_block=trigger_block):
                self.assertIsNotNone(
                    PATH_FILTER.search(trigger_block),
                    "inline YAML path filters would let documentation bypass Accounting "
                    "Foundation CI acceptance",
                )

    def test_accounting_ci_does_not_ignore_documentation_changes(self) -> None:
        """Docs and Markdown changes must not bypass repository accounting validation."""
        workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(
            encoding="utf-8"
        )
        pull_request_block = workflow.split("  pull_request:", 1)[1].split(
            "  push:", 1
        )[0]
        push_block = workflow.split("  push:", 1)[1].split("\npermissions:", 1)[0]

        for trigger_block in (pull_request_block, push_block):
            self.assertIsNone(
                PATH_FILTER.search(trigger_block),
                "Accounting Foundation CI pull_request/push triggers must not define "
                "paths or paths-ignore filters; authority-bearing documentation must "
                "receive the same exact-head acceptance as source changes.",
            )


if __name__ == "__main__":
    unittest.main()
