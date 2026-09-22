"""RED contracts for the reconciliation date-window runtime domain.

The matching policy is durable control input, not a trusted Python annotation.
Integer subclasses must not execute caller comparison behavior while validating a
non-negative date window; ordinary built-in integers preserve existing policy.
"""

from __future__ import annotations

import unittest

from accounting_information_platform.reconciliation import DeterministicMatchPolicy


class _ExplodingWindowInt(int):
    """Expose caller-controlled ordering if an integer subclass reaches validation."""

    def __lt__(self, other: object) -> bool:
        """Fail if policy admission executes caller ordering semantics."""
        raise RuntimeError("caller-controlled date-window ordering must not execute")


class ReconciliationDateWindowRuntimeDomainRedTests(unittest.TestCase):
    """Require exact built-in non-negative integers for the weak-rule date window."""

    def test_integer_subclass_fails_before_custom_ordering(self) -> None:
        """A caller-defined integer cannot become deterministic matching policy."""
        with self.assertRaisesRegex(
            ValueError,
            "date_window_days must be a non-negative integer",
        ):
            DeterministicMatchPolicy(date_window_days=_ExplodingWindowInt(2))

    def test_bool_remains_invalid(self) -> None:
        """Boolean values remain outside the date-window integer domain."""
        for value in (False, True):
            with self.subTest(value=value):
                with self.assertRaisesRegex(
                    ValueError,
                    "date_window_days must be a non-negative integer",
                ):
                    DeterministicMatchPolicy(date_window_days=value)

    def test_negative_builtin_integer_remains_invalid(self) -> None:
        """Negative built-in integers keep the existing fail-closed behavior."""
        with self.assertRaisesRegex(
            ValueError,
            "date_window_days must be a non-negative integer",
        ):
            DeterministicMatchPolicy(date_window_days=-1)

    def test_builtin_non_negative_integers_remain_valid(self) -> None:
        """Same-day and bounded-day built-in policies preserve current semantics."""
        self.assertEqual(DeterministicMatchPolicy(date_window_days=0).date_window_days, 0)
        self.assertEqual(DeterministicMatchPolicy(date_window_days=2).date_window_days, 2)


if __name__ == "__main__":
    unittest.main()
