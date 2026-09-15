"""PostgreSQL REDs for complete camt.053 statement-pagination evidence."""

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
    """Do not admit a partial statement page as complete reconciliation evidence."""

    @classmethod
    def setUpClass(cls) -> None:
        """Reuse the canonical PostgreSQL integration fixture."""
        posting.PostgresPostingTests.setUpClass()

    def setUp(self) -> None:
        """Create source-real pagination variants around one canonical statement."""
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

        # Page 1 explicitly says more statement pages follow. Page 2 may say it is
        # final, but admitting it alone still omits page 1. Neither is a complete
        # statement population for reconciliation or retained evidence.
        self.first_nonfinal_payload = with_pagination("1", "false")
        self.second_final_payload = with_pagination("2", "true")
        # A one-page statement is complete. Production may either support this
        # pagination form with exact provenance or fail closed on StmtPgntn until
        # a pagination aggregate exists.
        self.standalone_complete_payload = with_pagination("1", "true")

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
        """Return a parsed statement, or None when pagination is intentionally unsupported."""
        try:
            return parse_bank_statement_payload(payload, CAMT053_MESSAGE_DEFINITION)
        except AccountingValidationError:
            return None

    def _command(self, payload: bytes, suffix: str) -> dict[str, str]:
        """Build one supported ingest command without changing accounting identity."""
        return {
            "tenant_reference": self.case.policy.tenant_reference,
            "bank_account_reference": self.bank_account_reference,
            "ingestion_idempotency_key": f"statement-pagination-{suffix}-{uuid.uuid4().hex}",
            "message_definition_identifier": CAMT053_MESSAGE_DEFINITION,
            "statement_payload": payload.decode("utf-8"),
        }

    def test_parser_rejects_pages_that_are_not_a_complete_statement_population(self) -> None:
        """A non-final page or a later final page must not become standalone statement truth."""
        for payload in (self.first_nonfinal_payload, self.second_final_payload):
            with self.subTest(payload=payload):
                with self.assertRaises(AccountingValidationError):
                    parse_bank_statement_payload(payload, CAMT053_MESSAGE_DEFINITION)

    def test_supported_ingest_rejects_incomplete_pagination_before_retained_truth(self) -> None:
        """Supported admission must not retain a partial page as a complete bank statement."""
        for suffix, payload in (
            ("first-nonfinal", self.first_nonfinal_payload),
            ("second-final", self.second_final_payload),
        ):
            with self.subTest(suffix=suffix):
                with self.assertRaises(AccountingValidationError):
                    accept_bank_statement_evidence(
                        self._command(payload, suffix),
                        posting.DATABASE_URL,
                        self.case.policy.tenant_reference,
                        artifact_store=self.store,
                    )

    def test_standalone_complete_pagination_is_rejected_or_preserved_exactly(self) -> None:
        """If page 1/last-page is supported, both pagination facts are immutable evidence."""
        statement = self._parse_or_reject(self.standalone_complete_payload)
        if statement is None:
            with self.assertRaises(AccountingValidationError):
                accept_bank_statement_evidence(
                    self._command(self.standalone_complete_payload, "standalone-rejected"),
                    posting.DATABASE_URL,
                    self.case.policy.tenant_reference,
                    artifact_store=self.store,
                )
            return

        self.assertTrue(hasattr(statement, "statement_page_number"))
        self.assertTrue(hasattr(statement, "statement_last_page_indicator"))
        self.assertEqual(str(statement.statement_page_number), "1")
        self.assertIs(statement.statement_last_page_indicator, True)

        accepted = accept_bank_statement_evidence(
            self._command(self.standalone_complete_payload, "standalone-supported"),
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
            artifact_store=self.store,
        )
        document = lookup_bank_statement(
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
            str(accepted["bank_statement_record_id"]),
        )
        self.assertEqual(str(document["statement_page_number"]), "1")
        self.assertIs(document["statement_last_page_indicator"], True)


if __name__ == "__main__":
    unittest.main()
