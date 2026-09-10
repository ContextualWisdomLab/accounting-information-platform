"""PostgreSQL REDs for camt.053 GroupHeader MessageIdentification evidence."""

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


class BankStatementGroupMessageIdentityEvidenceRedTests(unittest.TestCase):
    """Retain present GrpHdr/MsgId as immutable statement-source provenance."""

    @classmethod
    def setUpClass(cls) -> None:
        """Reuse the canonical PostgreSQL integration fixture."""
        posting.PostgresPostingTests.setUpClass()

    def setUp(self) -> None:
        """Create two statements differing only in GroupHeader MessageIdentification."""
        self.case = posting.PostgresPostingTests("setUp")
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.addCleanup(self.case.tearDown)
        fixture = load_canonical_statement_fixture().decode("utf-8")

        original = "      <MsgId>STMT-2026-08-24-001</MsgId>"
        self.assertEqual(fixture.count(original), 1)
        self.first_group_message_identity_reference = "GROUP-MSG-FIRST"
        self.second_group_message_identity_reference = "GROUP-MSG-SECOND"
        self.first_payload = fixture.replace(
            original,
            f"      <MsgId>{self.first_group_message_identity_reference}</MsgId>",
            1,
        ).encode("utf-8")
        self.second_payload = fixture.replace(
            original,
            f"      <MsgId>{self.second_group_message_identity_reference}</MsgId>",
            1,
        ).encode("utf-8")

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

    def test_group_message_identity_changes_normalized_statement_identity(self) -> None:
        """Changing only GrpHdr/MsgId changes the normalized statement evidence digest."""
        self.assertNotEqual(self.first_payload, self.second_payload)
        self.assertNotEqual(
            self.first_statement.source_artifact_hash,
            self.second_statement.source_artifact_hash,
        )
        self.assertNotEqual(
            self.first_statement.normalized_payload_hash,
            self.second_statement.normalized_payload_hash,
        )

    def test_same_statement_identity_cannot_replay_changed_group_message_identity(self) -> None:
        """A changed GroupHeader message identity requires correction, not silent replay."""
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

    def test_statement_lookup_preserves_group_message_identity(self) -> None:
        """Buyer-visible statement reads expose the retained GroupHeader message identity."""
        accepted = accept_bank_statement_evidence(
            self._command(self.first_payload, "lookup"),
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
            artifact_store=self.store,
        )

        document = lookup_bank_statement(
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
            str(accepted["bank_statement_record_id"]),
        )

        self.assertIn("group_message_identity_reference", document)
        self.assertEqual(
            document["group_message_identity_reference"],
            self.first_group_message_identity_reference,
        )

    def _command(self, payload: bytes, suffix: str) -> dict[str, object]:
        """Return one supported ingest command with a fresh replay key."""
        return {
            "tenant_reference": self.case.policy.tenant_reference,
            "bank_account_reference": self.bank_account_reference,
            "ingestion_idempotency_key": f"group-message-{suffix}-{uuid.uuid4().hex}",
            "message_definition_identifier": CAMT053_MESSAGE_DEFINITION,
            "statement_payload": payload.decode("utf-8"),
        }


if __name__ == "__main__":
    unittest.main()
