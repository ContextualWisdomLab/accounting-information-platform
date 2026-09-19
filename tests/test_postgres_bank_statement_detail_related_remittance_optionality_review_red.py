"""Focused REDs for RemittanceLocation8 optional/population evidence semantics."""

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
from tests import test_postgres_posting as posting

_HASH_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_RELATED_REMITTANCE_PURPOSE = "camt.053.001.14/Stmt/Ntry/NtryDtls/TxDtls/RltdRmtInf"
_CORRECTION_ERROR = (
    r"^statement identity already exists with different entry evidence\. "
    r"Use an explicit correction contract, then retry ingest\.$"
)

RemittanceLocation = tuple[str, str]
RelatedRemittance = tuple[str | None, tuple[RemittanceLocation, ...]]
RelatedRemittances = tuple[RelatedRemittance, ...]


class BankStatementDetailRelatedRemittanceOptionalityReviewRedTests(unittest.TestCase):
    """Preserve optional/repeated RemittanceLocation8 population as reconciliation evidence."""

    @classmethod
    def setUpClass(cls) -> None:
        """Reuse the canonical real-PostgreSQL integration fixture."""
        posting.PostgresPostingTests.setUpClass()

    def setUp(self) -> None:
        """Prepare schema-valid related-remittance population variants."""
        self.case = posting.PostgresPostingTests("setUp")
        self.addCleanup(self.case.doCleanups)
        self.case.setUp()
        self.addCleanup(self.case.tearDown)

        self.fixture = load_canonical_statement_fixture().decode("utf-8")
        self.marker = (
            "            <RmtInf>\n"
            "              <Ustrd>Invoice 1001</Ustrd>\n"
            "            </RmtInf>"
        )
        self.assertEqual(self.fixture.count(self.marker), 1)

        self.base_records: RelatedRemittances = (
            (
                "RMT-LOC-OPT-001",
                (
                    ("EMAL", "cash-application@example.test"),
                    ("URID", "https://example.test/remittance/INV-1001"),
                ),
            ),
            (
                "RMT-LOC-OPT-002",
                (("EDIC", "urn:cwl:edi:partner:1001"),),
            ),
        )
        self.variants: dict[str, RelatedRemittances | None] = {
            "all-related-remittance-absent": None,
            "remittance-identification-absent": (
                (None, self.base_records[0][1]),
                self.base_records[1],
            ),
            "location-details-absent": (
                (self.base_records[0][0], ()),
                self.base_records[1],
            ),
            "later-location-absent": (
                (self.base_records[0][0], (self.base_records[0][1][0],)),
                self.base_records[1],
            ),
            "later-related-remittance-absent": (self.base_records[0],),
        }
        self.base_payload = self._payload(self.base_records)
        self.variant_payloads = {
            label: self._payload(records) for label, records in self.variants.items()
        }
        self.base_statement = self._parse(self.base_payload)
        self.bank_account_reference = self._register_statement_account(self.base_payload)
        self.store = MemoryArtifactStore()

    def test_optional_and_repeated_population_changes_canonical_related_remittance_evidence(
        self,
    ) -> None:
        """Container, RmtId, detail count, and record count are material source facts."""
        base_entry = self.base_statement.entries[0]
        base_detail = base_entry.entry_details[0]
        base_sibling = self.base_statement.entries[1]
        base_digest = getattr(base_detail, "related_remittance_evidence_hash", None)
        self._assert_sha256(base_digest)
        self.assertEqual(base_digest, self._expected_hash(self.base_records))
        for value in (
            base_detail.source_detail_hash,
            base_entry.source_entry_hash,
            self.base_statement.normalized_payload_hash,
            self.base_statement.account_identifier_hash,
            base_sibling.source_entry_hash,
        ):
            self._assert_sha256(value)
        self._assert_exact_amount(base_entry, base_detail)

        expected_digests: set[str | None] = {base_digest}
        for label, expected_records in self.variants.items():
            with self.subTest(semantic=label):
                changed = self._parse(self.variant_payloads[label])
                changed_entry = changed.entries[0]
                changed_detail = changed_entry.entry_details[0]
                changed_sibling = changed.entries[1]
                changed_digest = getattr(
                    changed_detail,
                    "related_remittance_evidence_hash",
                    None,
                )

                if expected_records is None:
                    self.assertIsNone(changed_digest)
                else:
                    self._assert_sha256(changed_digest)
                    self.assertEqual(
                        changed_digest,
                        self._expected_hash(expected_records),
                    )
                self.assertNotIn(
                    changed_digest,
                    expected_digests,
                    "each optional/population variant must retain a distinct semantic digest",
                )
                expected_digests.add(changed_digest)

                for value in (
                    changed_detail.source_detail_hash,
                    changed_entry.source_entry_hash,
                    changed.normalized_payload_hash,
                    changed.account_identifier_hash,
                    changed_sibling.source_entry_hash,
                ):
                    self._assert_sha256(value)
                self.assertNotEqual(
                    base_detail.source_detail_hash,
                    changed_detail.source_detail_hash,
                )
                self.assertNotEqual(
                    base_entry.source_entry_hash,
                    changed_entry.source_entry_hash,
                )
                self.assertNotEqual(
                    self.base_statement.normalized_payload_hash,
                    changed.normalized_payload_hash,
                )
                self.assertEqual(
                    self.base_statement.account_identifier_hash,
                    changed.account_identifier_hash,
                )
                self.assertEqual(
                    base_sibling.source_entry_hash,
                    changed_sibling.source_entry_hash,
                )
                self._assert_exact_amount(changed_entry, changed_detail)

    def test_optional_and_repeated_population_changes_reach_explicit_correction_boundary(
        self,
    ) -> None:
        """Accepted remittance population cannot silently disappear or contract on replay."""
        accepted = accept_bank_statement_evidence(
            self._command(self.base_payload, self.bank_account_reference, "baseline"),
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
            artifact_store=self.store,
        )
        self.assertFalse(accepted["replayed"])

        for label, payload in self.variant_payloads.items():
            with self.subTest(semantic=label):
                with self.assertRaisesRegex(AccountingValidationError, _CORRECTION_ERROR):
                    accept_bank_statement_evidence(
                        self._command(
                            payload,
                            self.bank_account_reference,
                            f"changed-{label}",
                        ),
                        posting.DATABASE_URL,
                        self.case.policy.tenant_reference,
                        artifact_store=self.store,
                    )

    def test_buyer_read_preserves_absence_and_population_without_synthesizing_locations(
        self,
    ) -> None:
        """Tenant readback round-trips optional and repeated remittance populations exactly."""
        for label, expected_records in self.variants.items():
            with self.subTest(semantic=label):
                payload = self._with_unique_statement_id(
                    self.variant_payloads[label],
                    label,
                )
                reference = self._register_statement_account(payload)
                accepted = accept_bank_statement_evidence(
                    self._command(payload, reference, f"lookup-{label}"),
                    posting.DATABASE_URL,
                    self.case.policy.tenant_reference,
                    artifact_store=MemoryArtifactStore(),
                )
                document = lookup_bank_statement_entries(
                    posting.DATABASE_URL,
                    self.case.policy.tenant_reference,
                    str(accepted["bank_statement_record_id"]),
                )
                entry = document["bank_statement_entries"][0]
                detail = entry["entry_details"][0]
                actual = detail.get("related_remittance_information")

                if expected_records is None:
                    self.assertIsNone(actual)
                    self.assertIsNone(detail.get("related_remittance_evidence_hash"))
                else:
                    expected_projection = self._expected_records(expected_records)
                    self.assertEqual(actual, expected_projection)
                    self.assertEqual(
                        detail.get("related_remittance_evidence_hash"),
                        self._expected_hash(expected_records),
                    )
                    if label == "remittance-identification-absent":
                        self.assertNotIn("remittance_identifier", actual[0])
                    elif label == "location-details-absent":
                        self.assertNotIn("remittance_location_details", actual[0])
                    elif label == "later-location-absent":
                        self.assertEqual(len(actual[0]["remittance_location_details"]), 1)
                    elif label == "later-related-remittance-absent":
                        self.assertEqual(len(actual), 1)

                self.assertEqual(Decimal(str(entry["entry_amount"])), Decimal("25000.00"))
                self.assertEqual(entry["entry_currency_code"], "KRW")
                self.assertEqual(Decimal(str(detail["detail_amount"])), Decimal("25000.00"))
                self.assertEqual(detail["detail_currency_code"], "KRW")

    def _payload(self, records: RelatedRemittances | None) -> bytes:
        """Insert zero or more schema-ordered RemittanceLocation8 elements before RmtInf."""
        if records is None:
            return self.fixture.encode("utf-8")
        xml = self._related_remittance_xml(records)
        return self.fixture.replace(self.marker, xml + self.marker, 1).encode("utf-8")

    @staticmethod
    def _related_remittance_xml(records: RelatedRemittances) -> str:
        """Serialize optional RmtId and repeatable RmtLctnDtls without inventing fields."""
        chunks: list[str] = []
        for remittance_identifier, locations in records:
            chunks.append("            <RltdRmtInf>\n")
            if remittance_identifier is not None:
                chunks.append(
                    f"              <RmtId>{remittance_identifier}</RmtId>\n"
                )
            for method, electronic_address in locations:
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
    def _expected_hash(cls, records: RelatedRemittances) -> str:
        """Digest the exact source-ordered RemittanceLocation8 canonical projection."""
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
        """Return source-faithful semantics without manufacturing absent optional keys."""
        projected: list[dict[str, object]] = []
        for remittance_identifier, locations in records:
            record: dict[str, object] = {}
            if remittance_identifier is not None:
                record["remittance_identifier"] = remittance_identifier
            if locations:
                record["remittance_location_details"] = [
                    {
                        "method": method,
                        "electronic_address": electronic_address,
                        "postal_address": None,
                    }
                    for method, electronic_address in locations
                ]
            projected.append(record)
        return projected

    @staticmethod
    def _parse(payload: bytes) -> object:
        """Parse one V14 statement through the supported adapter boundary."""
        return parse_bank_statement_payload(payload, CAMT053_MESSAGE_DEFINITION)

    def _register_statement_account(self, payload: bytes) -> str:
        """Register only the statement-owner account required for supported ingest."""
        statement = self._parse(payload)
        reference = f"urn:cwl:bank_account:related-remittance-optionality:{uuid.uuid4().hex}"
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

    def _command(
        self,
        payload: bytes,
        account_reference: str,
        suffix: str,
    ) -> dict[str, object]:
        """Build one supported ingest command with an isolated replay key."""
        return {
            "tenant_reference": self.case.policy.tenant_reference,
            "bank_account_reference": account_reference,
            "ingestion_idempotency_key": (
                f"detail-related-remittance-optionality-{suffix}-{uuid.uuid4().hex}"
            ),
            "message_definition_identifier": CAMT053_MESSAGE_DEFINITION,
            "statement_payload": payload.decode("utf-8"),
        }

    @staticmethod
    def _with_unique_statement_id(payload: bytes, suffix: str) -> bytes:
        """Give lookup variants independent statement identity without changing owner truth."""
        old = b"<Id>STMT-2026-08-24-001</Id>"
        new = f"<Id>STMT-RMTOPT-{suffix}-{uuid.uuid4().hex[:12]}</Id>".encode("utf-8")
        if payload.count(old) != 1:
            raise AssertionError("canonical statement Id marker must occur exactly once")
        return payload.replace(old, new, 1)

    @staticmethod
    def _assert_sha256(value: object) -> None:
        """Require canonical purpose-bound SHA-256 evidence."""
        if not isinstance(value, str) or _HASH_RE.fullmatch(value) is None:
            raise AssertionError("evidence hash must be canonical SHA-256")

    @staticmethod
    def _assert_exact_amount(entry: object, detail: object) -> None:
        """Keep exact accounting facts independent from remittance optionality."""
        if getattr(entry, "entry_amount", None) != Decimal("25000.00"):
            raise AssertionError("entry amount must remain exactly 25000.00")
        if getattr(entry, "entry_currency_code", None) != "KRW":
            raise AssertionError("entry currency must remain KRW")
        if getattr(detail, "detail_amount", None) != Decimal("25000.00"):
            raise AssertionError("detail amount must remain exactly 25000.00")
        if getattr(detail, "detail_currency_code", None) != "KRW":
            raise AssertionError("detail currency must remain KRW")


if __name__ == "__main__":
    unittest.main()
