"""PostgreSQL REDs for bank-statement transaction currency-exchange evidence."""

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
    lookup_bank_statement_entries,
    parse_bank_statement_payload,
)
from tests import test_postgres_posting as posting


class BankStatementTransactionCurrencyExchangeEvidenceRedTests(unittest.TestCase):
    """Keep present TxAmt/CcyXchg facts in normalized transaction evidence."""

    @classmethod
    def setUpClass(cls) -> None:
        """Reuse the canonical PostgreSQL integration fixture."""
        posting.PostgresPostingTests.setUpClass()

    def setUp(self) -> None:
        """Create two statements differing only in the reported transaction FX rate."""
        self.case = posting.PostgresPostingTests("setUp")
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.addCleanup(self.case.tearDown)
        fixture = load_canonical_statement_fixture().decode("utf-8")
        marker = (
            "              <TxAmt>\n"
            "                <Amt Ccy=\"KRW\">25000.00</Amt>\n"
            "              </TxAmt>"
        )
        self.assertEqual(fixture.count(marker), 1)
        self.source_currency_code = "USD"
        self.target_currency_code = "KRW"
        self.unit_currency_code = "USD"
        self.first_exchange_rate = "1333.333333"
        self.second_exchange_rate = "1333.333334"
        self.first_payload = self._with_transaction_exchange_rate(
            fixture,
            marker,
            self.first_exchange_rate,
        )
        self.second_payload = self._with_transaction_exchange_rate(
            fixture,
            marker,
            self.second_exchange_rate,
        )
        self.first_statement = parse_bank_statement_payload(
            self.first_payload,
            CAMT053_MESSAGE_DEFINITION,
        )
        self.second_statement = parse_bank_statement_payload(
            self.second_payload,
            CAMT053_MESSAGE_DEFINITION,
        )
        self.bank_account_reference = f"urn:cwl:bank_account:{uuid.uuid4().hex}"
        accept_bank_account_record(
            {
                "tenant_reference": self.case.policy.tenant_reference,
                "bank_account_reference": self.bank_account_reference,
                "account_currency_code": self.first_statement.account_currency_code,
                "account_identifier_hash": self.first_statement.account_identifier_hash,
            },
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
        )
        self.store = MemoryArtifactStore()

    def test_present_transaction_exchange_rate_changes_canonical_hashes(self) -> None:
        """Changing only TxAmt/CcyXchg/XchgRate changes detail, entry, and statement identity."""
        self.assertNotEqual(
            self.first_statement.entries[0].entry_details[0].source_detail_hash,
            self.second_statement.entries[0].entry_details[0].source_detail_hash,
        )
        self.assertNotEqual(
            self.first_statement.entries[0].source_entry_hash,
            self.second_statement.entries[0].source_entry_hash,
        )
        self.assertNotEqual(
            self.first_statement.normalized_payload_hash,
            self.second_statement.normalized_payload_hash,
        )

    def test_same_statement_identity_cannot_replay_changed_transaction_exchange_rate(self) -> None:
        """Changed bank-reported FX evidence requires correction, not silent replay."""
        accept_bank_statement_evidence(
            self._command(self.first_payload, "first"),
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
            artifact_store=self.store,
        )

        with self.assertRaises(AccountingValidationError):
            accept_bank_statement_evidence(
                self._command(self.second_payload, "second"),
                posting.DATABASE_URL,
                self.case.policy.tenant_reference,
                artifact_store=self.store,
            )

    def test_entry_lookup_preserves_present_transaction_currency_exchange(self) -> None:
        """Buyer-visible detail reads expose the exact reported transaction FX evidence."""
        accepted = accept_bank_statement_evidence(
            self._command(self.first_payload, "lookup"),
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
            artifact_store=self.store,
        )

        document = lookup_bank_statement_entries(
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
            str(accepted["bank_statement_record_id"]),
        )

        first_detail = document["bank_statement_entries"][0]["entry_details"][0]
        self.assertEqual(
            first_detail["transaction_exchange_source_currency_code"],
            self.source_currency_code,
        )
        self.assertEqual(
            first_detail["transaction_exchange_target_currency_code"],
            self.target_currency_code,
        )
        self.assertEqual(
            first_detail["transaction_exchange_unit_currency_code"],
            self.unit_currency_code,
        )
        self.assertEqual(
            first_detail["transaction_exchange_rate"],
            self.first_exchange_rate,
        )

    def _with_transaction_exchange_rate(
        self,
        fixture: str,
        marker: str,
        exchange_rate: str,
    ) -> bytes:
        """Insert one schema-shaped currency-exchange block under the first TxAmt."""
        return fixture.replace(
            marker,
            "              <TxAmt>\n"
            "                <Amt Ccy=\"KRW\">25000.00</Amt>\n"
            "                <CcyXchg>\n"
            f"                  <SrcCcy>{self.source_currency_code}</SrcCcy>\n"
            f"                  <TrgtCcy>{self.target_currency_code}</TrgtCcy>\n"
            f"                  <UnitCcy>{self.unit_currency_code}</UnitCcy>\n"
            f"                  <XchgRate>{exchange_rate}</XchgRate>\n"
            "                </CcyXchg>\n"
            "              </TxAmt>",
            1,
        ).encode("utf-8")

    def _command(self, payload: bytes, suffix: str) -> dict[str, object]:
        """Return one supported ingest command with a fresh replay key."""
        return {
            "tenant_reference": self.case.policy.tenant_reference,
            "bank_account_reference": self.bank_account_reference,
            "ingestion_idempotency_key": (
                f"transaction-currency-exchange-{suffix}-{uuid.uuid4().hex}"
            ),
            "message_definition_identifier": CAMT053_MESSAGE_DEFINITION,
            "statement_payload": payload.decode("utf-8"),
        }


if __name__ == "__main__":
    unittest.main()
