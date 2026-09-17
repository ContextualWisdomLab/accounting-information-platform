"""PostgreSQL REDs for AddressType3Choice inside a standard transaction-agent role."""

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


class BankStatementDetailPaymentAgentAddressTypeChoiceEvidenceRedTests(unittest.TestCase):
    """Retain standard-agent address-type choice semantics without changing accounting truth."""

    @classmethod
    def setUpClass(cls) -> None:
        """Reuse the canonical real-PostgreSQL integration fixture."""
        posting.PostgresPostingTests.setUpClass()

    def setUp(self) -> None:
        """Prepare proprietary and coded address-type variants for one InstgAgt."""
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
            "postal_address": {
                "address_type": {
                    "proprietary": {
                        "id": "CSTM",
                        "issuer": "CWL Address Registry",
                        "scheme_name": "ADDR_TYPE",
                    }
                },
                "country": "DE",
            },
            "branch": {
                "id": "INSTG-FRA-001",
                "name": "Frankfurt Payments Branch",
                "postal_address": {
                    "address_type": {
                        "proprietary": {
                            "id": "CSTB",
                            "issuer": "CWL Branch Registry",
                            "scheme_name": "BRANCH_ADDR_TYPE",
                        }
                    },
                    "country": "DE",
                },
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
            "                      <Prtry>\n",
            "                      <Prtry>\n                        \n",
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

    def test_address_type_choice_and_proprietary_fields_are_material(self) -> None:
        """Cd/Prtry and every admitted proprietary field affect canonical evidence identity."""
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
                    "BICFI is unchanged; the existing purpose digest must remain stable",
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

    def test_proprietary_address_type_xml_layout_is_representation_only(self) -> None:
        """Whitespace inside Prtry changes source bytes, not admitted semantics."""
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

    def test_changed_address_type_choice_requires_explicit_correction(self) -> None:
        """Accepted address-type provenance cannot be silently replaced on replay."""
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

    def test_buyer_read_remains_digest_only_across_address_type_choice(self) -> None:
        """Choice materiality stays internal without adding reversible address-type fields."""
        proprietary_detail = self._ingest_and_read(
            self.base_payload,
            "lookup-proprietary",
        )

        coded_payload = self.variant_payloads["institution-choice-code"]
        coded_statement = self.variant_statements["institution-choice-code"]
        coded_reference = f"urn:cwl:bank_account:{uuid.uuid4().hex}"
        accept_bank_account_record(
            {
                "tenant_reference": self.case.policy.tenant_reference,
                "bank_account_reference": coded_reference,
                "account_currency_code": coded_statement.account_currency_code,
                "account_identifier_hash": coded_statement.account_identifier_hash,
            },
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
        )
        coded_detail = self._ingest_and_read(
            coded_payload,
            "lookup-coded",
            bank_account_reference=coded_reference,
        )

        for projection in (proprietary_detail, coded_detail):
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
            proprietary_detail["instructing_agent_evidence_hash"],
            coded_detail["instructing_agent_evidence_hash"],
        )
        self.assertNotEqual(
            proprietary_detail["source_detail_hash"],
            coded_detail["source_detail_hash"],
        )

        proprietary_projection = dict(proprietary_detail)
        coded_projection = dict(coded_detail)
        for projection in (proprietary_projection, coded_projection):
            projection.pop("instructing_agent_evidence_hash")
            projection.pop("source_detail_hash")
        self.assertEqual(proprietary_projection, coded_projection)

    @staticmethod
    def _variants(base: dict[str, object]) -> dict[str, dict[str, object]]:
        """Change each proprietary field and the Cd/Prtry discriminator independently."""
        variants: dict[str, dict[str, object]] = {}
        for owner in ("institution", "branch"):
            for field, replacement in (
                ("id", "OTHR"),
                ("issuer", "Alternate Address Registry"),
                ("scheme_name", "ALT_ADDR_TYPE"),
            ):
                variant = copy.deepcopy(base)
                if owner == "institution":
                    address = variant.get("postal_address")
                else:
                    branch = variant.get("branch")
                    if not isinstance(branch, dict):
                        raise AssertionError("branch must be a mapping")
                    address = branch.get("postal_address")
                if not isinstance(address, dict):
                    raise AssertionError("postal address must be a mapping")
                address_type = address.get("address_type")
                if not isinstance(address_type, dict):
                    raise AssertionError("address type must be a mapping")
                proprietary = address_type.get("proprietary")
                if not isinstance(proprietary, dict):
                    raise AssertionError("base address type must be proprietary")
                proprietary[field] = replacement
                variants[f"{owner}-proprietary-{field}"] = variant

            without_scheme = copy.deepcopy(base)
            if owner == "institution":
                address = without_scheme.get("postal_address")
            else:
                branch = without_scheme.get("branch")
                if not isinstance(branch, dict):
                    raise AssertionError("branch must be a mapping")
                address = branch.get("postal_address")
            if not isinstance(address, dict):
                raise AssertionError("postal address must be a mapping")
            address_type = address.get("address_type")
            if not isinstance(address_type, dict):
                raise AssertionError("address type must be a mapping")
            proprietary = address_type.get("proprietary")
            if not isinstance(proprietary, dict):
                raise AssertionError("base address type must be proprietary")
            proprietary.pop("scheme_name", None)
            variants[f"{owner}-proprietary-scheme-absent"] = without_scheme

            coded = copy.deepcopy(base)
            if owner == "institution":
                address = coded.get("postal_address")
            else:
                branch = coded.get("branch")
                if not isinstance(branch, dict):
                    raise AssertionError("branch must be a mapping")
                address = branch.get("postal_address")
            if not isinstance(address, dict):
                raise AssertionError("postal address must be a mapping")
            address["address_type"] = {"code": "BIZZ"}
            variants[f"{owner}-choice-code"] = coded
        return variants

    def _with_instructing_agent(self, value: dict[str, object]) -> bytes:
        """Insert one address-type-rich InstgAgt into the outgoing-payment detail."""
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
        """Serialize InstgAgt address-type evidence in V14 sequence order."""
        bicfi = value.get("bicfi")
        name = value.get("name")
        institution_address = value.get("postal_address")
        branch = value.get("branch")
        if not isinstance(bicfi, str) or not bicfi:
            raise AssertionError("focused instructing agent requires BICFI")
        if not isinstance(name, str) or not name:
            raise AssertionError("focused instructing agent requires institution name")
        if not isinstance(institution_address, dict):
            raise AssertionError("institution postal address must be a mapping")
        if not isinstance(branch, dict):
            raise AssertionError("focused instructing agent requires branch")
        branch_address = branch.get("postal_address")
        if not isinstance(branch_address, dict):
            raise AssertionError("branch postal address must be a mapping")

        return (
            "              <InstgAgt>\n"
            "                <FinInstnId>\n"
            f"                  <BICFI>{bicfi}</BICFI>\n"
            f"                  <Nm>{name}</Nm>\n"
            + cls._postal_xml(institution_address, "                  ")
            + "                </FinInstnId>\n"
            "                <BrnchId>\n"
            f"                  <Id>{branch['id']}</Id>\n"
            f"                  <Nm>{branch['name']}</Nm>\n"
            + cls._postal_xml(branch_address, "                  ")
            + "                </BrnchId>\n"
            "              </InstgAgt>\n"
        )

    @staticmethod
    def _postal_xml(address: dict[str, object], indent: str) -> str:
        """Serialize one PostalAddress27 with exactly one AddressType3Choice branch."""
        address_type = address.get("address_type")
        if not isinstance(address_type, dict) or len(address_type) != 1:
            raise AssertionError("AdrTp must contain exactly one AddressType3Choice branch")
        if "code" in address_type:
            address_type_xml = (
                f"{indent}  <AdrTp>\n"
                f"{indent}    <Cd>{address_type['code']}</Cd>\n"
                f"{indent}  </AdrTp>\n"
            )
        elif "proprietary" in address_type:
            proprietary = address_type["proprietary"]
            if not isinstance(proprietary, dict):
                raise AssertionError("proprietary AdrTp must be structured")
            scheme_name = proprietary.get("scheme_name")
            scheme_xml = (
                f"{indent}      <SchmeNm>{scheme_name}</SchmeNm>\n"
                if scheme_name is not None
                else ""
            )
            address_type_xml = (
                f"{indent}  <AdrTp>\n"
                f"{indent}    <Prtry>\n"
                f"{indent}      <Id>{proprietary['id']}</Id>\n"
                f"{indent}      <Issr>{proprietary['issuer']}</Issr>\n"
                + scheme_xml
                + f"{indent}    </Prtry>\n"
                f"{indent}  </AdrTp>\n"
            )
        else:
            raise AssertionError("unsupported AddressType3Choice branch")

        return (
            f"{indent}<PstlAdr>\n"
            + address_type_xml
            + f"{indent}  <Ctry>{address['country']}</Ctry>\n"
            f"{indent}</PstlAdr>\n"
        )

    @staticmethod
    def _assert_exact_accounting_amount(entry: object, detail: object) -> None:
        """Pin accounting truth independently from bank-reported address-type semantics."""
        if getattr(entry, "entry_amount", None) != Decimal("6000.00"):
            raise AssertionError("address-type evidence must retain exact 6000.00 entry amount")
        if getattr(entry, "entry_currency_code", None) != "KRW":
            raise AssertionError("address-type evidence must retain KRW entry currency")
        if getattr(detail, "detail_amount", None) != Decimal("6000.00"):
            raise AssertionError("address-type evidence must retain exact 6000.00 detail amount")
        if getattr(detail, "detail_currency_code", None) != "KRW":
            raise AssertionError("address-type evidence must retain KRW detail currency")

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
                f"payment-agent-address-type-{suffix}-{uuid.uuid4().hex}"
            ),
            "message_definition_identifier": CAMT053_MESSAGE_DEFINITION,
            "statement_payload": payload.decode("utf-8"),
        }


if __name__ == "__main__":
    unittest.main()
