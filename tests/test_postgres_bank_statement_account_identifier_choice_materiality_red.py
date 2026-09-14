"""PostgreSQL RED for account-identification choice materiality."""

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


class BankStatementAccountIdentifierChoiceMaterialityRedTests(unittest.TestCase):
    """Keep IBAN and Other identities distinct even when their text is identical."""

    @classmethod
    def setUpClass(cls) -> None:
        """Reuse the canonical real-PostgreSQL integration fixture."""
        posting.PostgresPostingTests.setUpClass()

    def setUp(self) -> None:
        """Prepare two lawful account-identification choices with the same lexical value."""
        self.case = posting.PostgresPostingTests("setUp")
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.addCleanup(self.case.tearDown)

        fixture = load_canonical_statement_fixture().decode("utf-8")
        marker = (
            "<Id>\n"
            "          <Othr>\n"
            "            <Id>acct-opaque-fixture-only</Id>\n"
            "          </Othr>\n"
            "        </Id>"
        )
        self.assertEqual(fixture.count(marker), 1)
        self.same_identifier = "DE89370400440532013000"
        iban_identifier = (
            "<Id>\n"
            f"          <IBAN>{self.same_identifier}</IBAN>\n"
            "        </Id>"
        )
        other_identifier = (
            "<Id>\n"
            "          <Othr>\n"
            f"            <Id>{self.same_identifier}</Id>\n"
            "          </Othr>\n"
            "        </Id>"
        )
        self.iban_payload = fixture.replace(marker, iban_identifier, 1).encode("utf-8")
        self.other_payload = fixture.replace(marker, other_identifier, 1).encode("utf-8")
        self.iban_statement = parse_bank_statement_payload(
            self.iban_payload,
            CAMT053_MESSAGE_DEFINITION,
        )
        self.other_statement = parse_bank_statement_payload(
            self.other_payload,
            CAMT053_MESSAGE_DEFINITION,
        )
        self.bank_account_reference = f"urn:cwl:bank_account:{uuid.uuid4().hex}"
        self.store = MemoryArtifactStore()

    def test_identifier_choice_discriminator_is_material_to_account_and_statement_identity(self) -> None:
        """The same text under IBAN and Other cannot provenance-alias."""
        self.assertNotEqual(
            self.iban_statement.source_artifact_hash,
            self.other_statement.source_artifact_hash,
        )
        self.assertNotEqual(
            self.iban_statement.account_identifier_hash,
            self.other_statement.account_identifier_hash,
        )
        self.assertNotEqual(
            self.iban_statement.normalized_payload_hash,
            self.other_statement.normalized_payload_hash,
        )

    def test_other_choice_cannot_reuse_an_account_registered_from_iban_choice(self) -> None:
        """Supported ingest binds the durable bank account to the exact identifier choice."""
        accept_bank_account_record(
            {
                "tenant_reference": self.case.policy.tenant_reference,
                "bank_account_reference": self.bank_account_reference,
                "account_currency_code": "KRW",
                "account_identifier_hash": self.iban_statement.account_identifier_hash,
            },
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
        )
        first = accept_bank_statement_evidence(
            self._command(self.iban_payload, "iban"),
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
            artifact_store=self.store,
        )
        self.assertFalse(first["replayed"])

        with self.assertRaisesRegex(
            AccountingValidationError,
            r"^statement account identifier does not match the registered bank account\.",
        ):
            accept_bank_statement_evidence(
                self._command(self.other_payload, "other"),
                posting.DATABASE_URL,
                self.case.policy.tenant_reference,
                artifact_store=self.store,
            )

    def _command(self, payload: bytes, suffix: str) -> dict[str, object]:
        """Return one supported ingest command with an independent replay key."""
        return {
            "tenant_reference": self.case.policy.tenant_reference,
            "bank_account_reference": self.bank_account_reference,
            "ingestion_idempotency_key": (
                f"account-id-choice-materiality-{suffix}-{uuid.uuid4().hex}"
            ),
            "message_definition_identifier": CAMT053_MESSAGE_DEFINITION,
            "statement_payload": payload.decode("utf-8"),
        }


if __name__ == "__main__":
    unittest.main()
