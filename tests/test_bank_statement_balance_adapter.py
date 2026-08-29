"""RED/GREEN adapter bounds and proprietary-balance evidence contracts."""

from __future__ import annotations

import unittest

from accounting_information_platform import (
    CAMT053_MESSAGE_DEFINITION,
    AccountingValidationError,
    load_canonical_statement_fixture,
    parse_bank_statement_payload,
)


class BankStatementBalanceAdapterTests(unittest.TestCase):
    """Keep balance evidence lossless and bounded before relational persistence."""

    def test_proprietary_balance_type_survives_normalization(self) -> None:
        """CdOrPrtry/Prtry remains distinct evidence instead of collapsing to null."""
        payload = load_canonical_statement_fixture().replace(
            b"<Cd>OPBD</Cd>", b"<Prtry>CUSTOM-OPEN</Prtry>", 1
        )
        statement = parse_bank_statement_payload(payload, CAMT053_MESSAGE_DEFINITION)
        self.assertEqual(statement.balances[0].balance_type_code, "CUSTOM-OPEN")
        self.assertEqual(statement.balances[0].balance_type_source_code, "prtry")
        self.assertNotEqual(
            statement.balances[0].source_balance_hash,
            statement.balances[1].source_balance_hash,
        )

    def test_standard_and_proprietary_same_code_remain_distinct(self) -> None:
        """The Cd versus Prtry choice remains material when the codes match."""
        standard_payload = load_canonical_statement_fixture()
        proprietary_payload = standard_payload.replace(
            b"<Cd>OPBD</Cd>", b"<Prtry>OPBD</Prtry>", 1
        )
        standard = parse_bank_statement_payload(
            standard_payload, CAMT053_MESSAGE_DEFINITION
        )
        proprietary = parse_bank_statement_payload(
            proprietary_payload, CAMT053_MESSAGE_DEFINITION
        )

        self.assertEqual(standard.balances[0].balance_type_code, "OPBD")
        self.assertEqual(proprietary.balances[0].balance_type_code, "OPBD")
        self.assertEqual(standard.balances[0].balance_type_source_code, "cd")
        self.assertEqual(proprietary.balances[0].balance_type_source_code, "prtry")
        self.assertIsNotNone(standard.opening_balance_hash)
        self.assertIsNone(proprietary.opening_balance_hash)
        self.assertNotEqual(
            standard.balances[0].source_balance_hash,
            proprietary.balances[0].source_balance_hash,
        )
        self.assertNotEqual(
            standard.normalized_payload_hash,
            proprietary.normalized_payload_hash,
        )

    def test_balance_population_has_a_domain_bound(self) -> None:
        """One valid-sized XML document cannot fan out into unbounded balance rows."""
        payload = load_canonical_statement_fixture()
        start = payload.index(b"      <Bal>\n")
        end = payload.index(b"      </Bal>\n", start) + len(b"      </Bal>\n")
        balance_block = payload[start:end]
        # The product contract admits at most 64 balance facts per statement.
        oversized = payload[:start] + balance_block * 65 + payload[end:]
        with self.assertRaisesRegex(AccountingValidationError, "balance count exceeds"):
            parse_bank_statement_payload(oversized, CAMT053_MESSAGE_DEFINITION)


if __name__ == "__main__":
    unittest.main()
