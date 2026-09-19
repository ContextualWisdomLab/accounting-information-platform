"""REDs for fail-closed camt.053 related-corporate-action official-event admission."""

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

_UNSUPPORTED_OFFICIAL_EVENT_ERROR = (
    r"^related corporate action official event identification is not supported\. "
    r"Remove RltdCorpActn/OffclCorpActnEvtId or use the supported CorpActnEvtId only, "
    r"then retry ingest\.$"
)


class BankStatementDetailCorporateActionOfficialEventAdmissionRedTests(unittest.TestCase):
    """Reject schema-admitted official event IDs until their semantics are preserved."""

    @classmethod
    def setUpClass(cls) -> None:
        """Reuse the canonical real-PostgreSQL integration fixture."""
        posting.PostgresPostingTests.setUpClass()

    def setUp(self) -> None:
        """Prepare one supported corporate-action base and one official-ID extension."""
        self.case = posting.PostgresPostingTests("setUp")
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.addCleanup(self.case.tearDown)

        fixture = load_canonical_statement_fixture().decode("utf-8")
        marker = (
            "            <RmtInf>\n"
            "              <Ustrd>Invoice 1001</Ustrd>\n"
            "            </RmtInf>\n"
        )
        self.assertEqual(fixture.count(marker), 1)

        base_corporate_action = (
            "            <RltdCorpActn>\n"
            "              <EvtTp>\n"
            "                <Cd>DVCA</Cd>\n"
            "              </EvtTp>\n"
            "              <CorpActnEvtId>EVT-2026-001</CorpActnEvtId>\n"
            "            </RltdCorpActn>\n"
        )
        official_event_corporate_action = (
            "            <RltdCorpActn>\n"
            "              <EvtTp>\n"
            "                <Cd>DVCA</Cd>\n"
            "              </EvtTp>\n"
            "              <CorpActnEvtId>EVT-2026-001</CorpActnEvtId>\n"
            "              <OffclCorpActnEvtId>OFFICIAL-EVT-2026-001</OffclCorpActnEvtId>\n"
            "            </RltdCorpActn>\n"
        )
        self.base_payload = fixture.replace(
            marker,
            marker + base_corporate_action,
            1,
        ).encode("utf-8")
        self.official_event_payload = fixture.replace(
            marker,
            marker + official_event_corporate_action,
            1,
        ).encode("utf-8")

        self.base_statement = parse_bank_statement_payload(
            self.base_payload,
            CAMT053_MESSAGE_DEFINITION,
        )
        self.bank_account_reference = f"urn:cwl:bank_account:{uuid.uuid4().hex}"
        accept_bank_account_record(
            {
                "tenant_reference": self.case.policy.tenant_reference,
                "bank_account_reference": self.bank_account_reference,
                "account_currency_code": self.base_statement.account_currency_code,
                "account_identifier_hash": self.base_statement.account_identifier_hash,
            },
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
        )
        self.store = MemoryArtifactStore()

    def test_direct_parser_rejects_unpreserved_official_corporate_action_event_id(self) -> None:
        """Parser fails closed instead of silently dropping OffclCorpActnEvtId."""
        with self.assertRaisesRegex(
            AccountingValidationError,
            _UNSUPPORTED_OFFICIAL_EVENT_ERROR,
        ):
            parse_bank_statement_payload(
                self.official_event_payload,
                CAMT053_MESSAGE_DEFINITION,
            )

    def test_supported_ingest_rejects_official_event_id_before_it_can_alias_base_evidence(
        self,
    ) -> None:
        """A valid base remains admissible while its unpreserved official ID fails closed."""
        accepted = accept_bank_statement_evidence(
            self._command(self.base_payload, "base"),
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
            artifact_store=self.store,
        )
        self.assertFalse(accepted["replayed"])

        with self.assertRaisesRegex(
            AccountingValidationError,
            _UNSUPPORTED_OFFICIAL_EVENT_ERROR,
        ):
            accept_bank_statement_evidence(
                self._command(self.official_event_payload, "official-event"),
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
                f"detail-related-corporate-action-official-{suffix}-{uuid.uuid4().hex}"
            ),
            "message_definition_identifier": CAMT053_MESSAGE_DEFINITION,
            "statement_payload": payload.decode("utf-8"),
        }


if __name__ == "__main__":
    unittest.main()
