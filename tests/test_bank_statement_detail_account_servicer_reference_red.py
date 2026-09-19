"""RED contract for transaction-detail account-servicer-reference provenance."""

from __future__ import annotations

import unittest

from accounting_information_platform import (
    CAMT053_MESSAGE_DEFINITION,
    load_canonical_statement_fixture,
    parse_bank_statement_payload,
)

_FIRST_DETAIL_REFS = b"""            <Refs>\n              <EndToEndId>E2E-1</EndToEndId>\n              <MndtId>MND-1</MndtId>\n            </Refs>"""
_FIRST_DETAIL_REFS_ASV_1 = b"""            <Refs>\n              <AcctSvcrRef>ASV-DETAIL-1</AcctSvcrRef>\n              <EndToEndId>E2E-1</EndToEndId>\n              <MndtId>MND-1</MndtId>\n            </Refs>"""
_FIRST_DETAIL_REFS_ASV_2 = b"""            <Refs>\n              <AcctSvcrRef>ASV-DETAIL-2</AcctSvcrRef>\n              <EndToEndId>E2E-1</EndToEndId>\n              <MndtId>MND-1</MndtId>\n            </Refs>"""


class BankStatementDetailAccountServicerReferenceRedTests(unittest.TestCase):
    """Keep a TxDtls-level AcctSvcrRef inside immutable detail provenance."""

    def test_detail_account_servicer_reference_changes_all_derived_evidence_hashes(self) -> None:
        """Two distinct retained servicer references must not provenance-alias."""
        fixture = load_canonical_statement_fixture()
        self.assertEqual(fixture.count(_FIRST_DETAIL_REFS), 1)

        payload_one = fixture.replace(_FIRST_DETAIL_REFS, _FIRST_DETAIL_REFS_ASV_1, 1)
        payload_two = fixture.replace(_FIRST_DETAIL_REFS, _FIRST_DETAIL_REFS_ASV_2, 1)

        statement_one = parse_bank_statement_payload(payload_one, CAMT053_MESSAGE_DEFINITION)
        statement_two = parse_bank_statement_payload(payload_two, CAMT053_MESSAGE_DEFINITION)
        entry_one = statement_one.entries[0]
        entry_two = statement_two.entries[0]
        detail_one = entry_one.entry_details[0]
        detail_two = entry_two.entry_details[0]

        self.assertEqual(detail_one.account_servicer_reference, "ASV-DETAIL-1")
        self.assertEqual(detail_two.account_servicer_reference, "ASV-DETAIL-2")
        self.assertNotEqual(detail_one.source_detail_hash, detail_two.source_detail_hash)
        self.assertNotEqual(entry_one.source_entry_hash, entry_two.source_entry_hash)
        self.assertNotEqual(
            statement_one.normalized_payload_hash,
            statement_two.normalized_payload_hash,
        )
        self.assertEqual(
            statement_one.entries[1].source_entry_hash,
            statement_two.entries[1].source_entry_hash,
        )


if __name__ == "__main__":
    unittest.main()
