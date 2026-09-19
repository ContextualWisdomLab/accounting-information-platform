"""Focused REDs for proprietary related-price type and currency materiality."""

from __future__ import annotations

import hashlib
import json
import unittest

from accounting_information_platform import (
    CAMT053_MESSAGE_DEFINITION,
    load_canonical_statement_fixture,
    parse_bank_statement_payload,
)
from accounting_information_platform import bank_statement

_RELATED_PRICE_PATH = "camt.053.001.14/Stmt/Ntry/NtryDtls/TxDtls/RltdPric"


class BankStatementDetailRelatedPriceProprietaryMaterialityRedTests(unittest.TestCase):
    """Keep each admitted ProprietaryPrice2 scalar material to evidence identity."""

    def setUp(self) -> None:
        """Build one realistic V14 statement and one-field proprietary variants."""
        self.fixture = load_canonical_statement_fixture().decode("utf-8")
        self.marker = (
            "            <RmtInf>\n"
            "              <Ustrd>Invoice 1001</Ustrd>\n"
            "            </RmtInf>"
        )
        self.assertEqual(self.fixture.count(self.marker), 1)
        self.base_semantics = {
            "choice": "Prtry",
            "proprietary_prices": [
                {"type": "BANK_EXECUTION", "value": "99.5", "currency": "KRW"},
                {"type": "BANK_REFERENCE", "value": "100.0", "currency": "KRW"},
            ],
        }

    def test_proprietary_type_and_currency_are_independently_material(self) -> None:
        """Changing only Tp or only Pric/@Ccy changes the complete semantic hash chain."""
        base_statement = self._parse(self.base_semantics)
        base_entry = base_statement.entries[0]
        base_detail = base_entry.entry_details[0]
        base_hash = self._expected_hash(self.base_semantics)

        variants = []
        type_variant = self._clone_semantics()
        type_variant["proprietary_prices"][0]["type"] = "BANK_SETTLEMENT"
        variants.append(("type", type_variant))

        currency_variant = self._clone_semantics()
        currency_variant["proprietary_prices"][0]["currency"] = "USD"
        variants.append(("currency", currency_variant))

        self.assertEqual(getattr(base_detail, "related_price", None), self.base_semantics)
        self.assertEqual(
            getattr(base_detail, "related_price_evidence_hash", None), base_hash
        )
        self._assert_entry_hash_binding(base_entry, base_hash)

        for field, semantics in variants:
            with self.subTest(field=field):
                statement = self._parse(semantics)
                entry = statement.entries[0]
                detail = entry.entry_details[0]
                expected_hash = self._expected_hash(semantics)

                self.assertNotEqual(base_hash, expected_hash)
                self.assertEqual(getattr(detail, "related_price", None), semantics)
                self.assertEqual(
                    getattr(detail, "related_price_evidence_hash", None), expected_hash
                )
                self._assert_entry_hash_binding(entry, expected_hash)
                self.assertEqual(entry.entry_amount, base_entry.entry_amount)
                self.assertEqual(detail.detail_amount, base_detail.detail_amount)
                self.assertEqual(entry.entry_currency_code, base_entry.entry_currency_code)
                self.assertEqual(
                    detail.detail_currency_code, base_detail.detail_currency_code
                )
                self.assertNotEqual(
                    base_detail.source_detail_hash, detail.source_detail_hash
                )
                self.assertNotEqual(base_entry.source_entry_hash, entry.source_entry_hash)
                self.assertNotEqual(
                    base_statement.normalized_payload_hash,
                    statement.normalized_payload_hash,
                )
                self.assertEqual(
                    base_statement.entries[1].source_entry_hash,
                    statement.entries[1].source_entry_hash,
                )

    def _clone_semantics(self) -> dict[str, object]:
        """Copy the two-row proprietary price projection without sharing nested state."""
        return json.loads(json.dumps(self.base_semantics))

    def _parse(self, semantics: dict[str, object]) -> object:
        """Insert schema-positioned RltdPric and parse through the supported adapter."""
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
        related_price = (
            "            <RltdPric>\n"
            + "\n".join(rendered)
            + "\n            </RltdPric>"
        )
        payload = self.fixture.replace(
            self.marker, f"{self.marker}\n{related_price}", 1
        ).encode("utf-8")
        return parse_bank_statement_payload(payload, CAMT053_MESSAGE_DEFINITION)

    @staticmethod
    def _expected_hash(semantics: dict[str, object]) -> str:
        """Compute the exact purpose-bound related-price evidence digest."""
        preimage = json.dumps(
            {"evidence_type": _RELATED_PRICE_PATH, "related_price": semantics},
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return f"sha256:{hashlib.sha256(preimage).hexdigest()}"

    @staticmethod
    def _assert_entry_hash_binding(entry: object, expected_hash: str) -> None:
        """Prove the purpose digest is inside the canonical entry-hash preimage."""
        projection = dict(bank_statement._entry_payload(entry))
        details = projection.get("details")
        if not isinstance(details, list) or not details:
            raise AssertionError("canonical entry projection must retain transaction details")
        detail = details[0]
        if not isinstance(detail, dict):
            raise AssertionError("canonical detail projection must be a mapping")
        if detail.get("related_price_evidence_hash") != expected_hash:
            raise AssertionError(
                "canonical detail must carry exact related_price_evidence_hash"
            )
        preimage = json.dumps(
            projection, separators=(",", ":"), sort_keys=True
        ).encode("utf-8")
        expected_entry_hash = f"sha256:{hashlib.sha256(preimage).hexdigest()}"
        if getattr(entry, "source_entry_hash", None) != expected_entry_hash:
            raise AssertionError(
                "source_entry_hash must digest the related-price-bound projection"
            )


if __name__ == "__main__":
    unittest.main()
