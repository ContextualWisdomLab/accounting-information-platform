"""PostgreSQL REDs for PostalAddress27 inside one standard transaction-agent role."""

from __future__ import annotations

import copy
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

_CORRECTION_ERROR = (
    r"^statement identity already exists with different entry evidence\. "
    r"Use an explicit correction contract, then retry ingest\.$"
)


class BankStatementDetailPaymentAgentPostalAddressEvidenceRedTests(unittest.TestCase):
    """Retain institution and branch PostalAddress27 provenance for InstgAgt."""

    @classmethod
    def setUpClass(cls) -> None:
        """Reuse the canonical real-PostgreSQL integration fixture."""
        posting.PostgresPostingTests.setUpClass()

    def setUp(self) -> None:
        """Prepare path-specific instructing-agent postal materiality controls."""
        self.case = posting.PostgresPostingTests("setUp")
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.addCleanup(self.case.tearDown)

        fixture = load_canonical_statement_fixture().decode("utf-8")
        self.marker = (
            "            <AmtDtls>\n"
            "              <TxAmt>\n"
            "                <Amt Ccy=\"KRW\">6000.00</Amt>\n"
            "              </TxAmt>\n"
            "            </AmtDtls>\n"
            "            <RmtInf>"
        )
        self.assertEqual(fixture.count(self.marker), 1)
        self.fixture = fixture

        self.base = {
            "bicfi": "DEUTDEFF",
            "name": "Instructing Bank Frankfurt",
            "postal_address": self._institution_address(),
            "branch": {
                "id": "INSTG-FRA-001",
                "name": "Frankfurt Payments Branch",
                "postal_address": self._branch_address(),
            },
        }
        self.variants = self._variants(self.base)
        self.base_payload = self._with_instructing_agent(self.base)
        self.base_statement = parse_bank_statement_payload(
            self.base_payload, CAMT053_MESSAGE_DEFINITION
        )
        self.variant_payloads = {
            name: self._with_instructing_agent(value)
            for name, value in self.variants.items()
        }
        self.variant_statements = {
            name: parse_bank_statement_payload(payload, CAMT053_MESSAGE_DEFINITION)
            for name, payload in self.variant_payloads.items()
        }

        agent_xml = self._agent_xml(self.base)
        self.assertEqual(self.base_payload.count(agent_xml.encode("utf-8")), 1)
        reformatted_xml = agent_xml.replace(
            "                  <PstlAdr>\n",
            "                  <PstlAdr>\n                    \n",
            1,
        )
        self.assertNotEqual(reformatted_xml, agent_xml)
        self.reformatted_payload = self.base_payload.replace(
            agent_xml.encode("utf-8"),
            reformatted_xml.encode("utf-8"),
            1,
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

    def test_instructing_agent_postal_address_is_material(self) -> None:
        """Every admitted institution/branch postal fact must affect canonical evidence."""
        base_entry = self.base_statement.entries[1]
        base_detail = base_entry.entry_details[0]
        self._assert_exact_accounting_amount(base_entry, base_detail)

        for name, statement in self.variant_statements.items():
            with self.subTest(name=name):
                entry = statement.entries[1]
                detail = entry.entry_details[0]
                self._assert_exact_accounting_amount(entry, detail)
                self.assertEqual(
                    getattr(base_detail, "instructing_agent_evidence_hash", None),
                    getattr(detail, "instructing_agent_evidence_hash", None),
                    "BICFI is unchanged; the existing purpose digest must stay stable",
                )
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
                    self.base_statement.entries[0].source_entry_hash,
                    statement.entries[0].source_entry_hash,
                )

    def test_postal_xml_layout_is_representation_only(self) -> None:
        """Whitespace inside PstlAdr changes source bytes, not admitted semantics."""
        base_entry = self.base_statement.entries[1]
        base_detail = base_entry.entry_details[0]
        reformatted_entry = self.reformatted_statement.entries[1]
        reformatted_detail = reformatted_entry.entry_details[0]

        self.assertNotEqual(
            self.base_statement.source_artifact_hash,
            self.reformatted_statement.source_artifact_hash,
        )
        self.assertEqual(
            getattr(base_detail, "instructing_agent_evidence_hash", None),
            getattr(reformatted_detail, "instructing_agent_evidence_hash", None),
        )
        self.assertEqual(base_detail.source_detail_hash, reformatted_detail.source_detail_hash)
        self.assertEqual(base_entry.source_entry_hash, reformatted_entry.source_entry_hash)
        self.assertEqual(
            self.base_statement.normalized_payload_hash,
            self.reformatted_statement.normalized_payload_hash,
        )
        self._assert_exact_accounting_amount(reformatted_entry, reformatted_detail)

    def test_changed_postal_address_requires_explicit_correction(self) -> None:
        """Accepted postal provenance cannot be silently replaced on replay."""
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

    def test_buyer_read_stays_digest_only_while_postal_identity_is_material(self) -> None:
        """Tenant read exposes no postal delta beyond purpose digest and source identity."""
        postal_detail = self._ingest_and_read(self.base_payload, "lookup-postal")

        no_postal = copy.deepcopy(self.base)
        no_postal.pop("postal_address", None)
        branch = no_postal.get("branch")
        if not isinstance(branch, dict):
            raise AssertionError("digest-only baseline requires branch identity")
        branch.pop("postal_address", None)
        no_postal_payload = self._with_instructing_agent(no_postal)
        self.assertNotEqual(no_postal_payload, self.base_payload)
        no_postal_statement = parse_bank_statement_payload(
            no_postal_payload, CAMT053_MESSAGE_DEFINITION
        )
        no_postal_reference = f"urn:cwl:bank_account:{uuid.uuid4().hex}"
        accept_bank_account_record(
            {
                "tenant_reference": self.case.policy.tenant_reference,
                "bank_account_reference": no_postal_reference,
                "account_currency_code": no_postal_statement.account_currency_code,
                "account_identifier_hash": no_postal_statement.account_identifier_hash,
            },
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
        )
        no_postal_detail = self._ingest_and_read(
            no_postal_payload,
            "lookup-no-postal",
            bank_account_reference=no_postal_reference,
        )

        for projection in (postal_detail, no_postal_detail):
            self.assertIsInstance(projection.get("instructing_agent_evidence_hash"), str)
            self.assertRegex(
                str(projection["instructing_agent_evidence_hash"]),
                r"\Asha256:[0-9a-f]{64}\Z",
            )
            self.assertRegex(
                str(projection["source_detail_hash"]),
                r"\Asha256:[0-9a-f]{64}\Z",
            )
            self.assertEqual(projection["detail_amount"], "6000")
            self.assertEqual(projection["detail_currency_code"], "KRW")

        self.assertEqual(
            postal_detail["instructing_agent_evidence_hash"],
            no_postal_detail["instructing_agent_evidence_hash"],
        )
        self.assertNotEqual(
            postal_detail["source_detail_hash"],
            no_postal_detail["source_detail_hash"],
        )

        postal_projection = dict(postal_detail)
        no_postal_projection = dict(no_postal_detail)
        for projection in (postal_projection, no_postal_projection):
            projection.pop("instructing_agent_evidence_hash")
            projection.pop("source_detail_hash")
        self.assertEqual(postal_projection, no_postal_projection)

    @staticmethod
    def _institution_address() -> dict[str, object]:
        return {
            "address_type": {"code": "BIZZ"},
            "care_of": "Treasury Operations",
            "department": "Corporate Banking",
            "sub_department": "Payments",
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
            "care_of": "Payments Desk",
            "department": "Transaction Banking",
            "sub_department": "Instruction Services",
            "street_name": "Mainzer Landstrasse",
            "building_number": "46",
            "building_name": "Payments House",
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
    def _variants(cls, base: dict[str, object]) -> dict[str, dict[str, object]]:
        """Change every admitted postal field independently at institution and branch level."""
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
        for owner_key, label in (("postal_address", "institution"),):
            for field, replacement in scalar_changes.items():
                variant = copy.deepcopy(base)
                address = variant.get(owner_key)
                if not isinstance(address, dict):
                    raise AssertionError("institution postal address must be a mapping")
                address[field] = copy.deepcopy(replacement)
                variants[f"{label}-{field.replace('_', '-')}"] = variant
            first_line = copy.deepcopy(base)
            address = first_line.get(owner_key)
            if not isinstance(address, dict) or not isinstance(address.get("address_lines"), list):
                raise AssertionError("institution address lines must be source ordered")
            address["address_lines"][0] = "Alternate address line"
            variants[f"{label}-address-line-value"] = first_line
            reordered = copy.deepcopy(base)
            address = reordered.get(owner_key)
            if not isinstance(address, dict) or not isinstance(address.get("address_lines"), list):
                raise AssertionError("institution address lines must be source ordered")
            address["address_lines"].reverse()
            variants[f"{label}-address-line-order"] = reordered

        for field, replacement in scalar_changes.items():
            variant = copy.deepcopy(base)
            branch = variant.get("branch")
            if not isinstance(branch, dict):
                raise AssertionError("branch must be a mapping")
            address = branch.get("postal_address")
            if not isinstance(address, dict):
                raise AssertionError("branch postal address must be a mapping")
            address[field] = copy.deepcopy(replacement)
            variants[f"branch-{field.replace('_', '-')}"] = variant
        first_line = copy.deepcopy(base)
        branch = first_line.get("branch")
        if not isinstance(branch, dict):
            raise AssertionError("branch must be a mapping")
        address = branch.get("postal_address")
        if not isinstance(address, dict) or not isinstance(address.get("address_lines"), list):
            raise AssertionError("branch address lines must be source ordered")
        address["address_lines"][0] = "Alternate branch address line"
        variants["branch-address-line-value"] = first_line
        reordered = copy.deepcopy(base)
        branch = reordered.get("branch")
        if not isinstance(branch, dict):
            raise AssertionError("branch must be a mapping")
        address = branch.get("postal_address")
        if not isinstance(address, dict) or not isinstance(address.get("address_lines"), list):
            raise AssertionError("branch address lines must be source ordered")
        address["address_lines"].reverse()
        variants["branch-address-line-order"] = reordered

        institution_absent = copy.deepcopy(base)
        institution_absent.pop("postal_address", None)
        variants["institution-address-absent"] = institution_absent
        branch_absent = copy.deepcopy(base)
        branch = branch_absent.get("branch")
        if not isinstance(branch, dict):
            raise AssertionError("branch must be a mapping")
        branch.pop("postal_address", None)
        variants["branch-address-absent"] = branch_absent
        return variants

    def _with_instructing_agent(self, value: dict[str, object]) -> bytes:
        """Insert one PostalAddress27-rich InstgAgt into the outgoing payment detail."""
        replacement = (
            "            <AmtDtls>\n"
            "              <TxAmt>\n"
            "                <Amt Ccy=\"KRW\">6000.00</Amt>\n"
            "              </TxAmt>\n"
            "            </AmtDtls>\n"
            "            <RltdAgts>\n"
            + self._agent_xml(value)
            + "            </RltdAgts>\n"
            "            <RmtInf>"
        )
        return self.fixture.replace(self.marker, replacement, 1).encode("utf-8")

    @classmethod
    def _agent_xml(cls, value: dict[str, object]) -> str:
        """Serialize InstgAgt with institution and branch PostalAddress27 in XSD order."""
        bicfi = value.get("bicfi")
        if not isinstance(bicfi, str) or not bicfi:
            raise AssertionError("postal RED requires BICFI")
        parts = [
            "              <InstgAgt>\n",
            "                <FinInstnId>\n",
            f"                  <BICFI>{bicfi}</BICFI>\n",
        ]
        if isinstance(value.get("name"), str):
            parts.append(f"                  <Nm>{value['name']}</Nm>\n")
        institution_address = value.get("postal_address")
        if institution_address is not None:
            if not isinstance(institution_address, dict):
                raise AssertionError("institution postal_address must be a mapping")
            parts.append(cls._postal_xml(institution_address, "                  "))
        parts.append("                </FinInstnId>\n")

        branch = value.get("branch")
        if branch is not None:
            if not isinstance(branch, dict):
                raise AssertionError("branch must be a mapping")
            parts.append("                <BrnchId>\n")
            if isinstance(branch.get("id"), str):
                parts.append(f"                  <Id>{branch['id']}</Id>\n")
            if isinstance(branch.get("name"), str):
                parts.append(f"                  <Nm>{branch['name']}</Nm>\n")
            branch_address = branch.get("postal_address")
            if branch_address is not None:
                if not isinstance(branch_address, dict):
                    raise AssertionError("branch postal_address must be a mapping")
                parts.append(cls._postal_xml(branch_address, "                  "))
            parts.append("                </BrnchId>\n")
        parts.append("              </InstgAgt>\n")
        return "".join(parts)

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

    @staticmethod
    def _assert_exact_accounting_amount(entry: object, detail: object) -> None:
        """Keep accounting amounts independent from payment-agent postal provenance."""
        if getattr(entry, "entry_amount", None) != Decimal("6000.00"):
            raise AssertionError("payment-agent postal evidence must retain 6000.00")
        if getattr(entry, "entry_currency_code", None) != "KRW":
            raise AssertionError("payment-agent postal evidence must retain KRW")
        if getattr(detail, "detail_amount", None) != Decimal("6000.00"):
            raise AssertionError("payment-agent postal detail must retain 6000.00")
        if getattr(detail, "detail_currency_code", None) != "KRW":
            raise AssertionError("payment-agent postal detail must retain KRW")

    def _ingest_and_read(
        self,
        payload: bytes,
        suffix: str,
        *,
        bank_account_reference: str | None = None,
    ) -> dict[str, object]:
        """Ingest one fixture and return its outgoing-payment transaction detail."""
        reference = bank_account_reference or self.bank_account_reference
        accepted = accept_bank_statement_evidence(
            self._command(payload, suffix, bank_account_reference=reference),
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
            artifact_store=MemoryArtifactStore(),
        )
        document = lookup_bank_statement_entries(
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
            str(accepted["bank_statement_record_id"]),
        )
        return document["bank_statement_entries"][1]["entry_details"][0]

    def _command(
        self,
        payload: bytes,
        suffix: str,
        *,
        bank_account_reference: str | None = None,
    ) -> dict[str, object]:
        """Return one supported ingest command with an isolated replay key."""
        return {
            "tenant_reference": self.case.policy.tenant_reference,
            "bank_account_reference": bank_account_reference or self.bank_account_reference,
            "ingestion_idempotency_key": f"payment-agent-postal-{suffix}-{uuid.uuid4().hex}",
            "message_definition_identifier": CAMT053_MESSAGE_DEFINITION,
            "statement_payload": payload.decode("utf-8"),
        }


if __name__ == "__main__":
    unittest.main()
