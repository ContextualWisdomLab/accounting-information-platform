"""PostgreSQL RED for branch AddressType3Choice buyer-read privacy."""

from __future__ import annotations

import unittest
import uuid

from accounting_information_platform import accept_bank_account_record
from tests import test_postgres_posting as posting
from tests import (
    test_postgres_bank_statement_detail_payment_agent_address_type_choice_evidence_red
    as address_type_choice,
)


class BankStatementDetailPaymentAgentBranchAddressTypePrivacyRedTests(unittest.TestCase):
    """Prove branch address-type evidence stays internal to reconciliation evidence identity."""

    @classmethod
    def setUpClass(cls) -> None:
        """Reuse the canonical real-PostgreSQL integration fixture."""
        posting.PostgresPostingTests.setUpClass()

    def setUp(self) -> None:
        """Prepare the existing standard-agent choice fixture without duplicating its serializer."""
        choice_case = (
            address_type_choice.BankStatementDetailPaymentAgentAddressTypeChoiceEvidenceRedTests
        )
        self.choice = choice_case(
            "test_buyer_read_remains_digest_only_across_address_type_choice"
        )
        self.addCleanup(self.choice.doCleanups)
        self.choice.setUp()

    def test_branch_address_type_choice_does_not_expand_buyer_projection(self) -> None:
        """Branch AdrTp materiality changes canonical evidence without reversible buyer disclosure."""
        proprietary_detail = self.choice._ingest_and_read(
            self.choice.base_payload,
            "branch-privacy-proprietary",
        )

        coded_payload = self.choice.variant_payloads["branch-choice-code"]
        coded_statement = self.choice.variant_statements["branch-choice-code"]
        self.assertNotEqual(coded_payload, self.choice.base_payload)

        coded_reference = f"urn:cwl:bank_account:{uuid.uuid4().hex}"
        accept_bank_account_record(
            {
                "tenant_reference": self.choice.case.policy.tenant_reference,
                "bank_account_reference": coded_reference,
                "account_currency_code": coded_statement.account_currency_code,
                "account_identifier_hash": coded_statement.account_identifier_hash,
            },
            posting.DATABASE_URL,
            self.choice.case.policy.tenant_reference,
        )
        coded_detail = self.choice._ingest_and_read(
            coded_payload,
            "branch-privacy-coded",
            bank_account_reference=coded_reference,
        )

        for projection in (proprietary_detail, coded_detail):
            self.assertRegex(
                str(projection["instructing_agent_evidence_hash"]),
                r"\Asha256:[0-9a-f]{64}\Z",
            )
            self.assertRegex(
                str(projection["source_detail_hash"]),
                r"\Asha256:[0-9a-f]{64}\Z",
            )
            self.assertEqual(projection["detail_amount"], "6000")
            self.assertEqual(projection["detail_currency_code"], "KRW")

        self.assertEqual(
            proprietary_detail["instructing_agent_evidence_hash"],
            coded_detail["instructing_agent_evidence_hash"],
            "fixed BICFI keeps the purpose digest stable",
        )
        self.assertNotEqual(
            proprietary_detail["source_detail_hash"],
            coded_detail["source_detail_hash"],
            "branch AddressType3Choice is material to canonical detail evidence",
        )

        proprietary_projection = dict(proprietary_detail)
        coded_projection = dict(coded_detail)
        for projection in (proprietary_projection, coded_projection):
            projection.pop("instructing_agent_evidence_hash")
            projection.pop("source_detail_hash")
        self.assertEqual(
            proprietary_projection,
            coded_projection,
            "branch address-type evidence must not appear as raw, encoded, or split buyer fields",
        )


if __name__ == "__main__":
    unittest.main()
