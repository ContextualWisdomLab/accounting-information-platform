"""PostgreSQL REDs for proprietary bank-account servicer identity."""

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


class BankStatementAccountServicerIdentityRedTests(unittest.TestCase):
    """Scope proprietary Acct/Id/Othr identifiers by the reporting account servicer."""

    @classmethod
    def setUpClass(cls) -> None:
        """Reuse the canonical PostgreSQL integration fixture."""
        posting.PostgresPostingTests.setUpClass()

    def setUp(self) -> None:
        """Create two statements whose proprietary account id differs only by servicer BIC."""
        self.case = posting.PostgresPostingTests("setUp")
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.addCleanup(self.case.tearDown)
        fixture = load_canonical_statement_fixture().decode("utf-8")
        marker = "        <Ccy>KRW</Ccy>\n      </Acct>"
        self.assertEqual(fixture.count(marker), 1)
        self.first_servicer_bic = "AAAAKRSEXXX"
        self.second_servicer_bic = "BBBBKRSEXXX"
        self.first_payload = self._with_account_servicer(
            fixture,
            marker,
            self.first_servicer_bic,
        )
        self.second_payload = self._with_account_servicer(
            fixture,
            marker,
            self.second_servicer_bic,
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

    def test_proprietary_account_identity_is_scoped_by_account_servicer(self) -> None:
        """The same Acct/Id/Othr at two servicers is not one bank-account identity."""
        self.assertNotEqual(
            self.first_statement.account_identifier_hash,
            self.second_statement.account_identifier_hash,
        )

    def test_registered_account_rejects_same_proprietary_id_from_another_servicer(self) -> None:
        """A different servicing institution cannot alias into the registered bank account."""
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

    def _with_account_servicer(self, fixture: str, marker: str, bic: str) -> bytes:
        """Insert one schema-shaped Acct/Svcr/FinInstnId/BICFI after account currency."""
        return fixture.replace(
            marker,
            "        <Ccy>KRW</Ccy>\n"
            "        <Svcr>\n"
            "          <FinInstnId>\n"
            f"            <BICFI>{bic}</BICFI>\n"
            "          </FinInstnId>\n"
            "        </Svcr>\n"
            "      </Acct>",
            1,
        ).encode("utf-8")

    def _command(self, payload: bytes, suffix: str) -> dict[str, object]:
        """Return one supported ingest command with a fresh replay key."""
        return {
            "tenant_reference": self.case.policy.tenant_reference,
            "bank_account_reference": self.bank_account_reference,
            "ingestion_idempotency_key": f"account-servicer-identity-{suffix}-{uuid.uuid4().hex}",
            "message_definition_identifier": CAMT053_MESSAGE_DEFINITION,
            "statement_payload": payload.decode("utf-8"),
        }


if __name__ == "__main__":
    unittest.main()
