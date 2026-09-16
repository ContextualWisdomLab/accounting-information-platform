"""PostgreSQL REDs for complete camt.053 SecurityIdentification19 evidence."""

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
_FINANCIAL_INSTRUMENT_PATH = (
    "camt.053.001.14/Stmt/Ntry/NtryDtls/TxDtls/FinInstrmId"
)
_CORRECTION_ERROR = (
    r"^statement identity already exists with different entry evidence\. "
    r"Use an explicit correction contract, then retry ingest\.$"
)


class BankStatementDetailFinancialInstrumentOtherIdentificationEvidenceRedTests(
    unittest.TestCase
):
    """Retain complete SecurityIdentification19 evidence without creating position truth."""

    @classmethod
    def setUpClass(cls) -> None:
        """Reuse the canonical real-PostgreSQL integration fixture."""
        posting.PostgresPostingTests.setUpClass()

    def setUp(self) -> None:
        """Build one full V14 instrument and isolated OtherIdentification1 variants."""
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
            "isin": "US0378331005",
            "other_identifications": [
                {
                    "id": "037833100",
                    "suffix": "01",
                    "type": {"choice": "Cd", "value": "CUSP"},
                },
                {
                    "id": "BBG000B9XRY4",
                    "suffix": None,
                    "type": {"choice": "Prtry", "value": "FIGI"},
                },
            ],
            "description": "Apple Inc common equity",
        }
        self.variants = self._material_variants(self.base)
        self.base_payload = self._with_financial_instrument(fixture, marker, self.base)
        self.variant_payloads = {
            name: self._with_financial_instrument(fixture, marker, value)
            for name, value in self.variants.items()
        }

        self.base_statement = parse_bank_statement_payload(
            self.base_payload, CAMT053_MESSAGE_DEFINITION
        )
        self.variant_statements = {
            name: parse_bank_statement_payload(payload, CAMT053_MESSAGE_DEFINITION)
            for name, payload in self.variant_payloads.items()
        }

        instrument_xml = self._financial_instrument_xml(self.base)
        self.assertEqual(self.base_payload.count(instrument_xml.encode("utf-8")), 1)
        reformatted_xml = instrument_xml.replace(
            "            <FinInstrmId>\n",
            "            <FinInstrmId>\n              \n",
            1,
        )
        self.reformatted_payload = self.base_payload.replace(
            instrument_xml.encode("utf-8"),
            reformatted_xml.encode("utf-8"),
            1,
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

    def test_every_other_identification_semantic_is_material_evidence(self) -> None:
        """Id, suffix, type choice/value, repeated order, and description affect identity."""
        base_hash = self._expected_hash(self.base)
        base_entry = self.base_statement.entries[0]
        base_detail = base_entry.entry_details[0]

        self.assertRegex(base_hash, _HASH_PATTERN)
        self.assertEqual(
            getattr(base_detail, "financial_instrument_evidence_hash", None),
            base_hash,
        )
        self._assert_entry_hash_binding(base_entry, base_hash)

        for name, statement in self.variant_statements.items():
            with self.subTest(name=name):
                expected_hash = self._expected_hash(self.variants[name])
                entry = statement.entries[0]
                detail = entry.entry_details[0]

                self.assertNotEqual(base_hash, expected_hash)
                self.assertEqual(
                    getattr(detail, "financial_instrument_evidence_hash", None),
                    expected_hash,
                )
                self._assert_entry_hash_binding(entry, expected_hash)
                self.assertEqual(
                    self.base_statement.account_identifier_hash,
                    statement.account_identifier_hash,
                )
                self.assertEqual(base_entry.entry_amount, entry.entry_amount)
                self.assertEqual(
                    base_entry.entry_currency_code,
                    entry.entry_currency_code,
                )
                self.assertEqual(base_detail.detail_amount, detail.detail_amount)
                self.assertEqual(
                    base_detail.detail_currency_code,
                    detail.detail_currency_code,
                )
                self.assertNotEqual(
                    base_detail.source_detail_hash,
                    detail.source_detail_hash,
                )
                self.assertNotEqual(
                    base_entry.source_entry_hash,
                    entry.source_entry_hash,
                )
                self.assertNotEqual(
                    self.base_statement.normalized_payload_hash,
                    statement.normalized_payload_hash,
                )
                self.assertEqual(
                    self.base_statement.entries[1].source_entry_hash,
                    statement.entries[1].source_entry_hash,
                )

    def test_xml_layout_does_not_change_financial_instrument_semantics(self) -> None:
        """Whitespace belongs to artifact provenance, not SecurityIdentification19 identity."""
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
            base_detail.source_detail_hash,
            reformatted_detail.source_detail_hash,
        )
        self.assertEqual(
            base_entry.source_entry_hash,
            reformatted_entry.source_entry_hash,
        )
        self.assertEqual(
            self.base_statement.normalized_payload_hash,
            self.reformatted_statement.normalized_payload_hash,
        )

    def test_changed_other_identification_requires_explicit_statement_correction(self) -> None:
        """Accepted non-ISIN instrument evidence cannot be silently replaced."""
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

    def test_buyer_read_preserves_complete_security_identification19(self) -> None:
        """Tenant reads expose all admitted instrument IDs while 25000 KRW stays bank truth."""
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
        self.assertEqual(
            detail.get("financial_instrument_identification"),
            self.base,
        )
        self.assertEqual(entry["entry_amount"], "25000")
        self.assertEqual(entry["entry_currency_code"], "KRW")
        self.assertEqual(detail["detail_amount"], "25000")
        self.assertEqual(detail["detail_currency_code"], "KRW")

    @staticmethod
    def _material_variants(base: dict[str, object]) -> dict[str, dict[str, object]]:
        """Return one isolated mutation for every uncovered SecurityIdentification19 field."""
        variants: dict[str, dict[str, object]] = {}

        other_id = copy.deepcopy(base)
        other_id["other_identifications"][0]["id"] = "037833101"
        variants["other-id"] = other_id

        suffix = copy.deepcopy(base)
        suffix["other_identifications"][0]["suffix"] = "02"
        variants["suffix"] = suffix

        type_value = copy.deepcopy(base)
        type_value["other_identifications"][0]["type"]["value"] = "SEDL"
        variants["type-value"] = type_value

        type_discriminator = copy.deepcopy(base)
        type_discriminator["other_identifications"][0]["type"] = {
            "choice": "Prtry",
            "value": "CUSP",
        }
        variants["type-discriminator"] = type_discriminator

        proprietary_type_value = copy.deepcopy(base)
        proprietary_type_value["other_identifications"][1]["type"]["value"] = "CUSIP"
        variants["proprietary-type-value"] = proprietary_type_value

        repeated_order = copy.deepcopy(base)
        repeated_order["other_identifications"] = list(
            reversed(repeated_order["other_identifications"])
        )
        variants["other-identification-order"] = repeated_order

        description = copy.deepcopy(base)
        description["description"] = "Apple Inc voting common equity"
        variants["description"] = description

        return variants

    @staticmethod
    def _assert_entry_hash_binding(entry: object, expected_hash: str) -> None:
        """Require source_entry_hash to digest the instrument-bound canonical detail."""
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
            projection,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        expected_entry_hash = f"sha256:{hashlib.sha256(preimage).hexdigest()}"
        if getattr(entry, "source_entry_hash", None) != expected_entry_hash:
            raise AssertionError(
                "source_entry_hash must digest the canonical entry projection containing "
                "financial_instrument_evidence_hash"
            )

    @classmethod
    def _expected_hash(cls, value: dict[str, object]) -> str:
        """Digest the complete admitted SecurityIdentification19 projection."""
        preimage = json.dumps(
            {
                "evidence_type": _FINANCIAL_INSTRUMENT_PATH,
                "financial_instrument_identification": value,
            },
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return f"sha256:{hashlib.sha256(preimage).hexdigest()}"

    @classmethod
    def _financial_instrument_xml(cls, value: dict[str, object]) -> str:
        """Serialize SecurityIdentification19 in V14 schema order."""
        lines = ["            <FinInstrmId>"]
        isin = value.get("isin")
        if isin:
            lines.append(f"              <ISIN>{isin}</ISIN>")
        for other in value["other_identifications"]:
            lines.extend(
                [
                    "              <OthrId>",
                    f"                <Id>{other['id']}</Id>",
                ]
            )
            if other.get("suffix") is not None:
                lines.append(f"                <Sfx>{other['suffix']}</Sfx>")
            lines.extend(
                [
                    "                <Tp>",
                    (
                        f"                  <{other['type']['choice']}>"
                        f"{other['type']['value']}</{other['type']['choice']}>"
                    ),
                    "                </Tp>",
                    "              </OthrId>",
                ]
            )
        description = value.get("description")
        if description:
            lines.append(f"              <Desc>{description}</Desc>")
        lines.extend(["            </FinInstrmId>", ""])
        return "\n".join(lines)

    @classmethod
    def _with_financial_instrument(
        cls,
        fixture: str,
        marker: str,
        value: dict[str, object],
    ) -> bytes:
        """Insert the complete FinInstrmId after RmtInf, before later optional siblings."""
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
                f"detail-financial-instrument-other-{suffix}-{uuid.uuid4().hex}"
            ),
            "message_definition_identifier": CAMT053_MESSAGE_DEFINITION,
            "statement_payload": payload.decode("utf-8"),
        }


if __name__ == "__main__":
    unittest.main()
