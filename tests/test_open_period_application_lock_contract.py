"""Static regression for the ordinary open-period application lock profile."""

from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PERSISTENCE = ROOT / "src/accounting_information_platform/persistence.py"


class OpenPeriodApplicationLockContractTests(unittest.TestCase):
    """Keep ordinary posting from collapsing onto close-command advisory mutexes."""

    def test_open_period_lookup_does_not_take_close_command_lock(self) -> None:
        """Database admission owns journal/transition ordering; ordinary posts need no close mutex."""
        source = PERSISTENCE.read_text(encoding="utf-8")
        helper_start = source.index("    def _require_open_book_period_bounds(")
        helper_end = source.index("    def _require_adjusting_period(", helper_start)
        helper_source = source[helper_start:helper_end]

        self.assertNotIn(
            "self._acquire_command_lock(",
            helper_source,
            "ordinary open-period lookup must not acquire a command-level advisory mutex",
        )
        self.assertIn(
            'if row[2] != "open":',
            helper_source,
            "removing the advisory mutex must retain fail-closed application validation for a non-open period",
        )


if __name__ == "__main__":
    unittest.main()
