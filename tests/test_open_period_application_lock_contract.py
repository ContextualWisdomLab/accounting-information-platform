"""Static regression for the ordinary open-period application lock profile."""

from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PERSISTENCE = ROOT / "src/accounting_information_platform/persistence.py"


class OpenPeriodApplicationLockContractTests(unittest.TestCase):
    """Keep ordinary posting from collapsing onto the close-command advisory mutex."""

    def test_open_period_lookup_does_not_take_exclusive_close_command_lock(self) -> None:
        """Database admission owns journal/transition ordering; ordinary posts need no close mutex."""
        source = PERSISTENCE.read_text(encoding="utf-8")
        helper_start = source.index("    def _require_open_book_period_bounds(")
        helper_end = source.index("    def _require_adjusting_period(", helper_start)
        helper_source = source[helper_start:helper_end]

        self.assertNotIn(
            'self._acquire_command_lock(connection, f"period:{book_id}:{period_code}")',
            helper_source,
            "ordinary open-period posting still serializes on the exclusive period-close advisory lock",
        )
        self.assertGreaterEqual(
            helper_source.count("period_status_code"),
            2,
            "removing the advisory mutex must retain the before/after open-state verification",
        )


if __name__ == "__main__":
    unittest.main()
