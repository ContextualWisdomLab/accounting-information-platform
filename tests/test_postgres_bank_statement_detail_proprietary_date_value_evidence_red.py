"""Focused PostgreSQL RED for proprietary TransactionDates3 value materiality."""

from __future__ import annotations

import copy
import re
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
from tests.test_postgres_bank_statement_detail_interbank_settlement_date_evidence_red import (
    BankStatementDetailRelatedDatesEvidenceRedTests as RelatedDatesRed,
)

_CORRECTION_ERROR = re.compile(
    r"^statement identity already exists with different entry evidence\. "
    r"Use an explicit correction contract, then retry ingest\.$"
)


class BankStatementDetailProprietaryDateValueEvidenceRedTests(unittest.TestCase):
    """Prove proprietary date values are material independently of type, choice, and order."""

    @classmethod
    def setUpClass(cls) -> None:
        """Reuse the canonical real-PostgreSQL integration fixture."""
        posting.PostgresPostingTests.setUpClass()

    def setUp(self) -> None:
        """Build payloads that differ only in one proprietary TransactionDates3 value."""
        self.case = posting.PostgresPostingTests("setUp")
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.addCleanup(self.case.tearDown)

        fixture = load_canonical_statement_fixture().decode("utf-8")
        marker = (
            "            <RmtInf>\n"
            "              <Ustrd>Invoice 1001</Ustrd>\n"
            "            </RmtInf>"
        )
        self.assertEqual(fixture.count(marker), 1)

        self.base_semantics = {
            "acceptance_datetime": "2026-08-22T08:30:00Z",
            "trade_activity_contractual_settlement_date": "2026-08-23",
            "trade_date": "2026-08-22",
            "interbank_settlement_date": "2026-08-24",
            "start_date": "2026-08-01",
            "end_date": "2026-08-31",
            "transaction_datetime": "2026-08-22T08:31:00Z",
            "proprietary_dates": [
                {
                    "type": "BANK_CUTOFF",
                    "date_choice": "DtTm",
                    "date_value": "2026-08-22T17:00:00Z",
                },
                {
                    "type": "BANK_BUSINESS_DATE",
                    "date_choice": "Dt",
                    "date_value": "2026-08-22",
                },
            ],
        }
        self.changed_semantics = copy.deepcopy(self.base_semantics)
        self.changed_semantics["proprietary_dates"][0]["date_value"] = (
            "2026-08-22T17:00:01Z"
        )

        self.base_payload = RelatedDatesRed._with_related_dates(
            fixture,
            marker,
            self.base_semantics,
        )
        self.changed_payload = RelatedDatesRed._with_related_dates(
            fixture,
            marker,
            self.changed_semantics,
        )
        self.base_statement = parse_bank_statement_payload(
            self.base_payload,
            CAMT053_MESSAGE_DEFINITION,
        )
        self.changed_statement = parse_bank_statement_payload(
            self.changed_payload,
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

    def test_proprietary_date_value_is_independently_material(self) -> None:
        """Changing only Prtry/Dt/DtTm value must reach the purpose and statement hash chain."""
        base_hash = RelatedDatesRed._expected_hash(self.base_semantics)
        changed_hash = RelatedDatesRed._expected_hash(self.changed_semantics)
        base_entry = self.base_statement.entries[0]
        changed_entry = self.changed_statement.entries[0]
        base_detail = base_entry.entry_details[0]
        changed_detail = changed_entry.entry_details[0]

        self.assertNotEqual(base_hash, changed_hash)
        self.assertEqual(getattr(base_detail, "related_dates_evidence_hash", None), base_hash)
        self.assertEqual(
            getattr(changed_detail, "related_dates_evidence_hash", None),
            changed_hash,
        )
        RelatedDatesRed._assert_entry_hash_binding(base_entry, base_hash)
        RelatedDatesRed._assert_entry_hash_binding(changed_entry, changed_hash)

        self.assertEqual(
            self.base_statement.account_identifier_hash,
            self.changed_statement.account_identifier_hash,
        )
        self.assertEqual(base_entry.entry_amount, changed_entry.entry_amount)
        self.assertEqual(base_detail.detail_amount, changed_detail.detail_amount)
        self.assertNotEqual(base_detail.source_detail_hash, changed_detail.source_detail_hash)
        self.assertNotEqual(base_entry.source_entry_hash, changed_entry.source_entry_hash)
        self.assertNotEqual(
            self.base_statement.normalized_payload_hash,
            self.changed_statement.normalized_payload_hash,
        )
        self.assertEqual(
            self.base_statement.entries[1].source_entry_hash,
            self.changed_statement.entries[1].source_entry_hash,
        )

    def test_proprietary_date_value_change_requires_explicit_correction(self) -> None:
        """A one-value proprietary date change must not silently replace accepted evidence."""
        accepted = accept_bank_statement_evidence(
            self._command(self.base_payload, "base"),
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
            artifact_store=self.store,
        )
        self.assertFalse(accepted["replayed"])

        with self.assertRaisesRegex(AccountingValidationError, _CORRECTION_ERROR):
            accept_bank_statement_evidence(
                self._command(self.changed_payload, "changed-value"),
                posting.DATABASE_URL,
                self.case.policy.tenant_reference,
                artifact_store=self.store,
            )

    def _command(self, payload: bytes, suffix: str) -> dict[str, object]:
        """Return one supported ingest command with a fresh replay key."""
        return {
            "tenant_reference": self.case.policy.tenant_reference,
            "bank_account_reference": self.bank_account_reference,
            "ingestion_idempotency_key": (
                f"detail-proprietary-date-value-{suffix}-{uuid.uuid4().hex}"
            ),
            "message_definition_identifier": CAMT053_MESSAGE_DEFINITION,
            "statement_payload": payload.decode("utf-8"),
        }


if __name__ == "__main__":
    unittest.main()
