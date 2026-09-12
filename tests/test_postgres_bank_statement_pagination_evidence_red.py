"""PostgreSQL REDs for camt.053 statement-pagination evidence."""

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


class BankStatementPaginationEvidenceRedTests(unittest.TestCase):
    """Reject or retain present StmtPgntn evidence instead of silently dropping it."""

    @classmethod
    def setUpClass(cls) -> None:
        """Reuse the canonical PostgreSQL integration fixture."""
        posting.PostgresPostingTests.setUpClass()

    def setUp(self) -> None:
        """Create source-real statement pages that differ only in pagination evidence."""
        self.case = posting.PostgresPostingTests("setUp")
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.addCleanup(self.case.tearDown)

        self.fixture = load_canonical_statement_fixture().decode("utf-8")
        marker = (
            "      <Id>BANK-STMT-2026-08-24</Id>\n"
            "      <ElctrncSeqNb>42</ElctrncSeqNb>"
        )
        self.assertEqual(self.fixture.count(marker), 1)

        def with_pagination(page_number: str, last_page: str) -> bytes:
            replacement = (
                "      <Id>BANK-STMT-2026-08-24</Id>\n"
                "      <StmtPgntn>\n"
                f"        <PgNb>{page_number}</PgNb>\n"
                f"        <LastPgInd>{last_page}</LastPgInd>\n"
                "      </StmtPgntn>\n"
                "      <ElctrncSeqNb>42</ElctrncSeqNb>"
            )
            return self.fixture.replace(marker, replacement, 1).encode("utf-8")

        self.page_one_payload = with_pagination("1", "false")
        self.page_two_payload = with_pagination("2", "false")
        self.final_page_payload = with_pagination("1", "true")

        canonical = parse_bank_statement_payload(
            self.fixture.encode("utf-8"),
            CAMT053_MESSAGE_DEFINITION,
        )
        self.bank_account_reference = f"urn:cwl:bank_account:{uuid.uuid4().hex}"
        accept_bank_account_record(
            {
                "tenant_reference": self.case.policy.tenant_reference,
                "bank_account_reference": self.bank_account_reference,
                "account_currency_code": canonical.account_currency_code,
                "account_identifier_hash": canonical.account_identifier_hash,
            },
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
        )
        self.store = MemoryArtifactStore()

    @staticmethod
    def _parse_or_reject(payload: bytes):
        """Return a parsed page, or None when the adapter intentionally fails closed."""
        try:
            return parse_bank_statement_payload(payload, CAMT053_MESSAGE_DEFINITION)
        except AccountingValidationError:
            return None

    def test_adapter_rejects_or_preserves_present_statement_pagination(self) -> None:
        """Present page identity must never be silently accepted and discarded."""
        statement = self._parse_or_reject(self.page_one_payload)
        if statement is None:
            return

        self.assertTrue(hasattr(statement, "statement_page_number"))
        self.assertTrue(hasattr(statement, "statement_last_page_indicator"))
        self.assertEqual(str(statement.statement_page_number), "1")
        self.assertIs(statement.statement_last_page_indicator, False)

    def test_accepted_pagination_changes_normalized_statement_identity(self) -> None:
        """If pagination is supported, both page number and final-page flag are evidence."""
        page_one = self._parse_or_reject(self.page_one_payload)
        page_two = self._parse_or_reject(self.page_two_payload)
        final_page = self._parse_or_reject(self.final_page_payload)
        parsed = (page_one, page_two, final_page)
        if any(statement is None for statement in parsed):
            self.assertTrue(all(statement is None for statement in parsed))
            return

        self.assertNotEqual(page_one.normalized_payload_hash, page_two.normalized_payload_hash)
        self.assertNotEqual(page_one.normalized_payload_hash, final_page.normalized_payload_hash)

    def test_supported_ingest_rejects_or_preserves_statement_pagination(self) -> None:
        """Supported PostgreSQL admission must fail closed or expose exact page provenance."""
        try:
            accepted = accept_bank_statement_evidence(
                {
                    "tenant_reference": self.case.policy.tenant_reference,
                    "bank_account_reference": self.bank_account_reference,
                    "ingestion_idempotency_key": f"statement-pagination-{uuid.uuid4().hex}",
                    "message_definition_identifier": CAMT053_MESSAGE_DEFINITION,
                    "statement_payload": self.page_one_payload.decode("utf-8"),
                },
                posting.DATABASE_URL,
                self.case.policy.tenant_reference,
                artifact_store=self.store,
            )
        except AccountingValidationError:
            return

        document = lookup_bank_statement(
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
            str(accepted["bank_statement_record_id"]),
        )
        self.assertEqual(str(document["statement_page_number"]), "1")
        self.assertIs(document["statement_last_page_indicator"], False)


if __name__ == "__main__":
    unittest.main()
