"""RED contracts for version-qualified ISO 20022 supplementary-data admission."""

from __future__ import annotations

import unittest
from unittest.mock import patch

import accounting_information_platform.bank_statement as bank_statement
from accounting_information_platform import (
    AccountingValidationError,
    CAMT053_MESSAGE_DEFINITION,
    load_canonical_statement_fixture,
    parse_bank_statement_payload,
)


class BankStatementSupplementaryDataAdmissionRedTests(unittest.TestCase):
    """Reject unregistered transaction extensions before normalized evidence exists."""

    def test_unregistered_transaction_extension_fails_before_normalization(self) -> None:
        """Do not silently discard an unqualified TxDtls/SplmtryData envelope."""
        fixture = load_canonical_statement_fixture()
        marker = (
            b"              <Ustrd>Invoice 1001</Ustrd>\n"
            b"            </RmtInf>\n"
        )
        self.assertEqual(fixture.count(marker), 1)
        supplementary = (
            marker
            + b"            <SplmtryData>\n"
            + b"              <PlcAndNm>TxDtls</PlcAndNm>\n"
            + b"              <Envlp>\n"
            + b"                <cwl:UnregisteredEvidence "
            + b"xmlns:cwl=\"urn:cwl:test:unregistered-supplementary\">\n"
            + b"                  <cwl:Marker>bank-reported-evidence</cwl:Marker>\n"
            + b"                </cwl:UnregisteredEvidence>\n"
            + b"              </Envlp>\n"
            + b"            </SplmtryData>\n"
        )
        hostile = fixture.replace(marker, supplementary, 1)

        parse_bank_statement_payload(fixture, CAMT053_MESSAGE_DEFINITION)

        with patch.object(
            bank_statement,
            "_normalize_statement",
            side_effect=AssertionError(
                "normalization ran before supplementary-data extension admission"
            ),
        ):
            with self.assertRaisesRegex(
                AccountingValidationError,
                r"^unregistered ISO 20022 supplementary-data extension$",
            ):
                parse_bank_statement_payload(hostile, CAMT053_MESSAGE_DEFINITION)


if __name__ == "__main__":
    unittest.main()
