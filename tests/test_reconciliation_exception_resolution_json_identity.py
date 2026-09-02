"""Regression tests for strict JSON reconciliation command identity."""

from __future__ import annotations

import unittest

from accounting_information_platform import AccountingValidationError
from accounting_information_platform import reconciliation_exception_resolution as resolution


class ReconciliationExceptionResolutionJsonIdentityTests(unittest.TestCase):
    """Keep exception-resolution source identity inside RFC 8259 JSON semantics."""

    def test_nonfinite_json_numbers_fail_before_hashing(self) -> None:
        """NaN and infinities cannot become durable command source identity."""
        for value in (float("nan"), float("inf"), float("-inf")):
            with self.subTest(value=repr(value)):
                with self.assertRaisesRegex(AccountingValidationError, "JSON-compatible"):
                    resolution._source_payload_hash(
                        {"request_context": {"numeric_evidence": value}}
                    )

    def test_finite_json_number_identity_remains_deterministic(self) -> None:
        """A finite JSON number retains one stable canonical source hash."""
        command = {"request_context": {"numeric_evidence": 1.25}}

        first = resolution._source_payload_hash(command)
        second = resolution._source_payload_hash(command)

        self.assertEqual(first, second)
        self.assertRegex(first, r"^sha256:[0-9a-f]{64}$")


if __name__ == "__main__":
    unittest.main()
