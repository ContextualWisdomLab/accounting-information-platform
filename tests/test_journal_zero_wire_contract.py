"""Wire-contract regressions for canonical zero journal amounts."""

from __future__ import annotations

import copy
import unittest

from accounting_information_platform import AccountingValidationError, ingest_journal_proposal


class JournalZeroWireContractTests(unittest.TestCase):
    """The Billing JSON boundary accepts only the schema's literal zero representation."""

    def _payload(self) -> dict[str, object]:
        proposal_id = "019d7b92-1aa0-7a7f-b61c-962c0f4bf612"
        source_payload_hash = "sha256:" + "a" * 64
        return {
            "proposal_id": proposal_id,
            "proposal_contract_version": 1,
            "idempotency_key": (
                f"urn:cwl:tenant_001:invoice_draft:{proposal_id}"
                f":{source_payload_hash}:v1"
            ),
            "tenant_reference": "urn:cwl:tenant_001",
            "legal_entity_reference": "urn:cwl:legal_entity:entity_001",
            "intended_book_role_code": "primary_statutory",
            "transaction_currency": "KRW",
            "transaction_date": "2026-08-31",
            "accounting_date": "2026-08-31",
            "source_payload_hash": source_payload_hash,
            "proposed_at": "2026-08-31T00:00:00Z",
            "proposal_status": "validated",
            "source_event_references": [
                f"urn:cwl:tenant_001:invoice_draft:{proposal_id}"
            ],
            "lines": [
                {
                    "line_number": 1,
                    "account_role_code": "accounts_receivable",
                    "debit_amount": "25000",
                    "credit_amount": "0",
                },
                {
                    "line_number": 2,
                    "account_role_code": "usage_revenue",
                    "debit_amount": "0",
                    "credit_amount": "25000",
                },
            ],
        }

    def test_equivalent_noncanonical_zero_strings_fail_closed(self) -> None:
        """0.0-like values cannot pass ingest when the schema requires literal ``0``."""
        for zero_text in ("0.0", "0.00", "0.000000"):
            for line_index, field_name in ((0, "credit_amount"), (1, "debit_amount")):
                with self.subTest(zero_text=zero_text, line_index=line_index, field=field_name):
                    payload = copy.deepcopy(self._payload())
                    lines = payload["lines"]
                    assert isinstance(lines, list)
                    line = lines[line_index]
                    assert isinstance(line, dict)
                    line[field_name] = zero_text
                    with self.assertRaisesRegex(
                        AccountingValidationError, "canonical zero"
                    ):
                        ingest_journal_proposal(payload)

    def test_literal_zero_remains_ingestible(self) -> None:
        """The published schema's literal ``0`` representation continues to ingest."""
        proposal = ingest_journal_proposal(self._payload())
        self.assertEqual(str(proposal.lines[0].credit_amount), "0")
        self.assertEqual(str(proposal.lines[1].debit_amount), "0")

    def test_invalid_decimal_text_reaches_the_domain_amount_guard(self) -> None:
        """Non-decimal text remains fail-closed when the domain value is constructed."""
        payload = copy.deepcopy(self._payload())
        lines = payload["lines"]
        assert isinstance(lines, list)
        line = lines[0]
        assert isinstance(line, dict)
        line["debit_amount"] = "not-a-decimal"
        with self.assertRaisesRegex(AccountingValidationError, "amount"):
            ingest_journal_proposal(payload)


if __name__ == "__main__":
    unittest.main()
