"""PostgreSQL REDs for complete camt.053 transaction related-date evidence."""

from __future__ import annotations

import copy
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
_RELATED_DATES_PURPOSE = "camt.053.001.14/Stmt/Ntry/NtryDtls/TxDtls/RltdDts"
_CORRECTION_ERROR = (
    r"^statement identity already exists with different entry evidence\. "
    r"Use an explicit correction contract, then retry ingest\.$"
)


class BankStatementDetailRelatedDatesEvidenceRedTests(unittest.TestCase):
    """Retain complete TransactionDates3 evidence without turning dates into posting authority."""

    @classmethod
    def setUpClass(cls) -> None:
        """Reuse the canonical real-PostgreSQL integration fixture."""
        posting.PostgresPostingTests.setUpClass()

    def setUp(self) -> None:
        """Build schema-shaped TransactionDates3 variants around one stable transaction."""
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
        self.variant_semantics = self._material_variants(self.base_semantics)
        self.base_payload = self._with_related_dates(fixture, marker, self.base_semantics)
        self.variant_payloads = {
            name: self._with_related_dates(fixture, marker, semantics)
            for name, semantics in self.variant_semantics.items()
        }

        related_dates_xml = self._related_dates_xml(self.base_semantics)
        self.assertEqual(self.base_payload.count(related_dates_xml.encode("utf-8")), 1)
        reformatted = related_dates_xml.replace(
            "            <RltdDts>\n",
            "            <RltdDts>\n              \n",
            1,
        )
        self.reformatted_payload = self.base_payload.replace(
            related_dates_xml.encode("utf-8"),
            reformatted.encode("utf-8"),
            1,
        )

        self.base_statement = parse_bank_statement_payload(
            self.base_payload, CAMT053_MESSAGE_DEFINITION
        )
        self.variant_statements = {
            name: parse_bank_statement_payload(payload, CAMT053_MESSAGE_DEFINITION)
            for name, payload in self.variant_payloads.items()
        }
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

    def test_every_transaction_dates3_semantic_is_material_evidence(self) -> None:
        """Every admitted singleton, proprietary discriminator/value, and source order affects identity."""
        base_hash = self._expected_hash(self.base_semantics)
        base_detail = self.base_statement.entries[0].entry_details[0]
        self.assertRegex(base_hash, _HASH_PATTERN)
        self.assertEqual(getattr(base_detail, "related_dates_evidence_hash", None), base_hash)
        self._assert_entry_hash_binding(self.base_statement.entries[0], base_hash)

        for name, statement in self.variant_statements.items():
            with self.subTest(name=name):
                expected_hash = self._expected_hash(self.variant_semantics[name])
                detail = statement.entries[0].entry_details[0]
                self.assertNotEqual(base_hash, expected_hash)
                self.assertEqual(
                    getattr(detail, "related_dates_evidence_hash", None), expected_hash
                )
                self._assert_entry_hash_binding(statement.entries[0], expected_hash)
                self.assertEqual(
                    self.base_statement.account_identifier_hash,
                    statement.account_identifier_hash,
                )
                self.assertEqual(
                    self.base_statement.entries[0].entry_amount,
                    statement.entries[0].entry_amount,
                )
                self.assertEqual(
                    self.base_statement.entries[0].entry_details[0].detail_amount,
                    detail.detail_amount,
                )
                self.assertNotEqual(base_detail.source_detail_hash, detail.source_detail_hash)
                self.assertNotEqual(
                    self.base_statement.entries[0].source_entry_hash,
                    statement.entries[0].source_entry_hash,
                )
                self.assertNotEqual(
                    self.base_statement.normalized_payload_hash,
                    statement.normalized_payload_hash,
                )
                self.assertEqual(
                    self.base_statement.entries[1].source_entry_hash,
                    statement.entries[1].source_entry_hash,
                )

    def test_xml_layout_does_not_change_related_date_semantics(self) -> None:
        """Whitespace belongs to artifact provenance, not TransactionDates3 semantic identity."""
        expected = self._expected_hash(self.base_semantics)
        base_detail = self.base_statement.entries[0].entry_details[0]
        reformatted_detail = self.reformatted_statement.entries[0].entry_details[0]

        self.assertNotEqual(
            self.base_statement.source_artifact_hash,
            self.reformatted_statement.source_artifact_hash,
        )
        self.assertEqual(getattr(base_detail, "related_dates_evidence_hash", None), expected)
        self.assertEqual(
            getattr(reformatted_detail, "related_dates_evidence_hash", None), expected
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

    def test_changed_related_dates_require_explicit_statement_correction(self) -> None:
        """Changed bank-reported dates must not silently replace accepted statement evidence."""
        accepted = accept_bank_statement_evidence(
            self._command(self.base_payload, "base"),
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
            artifact_store=self.store,
        )
        self.assertFalse(accepted["replayed"])

        for name, payload in self.variant_payloads.items():
            with self.subTest(name=name):
                with self.assertRaisesRegex(AccountingValidationError, _CORRECTION_ERROR):
                    accept_bank_statement_evidence(
                        self._command(payload, name),
                        posting.DATABASE_URL,
                        self.case.policy.tenant_reference,
                        artifact_store=self.store,
                    )

    def test_buyer_read_preserves_complete_transaction_dates3(self) -> None:
        """Buyer reads expose exact related-date provenance while transaction amounts stay distinct."""
        expected = self._expected_hash(self.base_semantics)
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

        self.assertEqual(detail.get("related_dates_evidence_hash"), expected)
        self.assertEqual(detail.get("related_dates"), self.base_semantics)
        self.assertEqual(
            detail.get("interbank_settlement_date"),
            self.base_semantics["interbank_settlement_date"],
        )
        self.assertEqual(entry["entry_amount"], "25000")
        self.assertEqual(entry["entry_currency_code"], "KRW")
        self.assertEqual(detail["detail_amount"], "25000")
        self.assertEqual(detail["detail_currency_code"], "KRW")

    @staticmethod
    def _material_variants(base: dict[str, object]) -> dict[str, dict[str, object]]:
        """Return one isolated material mutation for every TransactionDates3 semantic family."""
        changes: dict[str, tuple[str, object]] = {
            "acceptance": ("acceptance_datetime", "2026-08-22T08:30:01Z"),
            "contractual-settlement": (
                "trade_activity_contractual_settlement_date",
                "2026-08-24",
            ),
            "trade": ("trade_date", "2026-08-21"),
            "interbank": ("interbank_settlement_date", "2026-08-25"),
            "start": ("start_date", "2026-08-02"),
            "end": ("end_date", "2026-09-01"),
            "transaction-datetime": ("transaction_datetime", "2026-08-22T08:31:01Z"),
        }
        variants: dict[str, dict[str, object]] = {}
        for name, (field, value) in changes.items():
            variant = copy.deepcopy(base)
            variant[field] = value
            variants[name] = variant

        proprietary_type = copy.deepcopy(base)
        proprietary_type["proprietary_dates"][0]["type"] = "BANK_CUTOFF_ALT"
        variants["proprietary-type"] = proprietary_type

        proprietary_choice = copy.deepcopy(base)
        proprietary_choice["proprietary_dates"][1] = {
            "type": "BANK_BUSINESS_DATE",
            "date_choice": "DtTm",
            "date_value": "2026-08-22T00:00:00Z",
        }
        variants["proprietary-choice"] = proprietary_choice

        proprietary_order = copy.deepcopy(base)
        proprietary_order["proprietary_dates"] = list(
            reversed(proprietary_order["proprietary_dates"])
        )
        variants["proprietary-order"] = proprietary_order
        return variants

    @staticmethod
    def _assert_entry_hash_binding(entry: object, expected_hash: str) -> None:
        """Bind source_entry_hash to the related-date purpose digest in the canonical detail projection."""
        projection = dict(bank_statement._entry_payload(entry))
        details = projection.get("details")
        if not isinstance(details, list) or not details:
            raise AssertionError("canonical entry projection must retain transaction details")
        first_detail = details[0]
        if not isinstance(first_detail, dict):
            raise AssertionError("canonical entry detail projection must be a mapping")
        if first_detail.get("related_dates_evidence_hash") != expected_hash:
            raise AssertionError(
                "canonical entry projection must carry the exact related_dates_evidence_hash"
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
                "related_dates_evidence_hash"
            )

    @staticmethod
    def _expected_hash(semantics: dict[str, object]) -> str:
        """Digest the complete admitted TransactionDates3 semantics with proprietary source order."""
        preimage = json.dumps(
            {
                "evidence_type": _RELATED_DATES_PURPOSE,
                "related_dates": semantics,
            },
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return f"sha256:{hashlib.sha256(preimage).hexdigest()}"

    @staticmethod
    def _related_dates_xml(semantics: dict[str, object]) -> str:
        """Return one schema-ordered TransactionDates3 element."""
        lines = [
            "            <RltdDts>",
            f"              <AccptncDtTm>{semantics['acceptance_datetime']}</AccptncDtTm>",
            "              <TradActvtyCtrctlSttlmDt>"
            f"{semantics['trade_activity_contractual_settlement_date']}"
            "</TradActvtyCtrctlSttlmDt>",
            f"              <TradDt>{semantics['trade_date']}</TradDt>",
            f"              <IntrBkSttlmDt>{semantics['interbank_settlement_date']}</IntrBkSttlmDt>",
            f"              <StartDt>{semantics['start_date']}</StartDt>",
            f"              <EndDt>{semantics['end_date']}</EndDt>",
            f"              <TxDtTm>{semantics['transaction_datetime']}</TxDtTm>",
        ]
        proprietary_dates = semantics["proprietary_dates"]
        if not isinstance(proprietary_dates, list):
            raise AssertionError("proprietary_dates fixture must be a list")
        for item in proprietary_dates:
            if not isinstance(item, dict):
                raise AssertionError("proprietary date fixture must be a mapping")
            choice = item["date_choice"]
            lines.extend(
                [
                    "              <Prtry>",
                    f"                <Tp>{item['type']}</Tp>",
                    "                <Dt>",
                    f"                  <{choice}>{item['date_value']}</{choice}>",
                    "                </Dt>",
                    "              </Prtry>",
                ]
            )
        lines.append("            </RltdDts>")
        return "\n".join(lines)

    @classmethod
    def _with_related_dates(
        cls,
        fixture: str,
        marker: str,
        semantics: dict[str, object],
    ) -> bytes:
        """Insert TransactionDates3 after remittance and before later optional TxDtls fields."""
        return fixture.replace(
            marker,
            marker + "\n" + cls._related_dates_xml(semantics),
            1,
        ).encode("utf-8")

    def _command(self, payload: bytes, suffix: str) -> dict[str, object]:
        """Return one supported ingest command with a fresh replay key."""
        return {
            "tenant_reference": self.case.policy.tenant_reference,
            "bank_account_reference": self.bank_account_reference,
            "ingestion_idempotency_key": f"detail-related-dates-{suffix}-{uuid.uuid4().hex}",
            "message_definition_identifier": CAMT053_MESSAGE_DEFINITION,
            "statement_payload": payload.decode("utf-8"),
        }


if __name__ == "__main__":
    unittest.main()
