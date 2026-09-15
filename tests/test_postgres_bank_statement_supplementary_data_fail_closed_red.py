"""PostgreSQL REDs for uncontracted camt.053 statement supplementary data."""

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

_EXTENSION_ERROR = (
    r"^Stmt/SplmtryData is unsupported until a versioned extension contract is configured\. "
    r"Configure the extension contract, then retry ingest\.$"
)


class BankStatementSupplementaryDataFailClosedRedTests(unittest.TestCase):
    """Reject statement extensions that AIP cannot yet retain and interpret safely."""

    @classmethod
    def setUpClass(cls) -> None:
        """Reuse the canonical real-PostgreSQL integration fixture."""
        posting.PostgresPostingTests.setUpClass()

    def setUp(self) -> None:
        """Prepare one valid statement plus a schema-shaped statement extension."""
        self.case = posting.PostgresPostingTests("setUp")
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.addCleanup(self.case.tearDown)

        self.baseline_payload = load_canonical_statement_fixture()
        baseline = parse_bank_statement_payload(
            self.baseline_payload,
            CAMT053_MESSAGE_DEFINITION,
        )
        self.extended_payload = self._with_statement_supplementary_data(
            self.baseline_payload.decode("utf-8")
        )
        self.bank_account_reference = f"urn:cwl:bank_account:{uuid.uuid4().hex}"
        accept_bank_account_record(
            {
                "tenant_reference": self.case.policy.tenant_reference,
                "bank_account_reference": self.bank_account_reference,
                "account_currency_code": baseline.account_currency_code,
                "account_identifier_hash": baseline.account_identifier_hash,
            },
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
        )
        self.store = MemoryArtifactStore()

    def test_statement_supplementary_data_fails_closed_before_normalization(self) -> None:
        """Unknown Stmt/SplmtryData cannot disappear into the baseline statement identity."""
        with self.assertRaisesRegex(AccountingValidationError, _EXTENSION_ERROR):
            parse_bank_statement_payload(
                self.extended_payload,
                CAMT053_MESSAGE_DEFINITION,
            )

    def test_rejected_statement_extension_is_not_retained_as_ingested_evidence(self) -> None:
        """Fail closed before an unsupported extension can become durable statement evidence."""
        with self.assertRaisesRegex(AccountingValidationError, _EXTENSION_ERROR):
            accept_bank_statement_evidence(
                {
                    "tenant_reference": self.case.policy.tenant_reference,
                    "bank_account_reference": self.bank_account_reference,
                    "ingestion_idempotency_key": (
                        f"statement-supplementary-data-{uuid.uuid4().hex}"
                    ),
                    "message_definition_identifier": CAMT053_MESSAGE_DEFINITION,
                    "statement_payload": self.extended_payload.decode("utf-8"),
                },
                posting.DATABASE_URL,
                self.case.policy.tenant_reference,
                artifact_store=self.store,
            )

        self.assertEqual(
            self.store._artifacts,
            {},
            "unsupported extension bytes must not be retained as accepted evidence",
        )

    def test_supported_statement_without_extension_remains_parseable(self) -> None:
        """The fail-closed boundary must not reject the canonical supported profile."""
        statement = parse_bank_statement_payload(
            self.baseline_payload,
            CAMT053_MESSAGE_DEFINITION,
        )
        self.assertEqual(
            statement.message_definition_identifier,
            CAMT053_MESSAGE_DEFINITION,
        )
        self.assertTrue(statement.entries)

    @staticmethod
    def _with_statement_supplementary_data(fixture: str) -> bytes:
        """Add one lawful SupplementaryData1 envelope at the end of Stmt."""
        marker = "    </Stmt>\n  </BkToCstmrStmt>"
        if fixture.count(marker) != 1:
            raise AssertionError("canonical fixture must contain exactly one statement terminator")
        return fixture.replace(
            marker,
            "      <SplmtryData>\n"
            "        <PlcAndNm>/Document/BkToCstmrStmt/Stmt</PlcAndNm>\n"
            "        <Envlp>\n"
            "          <cwl:ReconciliationEvidence "
            'xmlns:cwl="urn:contextualwisdomlab:accounting:statement-evidence:v1">\n'
            "            <cwl:ControlReference>supplementary-control-001</cwl:ControlReference>\n"
            "          </cwl:ReconciliationEvidence>\n"
            "        </Envlp>\n"
            "      </SplmtryData>\n"
            "    </Stmt>\n"
            "  </BkToCstmrStmt>",
            1,
        ).encode("utf-8")


if __name__ == "__main__":
    unittest.main()
