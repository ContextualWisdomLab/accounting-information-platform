"""PostgreSQL REDs for camt.053 transaction-detail financial-instrument evidence."""

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
_FINANCIAL_INSTRUMENT_PATH = (
    "camt.053.001.14/Stmt/Ntry/NtryDtls/TxDtls/FinInstrmId"
)
_CORRECTION_ERROR = (
    r"^statement identity already exists with different entry evidence\. "
    r"Use an explicit correction contract, then retry ingest\.$"
)


class BankStatementDetailFinancialInstrumentEvidenceRedTests(unittest.TestCase):
    """Retain bank-reported ISIN evidence without promoting it to journal truth."""

    @classmethod
    def setUpClass(cls) -> None:
        """Reuse the canonical real-PostgreSQL integration fixture."""
        posting.PostgresPostingTests.setUpClass()

    def setUp(self) -> None:
        """Prepare one base ISIN, one material change, and one layout-only variant."""
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

        self.base = {"isin": "KR7005930003"}
        self.changed = {"isin": "US0378331005"}
        self.base_payload = self._with_financial_instrument(fixture, marker, self.base)
        self.changed_payload = self._with_financial_instrument(
            fixture, marker, self.changed
        )
        self.base_statement = parse_bank_statement_payload(
            self.base_payload, CAMT053_MESSAGE_DEFINITION
        )
        self.changed_statement = parse_bank_statement_payload(
            self.changed_payload, CAMT053_MESSAGE_DEFINITION
        )

        instrument_xml = self._financial_instrument_xml(self.base)
        self.assertEqual(self.base_payload.count(instrument_xml.encode("utf-8")), 1)
        reformatted_xml = instrument_xml.replace(
            "            <FinInstrmId>\n",
            "            <FinInstrmId>\n              \n",
            1,
        )
        self.reformatted_payload = self.base_payload.replace(
            instrument_xml.encode("utf-8"), reformatted_xml.encode("utf-8"), 1
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

    def test_isin_is_material_to_detail_entry_and_statement_identity(self) -> None:
        """Changing only FinInstrmId/ISIN changes normalized evidence identity."""
        base_hash = self._expected_hash(self.base)
        changed_hash = self._expected_hash(self.changed)
        base_entry = self.base_statement.entries[0]
        changed_entry = self.changed_statement.entries[0]
        base_detail = base_entry.entry_details[0]
        changed_detail = changed_entry.entry_details[0]

        self.assertRegex(base_hash, _HASH_PATTERN)
        self.assertRegex(changed_hash, _HASH_PATTERN)
        self.assertNotEqual(base_hash, changed_hash)
        self.assertEqual(
            getattr(base_detail, "financial_instrument_evidence_hash", None), base_hash
        )
        self.assertEqual(
            getattr(changed_detail, "financial_instrument_evidence_hash", None),
            changed_hash,
        )
        self._assert_entry_hash_binding(base_entry, base_hash)
        self._assert_entry_hash_binding(changed_entry, changed_hash)

        self.assertEqual(
            self.base_statement.account_identifier_hash,
            self.changed_statement.account_identifier_hash,
        )
        self.assertEqual(base_entry.entry_amount, changed_entry.entry_amount)
        self.assertEqual(base_detail.detail_amount, changed_detail.detail_amount)
        self.assertEqual(
            base_entry.entry_currency_code, changed_entry.entry_currency_code
        )
        self.assertEqual(
            base_detail.detail_currency_code, changed_detail.detail_currency_code
        )
        self.assertNotEqual(
            base_detail.source_detail_hash, changed_detail.source_detail_hash
        )
        self.assertNotEqual(base_entry.source_entry_hash, changed_entry.source_entry_hash)
        self.assertNotEqual(
            self.base_statement.normalized_payload_hash,
            self.changed_statement.normalized_payload_hash,
        )
        self.assertEqual(
            self.base_statement.entries[1].source_entry_hash,
            self.changed_statement.entries[1].source_entry_hash,
        )

    def test_xml_layout_does_not_change_financial_instrument_semantics(self) -> None:
        """Layout-only XML changes raw provenance but not financial-instrument identity."""
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
            getattr(base_detail, "financial_instrument_evidence_hash", None),
            expected_hash,
        )
        self.assertEqual(
            getattr(reformatted_detail, "financial_instrument_evidence_hash", None),
            expected_hash,
        )
        self._assert_entry_hash_binding(base_entry, expected_hash)
        self._assert_entry_hash_binding(reformatted_entry, expected_hash)
        self.assertEqual(
            base_detail.source_detail_hash, reformatted_detail.source_detail_hash
        )
        self.assertEqual(base_entry.source_entry_hash, reformatted_entry.source_entry_hash)
        self.assertEqual(
            self.base_statement.normalized_payload_hash,
            self.reformatted_statement.normalized_payload_hash,
        )

    def test_changed_isin_requires_explicit_statement_correction(self) -> None:
        """Accepted ISIN evidence cannot be silently replaced under one statement identity."""
        accepted = accept_bank_statement_evidence(
            self._command(self.base_payload, "base"),
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
            artifact_store=self.store,
        )
        self.assertFalse(accepted["replayed"])

        with self.assertRaisesRegex(AccountingValidationError, _CORRECTION_ERROR):
            accept_bank_statement_evidence(
                self._command(self.changed_payload, "changed"),
                posting.DATABASE_URL,
                self.case.policy.tenant_reference,
                artifact_store=self.store,
            )

    def test_buyer_read_preserves_isin_without_changing_amount_truth(self) -> None:
        """Tenant reads expose ISIN provenance while reported bank amounts remain unchanged."""
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

        self.assertEqual(detail.get("financial_instrument_evidence_hash"), expected_hash)
        self.assertEqual(detail.get("financial_instrument_identification"), self.base)
        self.assertEqual(entry["entry_amount"], "25000")
        self.assertEqual(entry["entry_currency_code"], "KRW")
        self.assertEqual(detail["detail_amount"], "25000")
        self.assertEqual(detail["detail_currency_code"], "KRW")

    @staticmethod
    def _assert_entry_hash_binding(entry: object, expected_hash: str) -> None:
        """Require source_entry_hash to digest the exact instrument-bound detail hash."""
        projection = dict(bank_statement._entry_payload(entry))
        details = projection.get("details")
        if not isinstance(details, list) or not details:
            raise AssertionError("canonical entry projection must retain transaction details")
        first_detail = details[0]
        if not isinstance(first_detail, dict):
            raise AssertionError("canonical entry detail projection must be a mapping")
        if first_detail.get("financial_instrument_evidence_hash") != expected_hash:
            raise AssertionError(
                "canonical entry projection must carry the exact "
                "financial_instrument_evidence_hash"
            )
        preimage = json.dumps(
            projection, separators=(",", ":"), sort_keys=True
        ).encode("utf-8")
        expected_entry_hash = f"sha256:{hashlib.sha256(preimage).hexdigest()}"
        if getattr(entry, "source_entry_hash", None) != expected_entry_hash:
            raise AssertionError(
                "source_entry_hash must digest the canonical entry projection containing "
                "financial_instrument_evidence_hash"
            )

    @staticmethod
    def _expected_hash(value: dict[str, str]) -> str:
        """Digest the admitted FinInstrmId/ISIN projection."""
        preimage = json.dumps(
            {
                "evidence_type": _FINANCIAL_INSTRUMENT_PATH,
                "financial_instrument_identification": value,
            },
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return f"sha256:{hashlib.sha256(preimage).hexdigest()}"

    @staticmethod
    def _financial_instrument_xml(value: dict[str, str]) -> str:
        """Serialize one direct TxDtls/FinInstrmId ISIN in schema order."""
        return (
            "            <FinInstrmId>\n"
            f"              <ISIN>{value['isin']}</ISIN>\n"
            "            </FinInstrmId>\n"
        )

    @classmethod
    def _with_financial_instrument(
        cls, fixture: str, marker: str, value: dict[str, str]
    ) -> bytes:
        """Insert FinInstrmId after RmtInf, skipping earlier optional V14 elements."""
        return fixture.replace(
            marker,
            marker + cls._financial_instrument_xml(value),
            1,
        ).encode("utf-8")

    def _command(self, payload: bytes, suffix: str) -> dict[str, object]:
        """Build one supported ingest command with a unique replay key."""
        return {
            "tenant_reference": self.case.policy.tenant_reference,
            "bank_account_reference": self.bank_account_reference,
            "ingestion_idempotency_key": (
                f"detail-financial-instrument-{suffix}-{uuid.uuid4().hex}"
            ),
            "message_definition_identifier": CAMT053_MESSAGE_DEFINITION,
            "statement_payload": payload.decode("utf-8"),
        }


if __name__ == "__main__":
    unittest.main()
