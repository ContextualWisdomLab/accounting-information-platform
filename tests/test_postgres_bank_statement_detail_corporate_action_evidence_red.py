"""PostgreSQL REDs for camt.053 transaction-detail related-corporate-action evidence."""

from __future__ import annotations

import hashlib
import json
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
    lookup_bank_statement_entries,
    parse_bank_statement_payload,
)
from accounting_information_platform import bank_statement
from tests import test_postgres_posting as posting

_HASH_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
_RELATED_CORPORATE_ACTION_PATH = (
    "camt.053.001.14/Stmt/Ntry/NtryDtls/TxDtls/RltdCorpActn"
)
_CORRECTION_ERROR = (
    r"^statement identity already exists with different entry evidence\. "
    r"Use an explicit correction contract, then retry ingest\.$"
)


class BankStatementDetailCorporateActionEvidenceRedTests(unittest.TestCase):
    """Retain bank-reported related-corporate-action provenance without promoting it to accounting truth."""

    @classmethod
    def setUpClass(cls) -> None:
        """Reuse the canonical real-PostgreSQL integration fixture."""
        posting.PostgresPostingTests.setUpClass()

    def setUp(self) -> None:
        """Prepare causal event-type, event-identifier, and layout variants."""
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

        self.base = {
            "event_type": "DIVIDEND",
            "corporate_action_event_identification": "EVT-2026-001",
        }
        self.event_type_changed = {**self.base, "event_type": "BONUS"}
        self.event_identification_changed = {
            **self.base,
            "corporate_action_event_identification": "EVT-2026-002",
        }

        self.base_payload = self._with_related_corporate_action(fixture, marker, self.base)
        self.base_statement = self._parse(self.base_payload)
        self.variants = {
            "event_type": self._parse(
                self._with_related_corporate_action(
                    fixture, marker, self.event_type_changed
                )
            ),
            "event_identification": self._parse(
                self._with_related_corporate_action(
                    fixture, marker, self.event_identification_changed
                )
            ),
        }

        corporate_action_xml = self._related_corporate_action_xml(self.base)
        self.assertEqual(self.base_payload.count(corporate_action_xml.encode("utf-8")), 1)
        reformatted_xml = corporate_action_xml.replace(
            "            <RltdCorpActn>\n",
            "            <RltdCorpActn>\n              \n",
            1,
        )
        self.reformatted_payload = self.base_payload.replace(
            corporate_action_xml.encode("utf-8"), reformatted_xml.encode("utf-8"), 1
        )
        self.reformatted_statement = self._parse(self.reformatted_payload)

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

    def test_each_related_corporate_action_semantic_is_material_to_identity(self) -> None:
        """Event type and corporate-action event identification are independently material."""
        base_hash = self._expected_hash(self.base)
        base_entry = self.base_statement.entries[0]
        base_detail = base_entry.entry_details[0]
        self.assertRegex(base_hash, _HASH_PATTERN)
        self.assertEqual(
            getattr(base_detail, "related_corporate_action_evidence_hash", None),
            base_hash,
        )
        self._assert_entry_hash_binding(base_entry, base_hash)

        expected_values = {
            "event_type": self.event_type_changed,
            "event_identification": self.event_identification_changed,
        }
        for label, statement in self.variants.items():
            with self.subTest(label=label):
                expected_hash = self._expected_hash(expected_values[label])
                entry = statement.entries[0]
                detail = entry.entry_details[0]
                self.assertRegex(expected_hash, _HASH_PATTERN)
                self.assertNotEqual(base_hash, expected_hash)
                self.assertEqual(
                    getattr(
                        detail, "related_corporate_action_evidence_hash", None
                    ),
                    expected_hash,
                )
                self._assert_entry_hash_binding(entry, expected_hash)
                self.assertEqual(
                    self.base_statement.account_identifier_hash,
                    statement.account_identifier_hash,
                )
                self.assertEqual(base_entry.entry_amount, entry.entry_amount)
                self.assertEqual(base_detail.detail_amount, detail.detail_amount)
                self.assertEqual(base_entry.entry_currency_code, entry.entry_currency_code)
                self.assertEqual(base_detail.detail_currency_code, detail.detail_currency_code)
                self.assertNotEqual(base_detail.source_detail_hash, detail.source_detail_hash)
                self.assertNotEqual(base_entry.source_entry_hash, entry.source_entry_hash)
                self.assertNotEqual(
                    self.base_statement.normalized_payload_hash,
                    statement.normalized_payload_hash,
                )
                self.assertEqual(
                    self.base_statement.entries[1].source_entry_hash,
                    statement.entries[1].source_entry_hash,
                )

    def test_xml_layout_does_not_change_related_corporate_action_semantics(self) -> None:
        """Layout-only XML changes raw provenance but not related-corporate-action identity."""
        expected_hash = self._expected_hash(self.base)
        base_entry = self.base_statement.entries[0]
        reformatted_entry = self.reformatted_statement.entries[0]
        base_detail = base_entry.entry_details[0]
        reformatted_detail = reformatted_entry.entry_details[0]

        self.assertNotEqual(
            self.base_statement.source_artifact_hash,
            self.reformatted_statement.source_artifact_hash,
        )
        self.assertEqual(
            getattr(
                reformatted_detail, "related_corporate_action_evidence_hash", None
            ),
            expected_hash,
        )
        self._assert_entry_hash_binding(reformatted_entry, expected_hash)
        self.assertEqual(base_detail.source_detail_hash, reformatted_detail.source_detail_hash)
        self.assertEqual(base_entry.source_entry_hash, reformatted_entry.source_entry_hash)
        self.assertEqual(
            self.base_statement.normalized_payload_hash,
            self.reformatted_statement.normalized_payload_hash,
        )

    def test_changed_related_corporate_action_requires_explicit_statement_correction(
        self,
    ) -> None:
        """Accepted related-corporate-action provenance cannot be silently replaced."""
        accepted = accept_bank_statement_evidence(
            self._command(self.base_payload, "base"),
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
            artifact_store=self.store,
        )
        self.assertFalse(accepted["replayed"])

        changed_payload = self._with_related_corporate_action(
            load_canonical_statement_fixture().decode("utf-8"),
            (
                "            <RmtInf>\n"
                "              <Ustrd>Invoice 1001</Ustrd>\n"
                "            </RmtInf>\n"
            ),
            self.event_identification_changed,
        )
        with self.assertRaisesRegex(AccountingValidationError, _CORRECTION_ERROR):
            accept_bank_statement_evidence(
                self._command(changed_payload, "changed"),
                posting.DATABASE_URL,
                self.case.policy.tenant_reference,
                artifact_store=self.store,
            )

    def test_buyer_read_preserves_related_corporate_action_without_changing_amount_truth(
        self,
    ) -> None:
        """Tenant reads expose related-corporate-action provenance without changing bank amounts."""
        expected_hash = self._expected_hash(self.base)
        accepted = accept_bank_statement_evidence(
            self._command(self.base_payload, "lookup"),
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
            artifact_store=self.store,
        )
        document = lookup_bank_statement_entries(
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
            str(accepted["bank_statement_record_id"]),
        )
        entry = document["bank_statement_entries"][0]
        detail = entry["entry_details"][0]

        self.assertEqual(
            detail.get("related_corporate_action_evidence_hash"), expected_hash
        )
        self.assertEqual(detail.get("related_corporate_action"), self.base)
        self.assertEqual(entry["entry_amount"], "25000")
        self.assertEqual(entry["entry_currency_code"], "KRW")
        self.assertEqual(detail["detail_amount"], "25000")
        self.assertEqual(detail["detail_currency_code"], "KRW")

    @staticmethod
    def _parse(payload: bytes) -> object:
        return parse_bank_statement_payload(payload, CAMT053_MESSAGE_DEFINITION)

    @staticmethod
    def _assert_entry_hash_binding(entry: object, expected_hash: str) -> None:
        """Require source_entry_hash to digest the exact related-corporate-action-bound detail hash."""
        projection = dict(bank_statement._entry_payload(entry))
        details = projection.get("details")
        if not isinstance(details, list) or not details:
            raise AssertionError("canonical entry projection must retain transaction details")
        first_detail = details[0]
        if not isinstance(first_detail, dict):
            raise AssertionError("canonical entry detail projection must be a mapping")
        if first_detail.get("related_corporate_action_evidence_hash") != expected_hash:
            raise AssertionError(
                "canonical entry projection must carry the exact "
                "related_corporate_action_evidence_hash"
            )
        preimage = json.dumps(
            projection, separators=(",", ":"), sort_keys=True
        ).encode("utf-8")
        expected_entry_hash = f"sha256:{hashlib.sha256(preimage).hexdigest()}"
        if getattr(entry, "source_entry_hash", None) != expected_entry_hash:
            raise AssertionError(
                "source_entry_hash must digest the canonical entry projection containing "
                "related_corporate_action_evidence_hash"
            )

    @staticmethod
    def _expected_hash(value: dict[str, str]) -> str:
        """Digest the admitted direct RltdCorpActn projection."""
        preimage = json.dumps(
            {
                "evidence_type": _RELATED_CORPORATE_ACTION_PATH,
                "related_corporate_action": value,
            },
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return f"sha256:{hashlib.sha256(preimage).hexdigest()}"

    @staticmethod
    def _related_corporate_action_xml(value: dict[str, str]) -> str:
        """Serialize one direct TxDtls/RltdCorpActn in V14 schema order."""
        return (
            "            <RltdCorpActn>\n"
            f"              <EvtTp>{value['event_type']}</EvtTp>\n"
            "              <CorpActnEvtId>"
            f"{value['corporate_action_event_identification']}"
            "</CorpActnEvtId>\n"
            "            </RltdCorpActn>\n"
        )

    @classmethod
    def _with_related_corporate_action(
        cls, fixture: str, marker: str, value: dict[str, str]
    ) -> bytes:
        """Insert RltdCorpActn after RmtInf while skipping intervening optional V14 elements."""
        if fixture.count(marker) != 1:
            raise AssertionError("canonical first-detail RmtInf marker must occur exactly once")
        return fixture.replace(
            marker,
            marker + cls._related_corporate_action_xml(value),
            1,
        ).encode("utf-8")

    def _command(self, payload: bytes, suffix: str) -> dict[str, object]:
        """Build one supported ingest command with a unique replay key."""
        return {
            "tenant_reference": self.case.policy.tenant_reference,
            "bank_account_reference": self.bank_account_reference,
            "ingestion_idempotency_key": (
                f"detail-related-corporate-action-{suffix}-{uuid.uuid4().hex}"
            ),
            "message_definition_identifier": CAMT053_MESSAGE_DEFINITION,
            "statement_payload": payload.decode("utf-8"),
        }


if __name__ == "__main__":
    unittest.main()
