"""PostgreSQL REDs for camt.053 transaction-detail availability evidence."""

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
_DETAIL_AVAILABILITY_PURPOSE = "camt.053.001.14/Stmt/Ntry/NtryDtls/TxDtls/Avlbty"
_CORRECTION_ERROR = (
    r"^statement identity already exists with different entry evidence\. "
    r"Use an explicit correction contract, then retry ingest\.$"
)

AvailabilityRecord = tuple[str, str, str, str, str]


class BankStatementDetailAvailabilityEvidenceRedTests(unittest.TestCase):
    """Retain repeatable TxDtls availability evidence without making it ledger truth."""

    @classmethod
    def setUpClass(cls) -> None:
        """Reuse the canonical real-PostgreSQL integration fixture."""
        posting.PostgresPostingTests.setUpClass()

    def setUp(self) -> None:
        """Prepare availability variants while holding transaction monetary truth fixed."""
        self.case = posting.PostgresPostingTests("setUp")
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.addCleanup(self.case.tearDown)

        fixture = load_canonical_statement_fixture().decode("utf-8")
        marker = (
            "            </AmtDtls>\n"
            "            <RltdPties>"
        )
        self.assertEqual(fixture.count(marker), 1)

        self.base_records: tuple[AvailabilityRecord, ...] = (
            ("ActlDt", "2026-08-25", "20000.00", "KRW", "CRDT"),
            ("NbOfDays", "2", "4000.00", "KRW", "CRDT"),
        )
        self.changed_actual_date_records = (
            ("ActlDt", "2026-08-26", "20000.00", "KRW", "CRDT"),
            self.base_records[1],
        )
        self.changed_number_of_days_records = (
            self.base_records[0],
            ("NbOfDays", "3", "4000.00", "KRW", "CRDT"),
        )
        self.changed_amount_records = (
            self.base_records[0],
            ("NbOfDays", "2", "4000.01", "KRW", "CRDT"),
        )
        self.changed_direction_records = (
            self.base_records[0],
            ("NbOfDays", "2", "4000.00", "KRW", "DBIT"),
        )
        self.reordered_records = tuple(reversed(self.base_records))

        self.base_payload = self._with_availability(fixture, marker, self.base_records)
        self.changed_actual_date_payload = self._with_availability(
            fixture, marker, self.changed_actual_date_records
        )
        self.changed_number_of_days_payload = self._with_availability(
            fixture, marker, self.changed_number_of_days_records
        )
        self.changed_amount_payload = self._with_availability(
            fixture, marker, self.changed_amount_records
        )
        self.changed_direction_payload = self._with_availability(
            fixture, marker, self.changed_direction_records
        )
        self.reordered_payload = self._with_availability(
            fixture, marker, self.reordered_records
        )

        availability_xml = self._availability_xml(self.base_records)
        self.assertEqual(self.base_payload.count(availability_xml.encode("utf-8")), 1)
        reformatted = availability_xml.replace(
            "            <Avlbty>\n              <Dt><ActlDt>2026-08-25</ActlDt></Dt>\n",
            "            <Avlbty>\n              <Dt>\n                <ActlDt>2026-08-25</ActlDt>\n              </Dt>\n",
            1,
        )
        self.reformatted_payload = self.base_payload.replace(
            availability_xml.encode("utf-8"),
            reformatted.encode("utf-8"),
            1,
        )

        self.base_statement = parse_bank_statement_payload(
            self.base_payload, CAMT053_MESSAGE_DEFINITION
        )
        self.changed_actual_date_statement = parse_bank_statement_payload(
            self.changed_actual_date_payload, CAMT053_MESSAGE_DEFINITION
        )
        self.changed_number_of_days_statement = parse_bank_statement_payload(
            self.changed_number_of_days_payload, CAMT053_MESSAGE_DEFINITION
        )
        self.changed_amount_statement = parse_bank_statement_payload(
            self.changed_amount_payload, CAMT053_MESSAGE_DEFINITION
        )
        self.changed_direction_statement = parse_bank_statement_payload(
            self.changed_direction_payload, CAMT053_MESSAGE_DEFINITION
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

    def test_detail_availability_semantics_are_material_evidence(self) -> None:
        """Date choice/value, exact amount, direction, and source order all affect identity."""
        variants = (
            (self.base_statement, self.base_records),
            (self.changed_actual_date_statement, self.changed_actual_date_records),
            (self.changed_number_of_days_statement, self.changed_number_of_days_records),
            (self.changed_amount_statement, self.changed_amount_records),
            (self.changed_direction_statement, self.changed_direction_records),
            (self.reordered_statement, self.reordered_records),
        )
        hashes: list[str] = []
        for statement, records in variants:
            with self.subTest(records=records):
                expected_hash = self._expected_hash(records)
                detail = statement.entries[0].entry_details[0]
                self.assertRegex(expected_hash, _HASH_PATTERN)
                self.assertEqual(
                    getattr(detail, "detail_availability_evidence_hash", None),
                    expected_hash,
                )
                self._assert_entry_hash_binding(statement.entries[0], expected_hash)
                hashes.append(expected_hash)

        self.assertEqual(len(set(hashes)), len(hashes))
        for changed in (
            self.changed_actual_date_statement,
            self.changed_number_of_days_statement,
            self.changed_amount_statement,
            self.changed_direction_statement,
            self.reordered_statement,
        ):
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

    def test_xml_layout_does_not_change_detail_availability_semantics(self) -> None:
        """Whitespace is raw-artifact provenance, not CashAvailability1 semantic identity."""
        expected_hash = self._expected_hash(self.base_records)
        base_detail = self.base_statement.entries[0].entry_details[0]
        reformatted_detail = self.reformatted_statement.entries[0].entry_details[0]

        self.assertNotEqual(
            self.base_statement.source_artifact_hash,
            self.reformatted_statement.source_artifact_hash,
        )
        self.assertEqual(
            getattr(base_detail, "detail_availability_evidence_hash", None),
            expected_hash,
        )
        self.assertEqual(
            getattr(reformatted_detail, "detail_availability_evidence_hash", None),
            expected_hash,
        )
        self._assert_entry_hash_binding(self.base_statement.entries[0], expected_hash)
        self._assert_entry_hash_binding(self.reformatted_statement.entries[0], expected_hash)
        self.assertEqual(base_detail.source_detail_hash, reformatted_detail.source_detail_hash)
        self.assertEqual(
            self.base_statement.entries[0].source_entry_hash,
            self.reformatted_statement.entries[0].source_entry_hash,
        )
        self.assertEqual(
            self.base_statement.normalized_payload_hash,
            self.reformatted_statement.normalized_payload_hash,
        )

    def test_changed_detail_availability_requires_explicit_statement_correction(self) -> None:
        """Changed availability evidence cannot silently replace an accepted statement."""
        accepted = accept_bank_statement_evidence(
            self._command(self.base_payload, "base"),
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
            artifact_store=self.store,
        )
        self.assertFalse(accepted["replayed"])

        for suffix, changed_payload in (
            ("actual-date", self.changed_actual_date_payload),
            ("number-of-days", self.changed_number_of_days_payload),
            ("amount", self.changed_amount_payload),
            ("direction", self.changed_direction_payload),
            ("order", self.reordered_payload),
        ):
            with self.subTest(suffix=suffix):
                with self.assertRaisesRegex(AccountingValidationError, _CORRECTION_ERROR):
                    accept_bank_statement_evidence(
                        self._command(changed_payload, suffix),
                        posting.DATABASE_URL,
                        self.case.policy.tenant_reference,
                        artifact_store=self.store,
                    )

    def test_buyer_read_preserves_detail_availability_without_recomputing_amount_truth(
        self,
    ) -> None:
        """Buyer reads expose typed availability records while transaction amounts stay distinct."""
        expected_hash = self._expected_hash(self.base_records)
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

        self.assertEqual(detail.get("detail_availability_evidence_hash"), expected_hash)
        self.assertEqual(
            detail.get("availability_evidence"),
            [
                {
                    "date_choice": "ActlDt",
                    "date_value": "2026-08-25",
                    "amount": "20000.00",
                    "currency_code": "KRW",
                    "credit_debit_code": "CRDT",
                },
                {
                    "date_choice": "NbOfDays",
                    "date_value": "2",
                    "amount": "4000.00",
                    "currency_code": "KRW",
                    "credit_debit_code": "CRDT",
                },
            ],
        )
        self.assertEqual(entry["entry_amount"], "25000")
        self.assertEqual(entry["entry_currency_code"], "KRW")
        self.assertEqual(detail["detail_amount"], "25000")
        self.assertEqual(detail["detail_currency_code"], "KRW")

    @staticmethod
    def _assert_entry_hash_binding(entry: object, expected_hash: str) -> None:
        """Bind source_entry_hash to the detail purpose digest, not parallel raw fields."""
        projection = dict(bank_statement._entry_payload(entry))
        details = projection.get("details")
        if not isinstance(details, list) or not details:
            raise AssertionError("canonical entry projection must retain transaction details")
        first_detail = details[0]
        if not isinstance(first_detail, dict):
            raise AssertionError("canonical entry detail projection must be a mapping")
        if first_detail.get("detail_availability_evidence_hash") != expected_hash:
            raise AssertionError(
                "canonical entry projection must carry the exact "
                "detail_availability_evidence_hash"
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
                "detail_availability_evidence_hash"
            )

    @staticmethod
    def _expected_hash(records: tuple[AvailabilityRecord, ...]) -> str:
        """Digest complete repeatable CashAvailability1 semantics in source order."""
        preimage = json.dumps(
            {
                "evidence_type": _DETAIL_AVAILABILITY_PURPOSE,
                "availability_records": [
                    {
                        "date_choice": date_choice,
                        "date_value": date_value,
                        "amount": amount,
                        "currency_code": currency_code,
                        "credit_debit_code": credit_debit_code,
                    }
                    for (
                        date_choice,
                        date_value,
                        amount,
                        currency_code,
                        credit_debit_code,
                    ) in records
                ],
            },
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return f"sha256:{hashlib.sha256(preimage).hexdigest()}"

    @staticmethod
    def _availability_xml(records: tuple[AvailabilityRecord, ...]) -> str:
        """Return schema-ordered repeatable CashAvailability1 elements."""
        rendered: list[str] = []
        for date_choice, date_value, amount, currency_code, credit_debit_code in records:
            rendered.append(
                "            <Avlbty>\n"
                f"              <Dt><{date_choice}>{date_value}</{date_choice}></Dt>\n"
                f"              <Amt Ccy=\"{currency_code}\">{amount}</Amt>\n"
                f"              <CdtDbtInd>{credit_debit_code}</CdtDbtInd>\n"
                "            </Avlbty>\n"
            )
        return "".join(rendered)

    @classmethod
    def _with_availability(
        cls,
        fixture: str,
        marker: str,
        records: tuple[AvailabilityRecord, ...],
    ) -> bytes:
        """Insert TxDtls availability after AmtDtls and before later optional fields."""
        return fixture.replace(
            marker,
            "            </AmtDtls>\n"
            + cls._availability_xml(records)
            + "            <RltdPties>",
            1,
        ).encode("utf-8")

    def _command(self, payload: bytes, suffix: str) -> dict[str, object]:
        """Return one supported ingest command with a fresh replay key."""
        return {
            "tenant_reference": self.case.policy.tenant_reference,
            "bank_account_reference": self.bank_account_reference,
            "ingestion_idempotency_key": f"detail-availability-{suffix}-{uuid.uuid4().hex}",
            "message_definition_identifier": CAMT053_MESSAGE_DEFINITION,
            "statement_payload": payload.decode("utf-8"),
        }


if __name__ == "__main__":
    unittest.main()
