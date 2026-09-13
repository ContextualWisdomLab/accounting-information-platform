"""PostgreSQL REDs for unambiguous camt.053 entry date choices."""

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


class BankStatementEntryDateChoiceRedTests(unittest.TestCase):
    """Reject entries that populate both alternatives of an ISO date choice."""

    @classmethod
    def setUpClass(cls) -> None:
        """Reuse the canonical PostgreSQL integration fixture."""
        posting.PostgresPostingTests.setUpClass()

    def setUp(self) -> None:
        """Prepare one lawful account and source-real ambiguous-date fixtures."""
        self.case = posting.PostgresPostingTests("setUp")
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.addCleanup(self.case.tearDown)
        self.fixture = load_canonical_statement_fixture().decode("utf-8")
        self.booking_marker = (
            "<BookgDt>\n"
            "          <DtTm>2026-08-24T01:15:00+00:00</DtTm>\n"
            "        </BookgDt>"
        )
        self.value_marker = (
            "<ValDt>\n"
            "          <Dt>2026-08-24</Dt>\n"
            "        </ValDt>"
        )
        self.assertEqual(self.fixture.count(self.booking_marker), 1)
        self.assertEqual(self.fixture.count(self.value_marker), 1)

        base_statement = parse_bank_statement_payload(
            self.fixture.encode("utf-8"),
            CAMT053_MESSAGE_DEFINITION,
        )
        self.bank_account_reference = f"urn:cwl:bank_account:{uuid.uuid4().hex}"
        accept_bank_account_record(
            {
                "tenant_reference": self.case.policy.tenant_reference,
                "bank_account_reference": self.bank_account_reference,
                "account_currency_code": base_statement.account_currency_code,
                "account_identifier_hash": base_statement.account_identifier_hash,
            },
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
        )
        self.store = MemoryArtifactStore()

    def test_adapter_rejects_booking_date_with_both_date_and_datetime(self) -> None:
        """Fail closed instead of selecting one BookgDt choice arbitrarily."""
        with self.assertRaises(AccountingValidationError):
            parse_bank_statement_payload(
                self._ambiguous_booking_payload(),
                CAMT053_MESSAGE_DEFINITION,
            )

    def test_adapter_rejects_value_date_with_both_date_and_datetime(self) -> None:
        """Fail closed instead of selecting one ValDt choice arbitrarily."""
        with self.assertRaises(AccountingValidationError):
            parse_bank_statement_payload(
                self._ambiguous_value_payload(),
                CAMT053_MESSAGE_DEFINITION,
            )

    def test_supported_ingest_rejects_ambiguous_booking_date_choice(self) -> None:
        """Do not retain a statement whose booking-date evidence is ambiguous."""
        with self.assertRaises(AccountingValidationError):
            self._accept(self._ambiguous_booking_payload(), "booking")

    def test_supported_ingest_rejects_ambiguous_value_date_choice(self) -> None:
        """Do not retain a statement whose value-date evidence is ambiguous."""
        with self.assertRaises(AccountingValidationError):
            self._accept(self._ambiguous_value_payload(), "value")

    def _ambiguous_booking_payload(self) -> bytes:
        """Populate both alternatives of the first entry's BookgDt choice."""
        replacement = (
            "<BookgDt>\n"
            "          <Dt>2026-08-24</Dt>\n"
            "          <DtTm>2026-08-24T01:15:00+00:00</DtTm>\n"
            "        </BookgDt>"
        )
        return self.fixture.replace(self.booking_marker, replacement, 1).encode("utf-8")

    def _ambiguous_value_payload(self) -> bytes:
        """Populate both alternatives of the first entry's ValDt choice."""
        replacement = (
            "<ValDt>\n"
            "          <Dt>2026-08-24</Dt>\n"
            "          <DtTm>2026-08-24T00:00:00+00:00</DtTm>\n"
            "        </ValDt>"
        )
        return self.fixture.replace(self.value_marker, replacement, 1).encode("utf-8")

    def _accept(self, payload: bytes, suffix: str) -> dict[str, object]:
        """Run one source-real payload through the supported PostgreSQL ingest path."""
        return accept_bank_statement_evidence(
            {
                "tenant_reference": self.case.policy.tenant_reference,
                "bank_account_reference": self.bank_account_reference,
                "ingestion_idempotency_key": f"entry-date-choice-{suffix}-{uuid.uuid4().hex}",
                "message_definition_identifier": CAMT053_MESSAGE_DEFINITION,
                "statement_payload": payload.decode("utf-8"),
            },
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
            artifact_store=self.store,
        )


if __name__ == "__main__":
    unittest.main()
