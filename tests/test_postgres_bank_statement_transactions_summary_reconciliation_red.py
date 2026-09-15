"""PostgreSQL REDs for camt.053 transaction-summary reconciliation."""

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
    lookup_bank_statement,
    parse_bank_statement_payload,
)
from tests import test_postgres_posting as posting

_SUMMARY_MISMATCH = (
    r"^statement transaction summary does not reconcile to normalized entries\. "
    r"Correct TxsSummry totals, then retry ingest\.$"
)


class BankStatementTransactionsSummaryReconciliationRedTests(unittest.TestCase):
    """Treat TxsSummry as source control evidence, not independent accounting truth."""

    @classmethod
    def setUpClass(cls) -> None:
        """Reuse the canonical real-PostgreSQL integration fixture."""
        posting.PostgresPostingTests.setUpClass()

    def setUp(self) -> None:
        """Prepare one statement whose two entries reconcile to an explicit summary."""
        self.case = posting.PostgresPostingTests("setUp")
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.addCleanup(self.case.tearDown)

        fixture = load_canonical_statement_fixture().decode("utf-8")
        self.baseline_payload = fixture.encode("utf-8")
        self.valid_payload = self._with_summary(
            fixture,
            credit_count="1",
            credit_sum="25000.00",
            debit_count="1",
            debit_sum="10000.00",
        )
        self.credit_count_mismatch_payload = self._with_summary(
            fixture,
            credit_count="2",
            credit_sum="25000.00",
            debit_count="1",
            debit_sum="10000.00",
        )
        self.credit_sum_mismatch_payload = self._with_summary(
            fixture,
            credit_count="1",
            credit_sum="25001.00",
            debit_count="1",
            debit_sum="10000.00",
        )
        self.debit_sum_mismatch_payload = self._with_summary(
            fixture,
            credit_count="1",
            credit_sum="25000.00",
            debit_count="1",
            debit_sum="10001.00",
        )

        summary = self._summary_xml(
            credit_count="1",
            credit_sum="25000.00",
            debit_count="1",
            debit_sum="10000.00",
        )
        self.assertEqual(self.valid_payload.count(summary.encode("utf-8")), 1)
        reformatted = summary.replace(
            "      <TxsSummry>\n",
            "      <TxsSummry>\n        \n",
            1,
        )
        self.reformatted_valid_payload = self.valid_payload.replace(
            summary.encode("utf-8"), reformatted.encode("utf-8"), 1
        )

        self.baseline_statement = parse_bank_statement_payload(
            self.baseline_payload, CAMT053_MESSAGE_DEFINITION
        )
        self.valid_statement = parse_bank_statement_payload(
            self.valid_payload, CAMT053_MESSAGE_DEFINITION
        )
        self.reformatted_valid_statement = parse_bank_statement_payload(
            self.reformatted_valid_payload, CAMT053_MESSAGE_DEFINITION
        )

        self.bank_account_reference = f"urn:cwl:bank_account:{uuid.uuid4().hex}"
        accept_bank_account_record(
            {
                "tenant_reference": self.case.policy.tenant_reference,
                "bank_account_reference": self.bank_account_reference,
                "account_currency_code": self.valid_statement.account_currency_code,
                "account_identifier_hash": self.valid_statement.account_identifier_hash,
            },
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
        )
        self.store = MemoryArtifactStore()

    def test_valid_summary_is_validation_evidence_not_a_second_financial_truth(self) -> None:
        """A reconciled summary must not fork the normalized entry-derived statement facts."""
        self.assertNotEqual(
            self.baseline_statement.source_artifact_hash,
            self.valid_statement.source_artifact_hash,
        )
        self.assertEqual(
            self.baseline_statement.account_identifier_hash,
            self.valid_statement.account_identifier_hash,
        )
        self.assertEqual(
            self.baseline_statement.normalized_payload_hash,
            self.valid_statement.normalized_payload_hash,
        )

    def test_summary_whitespace_cannot_change_normalized_financial_evidence(self) -> None:
        """XML formatting around a reconciled TxsSummry is not accounting evidence."""
        self.assertNotEqual(
            self.valid_statement.source_artifact_hash,
            self.reformatted_valid_statement.source_artifact_hash,
        )
        self.assertEqual(
            self.valid_statement.normalized_payload_hash,
            self.reformatted_valid_statement.normalized_payload_hash,
        )

    def test_credit_count_mismatch_fails_closed(self) -> None:
        """Reported credit count must equal the normalized CRDT entry population."""
        with self.assertRaisesRegex(AccountingValidationError, _SUMMARY_MISMATCH):
            accept_bank_statement_evidence(
                self._command(self.credit_count_mismatch_payload, "credit-count"),
                posting.DATABASE_URL,
                self.case.policy.tenant_reference,
                artifact_store=self.store,
            )

    def test_credit_sum_mismatch_fails_closed(self) -> None:
        """Reported credit sum must equal the exact normalized CRDT amount total."""
        with self.assertRaisesRegex(AccountingValidationError, _SUMMARY_MISMATCH):
            accept_bank_statement_evidence(
                self._command(self.credit_sum_mismatch_payload, "credit-sum"),
                posting.DATABASE_URL,
                self.case.policy.tenant_reference,
                artifact_store=self.store,
            )

    def test_debit_sum_mismatch_fails_closed(self) -> None:
        """Reported debit sum must equal the exact normalized DBIT amount total."""
        with self.assertRaisesRegex(AccountingValidationError, _SUMMARY_MISMATCH):
            accept_bank_statement_evidence(
                self._command(self.debit_sum_mismatch_payload, "debit-sum"),
                posting.DATABASE_URL,
                self.case.policy.tenant_reference,
                artifact_store=self.store,
            )

    def test_reconciled_summary_accepts_without_replacing_entry_derived_totals(self) -> None:
        """Buyer totals remain derived from normalized entries after summary validation."""
        accepted = accept_bank_statement_evidence(
            self._command(self.valid_payload, "valid"),
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
            artifact_store=self.store,
        )
        document = lookup_bank_statement(
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
            str(accepted["bank_statement_record_id"]),
        )
        self.assertEqual(document["entry_count"], 2)
        self.assertEqual(document["credit_total_amount"], "25000")
        self.assertEqual(document["debit_total_amount"], "10000")

    @staticmethod
    def _summary_xml(
        *, credit_count: str, credit_sum: str, debit_count: str, debit_sum: str
    ) -> str:
        """Return the minimal V14 TotalTransactions6 controls used by this RED."""
        return (
            "      <TxsSummry>\n"
            "        <TtlCdtNtries>\n"
            f"          <NbOfNtries>{credit_count}</NbOfNtries>\n"
            f"          <Sum>{credit_sum}</Sum>\n"
            "        </TtlCdtNtries>\n"
            "        <TtlDbtNtries>\n"
            f"          <NbOfNtries>{debit_count}</NbOfNtries>\n"
            f"          <Sum>{debit_sum}</Sum>\n"
            "        </TtlDbtNtries>\n"
            "      </TxsSummry>\n"
        )

    @classmethod
    def _with_summary(
        cls,
        fixture: str,
        *,
        credit_count: str,
        credit_sum: str,
        debit_count: str,
        debit_sum: str,
    ) -> bytes:
        """Insert TxsSummry immediately before the first Ntry in schema order."""
        marker = "      <Ntry>\n"
        if fixture.count(marker) != 2:
            raise AssertionError("canonical fixture must retain exactly two statement entries")
        summary = cls._summary_xml(
            credit_count=credit_count,
            credit_sum=credit_sum,
            debit_count=debit_count,
            debit_sum=debit_sum,
        )
        return fixture.replace(marker, summary + marker, 1).encode("utf-8")

    def _command(self, payload: bytes, suffix: str) -> dict[str, object]:
        """Return one supported ingest command with a fresh idempotency identity."""
        return {
            "tenant_reference": self.case.policy.tenant_reference,
            "bank_account_reference": self.bank_account_reference,
            "ingestion_idempotency_key": (
                f"transactions-summary-reconciliation-{suffix}-{uuid.uuid4().hex}"
            ),
            "message_definition_identifier": CAMT053_MESSAGE_DEFINITION,
            "statement_payload": payload.decode("utf-8"),
        }


if __name__ == "__main__":
    unittest.main()
