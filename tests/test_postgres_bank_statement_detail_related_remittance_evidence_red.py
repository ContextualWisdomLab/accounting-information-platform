"""PostgreSQL REDs for camt.053 transaction-detail related-remittance evidence."""

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
_RELATED_REMITTANCE_PURPOSE = "camt.053.001.14/Stmt/Ntry/NtryDtls/TxDtls/RltdRmtInf"
_CORRECTION_ERROR = (
    r"^statement identity already exists with different entry evidence\. "
    r"Use an explicit correction contract, then retry ingest\.$"
)

RemittanceLocation = tuple[str, str]
RelatedRemittance = tuple[str, tuple[RemittanceLocation, ...]]


class BankStatementDetailRelatedRemittanceEvidenceRedTests(unittest.TestCase):
    """Retain source-ordered RemittanceLocation8 evidence for reconciliation consumers."""

    @classmethod
    def setUpClass(cls) -> None:
        """Reuse the canonical real-PostgreSQL integration fixture."""
        posting.PostgresPostingTests.setUpClass()

    def setUp(self) -> None:
        """Prepare related-remittance variants while holding accounting amounts constant."""
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

        self.base_records: tuple[RelatedRemittance, ...] = (
            (
                "RMT-LOC-001",
                (
                    ("EMAL", "cash-application@example.test"),
                    ("URID", "https://example.test/remittance/INV-1001"),
                ),
            ),
            (
                "RMT-LOC-002",
                (("EDIC", "urn:cwl:edi:partner:1001"),),
            ),
        )
        self.changed_identifier_records = (
            ("RMT-LOC-001B", self.base_records[0][1]),
            self.base_records[1],
        )
        self.changed_method_records = (
            (
                self.base_records[0][0],
                (
                    ("FAXI", self.base_records[0][1][0][1]),
                    self.base_records[0][1][1],
                ),
            ),
            self.base_records[1],
        )
        self.changed_address_records = (
            (
                self.base_records[0][0],
                (
                    (self.base_records[0][1][0][0], "cash-application-2@example.test"),
                    self.base_records[0][1][1],
                ),
            ),
            self.base_records[1],
        )
        self.reordered_locations_records = (
            (self.base_records[0][0], tuple(reversed(self.base_records[0][1]))),
            self.base_records[1],
        )
        self.reordered_records = tuple(reversed(self.base_records))

        self.base_payload = self._with_related_remittance(fixture, marker, self.base_records)
        self.changed_identifier_payload = self._with_related_remittance(
            fixture, marker, self.changed_identifier_records
        )
        self.changed_method_payload = self._with_related_remittance(
            fixture, marker, self.changed_method_records
        )
        self.changed_address_payload = self._with_related_remittance(
            fixture, marker, self.changed_address_records
        )
        self.reordered_locations_payload = self._with_related_remittance(
            fixture, marker, self.reordered_locations_records
        )
        self.reordered_payload = self._with_related_remittance(
            fixture, marker, self.reordered_records
        )

        related_xml = self._related_remittance_xml(self.base_records)
        self.assertEqual(self.base_payload.count(related_xml.encode("utf-8")), 1)
        reformatted = related_xml.replace(
            "            <RltdRmtInf>\n",
            "            <RltdRmtInf>\n              \n",
            1,
        )
        self.reformatted_payload = self.base_payload.replace(
            related_xml.encode("utf-8"),
            reformatted.encode("utf-8"),
            1,
        )

        self.base_statement = parse_bank_statement_payload(
            self.base_payload, CAMT053_MESSAGE_DEFINITION
        )
        self.changed_identifier_statement = parse_bank_statement_payload(
            self.changed_identifier_payload, CAMT053_MESSAGE_DEFINITION
        )
        self.changed_method_statement = parse_bank_statement_payload(
            self.changed_method_payload, CAMT053_MESSAGE_DEFINITION
        )
        self.changed_address_statement = parse_bank_statement_payload(
            self.changed_address_payload, CAMT053_MESSAGE_DEFINITION
        )
        self.reordered_locations_statement = parse_bank_statement_payload(
            self.reordered_locations_payload, CAMT053_MESSAGE_DEFINITION
        )
        self.reordered_statement = parse_bank_statement_payload(
            self.reordered_payload, CAMT053_MESSAGE_DEFINITION
        )
        self.reformatted_statement = parse_bank_statement_payload(
            self.reformatted_payload, CAMT053_MESSAGE_DEFINITION
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

    def test_related_remittance_fields_and_source_order_are_material_detail_evidence(self) -> None:
        """RmtId, method, address, nested order, and record order each affect evidence identity."""
        variants = (
            (self.base_statement, self.base_records),
            (self.changed_identifier_statement, self.changed_identifier_records),
            (self.changed_method_statement, self.changed_method_records),
            (self.changed_address_statement, self.changed_address_records),
            (self.reordered_locations_statement, self.reordered_locations_records),
            (self.reordered_statement, self.reordered_records),
        )
        expected_hashes = [self._expected_hash(records) for _, records in variants]
        self.assertEqual(len(set(expected_hashes)), len(expected_hashes))

        for (statement, _records), expected_hash in zip(variants, expected_hashes, strict=True):
            with self.subTest(expected_hash=expected_hash):
                detail = statement.entries[0].entry_details[0]
                self.assertRegex(expected_hash, _HASH_PATTERN)
                self.assertEqual(
                    getattr(detail, "related_remittance_evidence_hash", None),
                    expected_hash,
                )
                self._assert_entry_hash_binding(statement.entries[0], expected_hash)

        for changed, _records in variants[1:]:
            self.assertEqual(
                self.base_statement.account_identifier_hash,
                changed.account_identifier_hash,
            )
            self.assertEqual(
                self.base_statement.entries[0].entry_amount,
                changed.entries[0].entry_amount,
            )
            self.assertEqual(
                self.base_statement.entries[0].entry_details[0].detail_amount,
                changed.entries[0].entry_details[0].detail_amount,
            )
            self.assertNotEqual(
                self.base_statement.entries[0].entry_details[0].source_detail_hash,
                changed.entries[0].entry_details[0].source_detail_hash,
            )
            self.assertNotEqual(
                self.base_statement.entries[0].source_entry_hash,
                changed.entries[0].source_entry_hash,
            )
            self.assertNotEqual(
                self.base_statement.normalized_payload_hash,
                changed.normalized_payload_hash,
            )
            self.assertEqual(
                self.base_statement.entries[1].source_entry_hash,
                changed.entries[1].source_entry_hash,
            )

    def test_xml_layout_does_not_change_related_remittance_semantics(self) -> None:
        """Element-layout whitespace is artifact provenance, not RemittanceLocation8 identity."""
        expected = self._expected_hash(self.base_records)
        base_detail = self.base_statement.entries[0].entry_details[0]
        reformatted_detail = self.reformatted_statement.entries[0].entry_details[0]

        self.assertNotEqual(
            self.base_statement.source_artifact_hash,
            self.reformatted_statement.source_artifact_hash,
        )
        self.assertEqual(
            getattr(base_detail, "related_remittance_evidence_hash", None), expected
        )
        self.assertEqual(
            getattr(reformatted_detail, "related_remittance_evidence_hash", None), expected
        )
        self._assert_entry_hash_binding(self.base_statement.entries[0], expected)
        self._assert_entry_hash_binding(self.reformatted_statement.entries[0], expected)
        self.assertEqual(base_detail.source_detail_hash, reformatted_detail.source_detail_hash)
        self.assertEqual(
            self.base_statement.entries[0].source_entry_hash,
            self.reformatted_statement.entries[0].source_entry_hash,
        )
        self.assertEqual(
            self.base_statement.normalized_payload_hash,
            self.reformatted_statement.normalized_payload_hash,
        )

    def test_changed_related_remittance_requires_explicit_statement_correction(self) -> None:
        """Accepted remittance-routing evidence cannot be silently replaced under one identity."""
        accepted = accept_bank_statement_evidence(
            self._command(self.base_payload, "base"),
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
            artifact_store=self.store,
        )
        self.assertFalse(accepted["replayed"])

        for suffix, changed_payload in (
            ("identifier", self.changed_identifier_payload),
            ("method", self.changed_method_payload),
            ("address", self.changed_address_payload),
            ("location-order", self.reordered_locations_payload),
            ("record-order", self.reordered_payload),
        ):
            with self.subTest(suffix=suffix):
                with self.assertRaisesRegex(AccountingValidationError, _CORRECTION_ERROR):
                    accept_bank_statement_evidence(
                        self._command(changed_payload, suffix),
                        posting.DATABASE_URL,
                        self.case.policy.tenant_reference,
                        artifact_store=self.store,
                    )

    def test_buyer_read_preserves_related_remittance_without_changing_amount_truth(self) -> None:
        """Tenant-scoped reads expose routing evidence while journal-facing amounts stay distinct."""
        expected = self._expected_hash(self.base_records)
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

        self.assertEqual(detail.get("related_remittance_evidence_hash"), expected)
        self.assertEqual(
            detail.get("related_remittance_information"),
            self._expected_records(self.base_records),
        )
        self.assertEqual(entry["entry_amount"], "25000")
        self.assertEqual(entry["entry_currency_code"], "KRW")
        self.assertEqual(detail["detail_amount"], "25000")
        self.assertEqual(detail["detail_currency_code"], "KRW")

    @staticmethod
    def _assert_entry_hash_binding(entry: object, expected_hash: str) -> None:
        """Bind source_entry_hash to the purpose digest rather than parallel raw fields."""
        projection = dict(bank_statement._entry_payload(entry))
        details = projection.get("details")
        if not isinstance(details, list) or not details:
            raise AssertionError("canonical entry projection must retain transaction details")
        first_detail = details[0]
        if not isinstance(first_detail, dict):
            raise AssertionError("canonical entry detail projection must be a mapping")
        if first_detail.get("related_remittance_evidence_hash") != expected_hash:
            raise AssertionError(
                "canonical entry projection must carry the exact related_remittance_evidence_hash"
            )
        preimage = json.dumps(
            projection,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        expected_entry_hash = f"sha256:{hashlib.sha256(preimage).hexdigest()}"
        if getattr(entry, "source_entry_hash", None) != expected_entry_hash:
            raise AssertionError(
                "source_entry_hash must digest the canonical entry projection containing "
                "related_remittance_evidence_hash"
            )

    @classmethod
    def _expected_hash(cls, records: tuple[RelatedRemittance, ...]) -> str:
        """Digest exact source-ordered RemittanceLocation8 semantics."""
        preimage = json.dumps(
            {
                "evidence_type": _RELATED_REMITTANCE_PURPOSE,
                "related_remittance_information": cls._expected_records(records),
            },
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return f"sha256:{hashlib.sha256(preimage).hexdigest()}"

    @staticmethod
    def _expected_records(records: tuple[RelatedRemittance, ...]) -> list[dict[str, object]]:
        """Return the buyer-visible canonical projection for admitted electronic locations."""
        return [
            {
                "remittance_identifier": remittance_identifier,
                "remittance_location_details": [
                    {
                        "method": method,
                        "electronic_address": electronic_address,
                        "postal_address": None,
                    }
                    for method, electronic_address in location_details
                ],
            }
            for remittance_identifier, location_details in records
        ]

    @staticmethod
    def _related_remittance_xml(records: tuple[RelatedRemittance, ...]) -> str:
        """Return schema-ordered repeatable RemittanceLocation8 elements."""
        chunks: list[str] = []
        for remittance_identifier, location_details in records:
            chunks.append("            <RltdRmtInf>\n")
            chunks.append(f"              <RmtId>{remittance_identifier}</RmtId>\n")
            for method, electronic_address in location_details:
                chunks.extend(
                    (
                        "              <RmtLctnDtls>\n",
                        f"                <Mtd>{method}</Mtd>\n",
                        f"                <ElctrncAdr>{electronic_address}</ElctrncAdr>\n",
                        "              </RmtLctnDtls>\n",
                    )
                )
            chunks.append("            </RltdRmtInf>\n")
        return "".join(chunks)

    @classmethod
    def _with_related_remittance(
        cls,
        fixture: str,
        marker: str,
        records: tuple[RelatedRemittance, ...],
    ) -> bytes:
        """Insert RltdRmtInf after purpose fields and immediately before RmtInf."""
        return fixture.replace(
            marker,
            cls._related_remittance_xml(records) + marker,
            1,
        ).encode("utf-8")

    def _command(self, payload: bytes, suffix: str) -> dict[str, object]:
        """Return one supported ingest command with a fresh replay key."""
        return {
            "tenant_reference": self.case.policy.tenant_reference,
            "bank_account_reference": self.bank_account_reference,
            "ingestion_idempotency_key": (
                f"detail-related-remittance-{suffix}-{uuid.uuid4().hex}"
            ),
            "message_definition_identifier": CAMT053_MESSAGE_DEFINITION,
            "statement_payload": payload.decode("utf-8"),
        }


if __name__ == "__main__":
    unittest.main()
