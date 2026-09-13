"""RED for detail account-servicer reference provenance aliasing."""

from __future__ import annotations

import unittest

from accounting_information_platform import (
    CAMT053_MESSAGE_DEFINITION,
    load_canonical_statement_fixture,
    parse_bank_statement_payload,
)


class BankStatementDetailAccountServicerReferenceHashRedTests(unittest.TestCase):
    """Keep one accepted detail reference material to every normalized evidence hash."""

    def test_detail_account_servicer_reference_changes_normalized_evidence_hashes(self) -> None:
        """Distinct accepted AcctSvcrRef values must not share detail, entry, or statement hashes."""
        fixture = load_canonical_statement_fixture().decode("utf-8")
        marker = (
            "            <Refs>\n"
            "              <EndToEndId>E2E-1</EndToEndId>\n"
            "              <MndtId>MND-1</MndtId>\n"
            "            </Refs>"
        )
        self.assertEqual(fixture.count(marker), 1)

        def payload(reference: str) -> bytes:
            changed = marker.replace(
                "              <EndToEndId>E2E-1</EndToEndId>",
                (
                    f"              <AcctSvcrRef>{reference}</AcctSvcrRef>\n"
                    "              <EndToEndId>E2E-1</EndToEndId>"
                ),
                1,
            )
            return fixture.replace(marker, changed, 1).encode("utf-8")

        first = parse_bank_statement_payload(
            payload("DETAIL-ASR-ONE"),
            CAMT053_MESSAGE_DEFINITION,
        )
        second = parse_bank_statement_payload(
            payload("DETAIL-ASR-TWO"),
            CAMT053_MESSAGE_DEFINITION,
        )

        first_entry = first.entries[0]
        second_entry = second.entries[0]
        first_detail = first_entry.entry_details[0]
        second_detail = second_entry.entry_details[0]

        self.assertEqual(first_detail.account_servicer_reference, "DETAIL-ASR-ONE")
        self.assertEqual(second_detail.account_servicer_reference, "DETAIL-ASR-TWO")
        self.assertNotEqual(first.source_artifact_hash, second.source_artifact_hash)

        evidence_pairs = (
            (first_detail.source_detail_hash, second_detail.source_detail_hash),
            (first_entry.source_entry_hash, second_entry.source_entry_hash),
            (first.normalized_payload_hash, second.normalized_payload_hash),
        )
        self.assertTrue(
            all(left != right for left, right in evidence_pairs),
            "accepted TxDtls/Refs/AcctSvcrRef evidence must affect detail, entry, and statement hashes",
        )


if __name__ == "__main__":
    unittest.main()
