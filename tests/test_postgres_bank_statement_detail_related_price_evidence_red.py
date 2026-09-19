"""PostgreSQL REDs for camt.053 transaction-detail related-price evidence."""

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
_RELATED_PRICE_PATH = "camt.053.001.14/Stmt/Ntry/NtryDtls/TxDtls/RltdPric"
_CORRECTION_ERROR = (
    r"^statement identity already exists with different entry evidence\. "
    r"Use an explicit correction contract, then retry ingest\.$"
)


class BankStatementDetailRelatedPriceEvidenceRedTests(unittest.TestCase):
    """Retain bank-reported related prices as evidence, never accounting authority."""

    @classmethod
    def setUpClass(cls) -> None:
        """Reuse the canonical real-PostgreSQL integration fixture."""
        posting.PostgresPostingTests.setUpClass()

    def setUp(self) -> None:
        """Build schema-valid price variants that isolate source-material changes."""
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

        self.base_semantics = {
            "choice": "DealPric",
            "deal_price": {
                "type": {"choice": "ValTp", "value": "PARV"},
                "value": {"choice": "Rate", "value": "99.5"},
            },
        }
        self.changed_rate_semantics = copy.deepcopy(self.base_semantics)
        self.changed_rate_semantics["deal_price"]["value"]["value"] = "100.5"
        self.changed_type_semantics = copy.deepcopy(self.base_semantics)
        self.changed_type_semantics["deal_price"]["type"]["value"] = "PREM"
        self.yielded_type_semantics = copy.deepcopy(self.base_semantics)
        self.yielded_type_semantics["deal_price"]["type"] = {
            "choice": "Yldd",
            "value": True,
        }
        self.amount_choice_semantics = copy.deepcopy(self.base_semantics)
        self.amount_choice_semantics["deal_price"]["value"] = {
            "choice": "Amt",
            "value": "99.5",
            "currency": "KRW",
        }
        self.proprietary_semantics = {
            "choice": "Prtry",
            "proprietary_prices": [
                {"type": "BANK_EXECUTION", "value": "99.5", "currency": "KRW"},
                {"type": "BANK_REFERENCE", "value": "100.0", "currency": "KRW"},
            ],
        }
        self.proprietary_changed_semantics = copy.deepcopy(self.proprietary_semantics)
        self.proprietary_changed_semantics["proprietary_prices"][0]["value"] = "99.6"
        self.proprietary_reordered_semantics = copy.deepcopy(self.proprietary_semantics)
        self.proprietary_reordered_semantics["proprietary_prices"].reverse()

        self.base_payload = self._with_related_price(fixture, self.base_semantics)
        self.changed_rate_payload = self._with_related_price(
            fixture, self.changed_rate_semantics
        )
        self.changed_type_payload = self._with_related_price(
            fixture, self.changed_type_semantics
        )
        self.yielded_type_payload = self._with_related_price(
            fixture, self.yielded_type_semantics
        )
        self.amount_choice_payload = self._with_related_price(
            fixture, self.amount_choice_semantics
        )
        self.layout_payload = self._with_related_price(
            fixture, self.base_semantics, compact=True
        )
        self.proprietary_payload = self._with_related_price(
            fixture, self.proprietary_semantics
        )
        self.proprietary_changed_payload = self._with_related_price(
            fixture, self.proprietary_changed_semantics
        )
        self.proprietary_reordered_payload = self._with_related_price(
            fixture, self.proprietary_reordered_semantics
        )

        self.base_statement = self._parse(self.base_payload)
        self.changed_rate_statement = self._parse(self.changed_rate_payload)
        self.changed_type_statement = self._parse(self.changed_type_payload)
        self.yielded_type_statement = self._parse(self.yielded_type_payload)
        self.amount_choice_statement = self._parse(self.amount_choice_payload)
        self.layout_statement = self._parse(self.layout_payload)
        self.proprietary_statement = self._parse(self.proprietary_payload)
        self.proprietary_changed_statement = self._parse(
            self.proprietary_changed_payload
        )
        self.proprietary_reordered_statement = self._parse(
            self.proprietary_reordered_payload
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

    def test_deal_price_fields_and_choice_branches_are_material_to_hash_chain(self) -> None:
        """Price type/value changes and both nested choices remain material evidence."""
        cases = (
            (self.changed_rate_statement, self.changed_rate_semantics),
            (self.changed_type_statement, self.changed_type_semantics),
            (self.yielded_type_statement, self.yielded_type_semantics),
            (self.amount_choice_statement, self.amount_choice_semantics),
        )
        base_hash = self._expected_hash(self.base_semantics)
        base_entry = self.base_statement.entries[0]
        base_detail = base_entry.entry_details[0]

        self.assertRegex(base_hash, _HASH_PATTERN)
        self.assertEqual(getattr(base_detail, "related_price", None), self.base_semantics)
        self.assertEqual(
            getattr(base_detail, "related_price_evidence_hash", None), base_hash
        )
        self._assert_entry_hash_binding(base_entry, base_hash)

        for statement, semantics in cases:
            with self.subTest(semantics=semantics):
                changed_hash = self._expected_hash(semantics)
                changed_entry = statement.entries[0]
                changed_detail = changed_entry.entry_details[0]
                self.assertRegex(changed_hash, _HASH_PATTERN)
                self.assertNotEqual(base_hash, changed_hash)
                self.assertEqual(getattr(changed_detail, "related_price", None), semantics)
                self.assertEqual(
                    getattr(changed_detail, "related_price_evidence_hash", None),
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

    def test_proprietary_price_value_and_source_order_are_material(self) -> None:
        """Repeated proprietary prices retain exact values and source order."""
        base_hash = self._expected_hash(self.proprietary_semantics)
        base_entry = self.proprietary_statement.entries[0]
        base_detail = base_entry.entry_details[0]
        self.assertEqual(
            getattr(base_detail, "related_price", None), self.proprietary_semantics
        )
        self.assertEqual(
            getattr(base_detail, "related_price_evidence_hash", None), base_hash
        )
        self._assert_entry_hash_binding(base_entry, base_hash)

        for statement, semantics in (
            (self.proprietary_changed_statement, self.proprietary_changed_semantics),
            (self.proprietary_reordered_statement, self.proprietary_reordered_semantics),
        ):
            with self.subTest(semantics=semantics):
                changed_hash = self._expected_hash(semantics)
                changed_entry = statement.entries[0]
                changed_detail = changed_entry.entry_details[0]
                self.assertNotEqual(base_hash, changed_hash)
                self.assertEqual(getattr(changed_detail, "related_price", None), semantics)
                self.assertEqual(
                    getattr(changed_detail, "related_price_evidence_hash", None),
                    changed_hash,
                )
                self._assert_entry_hash_binding(changed_entry, changed_hash)
                self._assert_accounting_amount_unchanged(
                    self.proprietary_statement, statement
                )
                self.assertNotEqual(
                    base_detail.source_detail_hash, changed_detail.source_detail_hash
                )
                self.assertNotEqual(
                    base_entry.source_entry_hash, changed_entry.source_entry_hash
                )
                self.assertNotEqual(
                    self.proprietary_statement.normalized_payload_hash,
                    statement.normalized_payload_hash,
                )

    def test_xml_layout_is_not_related_price_semantics(self) -> None:
        """Whitespace-only XML layout changes alter raw provenance, not price semantics."""
        expected_hash = self._expected_hash(self.base_semantics)
        base_entry = self.base_statement.entries[0]
        layout_entry = self.layout_statement.entries[0]
        base_detail = base_entry.entry_details[0]
        layout_detail = layout_entry.entry_details[0]

        self.assertNotEqual(
            self.base_statement.source_artifact_hash,
            self.layout_statement.source_artifact_hash,
        )
        self.assertEqual(getattr(layout_detail, "related_price", None), self.base_semantics)
        self.assertEqual(
            getattr(layout_detail, "related_price_evidence_hash", None), expected_hash
        )
        self._assert_entry_hash_binding(layout_entry, expected_hash)
        self.assertEqual(base_detail.source_detail_hash, layout_detail.source_detail_hash)
        self.assertEqual(base_entry.source_entry_hash, layout_entry.source_entry_hash)
        self.assertEqual(
            self.base_statement.normalized_payload_hash,
            self.layout_statement.normalized_payload_hash,
        )

    def test_changed_related_price_requires_explicit_statement_correction(self) -> None:
        """An accepted bank-reported price cannot be silently replaced on replay."""
        accepted = accept_bank_statement_evidence(
            self._command(self.base_payload, "base"),
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
            artifact_store=self.store,
        )
        self.assertFalse(accepted["replayed"])

        with self.assertRaisesRegex(AccountingValidationError, _CORRECTION_ERROR):
            accept_bank_statement_evidence(
                self._command(self.changed_rate_payload, "changed-rate"),
                posting.DATABASE_URL,
                self.case.policy.tenant_reference,
                artifact_store=self.store,
            )

    def test_buyer_read_preserves_related_price_without_repricing_accounting_amount(self) -> None:
        """Tenant reads expose related-price evidence without deriving journal value."""
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

        self.assertEqual(detail.get("related_price"), self.base_semantics)
        self.assertEqual(detail.get("related_price_evidence_hash"), expected_hash)
        self.assertEqual(entry["entry_amount"], "25000")
        self.assertEqual(entry["entry_currency_code"], "KRW")
        self.assertEqual(detail["detail_amount"], "25000")
        self.assertEqual(detail["detail_currency_code"], "KRW")

    @staticmethod
    def _parse(payload: bytes) -> object:
        """Parse one V14 statement through the supported adapter boundary."""
        return parse_bank_statement_payload(payload, CAMT053_MESSAGE_DEFINITION)

    @staticmethod
    def _expected_hash(semantics: dict[str, object]) -> str:
        """Digest related-price semantics under their exact evidence purpose."""
        preimage = json.dumps(
            {"evidence_type": _RELATED_PRICE_PATH, "related_price": semantics},
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return f"sha256:{hashlib.sha256(preimage).hexdigest()}"

    @staticmethod
    def _assert_entry_hash_binding(entry: object, expected_hash: str) -> None:
        """Require source_entry_hash to digest the price-bound canonical projection."""
        projection = dict(bank_statement._entry_payload(entry))
        details = projection.get("details")
        if not isinstance(details, list) or not details:
            raise AssertionError("canonical entry projection must retain transaction details")
        first_detail = details[0]
        if not isinstance(first_detail, dict):
            raise AssertionError("canonical entry detail projection must be a mapping")
        if first_detail.get("related_price_evidence_hash") != expected_hash:
            raise AssertionError(
                "canonical entry projection must carry exact related_price_evidence_hash"
            )
        preimage = json.dumps(
            projection, separators=(",", ":"), sort_keys=True
        ).encode("utf-8")
        expected_entry_hash = f"sha256:{hashlib.sha256(preimage).hexdigest()}"
        if getattr(entry, "source_entry_hash", None) != expected_entry_hash:
            raise AssertionError(
                "source_entry_hash must digest the canonical entry projection containing "
                "related_price_evidence_hash"
            )

    @staticmethod
    def _assert_accounting_amount_unchanged(base: object, changed: object) -> None:
        """Keep the bank-reported accounting amount independent from price evidence."""
        base_entry = base.entries[0]
        changed_entry = changed.entries[0]
        base_detail = base_entry.entry_details[0]
        changed_detail = changed_entry.entry_details[0]
        if base_entry.entry_amount != changed_entry.entry_amount:
            raise AssertionError("related price must not reprice the reported entry amount")
        if base_detail.detail_amount != changed_detail.detail_amount:
            raise AssertionError("related price must not reprice the reported detail amount")
        if base_entry.entry_currency_code != changed_entry.entry_currency_code:
            raise AssertionError("related price must not change the entry currency")
        if base_detail.detail_currency_code != changed_detail.detail_currency_code:
            raise AssertionError("related price must not change the detail currency")

    def _with_related_price(
        self,
        fixture: str,
        semantics: dict[str, object],
        *,
        compact: bool = False,
    ) -> bytes:
        """Insert one schema-positioned RelatedPrice after omitted RelatedDates."""
        xml = self._related_price_xml(semantics, compact=compact)
        replacement = f"{self.marker}\n{xml}"
        self.assertEqual(fixture.count(self.marker), 1)
        return fixture.replace(self.marker, replacement, 1).encode("utf-8")

    @staticmethod
    def _related_price_xml(semantics: dict[str, object], *, compact: bool) -> str:
        """Render the two V14 TransactionPrice4Choice branches used by this RED."""
        choice = semantics["choice"]
        if choice == "DealPric":
            deal = semantics["deal_price"]
            price_type = deal["type"]
            price_value = deal["value"]
            if price_type["choice"] == "Yldd":
                type_value = str(price_type["value"]).lower()
            else:
                type_value = str(price_type["value"])
            type_xml = (
                f"<{price_type['choice']}>{type_value}</{price_type['choice']}>"
            )
            if price_value["choice"] == "Rate":
                value_xml = f"<Rate>{price_value['value']}</Rate>"
            elif price_value["choice"] == "Amt":
                value_xml = (
                    f"<Amt Ccy=\"{price_value['currency']}\">"
                    f"{price_value['value']}</Amt>"
                )
            else:
                raise AssertionError("unsupported focused PriceRateOrAmount3Choice")
            if compact:
                return (
                    "            <RltdPric><DealPric><Tp>"
                    f"{type_xml}</Tp><Val>{value_xml}</Val>"
                    "</DealPric></RltdPric>"
                )
            return (
                "            <RltdPric>\n"
                "              <DealPric>\n"
                f"                <Tp>{type_xml}</Tp>\n"
                f"                <Val>{value_xml}</Val>\n"
                "              </DealPric>\n"
                "            </RltdPric>"
            )
        if choice == "Prtry":
            prices = semantics["proprietary_prices"]
            rendered = []
            for price in prices:
                rendered.append(
                    "              <Prtry>\n"
                    f"                <Tp>{price['type']}</Tp>\n"
                    f"                <Pric Ccy=\"{price['currency']}\">"
                    f"{price['value']}</Pric>\n"
                    "              </Prtry>"
                )
            return (
                "            <RltdPric>\n"
                + "\n".join(rendered)
                + "\n            </RltdPric>"
            )
        raise AssertionError("unsupported focused TransactionPrice4Choice")

    def _command(self, payload: bytes, suffix: str) -> dict[str, object]:
        """Return one supported ingest command with a fresh tenant-scoped replay key."""
        return {
            "tenant_reference": self.case.policy.tenant_reference,
            "bank_account_reference": self.bank_account_reference,
            "ingestion_idempotency_key": f"related-price-{suffix}-{uuid.uuid4().hex}",
            "message_definition_identifier": CAMT053_MESSAGE_DEFINITION,
            "statement_payload": payload.decode("utf-8"),
        }


if __name__ == "__main__":
    unittest.main()
