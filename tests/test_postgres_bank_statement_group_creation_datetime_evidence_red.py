"""PostgreSQL REDs for camt.053 GroupHeader CreationDateTime evidence."""

from __future__ import annotations

import unittest
import uuid
from datetime import datetime

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


class BankStatementGroupCreationDateTimeEvidenceRedTests(unittest.TestCase):
    """Retain present GrpHdr/CreDtTm as immutable statement-source provenance."""

    @classmethod
    def setUpClass(cls) -> None:
        """Reuse the canonical PostgreSQL integration fixture."""
        posting.PostgresPostingTests.setUpClass()

    def setUp(self) -> None:
        """Create statements differing only in GroupHeader CreationDateTime."""
        self.case = posting.PostgresPostingTests("setUp")
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.addCleanup(self.case.tearDown)
        fixture = load_canonical_statement_fixture().decode("utf-8")

        original = "      <CreDtTm>2026-08-24T09:00:00+00:00</CreDtTm>"
        self.assertEqual(fixture.count(original), 1)
        self.first_group_message_created_at = "2026-08-24T09:00:00+00:00"
        self.second_group_message_created_at = "2026-08-24T09:00:01+00:00"
        self.invalid_group_message_created_at = "2026-99-99T99:99:99+00:00"
        self.first_payload = fixture.replace(
            original,
            f"      <CreDtTm>{self.first_group_message_created_at}</CreDtTm>",
            1,
        ).encode("utf-8")
        self.second_payload = fixture.replace(
            original,
            f"      <CreDtTm>{self.second_group_message_created_at}</CreDtTm>",
            1,
        ).encode("utf-8")
        self.invalid_payload = fixture.replace(
            original,
            f"      <CreDtTm>{self.invalid_group_message_created_at}</CreDtTm>",
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

    def test_group_creation_datetime_changes_normalized_statement_identity(self) -> None:
        """Changing only GrpHdr/CreDtTm changes normalized statement evidence identity."""
        self.assertNotEqual(self.first_payload, self.second_payload)
        self.assertNotEqual(
            self.first_statement.source_artifact_hash,
            self.second_statement.source_artifact_hash,
        )
        self.assertNotEqual(
            self.first_statement.normalized_payload_hash,
            self.second_statement.normalized_payload_hash,
        )

    def test_same_statement_identity_cannot_replay_changed_group_creation_datetime(self) -> None:
        """A changed GroupHeader creation instant requires correction, not silent replay."""
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

    def test_statement_lookup_preserves_group_creation_datetime(self) -> None:
        """Buyer-visible statement reads expose the retained GroupHeader creation instant."""
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

        self.assertIn("group_message_created_at", document)
        actual = datetime.fromisoformat(
            str(document["group_message_created_at"]).replace("Z", "+00:00")
        )
        expected = datetime.fromisoformat(self.first_group_message_created_at)
        self.assertEqual(actual, expected)

    def test_invalid_group_creation_datetime_fails_closed_in_adapter(self) -> None:
        """Malformed GrpHdr/CreDtTm cannot enter normalized statement evidence."""
        with self.assertRaises(AccountingValidationError):
            parse_bank_statement_payload(
                self.invalid_payload,
                CAMT053_MESSAGE_DEFINITION,
            )

    def test_invalid_group_creation_datetime_fails_closed_before_postgres_retention(self) -> None:
        """Supported ingest rejects malformed source-message creation time before retention."""
        with self.assertRaises(AccountingValidationError):
            accept_bank_statement_evidence(
                self._command(self.invalid_payload, "invalid"),
                posting.DATABASE_URL,
                self.case.policy.tenant_reference,
                artifact_store=self.store,
            )

    def _command(self, payload: bytes, suffix: str) -> dict[str, object]:
        """Return one supported ingest command with a fresh replay key."""
        return {
            "tenant_reference": self.case.policy.tenant_reference,
            "bank_account_reference": self.bank_account_reference,
            "ingestion_idempotency_key": f"group-created-at-{suffix}-{uuid.uuid4().hex}",
            "message_definition_identifier": CAMT053_MESSAGE_DEFINITION,
            "statement_payload": payload.decode("utf-8"),
        }


if __name__ == "__main__":
    unittest.main()
