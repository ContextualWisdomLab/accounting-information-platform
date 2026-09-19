"""PostgreSQL REDs for camt.053 entry-detail batch provenance."""

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
_BATCH_PURPOSE = "camt.053.001.14/Stmt/Ntry/NtryDtls/Btch"
_CORRECTION_ERROR = (
    r"^statement identity already exists with different entry evidence\. "
    r"Use an explicit correction contract, then retry ingest\.$"
)


class BankStatementEntryBatchEvidenceRedTests(unittest.TestCase):
    """Retain BatchInformation2 as bank evidence without promoting it to accounting truth."""

    @classmethod
    def setUpClass(cls) -> None:
        """Reuse the canonical real-PostgreSQL integration fixture."""
        posting.PostgresPostingTests.setUpClass()

    def setUp(self) -> None:
        """Prepare first-entry batch variants with one field changed at a time."""
        self.case = posting.PostgresPostingTests("setUp")
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.addCleanup(self.case.tearDown)

        fixture = load_canonical_statement_fixture().decode("utf-8")
        marker = (
            "        <NtryDtls>\n"
            "          <TxDtls>\n"
            "            <Refs>\n"
            "              <EndToEndId>E2E-1</EndToEndId>\n"
        )
        self.assertEqual(fixture.count(marker), 1)

        self.base_payload = self._with_batch(
            fixture,
            marker,
            message_identification="BATCH-MSG-001",
            payment_information_identification="PMT-INFO-001",
            number_of_transactions="1",
            total_amount="25000.00",
            credit_debit_code="CRDT",
        )
        self.changed_message_payload = self._with_batch(
            fixture,
            marker,
            message_identification="BATCH-MSG-002",
            payment_information_identification="PMT-INFO-001",
            number_of_transactions="1",
            total_amount="25000.00",
            credit_debit_code="CRDT",
        )
        self.changed_payment_information_payload = self._with_batch(
            fixture,
            marker,
            message_identification="BATCH-MSG-001",
            payment_information_identification="PMT-INFO-002",
            number_of_transactions="1",
            total_amount="25000.00",
            credit_debit_code="CRDT",
        )
        self.changed_count_payload = self._with_batch(
            fixture,
            marker,
            message_identification="BATCH-MSG-001",
            payment_information_identification="PMT-INFO-001",
            number_of_transactions="2",
            total_amount="25000.00",
            credit_debit_code="CRDT",
        )
        self.changed_amount_payload = self._with_batch(
            fixture,
            marker,
            message_identification="BATCH-MSG-001",
            payment_information_identification="PMT-INFO-001",
            number_of_transactions="1",
            total_amount="25000.01",
            credit_debit_code="CRDT",
        )
        self.changed_direction_payload = self._with_batch(
            fixture,
            marker,
            message_identification="BATCH-MSG-001",
            payment_information_identification="PMT-INFO-001",
            number_of_transactions="1",
            total_amount="25000.00",
            credit_debit_code="DBIT",
        )

        batch_xml = self._batch_xml(
            message_identification="BATCH-MSG-001",
            payment_information_identification="PMT-INFO-001",
            number_of_transactions="1",
            total_amount="25000.00",
            credit_debit_code="CRDT",
        )
        self.assertEqual(self.base_payload.count(batch_xml.encode("utf-8")), 1)
        reformatted_batch = batch_xml.replace(
            "          <Btch>\n",
            "          <Btch>\n            \n",
            1,
        )
        self.reformatted_payload = self.base_payload.replace(
            batch_xml.encode("utf-8"),
            reformatted_batch.encode("utf-8"),
            1,
        )

        self.base_statement = parse_bank_statement_payload(
            self.base_payload, CAMT053_MESSAGE_DEFINITION
        )
        self.changed_message_statement = parse_bank_statement_payload(
            self.changed_message_payload, CAMT053_MESSAGE_DEFINITION
        )
        self.changed_payment_information_statement = parse_bank_statement_payload(
            self.changed_payment_information_payload, CAMT053_MESSAGE_DEFINITION
        )
        self.changed_count_statement = parse_bank_statement_payload(
            self.changed_count_payload, CAMT053_MESSAGE_DEFINITION
        )
        self.changed_amount_statement = parse_bank_statement_payload(
            self.changed_amount_payload, CAMT053_MESSAGE_DEFINITION
        )
        self.changed_direction_statement = parse_bank_statement_payload(
            self.changed_direction_payload, CAMT053_MESSAGE_DEFINITION
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

    def test_batch_fields_are_independently_material_entry_evidence(self) -> None:
        """Every admitted BatchInformation2 field participates in canonical evidence identity."""
        variants = (
            (
                self.base_statement,
                self._expected_hash(
                    "BATCH-MSG-001", "PMT-INFO-001", "1", "25000.00", "CRDT"
                ),
            ),
            (
                self.changed_message_statement,
                self._expected_hash(
                    "BATCH-MSG-002", "PMT-INFO-001", "1", "25000.00", "CRDT"
                ),
            ),
            (
                self.changed_payment_information_statement,
                self._expected_hash(
                    "BATCH-MSG-001", "PMT-INFO-002", "1", "25000.00", "CRDT"
                ),
            ),
            (
                self.changed_count_statement,
                self._expected_hash(
                    "BATCH-MSG-001", "PMT-INFO-001", "2", "25000.00", "CRDT"
                ),
            ),
            (
                self.changed_amount_statement,
                self._expected_hash(
                    "BATCH-MSG-001", "PMT-INFO-001", "1", "25000.01", "CRDT"
                ),
            ),
            (
                self.changed_direction_statement,
                self._expected_hash(
                    "BATCH-MSG-001", "PMT-INFO-001", "1", "25000.00", "DBIT"
                ),
            ),
        )

        expected_hashes: list[str] = []
        for statement, expected_hash in variants:
            with self.subTest(expected_hash=expected_hash):
                entry = statement.entries[0]
                self.assertRegex(expected_hash, _HASH_PATTERN)
                self.assertEqual(
                    getattr(entry, "entry_batch_evidence_hash", None),
                    expected_hash,
                )
                self._assert_entry_hash_binding(entry, expected_hash)
                expected_hashes.append(expected_hash)

        self.assertEqual(len(set(expected_hashes)), len(expected_hashes))
        for changed in (
            self.changed_message_statement,
            self.changed_payment_information_statement,
            self.changed_count_statement,
            self.changed_amount_statement,
            self.changed_direction_statement,
        ):
            self.assertEqual(
                self.base_statement.account_identifier_hash,
                changed.account_identifier_hash,
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
            self.assertEqual(
                self.base_statement.entries[0].entry_amount,
                changed.entries[0].entry_amount,
            )
            self.assertEqual(
                self.base_statement.entries[0].entry_details[0].detail_amount,
                changed.entries[0].entry_details[0].detail_amount,
            )

    def test_xml_layout_does_not_change_batch_semantics(self) -> None:
        """Batch XML formatting is raw-artifact provenance, not semantic evidence."""
        expected_hash = self._expected_hash(
            "BATCH-MSG-001", "PMT-INFO-001", "1", "25000.00", "CRDT"
        )
        base_entry = self.base_statement.entries[0]
        reformatted_entry = self.reformatted_statement.entries[0]

        self.assertNotEqual(
            self.base_statement.source_artifact_hash,
            self.reformatted_statement.source_artifact_hash,
        )
        self.assertEqual(
            getattr(base_entry, "entry_batch_evidence_hash", None),
            expected_hash,
        )
        self.assertEqual(
            getattr(reformatted_entry, "entry_batch_evidence_hash", None),
            expected_hash,
        )
        self._assert_entry_hash_binding(base_entry, expected_hash)
        self._assert_entry_hash_binding(reformatted_entry, expected_hash)
        self.assertEqual(base_entry.source_entry_hash, reformatted_entry.source_entry_hash)
        self.assertEqual(
            self.base_statement.normalized_payload_hash,
            self.reformatted_statement.normalized_payload_hash,
        )

    def test_changed_batch_requires_explicit_statement_correction(self) -> None:
        """A material bank-reported batch change must not silently replace accepted evidence."""
        accepted = accept_bank_statement_evidence(
            self._command(self.base_payload, "base"),
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
            artifact_store=self.store,
        )
        self.assertFalse(accepted["replayed"])

        for suffix, changed_payload in (
            ("message", self.changed_message_payload),
            ("payment-information", self.changed_payment_information_payload),
            ("count", self.changed_count_payload),
            ("amount", self.changed_amount_payload),
            ("direction", self.changed_direction_payload),
        ):
            with self.subTest(suffix=suffix):
                with self.assertRaisesRegex(AccountingValidationError, _CORRECTION_ERROR):
                    accept_bank_statement_evidence(
                        self._command(changed_payload, suffix),
                        posting.DATABASE_URL,
                        self.case.policy.tenant_reference,
                        artifact_store=self.store,
                    )

    def test_buyer_read_preserves_batch_provenance_without_overriding_entry_amount(self) -> None:
        """Supported reads expose exact batch evidence while entry facts remain authoritative."""
        expected_hash = self._expected_hash(
            "BATCH-MSG-001", "PMT-INFO-001", "1", "25000.00", "CRDT"
        )
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

        self.assertEqual(entry.get("entry_batch_evidence_hash"), expected_hash)
        self.assertEqual(
            entry.get("batch_evidence"),
            [
                {
                    "message_identification": "BATCH-MSG-001",
                    "payment_information_identification": "PMT-INFO-001",
                    "number_of_transactions": "1",
                    "total_amount": {
                        "amount": "25000.00",
                        "currency_code": "KRW",
                    },
                    "credit_debit_code": "CRDT",
                }
            ],
        )
        self.assertEqual(entry["entry_amount"], "25000")
        self.assertEqual(entry["entry_currency_code"], "KRW")

    @staticmethod
    def _assert_entry_hash_binding(entry: object, expected_hash: str) -> None:
        """Bind source_entry_hash to batch semantics rather than XML representation."""
        projection = dict(bank_statement._entry_payload(entry))
        if projection.get("entry_batch_evidence_hash") != expected_hash:
            raise AssertionError(
                "canonical entry projection must carry the exact entry_batch_evidence_hash"
            )
        preimage = json.dumps(
            projection,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        expected_entry_hash = f"sha256:{hashlib.sha256(preimage).hexdigest()}"
        if entry.source_entry_hash != expected_entry_hash:
            raise AssertionError(
                "source_entry_hash must be the digest of the canonical entry projection"
            )

    @staticmethod
    def _expected_hash(
        message_identification: str,
        payment_information_identification: str,
        number_of_transactions: str,
        total_amount: str,
        credit_debit_code: str,
    ) -> str:
        """Digest the focused BatchInformation2 semantics as an ordered evidence list."""
        preimage = json.dumps(
            {
                "evidence_type": _BATCH_PURPOSE,
                "batches": [
                    {
                        "message_identification": message_identification,
                        "payment_information_identification": payment_information_identification,
                        "number_of_transactions": number_of_transactions,
                        "total_amount": {
                            "amount": total_amount,
                            "currency_code": "KRW",
                        },
                        "credit_debit_code": credit_debit_code,
                    }
                ],
            },
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return f"sha256:{hashlib.sha256(preimage).hexdigest()}"

    @staticmethod
    def _batch_xml(
        *,
        message_identification: str,
        payment_information_identification: str,
        number_of_transactions: str,
        total_amount: str,
        credit_debit_code: str,
    ) -> str:
        """Return one schema-ordered BatchInformation2 block."""
        return (
            "          <Btch>\n"
            f"            <MsgId>{message_identification}</MsgId>\n"
            f"            <PmtInfId>{payment_information_identification}</PmtInfId>\n"
            f"            <NbOfTxs>{number_of_transactions}</NbOfTxs>\n"
            f"            <TtlAmt Ccy=\"KRW\">{total_amount}</TtlAmt>\n"
            f"            <CdtDbtInd>{credit_debit_code}</CdtDbtInd>\n"
            "          </Btch>\n"
        )

    @classmethod
    def _with_batch(
        cls,
        fixture: str,
        marker: str,
        *,
        message_identification: str,
        payment_information_identification: str,
        number_of_transactions: str,
        total_amount: str,
        credit_debit_code: str,
    ) -> bytes:
        """Insert BatchInformation2 before TxDtls inside the first NtryDtls."""
        batch = cls._batch_xml(
            message_identification=message_identification,
            payment_information_identification=payment_information_identification,
            number_of_transactions=number_of_transactions,
            total_amount=total_amount,
            credit_debit_code=credit_debit_code,
        )
        return fixture.replace(
            marker,
            "        <NtryDtls>\n"
            + batch
            + "          <TxDtls>\n"
            + "            <Refs>\n"
            + "              <EndToEndId>E2E-1</EndToEndId>\n",
            1,
        ).encode("utf-8")

    def _command(self, payload: bytes, suffix: str) -> dict[str, object]:
        """Build one supported ingest command with a fresh idempotency identity."""
        return {
            "tenant_reference": self.case.policy.tenant_reference,
            "bank_account_reference": self.bank_account_reference,
            "ingestion_idempotency_key": f"entry-batch-{suffix}-{uuid.uuid4().hex}",
            "message_definition_identifier": CAMT053_MESSAGE_DEFINITION,
            "statement_payload": payload.decode("utf-8"),
        }


if __name__ == "__main__":
    unittest.main()
