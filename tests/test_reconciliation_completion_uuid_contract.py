"""Canonical UUID regressions for reconciliation completion command identity."""

from __future__ import annotations

import unittest

from accounting_information_platform.core import AccountingValidationError
from accounting_information_platform.reconciliation_completion import _parse_uuid


class ReconciliationCompletionUuidContractTests(unittest.TestCase):
    """Keep public run identity to one lowercase hyphenated UUID representation."""

    def test_lowercase_hyphenated_uuid_is_accepted(self) -> None:
        """The canonical external representation parses to the recorded UUID identity."""
        value = "11111111-1111-4111-8111-111111111111"
        self.assertEqual(str(_parse_uuid(value, "reconciliation_run_id")), value)

    def test_equivalent_noncanonical_uuid_spellings_fail_closed(self) -> None:
        """Braces, uppercase hex, and compact hex cannot become alternate command spellings."""
        for value in (
            "{11111111-1111-4111-8111-111111111111}",
            "AAAAAAAA-AAAA-4AAA-8AAA-AAAAAAAAAAAA",
            "11111111111141118111111111111111",
        ):
            with self.subTest(value=value), self.assertRaises(AccountingValidationError):
                _parse_uuid(value, "reconciliation_run_id")


if __name__ == "__main__":  # pragma: no cover - direct invocation convenience
    unittest.main()
