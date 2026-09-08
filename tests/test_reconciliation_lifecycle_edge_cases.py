"""Focused edge-case coverage for reconciliation lifecycle helpers."""

from __future__ import annotations

import unittest

from accounting_information_platform import AccountingValidationError
from accounting_information_platform import reconciliation_lifecycle as lifecycle


class ReconciliationLifecycleEdgeCaseTests(unittest.TestCase):
    """Exercise short-circuit validation paths not repeated by PostgreSQL fixtures."""

    def test_canonical_text_rejects_non_string_value(self) -> None:
        """Missing/non-string command evidence fails before database access."""
        with self.assertRaisesRegex(AccountingValidationError, "purpose_code"):
            lifecycle._canonical_text(None, "purpose_code")

    def test_canonical_text_accepts_canonical_value(self) -> None:
        """A canonical non-empty command field is returned unchanged."""
        self.assertEqual(
            lifecycle._canonical_text("month_end_reconciliation", "purpose_code"),
            "month_end_reconciliation",
        )


if __name__ == "__main__":
    unittest.main()
