"""Runtime-domain contracts for deterministic reconciliation matching policy."""

from __future__ import annotations

import unittest

from accounting_information_platform.reconciliation import DeterministicMatchPolicy


class ReconciliationPolicyDomainTests(unittest.TestCase):
    """Reject nonsensical date-window configuration before matching evidence."""

    def test_negative_date_window_is_rejected(self) -> None:
        """A negative matching window must fail before it can shape abstention results."""
        with self.assertRaisesRegex(ValueError, "date_window_days"):
            DeterministicMatchPolicy(date_window_days=-1)

    def test_boolean_date_window_is_rejected(self) -> None:
        """Boolean values are not an integer number of reconciliation days."""
        with self.assertRaisesRegex(ValueError, "date_window_days"):
            DeterministicMatchPolicy(date_window_days=True)

    def test_fractional_date_window_is_rejected(self) -> None:
        """A fractional day window is not part of the bounded date policy contract."""
        with self.assertRaisesRegex(ValueError, "date_window_days"):
            DeterministicMatchPolicy(date_window_days=1.5)  # type: ignore[arg-type]

    def test_zero_date_window_is_valid(self) -> None:
        """A same-day-only policy remains a valid deterministic matching boundary."""
        policy = DeterministicMatchPolicy(date_window_days=0)
        self.assertEqual(policy.date_window_days, 0)


if __name__ == "__main__":
    unittest.main()
