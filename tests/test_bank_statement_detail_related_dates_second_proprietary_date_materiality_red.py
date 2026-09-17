"""Focused RED for second repeated camt.053 proprietary-date value materiality."""

from __future__ import annotations

import hashlib
import json
import unittest
from decimal import Decimal

from accounting_information_platform import (
    CAMT053_MESSAGE_DEFINITION,
    load_canonical_statement_fixture,
    parse_bank_statement_payload,
)
from accounting_information_platform import bank_statement

_RELATED_DATES_PATH = "camt.053.001.14/Stmt/Ntry/NtryDtls/TxDtls/RltdDts"


class BankStatementDetailRelatedDatesSecondProprietaryDateMaterialityRedTests(
    unittest.TestCase
):
    """Keep the second repeated ProprietaryDate3 `Dt` scalar material at index one."""

    def test_second_proprietary_date_date_value_is_independently_material(self) -> None:
        """Change only `Prtry[1]/Dt/Dt` while preserving the preceding record."""
        fixture = load_canonical_statement_fixture().decode("utf-8")
        marker = (
            "            <RmtInf>\n"
            "              <Ustrd>Invoice 1001</Ustrd>\n"
            "            </RmtInf>"
        )
        self.assertEqual(fixture.count(marker), 1)

        base_semantics = {
            "proprietary_dates": [
                {
                    "type": "BANK_BOOKING_CUTOFF",
                    "date": {
                        "choice": "DtTm",
                        "value": "2026-08-23T16:00:00+00:00",
                    },
                },
                {
                    "type": "BANK_VALUE_OVERRIDE",
                    "date": {"choice": "Dt", "value": "2026-08-24"},
                },
            ]
        }
        changed_semantics = {
            "proprietary_dates": [
                {
                    "type": "BANK_BOOKING_CUTOFF",
                    "date": {
                        "choice": "DtTm",
                        "value": "2026-08-23T16:00:00+00:00",
                    },
                },
                {
                    "type": "BANK_VALUE_OVERRIDE",
                    "date": {"choice": "Dt", "value": "2026-08-25"},
                },
            ]
        }

        base = self._parse(self._with_related_dates(fixture, marker, "2026-08-24"))
        changed = self._parse(
            self._with_related_dates(fixture, marker, "2026-08-25")
        )
        base_entry = base.entries[0]
        changed_entry = changed.entries[0]
        base_detail = base_entry.entry_details[0]
        changed_detail = changed_entry.entry_details[0]
        base_hash = self._expected_hash(base_semantics)
        changed_hash = self._expected_hash(changed_semantics)

        self.assertNotEqual(base_hash, changed_hash)
        self.assertEqual(getattr(base_detail, "related_dates", None), base_semantics)
        self.assertEqual(
            getattr(changed_detail, "related_dates", None), changed_semantics
        )
        self.assertEqual(
            getattr(base_detail, "related_dates_evidence_hash", None), base_hash
        )
        self.assertEqual(
            getattr(changed_detail, "related_dates_evidence_hash", None), changed_hash
        )
        self._assert_entry_hash_binding(base_entry, base_hash)
        self._assert_entry_hash_binding(changed_entry, changed_hash)

        self.assertEqual(base_entry.entry_amount, Decimal("25000.00"))
        self.assertEqual(base_entry.entry_currency_code, "KRW")
        self.assertEqual(base_detail.detail_amount, Decimal("25000.00"))
        self.assertEqual(base_detail.detail_currency_code, "KRW")
        self.assertEqual(base_entry.entry_amount, changed_entry.entry_amount)
        self.assertEqual(base_entry.entry_currency_code, changed_entry.entry_currency_code)
        self.assertEqual(base_detail.detail_amount, changed_detail.detail_amount)
        self.assertEqual(
            base_detail.detail_currency_code, changed_detail.detail_currency_code
        )
        self.assertNotEqual(base_detail.source_detail_hash, changed_detail.source_detail_hash)
        self.assertNotEqual(base_entry.source_entry_hash, changed_entry.source_entry_hash)
        self.assertNotEqual(base.normalized_payload_hash, changed.normalized_payload_hash)
        self.assertEqual(
            base.entries[1].source_entry_hash, changed.entries[1].source_entry_hash
        )

    @staticmethod
    def _parse(payload: bytes) -> object:
        """Parse through the supported V14 adapter boundary."""
        return parse_bank_statement_payload(payload, CAMT053_MESSAGE_DEFINITION)

    @staticmethod
    def _with_related_dates(fixture: str, marker: str, second_date_value: str) -> bytes:
        """Insert two ordered ProprietaryDate3 records while changing only index-one `Dt`."""
        related_dates = (
            "            <RltdDts>\n"
            "              <Prtry>"
            "<Tp>BANK_BOOKING_CUTOFF</Tp>"
            "<Dt><DtTm>2026-08-23T16:00:00+00:00</DtTm></Dt>"
            "</Prtry>\n"
            "              <Prtry>"
            "<Tp>BANK_VALUE_OVERRIDE</Tp>"
            "<Dt>"
            f"<Dt>{second_date_value}</Dt>"
            "</Dt>"
            "</Prtry>\n"
            "            </RltdDts>"
        )
        return fixture.replace(marker, f"{marker}\n{related_dates}", 1).encode(
            "utf-8"
        )

    @staticmethod
    def _expected_hash(semantics: dict[str, object]) -> str:
        """Digest the ordered repeated date evidence under its exact purpose."""
        preimage = json.dumps(
            {"evidence_type": _RELATED_DATES_PATH, "related_dates": semantics},
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return f"sha256:{hashlib.sha256(preimage).hexdigest()}"

    @staticmethod
    def _assert_entry_hash_binding(entry: object, expected_hash: str) -> None:
        """Require source-entry identity to include the exact related-date digest."""
        projection = dict(bank_statement._entry_payload(entry))
        details = projection.get("details")
        if not isinstance(details, list) or not details:
            raise AssertionError("canonical entry projection must retain details")
        detail = details[0]
        if not isinstance(detail, dict):
            raise AssertionError("canonical entry detail projection must be a mapping")
        if detail.get("related_dates_evidence_hash") != expected_hash:
            raise AssertionError(
                "canonical entry projection must carry related_dates_evidence_hash"
            )
        preimage = json.dumps(
            projection, separators=(",", ":"), sort_keys=True
        ).encode("utf-8")
        expected_entry_hash = f"sha256:{hashlib.sha256(preimage).hexdigest()}"
        if getattr(entry, "source_entry_hash", None) != expected_entry_hash:
            raise AssertionError(
                "source_entry_hash must digest the related-date-bound projection"
            )


if __name__ == "__main__":
    unittest.main()
