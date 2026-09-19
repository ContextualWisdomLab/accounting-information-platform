"""PostgreSQL REDs for camt.053 GroupHeader OriginalBusinessQuery evidence."""

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


class BankStatementOriginalBusinessQueryEvidenceRedTests(unittest.TestCase):
    """Retain GrpHdr/OrgnlBizQry as immutable source-request provenance."""

    @classmethod
    def setUpClass(cls) -> None:
        """Reuse the canonical real-PostgreSQL integration fixture."""
        posting.PostgresPostingTests.setUpClass()

    def setUp(self) -> None:
        """Prepare statements that differ only in original-query provenance."""
        self.case = posting.PostgresPostingTests("setUp")
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.addCleanup(self.case.tearDown)

        self.fixture = load_canonical_statement_fixture().decode("utf-8")
        self.first_query_message_identity_reference = "QUERY-REQUEST-2026-08-24-001"
        self.second_query_message_identity_reference = "QUERY-REQUEST-2026-08-24-002"
        self.query_message_name_identifier = "camt.060.001.07"
        self.second_query_message_name_identifier = "camt.060.001.06"
        self.query_created_at = "2026-08-24T08:55:00+00:00"
        self.query_created_at_equivalent = "2026-08-24T17:55:00+09:00"
        self.second_query_created_at = "2026-08-24T08:55:01+00:00"

        self.first_payload = self._with_original_business_query(
            message_identity_reference=self.first_query_message_identity_reference,
            message_name_identifier=self.query_message_name_identifier,
            created_at=self.query_created_at,
        )
        self.second_payload = self._with_original_business_query(
            message_identity_reference=self.second_query_message_identity_reference,
            message_name_identifier=self.query_message_name_identifier,
            created_at=self.query_created_at,
        )
        self.name_changed_payload = self._with_original_business_query(
            message_identity_reference=self.first_query_message_identity_reference,
            message_name_identifier=self.second_query_message_name_identifier,
            created_at=self.query_created_at,
        )
        self.instant_changed_payload = self._with_original_business_query(
            message_identity_reference=self.first_query_message_identity_reference,
            message_name_identifier=self.query_message_name_identifier,
            created_at=self.second_query_created_at,
        )
        self.equivalent_instant_payload = self._with_original_business_query(
            message_identity_reference=self.first_query_message_identity_reference,
            message_name_identifier=self.query_message_name_identifier,
            created_at=self.query_created_at_equivalent,
        )

        self.first_statement = self._parse(self.first_payload)
        self.second_statement = self._parse(self.second_payload)
        self.name_changed_statement = self._parse(self.name_changed_payload)
        self.instant_changed_statement = self._parse(self.instant_changed_payload)
        self.equivalent_instant_statement = self._parse(self.equivalent_instant_payload)

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

    def test_original_business_query_fields_change_normalized_statement_identity(self) -> None:
        """MsgId, MsgNmId, and the query creation instant are material provenance."""
        for label, changed_statement in (
            ("message-identity", self.second_statement),
            ("message-name", self.name_changed_statement),
            ("creation-instant", self.instant_changed_statement),
        ):
            with self.subTest(field=label):
                self.assertEqual(
                    self.first_statement.account_identifier_hash,
                    changed_statement.account_identifier_hash,
                )
                self.assertNotEqual(
                    self.first_statement.source_artifact_hash,
                    changed_statement.source_artifact_hash,
                )
                self.assertNotEqual(
                    self.first_statement.normalized_payload_hash,
                    changed_statement.normalized_payload_hash,
                )

    def test_original_business_query_datetime_is_instant_semantic(self) -> None:
        """Equivalent ISODateTime offsets retain one normalized query provenance instant."""
        self.assertNotEqual(self.first_payload, self.equivalent_instant_payload)
        self.assertNotEqual(
            self.first_statement.source_artifact_hash,
            self.equivalent_instant_statement.source_artifact_hash,
        )
        self.assertEqual(
            self.first_statement.normalized_payload_hash,
            self.equivalent_instant_statement.normalized_payload_hash,
        )

    def test_same_statement_identity_cannot_replay_changed_original_query(self) -> None:
        """A changed original-query identity requires correction, not silent replay."""
        accept_bank_statement_evidence(
            self._command(self.first_payload, "first"),
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
            artifact_store=self.store,
        )

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

    def test_statement_lookup_preserves_original_business_query(self) -> None:
        """Authorized statement reads expose the retained original-query provenance."""
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

        self.assertEqual(
            document["original_business_query_message_identity_reference"],
            self.first_query_message_identity_reference,
        )
        self.assertEqual(
            document["original_business_query_message_name_identifier"],
            self.query_message_name_identifier,
        )
        actual_created_at = datetime.fromisoformat(
            str(document["original_business_query_created_at"]).replace("Z", "+00:00")
        )
        expected_created_at = datetime.fromisoformat(self.query_created_at)
        self.assertEqual(actual_created_at, expected_created_at)

    def _parse(self, payload: bytes):
        """Parse one test payload through the supported adapter."""
        return parse_bank_statement_payload(payload, CAMT053_MESSAGE_DEFINITION)

    def _command(self, payload: bytes, suffix: str) -> dict[str, object]:
        """Build one supported ingest command with a fresh replay identity."""
        return {
            "tenant_reference": self.case.policy.tenant_reference,
            "bank_account_reference": self.bank_account_reference,
            "ingestion_idempotency_key": f"original-business-query-{suffix}-{uuid.uuid4().hex}",
            "message_definition_identifier": CAMT053_MESSAGE_DEFINITION,
            "statement_payload": payload.decode("utf-8"),
        }

    def _with_original_business_query(
        self,
        *,
        message_identity_reference: str,
        message_name_identifier: str,
        created_at: str,
    ) -> bytes:
        """Insert one schema-shaped OriginalBusinessQuery1 after GroupHeader creation time."""
        marker = (
            "      <CreDtTm>2026-08-24T09:00:00+00:00</CreDtTm>\n"
            "    </GrpHdr>"
        )
        if self.fixture.count(marker) != 1:
            raise AssertionError("canonical fixture must contain exactly one GroupHeader terminator")
        return self.fixture.replace(
            marker,
            "      <CreDtTm>2026-08-24T09:00:00+00:00</CreDtTm>\n"
            "      <OrgnlBizQry>\n"
            f"        <MsgId>{message_identity_reference}</MsgId>\n"
            f"        <MsgNmId>{message_name_identifier}</MsgNmId>\n"
            f"        <CreDtTm>{created_at}</CreDtTm>\n"
            "      </OrgnlBizQry>\n"
            "    </GrpHdr>",
            1,
        ).encode("utf-8")


if __name__ == "__main__":
    unittest.main()
