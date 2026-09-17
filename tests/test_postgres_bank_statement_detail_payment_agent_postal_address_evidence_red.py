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
        self.addCleanup(self.case.doCleanups)
        self.case.setUp()
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

    def test_buyer_read_remains_digest_only_across_postal_evidence(self) -> None:
        """Postal materiality stays internal without adding reversible buyer fields."""
        postal_detail = self._ingest_and_read(self.base_payload, "lookup-postal")
        no_postal = copy.deepcopy(self.base)
        no_postal.pop("postal_address", None)
        branch = no_postal.get("branch")
        if not isinstance(branch, dict):
            raise AssertionError("branch must be a mapping")
        branch.pop("postal_address", None)
        no_postal_payload = self._with_instructing_agent(no_postal)
        self.assertNotEqual(no_postal_payload, self.base_payload)
        no_postal_statement = parse_bank_statement_payload(
            no_postal_payload,
            CAMT053_MESSAGE_DEFINITION,
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
    def _variants(base: dict[str, object]) -> dict[str, dict[str, object]]:
        """Change every direct PostalAddress27 field and ordered AdrLine evidence."""
        variants: dict[str, dict[str, object]] = {}
        scalar_changes = {
            "address_type": "ADDR",
            "care_of": "Alt Treasury Desk",
            "department": "Alternate Payments",
            "sub_department": "Alternate Clearing",
            "street_name": "Goethestrasse",
            "building_number": "99",
            "building_name": "Alternate Haus",
            "floor": "9",
            "unit_number": "9A",
            "post_box": "999",
            "room": "909",
            "post_code": "60311",
            "town_name": "Berlin",
            "town_location_name": "Mitte",
            "district_name": "Central",
            "country_subdivision": "BE",
            "country": "FR",
        }
        for owner in ("institution", "branch"):
            for field, replacement in scalar_changes.items():
                variant = copy.deepcopy(base)
                address = self_address = variant.get("postal_address")
                if owner == "branch":
                    branch_value = variant.get("branch")
                    if not isinstance(branch_value, dict):
                        raise AssertionError("branch must be a mapping")
                    address = branch_value.get("postal_address")
                if not isinstance(address, dict):
                    raise AssertionError("postal address must be a mapping")
                address[field] = replacement
                variants[f"{owner}-{field}-value"] = variant
                if owner == "institution" and self_address is not address:
                    raise AssertionError("institution address identity drifted")

            address_line = copy.deepcopy(base)
            address = address_line.get("postal_address")
            if owner == "branch":
                branch_value = address_line.get("branch")
                if not isinstance(branch_value, dict):
                    raise AssertionError("branch must be a mapping")
                address = branch_value.get("postal_address")
            if not isinstance(address, dict):
                raise AssertionError("postal address must be a mapping")
            address["address_lines"][0] = "Alternate line 1"
            variants[f"{owner}-address-line-value"] = address_line

            address_order = copy.deepcopy(base)
            address = address_order.get("postal_address")
            if owner == "branch":
                branch_value = address_order.get("branch")
                if not isinstance(branch_value, dict):
                    raise AssertionError("branch must be a mapping")
                address = branch_value.get("postal_address")
            if not isinstance(address, dict):
                raise AssertionError("postal address must be a mapping")
            lines = address.get("address_lines")
            if not isinstance(lines, list) or len(lines) != 2:
                raise AssertionError("focused address must have two ordered AdrLine values")
            lines.reverse()
            variants[f"{owner}-address-line-order"] = address_order

            absent = copy.deepcopy(base)
            if owner == "institution":
                absent.pop("postal_address", None)
            else:
                branch_value = absent.get("branch")
                if not isinstance(branch_value, dict):
                    raise AssertionError("branch must be a mapping")
                branch_value.pop("postal_address", None)
            variants[f"{owner}-postal-address-absent"] = absent
        return variants

    @staticmethod
    def _institution_address() -> dict[str, object]:
        """Return one fully populated institution PostalAddress27 fixture."""
        return {
            "address_type": "BIZZ",
            "care_of": "Treasury Desk",
            "department": "Payments",
            "sub_department": "Clearing",
            "street_name": "Taunusanlage",
            "building_number": "12",
            "building_name": "Main Tower",
            "floor": "4",
            "unit_number": "4B",
            "post_box": "100",
            "room": "401",
            "post_code": "60325",
            "town_name": "Frankfurt",
            "town_location_name": "Westend",
            "district_name": "Innenstadt",
            "country_subdivision": "HE",
            "country": "DE",
            "address_lines": ["Institution line 1", "Institution line 2"],
        }

    @staticmethod
    def _branch_address() -> dict[str, object]:
        """Return one fully populated branch PostalAddress27 fixture."""
        return {
            "address_type": "BIZZ",
            "care_of": "Branch Operations",
            "department": "Payment Operations",
            "sub_department": "Investigation",
            "street_name": "Gallusanlage",
            "building_number": "8",
            "building_name": "Branch Tower",
            "floor": "6",
            "unit_number": "6C",
            "post_box": "200",
            "room": "602",
            "post_code": "60329",
            "town_name": "Frankfurt",
            "town_location_name": "Bahnhofsviertel",
            "district_name": "Gallus",
            "country_subdivision": "HE",
            "country": "DE",
            "address_lines": ["Branch line 1", "Branch line 2"],
        }

    @classmethod
    def _agent_xml(cls, value: dict[str, object]) -> str:
        """Serialize one V14 InstgAgt with institution and optional branch address."""
        bicfi = value.get("bicfi")
        name = value.get("name")
        postal_address = value.get("postal_address")
        branch = value.get("branch")
        if not isinstance(bicfi, str) or not bicfi:
            raise AssertionError("focused instructing agent requires BICFI")
        if not isinstance(name, str) or not name:
            raise AssertionError("focused instructing agent requires name")
        if not isinstance(branch, dict):
            raise AssertionError("focused instructing agent requires branch")

        institution_postal = (
            cls._postal_xml(postal_address, "                  ")
            if isinstance(postal_address, dict)
            else ""
        )
        branch_address = branch.get("postal_address")
        branch_postal = (
            cls._postal_xml(branch_address, "                  ")
            if isinstance(branch_address, dict)
            else ""
        )
        return (
            "              <InstgAgt>\n"
            "                <FinInstnId>\n"
            f"                  <BICFI>{bicfi}</BICFI>\n"
            f"                  <Nm>{name}</Nm>\n"
            + institution_postal
            + "                </FinInstnId>\n"
            "                <BrnchId>\n"
            f"                  <Id>{branch['id']}</Id>\n"
            f"                  <Nm>{branch['name']}</Nm>\n"
            + branch_postal
            + "                </BrnchId>\n"
            "              </InstgAgt>\n"
        )

    @staticmethod
    def _postal_xml(address: dict[str, object], indent: str) -> str:
        """Serialize PostalAddress27 in V14 field order."""
        fields = (
            ("address_type", "AdrTp"),
            ("care_of", "CareOf"),
            ("department", "Dept"),
            ("sub_department", "SubDept"),
            ("street_name", "StrtNm"),
            ("building_number", "BldgNb"),
            ("building_name", "BldgNm"),
            ("floor", "Flr"),
            ("unit_number", "UnitNb"),
            ("post_box", "PstBx"),
            ("room", "Room"),
            ("post_code", "PstCd"),
            ("town_name", "TwnNm"),
            ("town_location_name", "TwnLctnNm"),
            ("district_name", "DstrctNm"),
            ("country_subdivision", "CtrySubDvsn"),
            ("country", "Ctry"),
        )
        content = ""
        for key, tag in fields:
            value = address.get(key)
            if value is not None:
                content += f"{indent}  <{tag}>{value}</{tag}>\n"
        lines = address.get("address_lines", [])
        if not isinstance(lines, list):
            raise AssertionError("address_lines must be a list")
        for value in lines:
            content += f"{indent}  <AdrLine>{value}</AdrLine>\n"
        return f"{indent}<PstlAdr>\n{content}{indent}</PstlAdr>\n"

    def _with_instructing_agent(self, value: dict[str, object]) -> bytes:
        """Insert one postal-rich InstgAgt into the outgoing-payment detail."""
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

    @staticmethod
    def _assert_exact_accounting_amount(entry: object, detail: object) -> None:
        """Pin accounting truth independently from bank-reported postal provenance."""
        if getattr(entry, "entry_amount", None) != Decimal("6000.00"):
            raise AssertionError("postal evidence must retain exact 6000.00 entry amount")
        if getattr(entry, "entry_currency_code", None) != "KRW":
            raise AssertionError("postal evidence must retain KRW entry currency")
        if getattr(detail, "detail_amount", None) != Decimal("6000.00"):
            raise AssertionError("postal evidence must retain exact 6000.00 detail amount")
        if getattr(detail, "detail_currency_code", None) != "KRW":
            raise AssertionError("postal evidence must retain KRW detail currency")

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
            "ingestion_idempotency_key": (
                f"payment-agent-postal-{suffix}-{uuid.uuid4().hex}"
            ),
            "message_definition_identifier": CAMT053_MESSAGE_DEFINITION,
            "statement_payload": payload.decode("utf-8"),
        }


if __name__ == "__main__":
    unittest.main()
