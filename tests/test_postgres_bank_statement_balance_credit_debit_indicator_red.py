"""PostgreSQL REDs for fail-closed bank-statement balance direction evidence."""

from __future__ import annotations

import unittest
import uuid

from accounting_information_platform import (
    AccountingValidationError,
    CAMT053_MESSAGE_DEFINITION,
    MemoryArtifactStore,
    accept_bank_account_record,
    accept_bank_statement_evidence,
    load_canonical_statement_fixture,
    parse_bank_statement_payload,
)
from tests import test_postgres_posting as posting


class BankStatementBalanceCreditDebitIndicatorRedTests(unittest.TestCase):
    """Reject balance evidence whose credit/debit indicator is outside ISO 20022."""

    @classmethod
    def setUpClass(cls) -> None:
        """Reuse the canonical PostgreSQL integration fixture."""
        posting.PostgresPostingTests.setUpClass()

    def setUp(self) -> None:
        """Create one source-real statement with only the opening-balance indicator corrupted."""
        self.case = posting.PostgresPostingTests("setUp")
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.addCleanup(self.case.tearDown)
        fixture = load_canonical_statement_fixture().decode("utf-8")
        marker = (
            '<Amt Ccy="KRW">100000.00</Amt>\n'
            "        <CdtDbtInd>CRDT</CdtDbtInd>"
        )
        self.assertEqual(fixture.count(marker), 1)
        self.invalid_payload = fixture.replace(
            marker,
            '<Amt Ccy="KRW">100000.00</Amt>\n'
            "        <CdtDbtInd>INVALID</CdtDbtInd>",
            1,
        ).encode("utf-8")
        self.bank_account_reference = f"urn:cwl:bank_account:{uuid.uuid4().hex}"
        accept_bank_account_record(
            {
                "tenant_reference": self.case.policy.tenant_reference,
                "bank_account_reference": self.bank_account_reference,
                "account_currency_code": "KRW",
                "account_identifier": "acct-opaque-fixture-only",
            },
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
        )

    def test_parser_rejects_invalid_opening_balance_credit_debit_indicator(self) -> None:
        """A normalized balance cannot retain an out-of-domain CreditDebitCode value."""
        with self.assertRaises(AccountingValidationError):
            parse_bank_statement_payload(
                self.invalid_payload,
                CAMT053_MESSAGE_DEFINITION,
            )

    def test_supported_ingest_rejects_invalid_balance_direction_before_persistence(self) -> None:
        """The supported PostgreSQL ingest boundary fails closed on invalid balance direction."""
        with self.assertRaises(AccountingValidationError):
            accept_bank_statement_evidence(
                {
                    "tenant_reference": self.case.policy.tenant_reference,
                    "bank_account_reference": self.bank_account_reference,
                    "ingestion_idempotency_key": f"invalid-balance-side-{uuid.uuid4().hex}",
                    "message_definition_identifier": CAMT053_MESSAGE_DEFINITION,
                    "statement_payload": self.invalid_payload.decode("utf-8"),
                },
                posting.DATABASE_URL,
                self.case.policy.tenant_reference,
                artifact_store=MemoryArtifactStore(),
            )


if __name__ == "__main__":
    unittest.main()
