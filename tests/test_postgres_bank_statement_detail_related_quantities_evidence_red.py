"""PostgreSQL REDs for camt.053 transaction-detail related-quantity evidence."""

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
_RELATED_QUANTITIES_PATH = "camt.053.001.14/Stmt/Ntry/NtryDtls/TxDtls/RltdQties"
_CORRECTION_ERROR = (
    r"^statement identity already exists with different entry evidence\. "
    r"Use an explicit correction contract, then retry ingest\.$"
)


class BankStatementDetailRelatedQuantitiesEvidenceRedTests(unittest.TestCase):
    """Retain bank-reported quantities as evidence, never accounting authority."""

    @classmethod
    def setUpClass(cls) -> None:
        """Reuse the canonical real-PostgreSQL integration fixture."""
        posting.PostgresPostingTests.setUpClass()

    def setUp(self) -> None:
        """Build schema-valid V14 quantity variants with one material change each."""
        self.case = posting.PostgresPostingTests("setUp")
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.addCleanup(self.case.tearDown)

        fixture = load_canonical_statement_fixture().decode("utf-8")
        self.marker = (
            "            <RmtInf>\n"
            "              <Ustrd>Invoice 1001</Ustrd>\n"
            "            </RmtInf>"
        )
        self.assertEqual(fixture.count(self.marker), 1)

        self.base_semantics: list[dict[str, object]] = [
            {"choice": "Qty", "quantity": {"choice": "Unit", "value": "100"}},
            {
                "choice": "OrgnlAndCurFaceAmt",
                "original_and_current_face_amount": {
                    "face_amount": "1000",
                    "amortised_value": "900",
                },
            },
            {
                "choice": "Prtry",
                "proprietary": {"type": "BANK_LOT", "quantity": "42"},
            },
        ]
        variants = {
            "unit_value": copy.deepcopy(self.base_semantics),
            "qty_face_amount_choice": copy.deepcopy(self.base_semantics),
            "qty_amortised_value_choice": copy.deepcopy(self.base_semantics),
            "qty_digital_token_choice": copy.deepcopy(self.base_semantics),
            "original_face_amount": copy.deepcopy(self.base_semantics),
            "original_amortised_value": copy.deepcopy(self.base_semantics),
            "proprietary_type": copy.deepcopy(self.base_semantics),
            "proprietary_quantity": copy.deepcopy(self.base_semantics),
            "order": copy.deepcopy(self.base_semantics),
        }
        variants["unit_value"][0]["quantity"]["value"] = "101"
        variants["qty_face_amount_choice"][0]["quantity"] = {
            "choice": "FaceAmt",
            "value": "100",
        }
        variants["qty_amortised_value_choice"][0]["quantity"] = {
            "choice": "AmtsdVal",
            "value": "100",
        }
        variants["qty_digital_token_choice"][0]["quantity"] = {
            "choice": "DgtlTknUnit",
            "value": "100",
        }
        variants["original_face_amount"][1]["original_and_current_face_amount"][
            "face_amount"
        ] = "1001"
        variants["original_amortised_value"][1]["original_and_current_face_amount"][
            "amortised_value"
        ] = "901"
        variants["proprietary_type"][2]["proprietary"]["type"] = "BANK_POSITION"
        variants["proprietary_quantity"][2]["proprietary"]["quantity"] = "43"
        variants["order"].reverse()
        self.variants = variants

        self.base_payload = self._with_related_quantities(fixture, self.base_semantics)
        self.base_statement = self._parse(self.base_payload)
        self.variant_payloads = {
            name: self._with_related_quantities(fixture, semantics)
            for name, semantics in self.variants.items()
        }
        self.variant_statements = {
            name: self._parse(payload) for name, payload in self.variant_payloads.items()
        }
        self.layout_payload = self._with_related_quantities(
            fixture, self.base_semantics, compact=True
        )
        self.layout_statement = self._parse(self.layout_payload)

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

    def test_all_v14_quantity_branches_and_fields_are_material_to_hash_chain(self) -> None:
        """Every admitted V14 quantity branch and scalar remains material evidence."""
        base_hash = self._expected_hash(self.base_semantics)
        base_entry = self.base_statement.entries[0]
        base_detail = base_entry.entry_details[0]

        self.assertRegex(base_hash, _HASH_PATTERN)
        self.assertEqual(
            getattr(base_detail, "related_quantities", None), self.base_semantics
        )
        self.assertEqual(
            getattr(base_detail, "related_quantities_evidence_hash", None), base_hash
        )
        self._assert_entry_hash_binding(base_entry, base_hash)

        for name, statement in self.variant_statements.items():
            semantics = self.variants[name]
            with self.subTest(name=name):
                changed_hash = self._expected_hash(semantics)
                changed_entry = statement.entries[0]
                changed_detail = changed_entry.entry_details[0]
                self.assertRegex(changed_hash, _HASH_PATTERN)
                self.assertNotEqual(base_hash, changed_hash)
                self.assertEqual(
                    getattr(changed_detail, "related_quantities", None), semantics
                )
                self.assertEqual(
                    getattr(changed_detail, "related_quantities_evidence_hash", None),
                    changed_hash,
                )
                self._assert_entry_hash_binding(changed_entry, changed_hash)
                self._assert_accounting_amount_unchanged(
                    self.base_statement, statement
                )
                self.assertNotEqual(
                    base_detail.source_detail_hash, changed_detail.source_detail_hash
                )
                self.assertNotEqual(
                    base_entry.source_entry_hash, changed_entry.source_entry_hash
                )
                self.assertNotEqual(
                    self.base_statement.normalized_payload_hash,
                    statement.normalized_payload_hash,
                )
                self.assertEqual(
                    self.base_statement.entries[1].source_entry_hash,
                    statement.entries[1].source_entry_hash,
                )

    def test_xml_layout_is_not_related_quantity_semantics(self) -> None:
        """Whitespace-only XML layout changes raw provenance, not quantity semantics."""
        expected_hash = self._expected_hash(self.base_semantics)
        base_entry = self.base_statement.entries[0]
        layout_entry = self.layout_statement.entries[0]
        base_detail = base_entry.entry_details[0]
        layout_detail = layout_entry.entry_details[0]

        self.assertNotEqual(
            self.base_statement.source_artifact_hash,
            self.layout_statement.source_artifact_hash,
        )
        self.assertEqual(
            getattr(layout_detail, "related_quantities", None), self.base_semantics
        )
        self.assertEqual(
            getattr(layout_detail, "related_quantities_evidence_hash", None),
            expected_hash,
        )
        self._assert_entry_hash_binding(layout_entry, expected_hash)
        self.assertEqual(base_detail.source_detail_hash, layout_detail.source_detail_hash)
        self.assertEqual(base_entry.source_entry_hash, layout_entry.source_entry_hash)
        self.assertEqual(
            self.base_statement.normalized_payload_hash,
            self.layout_statement.normalized_payload_hash,
        )

    def test_changed_related_quantity_requires_explicit_statement_correction(self) -> None:
        """Accepted quantity evidence cannot be silently replaced on replay."""
        accepted = accept_bank_statement_evidence(
            self._command(self.base_payload, "base"),
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
            artifact_store=self.store,
        )
        self.assertFalse(accepted["replayed"])

        with self.assertRaisesRegex(AccountingValidationError, _CORRECTION_ERROR):
            accept_bank_statement_evidence(
                self._command(self.variant_payloads["unit_value"], "changed-unit"),
                posting.DATABASE_URL,
                self.case.policy.tenant_reference,
                artifact_store=self.store,
            )

    def test_buyer_read_preserves_related_quantities_without_deriving_amount(self) -> None:
        """Tenant reads expose quantity evidence without recalculating accounting value."""
        expected_hash = self._expected_hash(self.base_semantics)
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

        self.assertEqual(detail.get("related_quantities"), self.base_semantics)
        self.assertEqual(detail.get("related_quantities_evidence_hash"), expected_hash)
        self.assertEqual(entry["entry_amount"], "25000")
        self.assertEqual(entry["entry_currency_code"], "KRW")
        self.assertEqual(detail["detail_amount"], "25000")
        self.assertEqual(detail["detail_currency_code"], "KRW")

    @staticmethod
    def _parse(payload: bytes) -> object:
        """Parse one V14 statement through the supported adapter boundary."""
        return parse_bank_statement_payload(payload, CAMT053_MESSAGE_DEFINITION)

    @staticmethod
    def _expected_hash(semantics: list[dict[str, object]]) -> str:
        """Digest related-quantity semantics under their exact evidence purpose."""
        preimage = json.dumps(
            {
                "evidence_type": _RELATED_QUANTITIES_PATH,
                "related_quantities": semantics,
            },
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return f"sha256:{hashlib.sha256(preimage).hexdigest()}"

    @staticmethod
    def _assert_entry_hash_binding(entry: object, expected_hash: str) -> None:
        """Require source_entry_hash to digest the quantity-bound canonical projection."""
        projection = dict(bank_statement._entry_payload(entry))
        details = projection.get("details")
        if not isinstance(details, list) or not details:
            raise AssertionError("canonical entry projection must retain transaction details")
        first_detail = details[0]
        if not isinstance(first_detail, dict):
            raise AssertionError("canonical entry detail projection must be a mapping")
        if first_detail.get("related_quantities_evidence_hash") != expected_hash:
            raise AssertionError(
                "canonical entry projection must carry exact "
                "related_quantities_evidence_hash"
            )
        preimage = json.dumps(
            projection, separators=(",", ":"), sort_keys=True
        ).encode("utf-8")
        expected_entry_hash = f"sha256:{hashlib.sha256(preimage).hexdigest()}"
        if getattr(entry, "source_entry_hash", None) != expected_entry_hash:
            raise AssertionError(
                "source_entry_hash must digest the canonical entry projection containing "
                "related_quantities_evidence_hash"
            )

    @staticmethod
    def _assert_accounting_amount_unchanged(base: object, changed: object) -> None:
        """Keep the reported accounting amount independent from quantity evidence."""
        base_entry = base.entries[0]
        changed_entry = changed.entries[0]
        base_detail = base_entry.entry_details[0]
        changed_detail = changed_entry.entry_details[0]
        if base_entry.entry_amount != changed_entry.entry_amount:
            raise AssertionError("related quantities must not change the entry amount")
        if base_detail.detail_amount != changed_detail.detail_amount:
            raise AssertionError("related quantities must not change the detail amount")
        if base_entry.entry_currency_code != changed_entry.entry_currency_code:
            raise AssertionError("related quantities must not change the entry currency")
        if base_detail.detail_currency_code != changed_detail.detail_currency_code:
            raise AssertionError("related quantities must not change the detail currency")

    def _with_related_quantities(
        self,
        fixture: str,
        semantics: list[dict[str, object]],
        *,
        compact: bool = False,
    ) -> bytes:
        """Insert repeated V14 RltdQties after omitted RltdDts/RltdPric siblings."""
        xml = self._related_quantities_xml(semantics, compact=compact)
        replacement = f"{self.marker}\n{xml}"
        self.assertEqual(fixture.count(self.marker), 1)
        return fixture.replace(self.marker, replacement, 1).encode("utf-8")

    @staticmethod
    def _related_quantities_xml(
        semantics: list[dict[str, object]], *, compact: bool
    ) -> str:
        """Render V14 TransactionQuantities4Choice and nested quantity choices."""
        rendered: list[str] = []
        for item in semantics:
            choice = item["choice"]
            if choice == "Qty":
                quantity = item["quantity"]
                inner = (
                    f"<{quantity['choice']}>{quantity['value']}</{quantity['choice']}>"
                )
                body = f"<Qty>{inner}</Qty>"
            elif choice == "OrgnlAndCurFaceAmt":
                face = item["original_and_current_face_amount"]
                body = (
                    "<OrgnlAndCurFaceAmt>"
                    f"<FaceAmt>{face['face_amount']}</FaceAmt>"
                    f"<AmtsdVal>{face['amortised_value']}</AmtsdVal>"
                    "</OrgnlAndCurFaceAmt>"
                )
            elif choice == "Prtry":
                proprietary = item["proprietary"]
                body = (
                    "<Prtry>"
                    f"<Tp>{proprietary['type']}</Tp>"
                    f"<Qty>{proprietary['quantity']}</Qty>"
                    "</Prtry>"
                )
            else:
                raise AssertionError("unsupported focused TransactionQuantities4Choice")
            if compact:
                rendered.append(f"            <RltdQties>{body}</RltdQties>")
            else:
                rendered.append(
                    "            <RltdQties>\n"
                    f"              {body}\n"
                    "            </RltdQties>"
                )
        return "\n".join(rendered)

    def _command(self, payload: bytes, suffix: str) -> dict[str, object]:
        """Return one supported ingest command with a fresh tenant-scoped replay key."""
        return {
            "tenant_reference": self.case.policy.tenant_reference,
            "bank_account_reference": self.bank_account_reference,
            "ingestion_idempotency_key": f"related-quantities-{suffix}-{uuid.uuid4().hex}",
            "message_definition_identifier": CAMT053_MESSAGE_DEFINITION,
            "statement_payload": payload.decode("utf-8"),
        }


if __name__ == "__main__":
    unittest.main()
