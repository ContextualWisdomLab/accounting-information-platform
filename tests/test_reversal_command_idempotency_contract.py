"""RED contracts for durable reversal-command replay and conflict semantics."""

from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ACCEPT_SOURCE = ROOT / "src/accounting_information_platform/accept.py"
PERSISTENCE_SOURCE = ROOT / "src/accounting_information_platform/persistence.py"


class ReversalCommandIdempotencyContractTests(unittest.TestCase):
    """Keep reversal retry identity distinct from original-journal lookup identity."""

    def test_accept_requires_distinct_reversal_command_idempotency_key(self) -> None:
        """The reversal command must carry its own key and pass it to durable reversal."""
        source = ACCEPT_SOURCE.read_text(encoding="utf-8")
        command = source.split("def accept_journal_reversal(", 1)[1].split(
            "\ndef accept_period_close(", 1
        )[0]

        self.assertIn('payload.get("reversal_idempotency_key")', command)
        self.assertRegex(
            command,
            re.compile(r"if\s+not\s+reversal_idempotency_key\s*:", re.MULTILINE),
        )
        self.assertIn("reversal_idempotency_key=reversal_idempotency_key", command)

    def test_postgres_reversal_replay_compares_command_key_and_hash(self) -> None:
        """Existing lineage may replay only for the exact immutable reversal command."""
        source = PERSISTENCE_SOURCE.read_text(encoding="utf-8")
        method = source.split("    def reverse(\n", 1)[1].split(
            "\n    def load_reversal_policy(", 1
        )[0]

        self.assertIn("reversal_idempotency_key: str", method)
        self.assertRegex(
            method,
            re.compile(
                r"SELECT[\s\S]+idempotency_key[\s\S]+source_payload_hash[\s\S]+journal_reversal",
                re.IGNORECASE,
            ),
        )
        self.assertIn("IdempotencyConflictError", method)
        self.assertRegex(
            method,
            re.compile(r"reversal[\s_-]*command[\s_-]*hash|command_hash", re.IGNORECASE),
        )


if __name__ == "__main__":
    unittest.main()
