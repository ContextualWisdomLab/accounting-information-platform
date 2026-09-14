"""RED contract for persisted transaction-detail servicer-reference projection."""

from __future__ import annotations

import hashlib
import unittest
import uuid

import psycopg

from accounting_information_platform import (
    CAMT053_MESSAGE_DEFINITION,
    MemoryArtifactStore,
    accept_bank_account_assignment,
    accept_bank_account_record,
    accept_bank_statement_evidence,
    load_canonical_statement_fixture,
    lookup_bank_statement_entries,
)
from tests import test_postgres_posting as posting

_DETAIL_REFS = b"""            <Refs>\n              <EndToEndId>E2E-1</EndToEndId>\n              <MndtId>MND-1</MndtId>\n            </Refs>"""
_DETAIL_REFS_WITH_SERVICER = b"""            <Refs>\n              <AcctSvcrRef>ASV-DETAIL-ROUNDTRIP</AcctSvcrRef>\n              <EndToEndId>E2E-1</EndToEndId>\n              <MndtId>MND-1</MndtId>\n            </Refs>"""


class BankStatementDetailServicerReferenceRoundTripRedTests(unittest.TestCase):
    """Keep retained TxDtls servicer identity visible after PostgreSQL round-trip."""

    @classmethod
    def setUpClass(cls) -> None:
        posting.PostgresPostingTests.setUpClass()

    def setUp(self) -> None:
        self.case = posting.PostgresPostingTests("setUp")
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.addCleanup(self.case.tearDown)
        self.store = MemoryArtifactStore()
        self.account_reference = f"urn:cwl:bank_account:{uuid.uuid4().hex}"
        accept_bank_account_record(
            {
                "tenant_reference": self.case.policy.tenant_reference,
                "bank_account_reference": self.account_reference,
                "account_currency_code": "KRW",
                "account_identifier": "acct-detail-servicer-roundtrip",
            },
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
        )
        accept_bank_account_assignment(
            {
                "tenant_reference": self.case.policy.tenant_reference,
                "bank_account_reference": self.account_reference,
                "legal_entity_reference": self.case.policy.legal_entity_reference,
                "accounting_book_reference": self.case.policy.accounting_book_reference,
                "chart_account_code": "110200",
                "valid_from": "2026-01-01T00:00:00Z",
                "assignment_idempotency_key": f"assign-detail-{uuid.uuid4().hex}",
            },
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
        )

    def test_detail_servicer_reference_survives_normalize_persist_and_read(self) -> None:
        """A retained detail AcctSvcrRef must not disappear from the read model."""
        fixture = load_canonical_statement_fixture()
        self.assertEqual(fixture.count(_DETAIL_REFS), 1)
        payload = fixture.replace(_DETAIL_REFS, _DETAIL_REFS_WITH_SERVICER, 1)
        command = {
            "tenant_reference": self.case.policy.tenant_reference,
            "bank_account_reference": self.account_reference,
            "ingestion_idempotency_key": f"urn:cwl:bank_statement:detail-{uuid.uuid4().hex}",
            "message_definition_identifier": CAMT053_MESSAGE_DEFINITION,
            "statement_payload": payload.decode("utf-8"),
            "source_artifact_hash": "sha256:" + hashlib.sha256(payload).hexdigest(),
        }
        document = accept_bank_statement_evidence(
            command,
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
            artifact_store=self.store,
        )

        with psycopg.connect(posting.DATABASE_URL) as connection:
            retained = connection.execute(
                """
                SELECT detail.account_servicer_reference
                FROM accounting_integration.bank_statement_entry_detail AS detail
                JOIN accounting_integration.bank_statement_entry AS entry
                  ON entry.tenant_account_id = detail.tenant_account_id
                 AND entry.bank_statement_entry_id = detail.bank_statement_entry_id
                WHERE entry.bank_statement_record_id = %s
                ORDER BY entry.entry_sequence_number, detail.detail_sequence_number
                LIMIT 1
                """,
                (document["bank_statement_record_id"],),
            ).fetchone()
        self.assertIsNotNone(retained)
        self.assertEqual(retained[0], "ASV-DETAIL-ROUNDTRIP")

        entries = lookup_bank_statement_entries(
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
            str(document["bank_statement_record_id"]),
        )["bank_statement_entries"]
        first_detail = entries[0]["entry_details"][0]
        self.assertEqual(
            first_detail["account_servicer_reference"],
            "ASV-DETAIL-ROUNDTRIP",
        )


if __name__ == "__main__":
    unittest.main()
