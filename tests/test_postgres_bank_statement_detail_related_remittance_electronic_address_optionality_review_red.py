"""Focused REDs for RemittanceLocationData2 electronic-address optionality."""

from __future__ import annotations

import hashlib
import json
import re
import unittest
import uuid
from decimal import Decimal

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

_HASH_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_RELATED_REMITTANCE_PURPOSE = "camt.053.001.14/Stmt/Ntry/NtryDtls/TxDtls/RltdRmtInf"
_CORRECTION_ERROR = (
    r"^statement identity already exists with different entry evidence\. "
    r"Use an explicit correction contract, then retry ingest\.$"
)

RemittanceLocation = tuple[str, str | None]
RelatedRemittance = tuple[str, tuple[RemittanceLocation, ...]]
RelatedRemittances = tuple[RelatedRemittance, ...]


class BankStatementDetailRelatedRemittanceElectronicAddressOptionalityReviewRedTests(
    unittest.TestCase
):
    """Preserve a method-only RemittanceLocationData2 without inventing an address."""

    @classmethod
    def setUpClass(cls) -> None:
        """Reuse the canonical real-PostgreSQL integration fixture."""
        posting.PostgresPostingTests.setUpClass()

    def setUp(self) -> None:
        """Remove only ElctrncAdr from one otherwise unchanged location detail."""
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
        if fixture.count(marker) != 1:
            raise AssertionError("canonical RmtInf marker must occur exactly once")

        self.base_records: RelatedRemittances = (
            (
                "RMT-LOC-ELADR-001",
                (
                    ("EMAL", "cash-application@example.test"),
                    ("URID", "https://example.test/remittance/INV-1001"),
                ),
            ),
            (
                "RMT-LOC-ELADR-002",
                (("EDIC", "urn:cwl:edi:partner:1001"),),
            ),
        )
        self.method_only_records: RelatedRemittances = (
            (
                self.base_records[0][0],
                (
                    ("EMAL", None),
                    self.base_records[0][1][1],
                ),
            ),
            self.base_records[1],
        )
        self.base_payload = self._payload(fixture, marker, self.base_records)
        self.method_only_payload = self._payload(
            fixture,
            marker,
            self.method_only_records,
        )
        self.base_statement = self._parse(self.base_payload)
        self.method_only_statement = self._parse(self.method_only_payload)
        self.base_expected_hash = self._expected_hash(self.base_records)
        self.method_only_expected_hash = self._expected_hash(self.method_only_records)
        self.bank_account_reference = self._register_statement_account(self.base_payload)
        self.store = MemoryArtifactStore()

    def test_absent_electronic_address_is_material_related_remittance_evidence(self) -> None:
        """ElctrncAdr absence changes only the targeted remittance evidence chain."""
        base_entry = self.base_statement.entries[0]
        base_detail = base_entry.entry_details[0]
        changed_entry = self.method_only_statement.entries[0]
        changed_detail = changed_entry.entry_details[0]

        base_digest = getattr(base_detail, "related_remittance_evidence_hash", None)
        changed_digest = getattr(changed_detail, "related_remittance_evidence_hash", None)
        for value in (
            base_digest,
            changed_digest,
            base_detail.source_detail_hash,
            changed_detail.source_detail_hash,
            base_entry.source_entry_hash,
            changed_entry.source_entry_hash,
            self.base_statement.normalized_payload_hash,
            self.method_only_statement.normalized_payload_hash,
            self.base_statement.account_identifier_hash,
            self.method_only_statement.account_identifier_hash,
            self.base_statement.entries[1].source_entry_hash,
            self.method_only_statement.entries[1].source_entry_hash,
        ):
            self._assert_sha256(value)

        self.assertEqual(base_digest, self.base_expected_hash)
        self.assertEqual(changed_digest, self.method_only_expected_hash)
        self.assertNotEqual(base_digest, changed_digest)
        self.assertNotEqual(base_detail.source_detail_hash, changed_detail.source_detail_hash)
        self.assertNotEqual(base_entry.source_entry_hash, changed_entry.source_entry_hash)
        self.assertNotEqual(
            self.base_statement.normalized_payload_hash,
            self.method_only_statement.normalized_payload_hash,
        )
        self.assertEqual(
            self.base_statement.account_identifier_hash,
            self.method_only_statement.account_identifier_hash,
        )
        self.assertEqual(
            self.base_statement.entries[1].source_entry_hash,
            self.method_only_statement.entries[1].source_entry_hash,
        )
        self._assert_exact_amount(base_entry, base_detail)
        self._assert_exact_amount(changed_entry, changed_detail)
        self._assert_entry_hash_binding(base_entry, self.base_expected_hash)
        self._assert_entry_hash_binding(changed_entry, self.method_only_expected_hash)

    def test_electronic_address_removal_reaches_explicit_correction_boundary(self) -> None:
        """An accepted routing address cannot disappear silently under one statement identity."""
        accepted = accept_bank_statement_evidence(
            self._command(self.base_payload, "electronic-address-base"),
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
            artifact_store=self.store,
        )
        self.assertFalse(accepted["replayed"])

        with self.assertRaisesRegex(AccountingValidationError, _CORRECTION_ERROR):
            accept_bank_statement_evidence(
                self._command(self.method_only_payload, "electronic-address-absent"),
                posting.DATABASE_URL,
                self.case.policy.tenant_reference,
                artifact_store=self.store,
            )

    def test_buyer_read_retains_method_only_location_without_fabricating_address_value(
        self,
    ) -> None:
        """Tenant readback retains the method-only location and no fabricated address value."""
        accepted = accept_bank_statement_evidence(
            self._command(self.method_only_payload, "electronic-address-lookup"),
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
        actual = detail.get("related_remittance_information")
        expected = self._expected_records(self.method_only_records)

        self.assertEqual(
            detail.get("related_remittance_evidence_hash"),
            self.method_only_expected_hash,
        )
        self.assertEqual(actual, expected)
        if not isinstance(actual, list) or not actual:
            raise AssertionError("related remittance readback must retain the source record")
        location_details = actual[0].get("remittance_location_details")
        if not isinstance(location_details, list) or not location_details:
            raise AssertionError("method-only location detail must remain buyer-visible")
        first_location = location_details[0]
        if not isinstance(first_location, dict):
            raise AssertionError("method-only location detail must be a mapping")
        self.assertEqual(first_location.get("method"), "EMAL")
        self.assertIsNone(first_location.get("electronic_address"))
        self.assertIsNone(first_location.get("postal_address"))
        self._assert_readback_amount(entry, detail)

    @classmethod
    def _payload(
        cls,
        fixture: str,
        marker: str,
        records: RelatedRemittances,
    ) -> bytes:
        """Insert schema-ordered related-remittance evidence immediately before RmtInf."""
        return fixture.replace(
            marker,
            cls._related_remittance_xml(records) + marker,
            1,
        ).encode("utf-8")

    @staticmethod
    def _related_remittance_xml(records: RelatedRemittances) -> str:
        """Serialize method-only and electronic-address locations without placeholders."""
        chunks: list[str] = []
        for remittance_identifier, locations in records:
            chunks.append("            <RltdRmtInf>\n")
            chunks.append(f"              <RmtId>{remittance_identifier}</RmtId>\n")
            for method, electronic_address in locations:
                chunks.append("              <RmtLctnDtls>\n")
                chunks.append(f"                <Mtd>{method}</Mtd>\n")
                if electronic_address is not None:
                    chunks.append(
                        f"                <ElctrncAdr>{electronic_address}</ElctrncAdr>\n"
                    )
                chunks.append("              </RmtLctnDtls>\n")
            chunks.append("            </RltdRmtInf>\n")
        return "".join(chunks)

    @classmethod
    def _expected_hash(cls, records: RelatedRemittances) -> str:
        """Digest the exact source-ordered related-remittance canonical projection."""
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
    def _expected_records(records: RelatedRemittances) -> list[dict[str, object]]:
        """Return current buyer/canonical location shape with nullable optional addresses."""
        return [
            {
                "remittance_identifier": remittance_identifier,
                "remittance_location_details": [
                    {
                        "method": method,
                        "electronic_address": electronic_address,
                        "postal_address": None,
                    }
                    for method, electronic_address in locations
                ],
            }
            for remittance_identifier, locations in records
        ]

    @staticmethod
    def _parse(payload: bytes) -> object:
        """Parse one V14 statement through the supported adapter boundary."""
        return parse_bank_statement_payload(payload, CAMT053_MESSAGE_DEFINITION)

    def _register_statement_account(self, payload: bytes) -> str:
        """Register only the statement-owner account required for supported ingest."""
        statement = self._parse(payload)
        reference = f"urn:cwl:bank_account:related-remittance-eladr:{uuid.uuid4().hex}"
        accept_bank_account_record(
            {
                "tenant_reference": self.case.policy.tenant_reference,
                "bank_account_reference": reference,
                "account_currency_code": statement.account_currency_code,
                "account_identifier_hash": statement.account_identifier_hash,
            },
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
        )
        return reference

    def _command(self, payload: bytes, suffix: str) -> dict[str, object]:
        """Build one supported ingest command with an isolated replay key."""
        return {
            "tenant_reference": self.case.policy.tenant_reference,
            "bank_account_reference": self.bank_account_reference,
            "ingestion_idempotency_key": (
                f"detail-related-remittance-eladr-{suffix}-{uuid.uuid4().hex}"
            ),
            "message_definition_identifier": CAMT053_MESSAGE_DEFINITION,
            "statement_payload": payload.decode("utf-8"),
        }

    @staticmethod
    def _assert_entry_hash_binding(entry: object, expected_hash: str) -> None:
        """Bind source_entry_hash to the canonical detail projection carrying this evidence."""
        projection = dict(bank_statement._entry_payload(entry))
        details = projection.get("details")
        if not isinstance(details, list) or not details:
            raise AssertionError("canonical entry projection must retain transaction details")
        first_detail = details[0]
        if not isinstance(first_detail, dict):
            raise AssertionError("canonical entry detail projection must be a mapping")
        if first_detail.get("related_remittance_evidence_hash") != expected_hash:
            raise AssertionError(
                "canonical entry projection must carry the exact related-remittance digest"
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
                "related-remittance evidence"
            )

    @staticmethod
    def _assert_sha256(value: object) -> None:
        """Require canonical purpose-bound SHA-256 evidence and stability hashes."""
        if not isinstance(value, str) or _HASH_RE.fullmatch(value) is None:
            raise AssertionError("evidence hash must be canonical SHA-256")

    @staticmethod
    def _assert_exact_amount(entry: object, detail: object) -> None:
        """Keep exact accounting facts independent from remittance routing optionality."""
        if getattr(entry, "entry_amount", None) != Decimal("25000.00"):
            raise AssertionError("entry amount must remain exactly 25000.00")
        if getattr(entry, "entry_currency_code", None) != "KRW":
            raise AssertionError("entry currency must remain KRW")
        if getattr(detail, "detail_amount", None) != Decimal("25000.00"):
            raise AssertionError("detail amount must remain exactly 25000.00")
        if getattr(detail, "detail_currency_code", None) != "KRW":
            raise AssertionError("detail currency must remain KRW")

    @staticmethod
    def _assert_readback_amount(entry: dict[str, object], detail: dict[str, object]) -> None:
        """Require exact buyer-visible amount and currency without float coercion."""
        if Decimal(str(entry.get("entry_amount"))) != Decimal("25000.00"):
            raise AssertionError("readback entry amount must remain exactly 25000.00")
        if entry.get("entry_currency_code") != "KRW":
            raise AssertionError("readback entry currency must remain KRW")
        if Decimal(str(detail.get("detail_amount"))) != Decimal("25000.00"):
            raise AssertionError("readback detail amount must remain exactly 25000.00")
        if detail.get("detail_currency_code") != "KRW":
            raise AssertionError("readback detail currency must remain KRW")


if __name__ == "__main__":
    unittest.main()
