"""PostgreSQL REDs for ambiguous camt.053 entry date-time evidence cardinality."""

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


class BankStatementEntryDateTimeCardinalityRedTests(unittest.TestCase):
    """Reject conflicting direct DtTm values inside one BookgDt or ValDt choice."""

    @classmethod
    def setUpClass(cls) -> None:
        """Reuse the canonical PostgreSQL integration fixture."""
        posting.PostgresPostingTests.setUpClass()

    def setUp(self) -> None:
        """Prepare one registered account and isolated contradictory date-time sources."""
        self.case = posting.PostgresPostingTests("setUp")
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.addCleanup(self.case.tearDown)

        self.fixture = load_canonical_statement_fixture().decode("utf-8")
        self.booking_marker = (
            "          <DtTm>2026-08-24T01:15:00+00:00</DtTm>"
        )
        self.value_marker = (
            "          <DtTm>2026-08-24T04:00:00+00:00</DtTm>"
        )
        self.assertEqual(self.fixture.count(self.booking_marker), 1)
        self.assertEqual(self.fixture.count(self.value_marker), 1)

        self.conflicting_booking_payload = self.fixture.replace(
            self.booking_marker,
            self.booking_marker
            + "\n"
            + "          <DtTm>2026-08-24T01:15:01+00:00</DtTm>",
            1,
        ).encode("utf-8")
        self.conflicting_value_payload = self.fixture.replace(
            self.value_marker,
            self.value_marker
            + "\n"
            + "          <DtTm>2026-08-24T04:00:01+00:00</DtTm>",
            1,
        ).encode("utf-8")

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

    def test_adapter_rejects_conflicting_entry_datetime_values(self) -> None:
        """Do not reduce contradictory direct date-times to document-order winners."""
        for label, payload in (
            ("booking", self.conflicting_booking_payload),
            ("value", self.conflicting_value_payload),
        ):
            with self.subTest(date_role=label):
                with self.assertRaises(AccountingValidationError):
                    parse_bank_statement_payload(
                        payload,
                        CAMT053_MESSAGE_DEFINITION,
                    )

    def test_supported_ingest_rejects_conflicting_entry_datetime_values(self) -> None:
        """Require pure-parser rejection before supported-ingest rejection can count."""
        for label, payload in (
            ("booking", self.conflicting_booking_payload),
            ("value", self.conflicting_value_payload),
        ):
            with self.subTest(date_role=label):
                with self.assertRaises(AccountingValidationError):
                    parse_bank_statement_payload(
                        payload,
                        CAMT053_MESSAGE_DEFINITION,
                    )

                with self.assertRaises(AccountingValidationError):
                    accept_bank_statement_evidence(
                        {
                            "tenant_reference": self.case.policy.tenant_reference,
                            "bank_account_reference": self.bank_account_reference,
                            "ingestion_idempotency_key": (
                                f"entry-datetime-cardinality-{label}-{uuid.uuid4().hex}"
                            ),
                            "message_definition_identifier": CAMT053_MESSAGE_DEFINITION,
                            "statement_payload": payload.decode("utf-8"),
                        },
                        posting.DATABASE_URL,
                        self.case.policy.tenant_reference,
                        artifact_store=self.store,
                    )


if __name__ == "__main__":
    unittest.main()
