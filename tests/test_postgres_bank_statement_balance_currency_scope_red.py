"""PostgreSQL REDs for camt.053 balance-currency admission scope."""

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


class BankStatementBalanceCurrencyScopeRedTests(unittest.TestCase):
    """Reject balance evidence outside the registered statement-account currency."""

    @classmethod
    def setUpClass(cls) -> None:
        """Reuse the canonical PostgreSQL integration fixture."""
        posting.PostgresPostingTests.setUpClass()

    def setUp(self) -> None:
        """Create source-real statements differing only in one balance currency."""
        self.case = posting.PostgresPostingTests("setUp")
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.addCleanup(self.case.tearDown)
        self.fixture = load_canonical_statement_fixture().decode("utf-8")
        self.opening_marker = '<Amt Ccy="KRW">100000.00</Amt>'
        self.closing_marker = '<Amt Ccy="KRW">115000.00</Amt>'
        self.assertEqual(self.fixture.count(self.opening_marker), 1)
        self.assertEqual(self.fixture.count(self.closing_marker), 1)

        self.canonical_statement = parse_bank_statement_payload(
            self.fixture.encode("utf-8"),
            CAMT053_MESSAGE_DEFINITION,
        )
        self.bank_account_reference = f"urn:cwl:bank_account:{uuid.uuid4().hex}"
        accept_bank_account_record(
            {
                "tenant_reference": self.case.policy.tenant_reference,
                "bank_account_reference": self.bank_account_reference,
                "account_currency_code": self.canonical_statement.account_currency_code,
                "account_identifier_hash": self.canonical_statement.account_identifier_hash,
            },
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
        )
        self.store = MemoryArtifactStore()

    def test_opening_balance_currency_must_match_statement_account_currency(self) -> None:
        """OPBD cannot introduce foreign-currency balance evidence while FX is unsupported."""
        payload = self._replace_balance_currency(self.opening_marker)

        with self.assertRaises(AccountingValidationError):
            parse_bank_statement_payload(payload, CAMT053_MESSAGE_DEFINITION)

    def test_closing_balance_currency_must_match_statement_account_currency(self) -> None:
        """CLBD cannot introduce foreign-currency balance evidence while FX is unsupported."""
        payload = self._replace_balance_currency(self.closing_marker)

        with self.assertRaises(AccountingValidationError):
            parse_bank_statement_payload(payload, CAMT053_MESSAGE_DEFINITION)

    def test_postgres_ingest_rejects_foreign_currency_balance_before_retention(self) -> None:
        """Supported ingest fails closed before retaining a mismatched balance currency."""
        payload = self._replace_balance_currency(self.opening_marker)

        with self.assertRaises(AccountingValidationError):
            accept_bank_statement_evidence(
                {
                    "tenant_reference": self.case.policy.tenant_reference,
                    "bank_account_reference": self.bank_account_reference,
                    "ingestion_idempotency_key": f"balance-currency-{uuid.uuid4().hex}",
                    "message_definition_identifier": CAMT053_MESSAGE_DEFINITION,
                    "statement_payload": payload.decode("utf-8"),
                },
                posting.DATABASE_URL,
                self.case.policy.tenant_reference,
                artifact_store=self.store,
            )

    def _replace_balance_currency(self, marker: str) -> bytes:
        """Replace exactly one canonical balance Ccy while preserving all other evidence."""
        replacement = marker.replace('Ccy="KRW"', 'Ccy="USD"')
        self.assertNotEqual(marker, replacement)
        payload = self.fixture.replace(marker, replacement, 1)
        self.assertEqual(payload.count(replacement), 1)
        return payload.encode("utf-8")


if __name__ == "__main__":
    unittest.main()
