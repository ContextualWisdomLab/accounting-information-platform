"""PostgreSQL REDs for trading-agent institution and branch postal evidence."""

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


class BankStatementDetailTradingPartyAgentPostalAddressEvidenceRedTests(unittest.TestCase):
    """Retain complete agent postal provenance without changing accounting truth."""

    @classmethod
    def setUpClass(cls) -> None:
        """Reuse the canonical real-PostgreSQL integration fixture."""
        posting.PostgresPostingTests.setUpClass()

    def setUp(self) -> None:
        """Prepare complete institution and branch PostalAddress27 variants."""
        self.case = posting.PostgresPostingTests("setUp")
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.addCleanup(self.case.tearDown)

        fixture = load_canonical_statement_fixture().decode("utf-8")
        marker = "              </Dbtr>\n            </RltdPties>"
        self.assertEqual(fixture.count(marker), 1)

        self.base = {
            "choice": "agent",
            "financial_institution": {
                "bicfi": "DEUTDEFFXXX",
                "lei": "7LTWFZYICNSX8D621K86",
                "name": "Trading Party Agent",
                "postal_address": self._institution_address(),
            },
            "branch": {
                "id": "TRADING-BRANCH-001",
                "lei": "529900Z6KVD8Y83D7K60",
                "name": "Frankfurt Custody Branch",
                "postal_address": self._branch_address(),
            },
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
            "                    <PstlAdr>\n",
            "                    <PstlAdr>\n                      \n",
            1,
        )
        self.reformatted_payload = self.base_payload.replace(
            trading_party_xml.encode("utf-8"), reformatted_xml.encode("utf-8"), 1
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

    def test_complete_institution_and_branch_postal_evidence_is_material(self) -> None:
        """Every admitted PostalAddress27 field and AdrLine order stays material."""
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

    def test_xml_layout_does_not_change_postal_evidence_identity(self) -> None:
        """Whitespace inside PstlAdr changes bytes, not semantic identity."""
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

    def test_changed_postal_evidence_requires_explicit_correction(self) -> None:
        """Accepted postal provenance cannot be replaced by silent replay."""
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

    def test_buyer_read_preserves_complete_postal_evidence_without_changing_amount(self) -> None:
        """Tenant reads expose both postal addresses while 25000 KRW stays fixed."""
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
        """Pin accounting truth independently from bank-reported postal evidence."""
        if getattr(entry, "entry_amount", None) != Decimal("25000.00"):
            raise AssertionError("postal evidence must retain exact 25000.00 entry amount")
        if getattr(entry, "entry_currency_code", None) != "KRW":
            raise AssertionError("postal evidence must retain KRW entry currency")
        if getattr(detail, "detail_amount", None) != Decimal("25000.00"):
            raise AssertionError("postal evidence must retain exact 25000.00 detail amount")
        if getattr(detail, "detail_currency_code", None) != "KRW":
            raise AssertionError("postal evidence must retain KRW detail currency")

    @staticmethod
    def _assert_entry_hash_binding(entry: object, expected_hash: str) -> None:
        """Require source_entry_hash to digest the trading-party evidence hash."""
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
        """Digest the complete admitted Party50Choice agent projection."""
        preimage = json.dumps(
            {"evidence_type": _TRADING_PARTY_PURPOSE, "trading_party": value},
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return f"sha256:{hashlib.sha256(preimage).hexdigest()}"

    @staticmethod
    def _institution_address() -> dict[str, object]:
        return {
            "address_type": {"code": "BIZZ"},
            "care_of": "Treasury Operations",
            "department": "Corporate Banking",
            "sub_department": "Reconciliation",
            "street_name": "Taunusanlage",
            "building_number": "12",
            "building_name": "Tower A",
            "floor": "7",
            "unit_number": "701",
            "post_box": "1001",
            "room": "Ops",
            "post_code": "60325",
            "town_name": "Frankfurt am Main",
            "town_location_name": "Westend",
            "district_name": "Innenstadt",
            "country_subdivision": "HE",
            "country": "DE",
            "address_lines": ["Taunusanlage 12", "60325 Frankfurt am Main"],
        }

    @staticmethod
    def _branch_address() -> dict[str, object]:
        return {
            "address_type": {"code": "BIZZ"},
            "care_of": "Custody Desk",
            "department": "Securities Services",
            "sub_department": "Settlement",
            "street_name": "Mainzer Landstrasse",
            "building_number": "46",
            "building_name": "Custody House",
            "floor": "3",
            "unit_number": "302",
            "post_box": "2002",
            "room": "Settlement",
            "post_code": "60329",
            "town_name": "Frankfurt am Main",
            "town_location_name": "Bahnhofsviertel",
            "district_name": "Innenstadt I",
            "country_subdivision": "HE",
            "country": "DE",
            "address_lines": ["Mainzer Landstrasse 46", "60329 Frankfurt am Main"],
        }

    @classmethod
    def _postal_variants(cls, base: dict[str, object]) -> dict[str, dict[str, object]]:
        """Change every admitted postal field independently for both agent levels."""
        variants: dict[str, dict[str, object]] = {}
        scalar_changes: dict[str, object] = {
            "address_type": {"code": "ADDR"},
            "care_of": "Alternate Care Of",
            "department": "Alternate Department",
            "sub_department": "Alternate SubDepartment",
            "street_name": "Alternate Street",
            "building_number": "99",
            "building_name": "Alternate Building",
            "floor": "9",
            "unit_number": "909",
            "post_box": "9090",
            "room": "Alternate Room",
            "post_code": "10115",
            "town_name": "Berlin",
            "town_location_name": "Mitte",
            "district_name": "Berlin-Mitte",
            "country_subdivision": "BE",
            "country": "NL",
        }
        for owner in ("financial_institution", "branch"):
            for field, replacement in scalar_changes.items():
                variant = copy.deepcopy(base)
                variant[owner]["postal_address"][field] = copy.deepcopy(replacement)  # type: ignore[index]
                variants[f"{owner}-{field}"] = variant

            first_line = copy.deepcopy(base)
            first_line[owner]["postal_address"]["address_lines"][0] = "Alternate address line"  # type: ignore[index]
            variants[f"{owner}-address-line-value"] = first_line

            reordered = copy.deepcopy(base)
            reordered[owner]["postal_address"]["address_lines"].reverse()  # type: ignore[index]
            variants[f"{owner}-address-line-order"] = reordered
        return variants

    @classmethod
    def _trading_party_xml(cls, value: dict[str, object]) -> str:
        """Serialize complete institution/branch postal provenance."""
        if value.get("choice") != "agent":
            raise AssertionError("postal RED requires the Agt branch")
        institution = value.get("financial_institution")
        branch = value.get("branch")
        if not isinstance(institution, dict) or not isinstance(branch, dict):
            raise AssertionError("trading-agent identity must provide institution and branch")
        institution_address = institution.get("postal_address")
        branch_address = branch.get("postal_address")
        if not isinstance(institution_address, dict) or not isinstance(branch_address, dict):
            raise AssertionError("both trading-agent levels must provide PostalAddress27")
        return (
            "              <TradgPty>\n"
            "                <Agt>\n"
            "                  <FinInstnId>\n"
            f"                    <BICFI>{institution['bicfi']}</BICFI>\n"
            f"                    <LEI>{institution['lei']}</LEI>\n"
            f"                    <Nm>{institution['name']}</Nm>\n"
            + cls._postal_xml(institution_address, "                    ")
            + "                  </FinInstnId>\n"
            "                  <BrnchId>\n"
            f"                    <Id>{branch['id']}</Id>\n"
            f"                    <LEI>{branch['lei']}</LEI>\n"
            f"                    <Nm>{branch['name']}</Nm>\n"
            + cls._postal_xml(branch_address, "                    ")
            + "                  </BrnchId>\n"
            "                </Agt>\n"
            "              </TradgPty>\n"
        )

    @staticmethod
    def _postal_xml(address: dict[str, object], indent: str) -> str:
        """Serialize PostalAddress27 in schema sequence order."""
        address_type = address.get("address_type")
        if not isinstance(address_type, dict) or set(address_type) != {"code"}:
            raise AssertionError("postal RED requires coded AddressType3Choice")
        lines = address.get("address_lines")
        if not isinstance(lines, list) or len(lines) < 2:
            raise AssertionError("postal RED requires source-ordered address lines")
        tags = (
            ("CareOf", "care_of"),
            ("Dept", "department"),
            ("SubDept", "sub_department"),
            ("StrtNm", "street_name"),
            ("BldgNb", "building_number"),
            ("BldgNm", "building_name"),
            ("Flr", "floor"),
            ("UnitNb", "unit_number"),
            ("PstBx", "post_box"),
            ("Room", "room"),
            ("PstCd", "post_code"),
            ("TwnNm", "town_name"),
            ("TwnLctnNm", "town_location_name"),
            ("DstrctNm", "district_name"),
            ("CtrySubDvsn", "country_subdivision"),
            ("Ctry", "country"),
        )
        parts = [
            f"{indent}<PstlAdr>\n",
            f"{indent}  <AdrTp>\n",
            f"{indent}    <Cd>{address_type['code']}</Cd>\n",
            f"{indent}  </AdrTp>\n",
        ]
        parts.extend(
            f"{indent}  <{tag}>{address[key]}</{tag}>\n" for tag, key in tags
        )
        parts.extend(f"{indent}  <AdrLine>{line}</AdrLine>\n" for line in lines)
        parts.append(f"{indent}</PstlAdr>\n")
        return "".join(parts)

    @classmethod
    def _with_trading_party(
        cls, fixture: str, marker: str, value: dict[str, object]
    ) -> bytes:
        """Insert complete agent postal identity after debtor evidence."""
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
                f"detail-trading-agent-postal-{suffix}-{uuid.uuid4().hex}"
            ),
            "message_definition_identifier": CAMT053_MESSAGE_DEFINITION,
            "statement_payload": payload.decode("utf-8"),
        }


if __name__ == "__main__":
    unittest.main()
