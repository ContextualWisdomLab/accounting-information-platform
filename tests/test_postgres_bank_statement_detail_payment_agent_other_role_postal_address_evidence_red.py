"""PostgreSQL REDs for PostalAddress27 across non-instructing payment-agent roles."""

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


class BankStatementDetailPaymentAgentOtherRolePostalAddressEvidenceRedTests(
    unittest.TestCase
):
    """Retain institution and branch postal provenance on every remaining standard role."""

    ROLES = {
        "InstdAgt": ("instructed_agent_evidence_hash", "BNPAFRPP"),
        "RcvgAgt": ("receiving_agent_evidence_hash", "CHASUS33"),
        "DlvrgAgt": ("delivering_agent_evidence_hash", "CITIUS33"),
        "IssgAgt": ("issuing_agent_evidence_hash", "PNCCUS33"),
        "SttlmPlc": ("settlement_place_evidence_hash", "BARCGB22"),
    }

    @classmethod
    def setUpClass(cls) -> None:
        """Reuse the canonical real-PostgreSQL integration fixture."""
        posting.PostgresPostingTests.setUpClass()

    def setUp(self) -> None:
        """Prepare role-specific postal materiality and buyer-privacy controls."""
        self.case = posting.PostgresPostingTests("setUp")
        self.addCleanup(self.case.doCleanups)
        self.case.setUp()
        self.addCleanup(self.case.tearDown)

        self.fixture = load_canonical_statement_fixture().decode("utf-8")
        self.marker = (
            "            <AmtDtls>\n"
            "              <TxAmt>\n"
            "                <Amt Ccy=\"KRW\">6000.00</Amt>\n"
            "              </TxAmt>\n"
            "            </AmtDtls>\n"
            "            <RmtInf>"
        )
        self.assertEqual(self.fixture.count(self.marker), 1)

    def test_postal_address_is_material_for_every_remaining_standard_role(self) -> None:
        """Role-specific institution and branch postal facts affect canonical evidence."""
        for element_name, (digest_key, bicfi) in self.ROLES.items():
            with self.subTest(element_name=element_name):
                base_value = self._agent_value(element_name, bicfi)
                base = self._statement(self._with_agent(element_name, base_value))
                variants = self._postal_variants(base_value)
                base_entry = base.entries[1]
                base_detail = base_entry.entry_details[0]
                self._assert_exact_accounting_amount(base_entry, base_detail)
                self.assertIsInstance(getattr(base_detail, digest_key, None), str)

                for variant_name, value in variants.items():
                    with self.subTest(
                        element_name=element_name,
                        variant_name=variant_name,
                    ):
                        statement = self._statement(self._with_agent(element_name, value))
                        entry = statement.entries[1]
                        detail = entry.entry_details[0]
                        self._assert_exact_accounting_amount(entry, detail)
                        self.assertEqual(
                            getattr(base_detail, digest_key, None),
                            getattr(detail, digest_key, None),
                            "BICFI is fixed; the existing role digest must stay stable",
                        )
                        self.assertEqual(
                            base.account_identifier_hash,
                            statement.account_identifier_hash,
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
                            base.normalized_payload_hash,
                            statement.normalized_payload_hash,
                        )
                        self.assertEqual(
                            base.entries[0].source_entry_hash,
                            statement.entries[0].source_entry_hash,
                        )

    def test_postal_xml_layout_is_representation_only_for_every_role(self) -> None:
        """Whitespace inside PstlAdr changes raw bytes but not admitted semantics."""
        for element_name, (digest_key, bicfi) in self.ROLES.items():
            with self.subTest(element_name=element_name):
                value = self._agent_value(element_name, bicfi)
                payload = self._with_agent(element_name, value)
                reformatted = payload.replace(
                    b"                  <PstlAdr>\n",
                    b"                  <PstlAdr>\n                    \n",
                    1,
                )
                self.assertNotEqual(payload, reformatted)
                base = self._statement(payload)
                changed = self._statement(reformatted)
                base_entry = base.entries[1]
                changed_entry = changed.entries[1]
                base_detail = base_entry.entry_details[0]
                changed_detail = changed_entry.entry_details[0]

                self.assertNotEqual(base.source_artifact_hash, changed.source_artifact_hash)
                self.assertEqual(
                    getattr(base_detail, digest_key, None),
                    getattr(changed_detail, digest_key, None),
                )
                self.assertEqual(
                    base_detail.source_detail_hash,
                    changed_detail.source_detail_hash,
                )
                self.assertEqual(base_entry.source_entry_hash, changed_entry.source_entry_hash)
                self.assertEqual(base.normalized_payload_hash, changed.normalized_payload_hash)
                self._assert_exact_accounting_amount(changed_entry, changed_detail)

    def test_postal_change_requires_explicit_correction_for_every_role(self) -> None:
        """Accepted role-specific postal evidence cannot be replaced by silent replay."""
        for element_name, (_, bicfi) in self.ROLES.items():
            with self.subTest(element_name=element_name):
                base_value = self._agent_value(element_name, bicfi)
                changed_value = copy.deepcopy(base_value)
                changed_value["postal_address"]["street_name"] = "Changed Institution Street"
                base_payload = self._with_agent(element_name, base_value)
                changed_payload = self._with_agent(element_name, changed_value)
                statement = self._statement(base_payload)
                account_reference = self._register_bank_account(statement)
                store = MemoryArtifactStore()
                accepted = accept_bank_statement_evidence(
                    self._command(base_payload, account_reference, f"base-{element_name}"),
                    posting.DATABASE_URL,
                    self.case.policy.tenant_reference,
                    artifact_store=store,
                )
                self.assertFalse(accepted["replayed"])
                with self.assertRaisesRegex(AccountingValidationError, _CORRECTION_ERROR):
                    accept_bank_statement_evidence(
                        self._command(
                            changed_payload,
                            account_reference,
                            f"changed-{element_name}",
                        ),
                        posting.DATABASE_URL,
                        self.case.policy.tenant_reference,
                        artifact_store=store,
                    )

    def test_postal_evidence_stays_private_on_buyer_projection_for_every_role(self) -> None:
        """Buyer reads keep the canonical role digest without reversible postal facts."""
        for element_name, (digest_key, bicfi) in self.ROLES.items():
            with self.subTest(element_name=element_name):
                rich_value = self._agent_value(element_name, bicfi)
                no_postal_value = copy.deepcopy(rich_value)
                no_postal_value.pop("postal_address", None)
                branch = no_postal_value.get("branch")
                if not isinstance(branch, dict):
                    raise AssertionError("privacy baseline requires branch identity")
                branch.pop("postal_address", None)

                rich_payload = self._with_agent(element_name, rich_value)
                no_postal_payload = self._with_agent(element_name, no_postal_value)
                rich_statement = self._statement(rich_payload)
                no_postal_statement = self._statement(no_postal_payload)
                rich_reference = self._register_bank_account(rich_statement)
                no_postal_reference = self._register_bank_account(no_postal_statement)
                rich_detail = self._ingest_and_read(
                    rich_payload,
                    rich_reference,
                    f"privacy-rich-{element_name}",
                )
                no_postal_detail = self._ingest_and_read(
                    no_postal_payload,
                    no_postal_reference,
                    f"privacy-no-postal-{element_name}",
                )

                for projection in (rich_detail, no_postal_detail):
                    self.assertIsInstance(projection.get(digest_key), str)
                    self.assertRegex(
                        str(projection[digest_key]),
                        r"\Asha256:[0-9a-f]{64}\Z",
                    )
                    self.assertRegex(
                        str(projection["source_detail_hash"]),
                        r"\Asha256:[0-9a-f]{64}\Z",
                    )
                    self.assertEqual(projection["detail_amount"], "6000")
                    self.assertEqual(projection["detail_currency_code"], "KRW")

                self.assertEqual(rich_detail[digest_key], no_postal_detail[digest_key])
                self.assertNotEqual(
                    rich_detail["source_detail_hash"],
                    no_postal_detail["source_detail_hash"],
                )
                rich_visible = dict(rich_detail)
                no_postal_visible = dict(no_postal_detail)
                for projection in (rich_visible, no_postal_visible):
                    projection.pop(digest_key)
                    projection.pop("source_detail_hash")
                self.assertEqual(rich_visible, no_postal_visible)

    @staticmethod
    def _agent_value(element_name: str, bicfi: str) -> dict[str, object]:
        return {
            "bicfi": bicfi,
            "name": f"{element_name} Institution",
            "postal_address": {
                "street_name": "Institution Street",
                "building_number": "10",
                "post_code": "10000",
                "town_name": "Institution City",
                "country": "DE",
                "address_lines": ["Institution Street 10", "10000 Institution City"],
            },
            "branch": {
                "id": f"{element_name}-BR-001",
                "name": f"{element_name} Branch",
                "postal_address": {
                    "street_name": "Branch Street",
                    "building_number": "20",
                    "post_code": "20000",
                    "town_name": "Branch City",
                    "country": "DE",
                    "address_lines": ["Branch Street 20", "20000 Branch City"],
                },
            },
        }

    @staticmethod
    def _postal_variants(base: dict[str, object]) -> dict[str, dict[str, object]]:
        variants: dict[str, dict[str, object]] = {}

        institution_street = copy.deepcopy(base)
        institution_address = institution_street.get("postal_address")
        if not isinstance(institution_address, dict):
            raise AssertionError("institution postal address must be a mapping")
        institution_address["street_name"] = "Alternate Institution Street"
        variants["institution-street"] = institution_street

        institution_absent = copy.deepcopy(base)
        institution_absent.pop("postal_address", None)
        variants["institution-address-absent"] = institution_absent

        branch_street = copy.deepcopy(base)
        branch = branch_street.get("branch")
        if not isinstance(branch, dict):
            raise AssertionError("branch identity must be a mapping")
        branch_address = branch.get("postal_address")
        if not isinstance(branch_address, dict):
            raise AssertionError("branch postal address must be a mapping")
        branch_address["street_name"] = "Alternate Branch Street"
        variants["branch-street"] = branch_street

        branch_absent = copy.deepcopy(base)
        branch = branch_absent.get("branch")
        if not isinstance(branch, dict):
            raise AssertionError("branch identity must be a mapping")
        branch.pop("postal_address", None)
        variants["branch-address-absent"] = branch_absent
        return variants

    def _with_agent(self, element_name: str, value: dict[str, object]) -> bytes:
        """Insert one standard role using BranchAndFinancialInstitutionIdentification8."""
        if element_name not in self.ROLES:
            raise AssertionError(f"unsupported standard transaction-agent role: {element_name}")
        replacement = (
            "            <AmtDtls>\n"
            "              <TxAmt>\n"
            "                <Amt Ccy=\"KRW\">6000.00</Amt>\n"
            "              </TxAmt>\n"
            "            </AmtDtls>\n"
            "            <RltdAgts>\n"
            + self._agent_xml(element_name, value)
            + "            </RltdAgts>\n"
            "            <RmtInf>"
        )
        return self.fixture.replace(self.marker, replacement, 1).encode("utf-8")

    @classmethod
    def _agent_xml(cls, element_name: str, value: dict[str, object]) -> str:
        """Serialize the focused standard-role institution and branch in XSD order."""
        lines = [
            f"              <{element_name}>\n",
            "                <FinInstnId>\n",
            f"                  <BICFI>{value['bicfi']}</BICFI>\n",
            f"                  <Nm>{value['name']}</Nm>\n",
        ]
        postal_address = value.get("postal_address")
        if isinstance(postal_address, dict):
            lines.extend(cls._address_xml(postal_address, indent="                  "))
        lines.append("                </FinInstnId>\n")

        branch = value.get("branch")
        if isinstance(branch, dict):
            lines.extend(
                [
                    "                <BrnchId>\n",
                    f"                  <Id>{branch['id']}</Id>\n",
                    f"                  <Nm>{branch['name']}</Nm>\n",
                ]
            )
            branch_address = branch.get("postal_address")
            if isinstance(branch_address, dict):
                lines.extend(cls._address_xml(branch_address, indent="                  "))
            lines.append("                </BrnchId>\n")

        lines.append(f"              </{element_name}>\n")
        return "".join(lines)

    @staticmethod
    def _address_xml(address: dict[str, object], *, indent: str) -> list[str]:
        """Serialize the focused PostalAddress27 subset in schema sequence order."""
        lines = [f"{indent}<PstlAdr>\n"]
        field_tags = (
            ("street_name", "StrtNm"),
            ("building_number", "BldgNb"),
            ("post_code", "PstCd"),
            ("town_name", "TwnNm"),
            ("country", "Ctry"),
        )
        for field, tag in field_tags:
            value = address.get(field)
            if value is not None:
                lines.append(f"{indent}  <{tag}>{value}</{tag}>\n")
        address_lines = address.get("address_lines", [])
        if not isinstance(address_lines, list):
            raise AssertionError("PostalAddress27 AdrLine values must be source ordered")
        for line in address_lines:
            lines.append(f"{indent}  <AdrLine>{line}</AdrLine>\n")
        lines.append(f"{indent}</PstlAdr>\n")
        return lines

    @staticmethod
    def _statement(payload: bytes):
        """Parse one source-real camt.053 payload through the supported adapter."""
        return parse_bank_statement_payload(payload, CAMT053_MESSAGE_DEFINITION)

    @staticmethod
    def _assert_exact_accounting_amount(entry: object, detail: object) -> None:
        """Keep postal evidence independent from exact accounting amount and currency."""
        if getattr(entry, "entry_amount", None) != Decimal("6000.00"):
            raise AssertionError("postal evidence must retain 6000.00 entry amount")
        if getattr(entry, "entry_currency_code", None) != "KRW":
            raise AssertionError("postal evidence must retain KRW entry currency")
        if getattr(detail, "detail_amount", None) != Decimal("6000.00"):
            raise AssertionError("postal evidence must retain 6000.00 detail amount")
        if getattr(detail, "detail_currency_code", None) != "KRW":
            raise AssertionError("postal evidence must retain KRW detail currency")

    def _register_bank_account(self, statement: object) -> str:
        """Register one isolated buyer bank account for a statement-evidence scenario."""
        reference = f"urn:cwl:bank_account:{uuid.uuid4().hex}"
        accept_bank_account_record(
            {
                "tenant_reference": self.case.policy.tenant_reference,
                "bank_account_reference": reference,
                "account_currency_code": statement.account_currency_code,
                "account_identifier_hash": statement.account_identifier_hash,
            },
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
        )
        return reference

    def _ingest_and_read(
        self,
        payload: bytes,
        account_reference: str,
        suffix: str,
    ) -> dict[str, object]:
        """Ingest one fixture and return the outgoing-payment transaction detail."""
        accepted = accept_bank_statement_evidence(
            self._command(payload, account_reference, suffix),
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
        account_reference: str,
        suffix: str,
    ) -> dict[str, object]:
        """Return one supported ingest command with an isolated replay key."""
        return {
            "tenant_reference": self.case.policy.tenant_reference,
            "bank_account_reference": account_reference,
            "ingestion_idempotency_key": (
                f"payment-agent-other-role-postal-{suffix}-{uuid.uuid4().hex}"
            ),
            "message_definition_identifier": CAMT053_MESSAGE_DEFINITION,
            "statement_payload": payload.decode("utf-8"),
        }


if __name__ == "__main__":
    unittest.main()
