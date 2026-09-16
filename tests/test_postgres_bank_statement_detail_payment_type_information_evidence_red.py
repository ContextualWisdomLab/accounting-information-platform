"""PostgreSQL REDs for camt.053 transaction-detail payment-type evidence."""

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
_PAYMENT_TYPE_PURPOSE = "camt.053.001.14/Stmt/Ntry/NtryDtls/TxDtls/PmtTpInf"
_CORRECTION_ERROR = (
    r"^statement identity already exists with different entry evidence\. "
    r"Use an explicit correction contract, then retry ingest\.$"
)


class BankStatementDetailPaymentTypeInformationEvidenceRedTests(unittest.TestCase):
    """Retain PaymentTypeInformation27 as bank-reported transaction-detail evidence."""

    @classmethod
    def setUpClass(cls) -> None:
        """Reuse the canonical real-PostgreSQL integration fixture."""
        posting.PostgresPostingTests.setUpClass()

    def setUp(self) -> None:
        """Prepare one-field, choice, repeat-order, and representation-control variants."""
        self.case = posting.PostgresPostingTests("setUp")
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.addCleanup(self.case.tearDown)

        fixture = load_canonical_statement_fixture().decode("utf-8")
        marker = "            </RltdPties>\n            <RmtInf>"
        self.assertEqual(fixture.count(marker), 1)

        self.base = {
            "instruction_priority": "HIGH",
            "clearing_channel": "RTGS",
            "service_levels": [
                {"choice": "code", "value": "SEPA"},
                {"choice": "proprietary", "value": "BANK-PRIORITY"},
            ],
            "local_instrument": {"choice": "code", "value": "CORE"},
            "sequence_type": "FRST",
            "category_purpose": {"choice": "code", "value": "SUPP"},
        }
        self.variants = {
            "priority": self._changed(instruction_priority="NORM"),
            "clearing-channel": self._changed(clearing_channel="RTNS"),
            "service-value": self._changed(
                service_levels=[
                    {"choice": "code", "value": "URGP"},
                    {"choice": "proprietary", "value": "BANK-PRIORITY"},
                ]
            ),
            "service-choice": self._changed(
                service_levels=[
                    {"choice": "proprietary", "value": "SEPA"},
                    {"choice": "proprietary", "value": "BANK-PRIORITY"},
                ]
            ),
            "service-order": self._changed(
                service_levels=[
                    {"choice": "proprietary", "value": "BANK-PRIORITY"},
                    {"choice": "code", "value": "SEPA"},
                ]
            ),
            "local-instrument-value": self._changed(
                local_instrument={"choice": "code", "value": "B2B"}
            ),
            "local-instrument-choice": self._changed(
                local_instrument={"choice": "proprietary", "value": "CORE"}
            ),
            "sequence-type": self._changed(sequence_type="RCUR"),
            "category-purpose-value": self._changed(
                category_purpose={"choice": "code", "value": "SALA"}
            ),
            "category-purpose-choice": self._changed(
                category_purpose={"choice": "proprietary", "value": "SUPP"}
            ),
        }

        self.base_payload = self._with_payment_type_information(fixture, marker, self.base)
        self.base_statement = parse_bank_statement_payload(
            self.base_payload, CAMT053_MESSAGE_DEFINITION
        )
        self.variant_payloads = {
            name: self._with_payment_type_information(fixture, marker, value)
            for name, value in self.variants.items()
        }
        self.variant_statements = {
            name: parse_bank_statement_payload(payload, CAMT053_MESSAGE_DEFINITION)
            for name, payload in self.variant_payloads.items()
        }

        payment_xml = self._payment_type_information_xml(self.base)
        self.assertEqual(self.base_payload.count(payment_xml.encode("utf-8")), 1)
        reformatted_xml = payment_xml.replace(
            "            <PmtTpInf>\n",
            "            <PmtTpInf>\n              \n",
            1,
        )
        self.reformatted_payload = self.base_payload.replace(
            payment_xml.encode("utf-8"), reformatted_xml.encode("utf-8"), 1
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

    def test_each_payment_type_semantic_is_material_to_evidence_identity(self) -> None:
        """Each admitted scalar, choice discriminator, and repeat order changes identity alone."""
        base_hash = self._expected_hash(self.base)
        base_detail = self.base_statement.entries[0].entry_details[0]
        self.assertRegex(base_hash, _HASH_PATTERN)
        self.assertEqual(
            getattr(base_detail, "payment_type_information_evidence_hash", None),
            base_hash,
        )
        self._assert_entry_hash_binding(self.base_statement.entries[0], base_hash)

        variant_hashes = {name: self._expected_hash(value) for name, value in self.variants.items()}
        self.assertEqual(len(set(variant_hashes.values())), len(variant_hashes))
        self.assertNotIn(base_hash, set(variant_hashes.values()))

        for name, statement in self.variant_statements.items():
            with self.subTest(name=name):
                expected_hash = variant_hashes[name]
                detail = statement.entries[0].entry_details[0]
                self.assertEqual(
                    getattr(detail, "payment_type_information_evidence_hash", None),
                    expected_hash,
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

    def test_xml_layout_does_not_change_payment_type_semantics(self) -> None:
        """Element-layout whitespace changes raw provenance, not PaymentTypeInformation27 identity."""
        expected = self._expected_hash(self.base)
        base_detail = self.base_statement.entries[0].entry_details[0]
        reformatted_detail = self.reformatted_statement.entries[0].entry_details[0]

        self.assertNotEqual(
            self.base_statement.source_artifact_hash,
            self.reformatted_statement.source_artifact_hash,
        )
        self.assertEqual(
            getattr(base_detail, "payment_type_information_evidence_hash", None), expected
        )
        self.assertEqual(
            getattr(reformatted_detail, "payment_type_information_evidence_hash", None),
            expected,
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

    def test_changed_payment_type_requires_explicit_statement_correction(self) -> None:
        """Accepted payment-routing evidence cannot be replaced by silent replay."""
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

    def test_buyer_read_preserves_payment_type_without_changing_amount_truth(self) -> None:
        """Tenant-scoped reads expose payment routing provenance without promoting amount truth."""
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

        self.assertEqual(detail.get("payment_type_information_evidence_hash"), expected_hash)
        self.assertEqual(detail.get("payment_type_information"), self.base)
        self.assertEqual(entry["entry_amount"], "25000")
        self.assertEqual(entry["entry_currency_code"], "KRW")
        self.assertEqual(detail["detail_amount"], "25000")
        self.assertEqual(detail["detail_currency_code"], "KRW")

    def _changed(self, **changes: object) -> dict[str, object]:
        """Return a deep-enough copy so every RED mutates one semantic dimension only."""
        value: dict[str, object] = {
            "instruction_priority": self.base["instruction_priority"],
            "clearing_channel": self.base["clearing_channel"],
            "service_levels": [dict(item) for item in self.base["service_levels"]],
            "local_instrument": dict(self.base["local_instrument"]),
            "sequence_type": self.base["sequence_type"],
            "category_purpose": dict(self.base["category_purpose"]),
        }
        value.update(changes)
        return value

    @staticmethod
    def _assert_entry_hash_binding(entry: object, expected_hash: str) -> None:
        """Require source_entry_hash to digest the purpose-bound payment-type hash."""
        projection = dict(bank_statement._entry_payload(entry))
        details = projection.get("details")
        if not isinstance(details, list) or not details:
            raise AssertionError("canonical entry projection must retain transaction details")
        first_detail = details[0]
        if not isinstance(first_detail, dict):
            raise AssertionError("canonical entry detail projection must be a mapping")
        if first_detail.get("payment_type_information_evidence_hash") != expected_hash:
            raise AssertionError(
                "canonical entry projection must carry the exact payment_type_information_evidence_hash"
            )
        preimage = json.dumps(
            projection, separators=(",", ":"), sort_keys=True
        ).encode("utf-8")
        expected_entry_hash = f"sha256:{hashlib.sha256(preimage).hexdigest()}"
        if getattr(entry, "source_entry_hash", None) != expected_entry_hash:
            raise AssertionError(
                "source_entry_hash must digest the canonical entry projection containing "
                "payment_type_information_evidence_hash"
            )

    @staticmethod
    def _expected_hash(value: dict[str, object]) -> str:
        """Digest the complete admitted PaymentTypeInformation27 projection."""
        preimage = json.dumps(
            {
                "evidence_type": _PAYMENT_TYPE_PURPOSE,
                "payment_type_information": value,
            },
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return f"sha256:{hashlib.sha256(preimage).hexdigest()}"

    @staticmethod
    def _choice_xml(name: str, value: object, indent: str) -> str:
        """Serialize one code/proprietary choice with the caller-provided element name."""
        if not isinstance(value, dict):
            raise AssertionError(f"{name} must be a choice mapping")
        choice = value["choice"]
        source_value = value["value"]
        tag = {"code": "Cd", "proprietary": "Prtry"}[str(choice)]
        return (
            f"{indent}<{name}>\n"
            f"{indent}  <{tag}>{source_value}</{tag}>\n"
            f"{indent}</{name}>\n"
        )

    @classmethod
    def _payment_type_information_xml(cls, value: dict[str, object]) -> str:
        """Serialize PaymentTypeInformation27 in camt.053.001.14 schema order."""
        lines = [
            "            <PmtTpInf>\n",
            f"              <InstrPrty>{value['instruction_priority']}</InstrPrty>\n",
            f"              <ClrChanl>{value['clearing_channel']}</ClrChanl>\n",
        ]
        service_levels = value["service_levels"]
        if not isinstance(service_levels, list):
            raise AssertionError("service_levels must be source-ordered")
        for service_level in service_levels:
            lines.append(cls._choice_xml("SvcLvl", service_level, "              "))
        lines.append(cls._choice_xml("LclInstrm", value["local_instrument"], "              "))
        lines.append(f"              <SeqTp>{value['sequence_type']}</SeqTp>\n")
        lines.append(cls._choice_xml("CtgyPurp", value["category_purpose"], "              "))
        lines.append("            </PmtTpInf>\n")
        return "".join(lines)

    @classmethod
    def _with_payment_type_information(
        cls, fixture: str, marker: str, value: dict[str, object]
    ) -> bytes:
        """Insert PmtTpInf after related parties and before later TxDtls elements."""
        return fixture.replace(
            marker,
            "            </RltdPties>\n"
            + cls._payment_type_information_xml(value)
            + "            <RmtInf>",
            1,
        ).encode("utf-8")

    def _command(self, payload: bytes, suffix: str) -> dict[str, object]:
        """Return one supported ingest command with a fresh replay key."""
        return {
            "tenant_reference": self.case.policy.tenant_reference,
            "bank_account_reference": self.bank_account_reference,
            "ingestion_idempotency_key": (
                f"detail-payment-type-{suffix}-{uuid.uuid4().hex}"
            ),
            "message_definition_identifier": CAMT053_MESSAGE_DEFINITION,
            "statement_payload": payload.decode("utf-8"),
        }


if __name__ == "__main__":
    unittest.main()
