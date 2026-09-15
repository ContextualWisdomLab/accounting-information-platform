"""PostgreSQL RED for camt.053 debit-entry summary count reconciliation."""

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
from tests.test_postgres_bank_statement_transactions_summary_reconciliation_red import (
    BankStatementTransactionsSummaryReconciliationRedTests as SummaryRed,
)

_SUMMARY_MISMATCH = (
    r"^statement transaction summary does not reconcile to normalized entries\. "
    r"Correct TxsSummry totals, then retry ingest\.$"
)


class BankStatementTransactionsSummaryDebitCountRedTests(unittest.TestCase):
    """Require TtlDbtNtries/NbOfNtries to reconcile with normalized DBIT entries."""

    @classmethod
    def setUpClass(cls) -> None:
        """Reuse the canonical real-PostgreSQL integration fixture."""
        posting.PostgresPostingTests.setUpClass()

    def setUp(self) -> None:
        """Prepare one source-real statement whose debit count alone is inconsistent."""
        self.case = posting.PostgresPostingTests("setUp")
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.addCleanup(self.case.tearDown)

        fixture = load_canonical_statement_fixture().decode("utf-8")
        self.payload = SummaryRed._with_summary(
            fixture,
            credit_count="1",
            credit_sum="25000.00",
            debit_count="2",
            debit_sum="10000.00",
        )
        statement = parse_bank_statement_payload(
            self.payload,
            CAMT053_MESSAGE_DEFINITION,
        )
        self.bank_account_reference = f"urn:cwl:bank_account:{uuid.uuid4().hex}"
        accept_bank_account_record(
            {
                "tenant_reference": self.case.policy.tenant_reference,
                "bank_account_reference": self.bank_account_reference,
                "account_currency_code": statement.account_currency_code,
                "account_identifier_hash": statement.account_identifier_hash,
            },
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
        )
        self.store = MemoryArtifactStore()

    def test_debit_count_mismatch_fails_closed(self) -> None:
        """Reported debit count must equal the normalized DBIT entry population."""
        with self.assertRaisesRegex(AccountingValidationError, _SUMMARY_MISMATCH):
            accept_bank_statement_evidence(
                {
                    "tenant_reference": self.case.policy.tenant_reference,
                    "bank_account_reference": self.bank_account_reference,
                    "ingestion_idempotency_key": (
                        f"transactions-summary-debit-count-{uuid.uuid4().hex}"
                    ),
                    "message_definition_identifier": CAMT053_MESSAGE_DEFINITION,
                    "statement_payload": self.payload.decode("utf-8"),
                },
                posting.DATABASE_URL,
                self.case.policy.tenant_reference,
                artifact_store=self.store,
            )


if __name__ == "__main__":
    unittest.main()
