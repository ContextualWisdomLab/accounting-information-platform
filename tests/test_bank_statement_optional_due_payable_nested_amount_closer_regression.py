"""Regression coverage for nested Amount closers in the DuePyblAmt absence RED."""

from __future__ import annotations

import unittest

from tests import (
    test_postgres_bank_statement_structured_referred_document_line_optional_due_payable_absence_evidence_red
    as due_payable_contract,
)

DuePayableContract = (
    due_payable_contract.BankStatementStructuredLineOptionalDuePayableAmountAbsenceEvidenceRedTests
)


class _WholePayloadSegment:
    """Expose one complete synthetic LineDtls payload as the target source segment."""

    @staticmethod
    def _second_line_segment(text: str) -> tuple[str, int, int]:
        return text, 0, len(text)


class DuePayableNestedAmountCloserRegressionTests(unittest.TestCase):
    """Keep a nested currency Amount close distinct from the direct Amount group close."""

    def test_extract_remove_restore_ignore_nested_standalone_amount_closer(
        self,
    ) -> None:
        """Exercise all source-edit paths with a standalone nested ``</Amt>`` line."""
        payload = (
            "                  <LineDtls>\n"
            "                    <Amt>\n"
            "                      <DuePyblAmt Ccy=\"KRW\">5100.10</DuePyblAmt>\n"
            "                      <DscntApldAmt>\n"
            "                        <Tp><Cd>APDS</Cd></Tp>\n"
            "                        <Amt Ccy=\"KRW\">\n"
            "                          100.00\n"
            "                        </Amt>\n"
            "                      </DscntApldAmt>\n"
            "                      <RmtdAmt Ccy=\"KRW\">5000.10</RmtdAmt>\n"
            "                    </Amt>\n"
            "                  </LineDtls>\n"
        ).encode("utf-8")

        contract = DuePayableContract("runTest")
        contract.parent = _WholePayloadSegment()
        contract.due_payable_line = contract._extract_second_line_due_payable_line(payload)

        changed = contract._remove_second_line_due_payable_amount(payload)
        self.assertNotIn(b"DuePyblAmt", changed)
        self.assertIn(b"                        </Amt>\n", changed)
        self.assertIn(b"                    </Amt>\n", changed)
        self.assertLess(
            changed.index(b"                        </Amt>\n"),
            changed.index(b"                    </Amt>\n"),
        )
        self.assertEqual(contract._restore_second_line_due_payable_amount(changed), payload)

        lines = payload.decode("utf-8").splitlines(keepends=True)
        start, end = contract._direct_amount_bounds(lines)
        nested_close = lines.index("                        </Amt>\n")
        direct_close = lines.index("                    </Amt>\n")
        self.assertLess(nested_close, direct_close)
        self.assertEqual(start, lines.index("                    <Amt>\n"))
        self.assertEqual(end, direct_close)


if __name__ == "__main__":
    unittest.main()
