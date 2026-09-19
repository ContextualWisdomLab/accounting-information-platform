"""REDs for camt.053 structured additional-remittance cardinality."""

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


class BankStatementStructuredAdditionalRemittanceCardinalityRedTests(unittest.TestCase):
    """Preserve the V14 AddtlRmtInf upper bound of three values per Strd block."""

    @classmethod
    def setUpClass(cls) -> None:
        """Reuse the canonical real-PostgreSQL integration fixture."""
        posting.PostgresPostingTests.setUpClass()

    def setUp(self) -> None:
        """Prepare exactly-three and four-value payloads in one structured remittance block."""
        self.case = posting.PostgresPostingTests("setUp")
        self.addCleanup(self.case.doCleanups)
        self.case.setUp()
        self.addCleanup(self.case.tearDown)

        fixture = load_canonical_statement_fixture().decode("utf-8")
        marker = "<Ustrd>Invoice 1001</Ustrd>"
        if fixture.count(marker) != 1:
            raise AssertionError("canonical Ustrd marker must occur exactly once")

        self.at_limit_payload = self._payload(fixture, marker, 3)
        self.overflow_payload = self._payload(fixture, marker, 4)

        canonical = parse_bank_statement_payload(
            fixture.encode("utf-8"), CAMT053_MESSAGE_DEFINITION
        )
        self.bank_account_reference = (
            f"urn:cwl:bank_account:structured-additional-remittance:{uuid.uuid4().hex}"
        )
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

    def test_parser_accepts_three_and_rejects_four_additional_remittance_values(self) -> None:
        """AddtlRmtInf is 0..3, so three values are valid and the fourth is not."""
        parse_bank_statement_payload(
            self.at_limit_payload,
            CAMT053_MESSAGE_DEFINITION,
        )

        with self.assertRaises(AccountingValidationError):
            parse_bank_statement_payload(
                self.overflow_payload,
                CAMT053_MESSAGE_DEFINITION,
            )

    def test_supported_ingest_rejects_fourth_additional_remittance_value(self) -> None:
        """Cardinality overflow fails before immutable statement evidence is accepted."""
        with self.assertRaises(AccountingValidationError):
            accept_bank_statement_evidence(
                {
                    "tenant_reference": self.case.policy.tenant_reference,
                    "bank_account_reference": self.bank_account_reference,
                    "ingestion_idempotency_key": (
                        "structured-additional-remittance-overflow-"
                        f"{uuid.uuid4().hex}"
                    ),
                    "message_definition_identifier": CAMT053_MESSAGE_DEFINITION,
                    "statement_payload": self.overflow_payload.decode("utf-8"),
                },
                posting.DATABASE_URL,
                self.case.policy.tenant_reference,
                artifact_store=self.store,
            )

    @staticmethod
    def _payload(fixture: str, marker: str, count: int) -> bytes:
        """Insert one Strd block with the requested AddtlRmtInf population."""
        values = "".join(
            f"                <AddtlRmtInf>Additional remittance {index:02d}</AddtlRmtInf>\n"
            for index in range(1, count + 1)
        )
        structured = (
            f"{marker}\n"
            "              <Strd>\n"
            f"{values}"
            "              </Strd>"
        )
        return fixture.replace(marker, structured, 1).encode("utf-8")


if __name__ == "__main__":
    unittest.main()
