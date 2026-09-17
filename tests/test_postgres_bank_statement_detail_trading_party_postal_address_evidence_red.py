"""PostgreSQL REDs for camt.053 trading-party PartyIdentification272 postal evidence."""

from __future__ import annotations

import copy
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

_HASH_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
_TRADING_PARTY_PURPOSE = "camt.053.001.14/Stmt/Ntry/NtryDtls/TxDtls/RltdPties/TradgPty"
_CORRECTION_ERROR = (
    r"^statement identity already exists with different entry evidence\. "
    r"Use an explicit correction contract, then retry ingest\.$"
)


class BankStatementDetailTradingPartyPostalAddressEvidenceRedTests(unittest.TestCase):
    """Retain complete party postal provenance without changing accounting truth."""

    @classmethod
    def setUpClass(cls) -> None:
        """Reuse the canonical real-PostgreSQL integration fixture."""
        posting.PostgresPostingTests.setUpClass()

    def setUp(self) -> None:
        """Prepare PartyIdentification272 PostalAddress27 value/presence variants."""
        self.case = posting.PostgresPostingTests("setUp")
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.addCleanup(self.case.tearDown)

        fixture = load_canonical_statement_fixture().decode("utf-8")
        marker = "              </Dbtr>\n            </RltdPties>"
        self.assertEqual(fixture.count(marker), 1)

        self.base = {
            "choice": "party",
            "name": "Trading Party Alpha",
            "postal_address": self._postal_address(),
        }
        self.variants = self._postal_variants(self.base)
        self.base_payload = self._with_trading_party(fixture, marker, self.base)
        self.base_statement = parse_bank_statement_payload(
            self.base_payload, CAMT053_MESSAGE_DEFINITION
        )
        self.variant_payloads = {
            name: self._with_trading_party(fixture, marker, value)
            for name, value in self.variants.items()
        }
        self.variant_statements = {
            name: parse_bank_statement_payload(payload, CAMT053_MESSAGE_DEFINITION)
            for name, payload in self.variant_payloads.items()
        }

        trading_party_xml = self._trading_party_xml(self.base)
        self.assertEqual(self.base_payload.count(trading_party_xml.encode("utf-8")), 1)
        reformatted_xml = trading_party_xml.replace(
            "                  <PstlAdr>\n",
            "                  <PstlAdr>\n                    \n",
            1,
        )
        self.assertNotEqual(reformatted_xml, trading_party_xml)
        self.reformatted_payload = self.base_payload.replace(
            trading_party_xml.encode("utf-8"), reformatted_xml.encode("utf-8"), 1
        )
        self.assertNotEqual(self.reformatted_payload, self.base_payload)
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

    def test_complete_party_postal_address_is_material_to_evidence_identity(self) -> None:
        """Every admitted PartyIdentification272/PstlAdr field and presence stays material."""
        base_hash = self._expected_hash(self.base)
        base_entry = self.base_statement.entries[0]
        base_detail = base_entry.entry_details[0]
        self.assertRegex(base_hash, _HASH_PATTERN)
        self.assertEqual(
            getattr(base_detail, "trading_party_evidence_hash", None), base_hash
        )
        self._assert_entry_hash_binding(base_entry, base_hash)
        self._assert_exact_accounting_amount(base_entry, base_detail)

        variant_hashes = {
            name: self._expected_hash(value) for name, value in self.variants.items()
        }
        self.assertEqual(len(set(variant_hashes.values())), len(variant_hashes))
        self.assertNotIn(base_hash, set(variant_hashes.values()))

        for name, statement in self.variant_statements.items():
            with self.subTest(name=name):
                expected_hash = variant_hashes[name]
                entry = statement.entries[0]
                detail = entry.entry_details[0]
                self.assertEqual(
                    getattr(detail, "trading_party_evidence_hash", None), expected_hash
                )
                self._assert_entry_hash_binding(entry, expected_hash)
                self._assert_exact_accounting_amount(entry, detail)
                self.assertEqual(
                    self.base_statement.account_identifier_hash,
                    statement.account_identifier_hash,
                )
                self.assertNotEqual(base_detail.source_detail_hash, detail.source_detail_hash)
                self.assertNotEqual(base_entry.source_entry_hash, entry.source_entry_hash)
                self.assertNotEqual(
                    self.base_statement.normalized_payload_hash,
                    statement.normalized_payload_hash,
                )
                self.assertEqual(
                    self.base_statement.entries[1].source_entry_hash,
                    statement.entries[1].source_entry_hash,
                )

    def test_xml_layout_does_not_change_party_postal_semantics(self) -> None:
        """Whitespace inside party PstlAdr changes raw bytes, not admitted semantics."""
        expected = self._expected_hash(self.base)
        base_entry = self.base_statement.entries[0]
        reformatted_entry = self.reformatted_statement.entries[0]
        base_detail = base_entry.entry_details[0]
        reformatted_detail = reformatted_entry.entry_details[0]

        self.assertNotEqual(
            self.base_statement.source_artifact_hash,
            self.reformatted_statement.source_artifact_hash,
        )
        self.assertEqual(
            getattr(base_detail, "trading_party_evidence_hash", None), expected
        )
        self.assertEqual(
            getattr(reformatted_detail, "trading_party_evidence_hash", None), expected
        )
        self._assert_entry_hash_binding(base_entry, expected)
        self._assert_entry_hash_binding(reformatted_entry, expected)
        self._assert_exact_accounting_amount(reformatted_entry, reformatted_detail)
        self.assertEqual(base_detail.source_detail_hash, reformatted_detail.source_detail_hash)
        self.assertEqual(base_entry.source_entry_hash, reformatted_entry.source_entry_hash)
        self.assertEqual(
            self.base_statement.normalized_payload_hash,
            self.reformatted_statement.normalized_payload_hash,
        )

    def test_changed_party_postal_evidence_requires_explicit_correction(self) -> None:
        """Accepted party postal provenance cannot be replaced by silent replay."""
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

    def test_buyer_read_preserves_party_postal_evidence_without_changing_amount(self) -> None:
        """Tenant reads expose postal provenance while exact 25000 KRW stays authoritative."""
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

        self.assertEqual(detail.get("trading_party_evidence_hash"), expected_hash)
        self.assertEqual(detail.get("trading_party"), self.base)
        self.assertEqual(entry["entry_amount"], "25000")
        self.assertEqual(entry["entry_currency_code"], "KRW")
        self.assertEqual(detail["detail_amount"], "25000")
        self.assertEqual(detail["detail_currency_code"], "KRW")

    @staticmethod
    def _assert_exact_accounting_amount(entry: object, detail: object) -> None:
        """Pin accounting truth independently from bank-reported postal provenance."""
        if getattr(entry, "entry_amount", None) != Decimal("25000.00"):
            raise AssertionError("party postal evidence must retain exact 25000.00 entry amount")
        if getattr(entry, "entry_currency_code", None) != "KRW":
            raise AssertionError("party postal evidence must retain KRW entry currency")
        if getattr(detail, "detail_amount", None) != Decimal("25000.00"):
            raise AssertionError("party postal evidence must retain exact 25000.00 detail amount")
        if getattr(detail, "detail_currency_code", None) != "KRW":
            raise AssertionError("party postal evidence must retain KRW detail currency")

    @staticmethod
    def _assert_entry_hash_binding(entry: object, expected_hash: str) -> None:
        """Require source_entry_hash to digest the purpose-bound trading-party hash."""
        projection = dict(bank_statement._entry_payload(entry))
        details = projection.get("details")
        if not isinstance(details, list) or not details:
            raise AssertionError("canonical entry projection must retain transaction details")
        first_detail = details[0]
        if not isinstance(first_detail, dict):
            raise AssertionError("canonical entry detail projection must be a mapping")
        if first_detail.get("trading_party_evidence_hash") != expected_hash:
            raise AssertionError(
                "canonical entry projection must carry exact trading_party_evidence_hash"
            )
        preimage = json.dumps(
            projection, separators=(",", ":"), sort_keys=True
        ).encode("utf-8")
        expected_entry_hash = f"sha256:{hashlib.sha256(preimage).hexdigest()}"
        if getattr(entry, "source_entry_hash", None) != expected_entry_hash:
            raise AssertionError(
                "source_entry_hash must digest the canonical entry projection containing "
                "trading_party_evidence_hash"
            )

    @staticmethod
    def _expected_hash(value: dict[str, object]) -> str:
        """Digest the complete admitted Party50Choice/Pty projection."""
        preimage = json.dumps(
            {"evidence_type": _TRADING_PARTY_PURPOSE, "trading_party": value},
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return f"sha256:{hashlib.sha256(preimage).hexdigest()}"

    @staticmethod
    def _postal_address() -> dict[str, object]:
        """Return one complete PostalAddress27 value in canonical semantic shape."""
        return {
            "address_type": {"code": "BIZZ"},
            "care_of": "Treasury Operations",
            "department": "Corporate Treasury",
            "sub_department": "Bank Reconciliation",
            "street_name": "Teheran-ro",
            "building_number": "152",
            "building_name": "Finance Center",
            "floor": "12",
            "unit_number": "1201",
            "post_box": "1001",
            "room": "Treasury",
            "post_code": "06236",
            "town_name": "Seoul",
            "town_location_name": "Gangnam",
            "district_name": "Gangnam-gu",
            "country_subdivision": "11",
            "country": "KR",
            "address_lines": ["152 Teheran-ro", "Gangnam-gu, Seoul 06236"],
        }

    @classmethod
    def _postal_variants(cls, base: dict[str, object]) -> dict[str, dict[str, object]]:
        """Change each admitted postal field, line semantics, and container presence."""
        variants: dict[str, dict[str, object]] = {}
        scalar_changes: dict[str, object] = {
            "address_type": {"code": "ADDR"},
            "care_of": "Alternate Care Of",
            "department": "Alternate Treasury",
            "sub_department": "Alternate Reconciliation",
            "street_name": "Eulji-ro",
            "building_number": "99",
            "building_name": "Alternate Center",
            "floor": "9",
            "unit_number": "901",
            "post_box": "9090",
            "room": "Alternate Room",
            "post_code": "04538",
            "town_name": "Busan",
            "town_location_name": "Jung-gu",
            "district_name": "Jung-gu",
            "country_subdivision": "26",
            "country": "DE",
        }
        for field, replacement in scalar_changes.items():
            variant = copy.deepcopy(base)
            postal = variant.get("postal_address")
            if not isinstance(postal, dict):
                raise AssertionError("postal variant requires PostalAddress27")
            postal[field] = copy.deepcopy(replacement)
            variants[f"postal-{field}"] = variant

        first_line = copy.deepcopy(base)
        first_postal = first_line.get("postal_address")
        if not isinstance(first_postal, dict):
            raise AssertionError("address-line variant requires PostalAddress27")
        lines = first_postal.get("address_lines")
        if not isinstance(lines, list) or len(lines) != 2:
            raise AssertionError("address-line variant requires two source-ordered lines")
        lines[0] = "Alternate address line"
        variants["postal-address-line-value"] = first_line

        reordered = copy.deepcopy(base)
        reordered_postal = reordered.get("postal_address")
        if not isinstance(reordered_postal, dict):
            raise AssertionError("address-line order variant requires PostalAddress27")
        reordered_lines = reordered_postal.get("address_lines")
        if not isinstance(reordered_lines, list) or len(reordered_lines) != 2:
            raise AssertionError("address-line order variant requires two source-ordered lines")
        reordered_lines.reverse()
        variants["postal-address-line-order"] = reordered

        absent = copy.deepcopy(base)
        absent.pop("postal_address")
        variants["postal-address-absent"] = absent
        return variants

    @classmethod
    def _trading_party_xml(cls, value: dict[str, object]) -> str:
        """Serialize PartyIdentification272 in schema order with optional PstlAdr."""
        if value.get("choice") != "party":
            raise AssertionError("party postal RED requires the Pty branch")
        name = str(value["name"])
        postal = value.get("postal_address")
        postal_xml = ""
        if postal is not None:
            if not isinstance(postal, dict):
                raise AssertionError("postal_address must be a mapping when present")
            postal_xml = cls._postal_xml(postal, "                  ")
        return (
            "              <TradgPty>\n"
            "                <Pty>\n"
            f"                  <Nm>{name}</Nm>\n"
            + postal_xml
            + "                </Pty>\n"
            "              </TradgPty>\n"
        )

    @staticmethod
    def _postal_xml(address: dict[str, object], indent: str) -> str:
        """Serialize PostalAddress27 in schema sequence order."""
        address_type = address.get("address_type")
        if not isinstance(address_type, dict) or set(address_type) != {"code"}:
            raise AssertionError("party postal RED requires coded AddressType3Choice")
        lines = address.get("address_lines")
        if not isinstance(lines, list):
            raise AssertionError("address_lines must preserve source order")
        body = (
            f"{indent}<PstlAdr>\n"
            f"{indent}  <AdrTp>\n"
            f"{indent}    <Cd>{address_type['code']}</Cd>\n"
            f"{indent}  </AdrTp>\n"
            f"{indent}  <CareOf>{address['care_of']}</CareOf>\n"
            f"{indent}  <Dept>{address['department']}</Dept>\n"
            f"{indent}  <SubDept>{address['sub_department']}</SubDept>\n"
            f"{indent}  <StrtNm>{address['street_name']}</StrtNm>\n"
            f"{indent}  <BldgNb>{address['building_number']}</BldgNb>\n"
            f"{indent}  <BldgNm>{address['building_name']}</BldgNm>\n"
            f"{indent}  <Flr>{address['floor']}</Flr>\n"
            f"{indent}  <UnitNb>{address['unit_number']}</UnitNb>\n"
            f"{indent}  <PstBx>{address['post_box']}</PstBx>\n"
            f"{indent}  <Room>{address['room']}</Room>\n"
            f"{indent}  <PstCd>{address['post_code']}</PstCd>\n"
            f"{indent}  <TwnNm>{address['town_name']}</TwnNm>\n"
            f"{indent}  <TwnLctnNm>{address['town_location_name']}</TwnLctnNm>\n"
            f"{indent}  <DstrctNm>{address['district_name']}</DstrctNm>\n"
            f"{indent}  <CtrySubDvsn>{address['country_subdivision']}</CtrySubDvsn>\n"
            f"{indent}  <Ctry>{address['country']}</Ctry>\n"
        )
        for line in lines:
            body += f"{indent}  <AdrLine>{line}</AdrLine>\n"
        return body + f"{indent}</PstlAdr>\n"

    @classmethod
    def _with_trading_party(
        cls,
        fixture: str,
        marker: str,
        value: dict[str, object],
    ) -> bytes:
        """Insert PartyIdentification272 postal evidence after the fixture debtor."""
        return fixture.replace(
            marker,
            "              </Dbtr>\n"
            + cls._trading_party_xml(value)
            + "            </RltdPties>",
            1,
        ).encode("utf-8")

    def _command(self, payload: bytes, suffix: str) -> dict[str, object]:
        """Return one supported ingest command with a fresh replay key."""
        return {
            "tenant_reference": self.case.policy.tenant_reference,
            "bank_account_reference": self.bank_account_reference,
            "ingestion_idempotency_key": (
                f"detail-trading-party-postal-{suffix}-{uuid.uuid4().hex}"
            ),
            "message_definition_identifier": CAMT053_MESSAGE_DEFINITION,
            "statement_payload": payload.decode("utf-8"),
        }


if __name__ == "__main__":
    unittest.main()
