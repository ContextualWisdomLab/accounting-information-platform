"""PostgreSQL REDs for camt.053 statement CreationDateTime evidence."""

from __future__ import annotations

import unittest
import uuid
from datetime import datetime, timezone

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


class BankStatementCreationDateTimeEvidenceRedTests(unittest.TestCase):
    """Retain Stmt/CreDtTm as immutable, instant-normalized source provenance."""

    @classmethod
    def setUpClass(cls) -> None:
        """Reuse the canonical real-PostgreSQL integration fixture."""
        posting.PostgresPostingTests.setUpClass()

    def setUp(self) -> None:
        """Create statements that vary only in statement creation time semantics."""
        self.case = posting.PostgresPostingTests("setUp")
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.addCleanup(self.case.tearDown)

        fixture = load_canonical_statement_fixture().decode("utf-8")
        marker = "      <LglSeqNb>7</LglSeqNb>\n"
        self.assertEqual(fixture.count(marker), 1)

        self.first_created_at = "2026-08-24T09:05:00+00:00"
        self.same_instant_offset = "2026-08-24T18:05:00+09:00"
        self.second_created_at = "2026-08-24T09:05:01+00:00"
        self.invalid_created_at = "2026-99-99T99:99:99+00:00"

        self.first_payload = self._with_statement_created_at(
            fixture, marker, self.first_created_at
        )
        self.same_instant_offset_payload = self._with_statement_created_at(
            fixture, marker, self.same_instant_offset
        )
        self.second_payload = self._with_statement_created_at(
            fixture, marker, self.second_created_at
        )
        self.invalid_payload = self._with_statement_created_at(
            fixture, marker, self.invalid_created_at
        )

        self.first_statement = parse_bank_statement_payload(
            self.first_payload,
            CAMT053_MESSAGE_DEFINITION,
        )
        self.same_instant_offset_statement = parse_bank_statement_payload(
            self.same_instant_offset_payload,
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

    def test_statement_creation_datetime_is_material_statement_evidence(self) -> None:
        """Changing only Stmt/CreDtTm changes normalized statement evidence identity."""
        first_created_at = getattr(self.first_statement, "statement_created_at", None)
        second_created_at = getattr(self.second_statement, "statement_created_at", None)

        self.assertEqual(
            first_created_at,
            datetime(2026, 8, 24, 9, 5, 0, tzinfo=timezone.utc),
        )
        self.assertEqual(
            second_created_at,
            datetime(2026, 8, 24, 9, 5, 1, tzinfo=timezone.utc),
        )
        self.assertNotEqual(
            self.first_statement.source_artifact_hash,
            self.second_statement.source_artifact_hash,
        )
        self.assertEqual(
            self.first_statement.account_identifier_hash,
            self.second_statement.account_identifier_hash,
        )
        self.assertNotEqual(
            self.first_statement.normalized_payload_hash,
            self.second_statement.normalized_payload_hash,
        )

    def test_timezone_representation_of_same_instant_is_not_material(self) -> None:
        """Equivalent ISODateTime offsets normalize to one statement creation instant."""
        expected = datetime(2026, 8, 24, 9, 5, 0, tzinfo=timezone.utc)
        self.assertNotEqual(
            self.first_statement.source_artifact_hash,
            self.same_instant_offset_statement.source_artifact_hash,
        )
        self.assertEqual(
            getattr(self.first_statement, "statement_created_at", None),
            expected,
        )
        self.assertEqual(
            getattr(self.same_instant_offset_statement, "statement_created_at", None),
            expected,
        )
        self.assertEqual(
            self.first_statement.account_identifier_hash,
            self.same_instant_offset_statement.account_identifier_hash,
        )
        self.assertEqual(
            self.first_statement.normalized_payload_hash,
            self.same_instant_offset_statement.normalized_payload_hash,
        )

    def test_changed_statement_creation_datetime_requires_correction(self) -> None:
        """The same statement identity cannot silently replay a changed creation instant."""
        first = accept_bank_statement_evidence(
            self._command(self.first_payload, "first"),
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
            artifact_store=self.store,
        )
        self.assertFalse(first["replayed"])

        with self.assertRaisesRegex(
            AccountingValidationError,
            (
                r"^statement identity already exists with different entry evidence\. "
                r"Use an explicit correction contract, then retry ingest\.$"
            ),
        ):
            accept_bank_statement_evidence(
                self._command(self.second_payload, "second"),
                posting.DATABASE_URL,
                self.case.policy.tenant_reference,
                artifact_store=self.store,
            )

    def test_statement_lookup_preserves_statement_creation_datetime(self) -> None:
        """Buyer reads expose the exact normalized Stmt/CreDtTm admitted at ingest."""
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
        self.assertIn("statement_created_at", document)
        actual = datetime.fromisoformat(
            str(document["statement_created_at"]).replace("Z", "+00:00")
        ).astimezone(timezone.utc)
        self.assertEqual(
            actual,
            datetime(2026, 8, 24, 9, 5, 0, tzinfo=timezone.utc),
        )

    def test_invalid_statement_creation_datetime_fails_closed_in_adapter(self) -> None:
        """Malformed Stmt/CreDtTm cannot enter normalized statement evidence."""
        with self.assertRaises(AccountingValidationError):
            parse_bank_statement_payload(
                self.invalid_payload,
                CAMT053_MESSAGE_DEFINITION,
            )

    def test_invalid_statement_creation_datetime_fails_closed_before_retention(self) -> None:
        """Supported ingest rejects malformed statement creation time before persistence."""
        with self.assertRaises(AccountingValidationError):
            accept_bank_statement_evidence(
                self._command(self.invalid_payload, "invalid"),
                posting.DATABASE_URL,
                self.case.policy.tenant_reference,
                artifact_store=self.store,
            )

    @staticmethod
    def _with_statement_created_at(fixture: str, marker: str, value: str) -> bytes:
        """Insert one lawful AccountStatement15 CreationDateTime after LglSeqNb."""
        return fixture.replace(
            marker,
            marker + f"      <CreDtTm>{value}</CreDtTm>\n",
            1,
        ).encode("utf-8")

    def _command(self, payload: bytes, suffix: str) -> dict[str, object]:
        """Return one supported ingest command with an independent replay key."""
        return {
            "tenant_reference": self.case.policy.tenant_reference,
            "bank_account_reference": self.bank_account_reference,
            "ingestion_idempotency_key": (
                f"statement-created-at-{suffix}-{uuid.uuid4().hex}"
            ),
            "message_definition_identifier": CAMT053_MESSAGE_DEFINITION,
            "statement_payload": payload.decode("utf-8"),
        }


if __name__ == "__main__":
    unittest.main()
