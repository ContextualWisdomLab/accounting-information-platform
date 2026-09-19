"""PostgreSQL REDs for PostalAddress27 inside repeated proprietary agents."""

from __future__ import annotations

import copy
import re
import unittest
import uuid
from decimal import Decimal

from tests import (
    test_postgres_bank_statement_detail_payment_agent_postal_address_evidence_red as payment_postal,
)
from tests import (
    test_postgres_bank_statement_detail_proprietary_agent_identity_evidence_red as proprietary,
)
from tests import test_postgres_posting as posting

_HASH_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_CORRECTION_ERROR = (
    r"^statement identity already exists with different entry evidence\. "
    r"Use an explicit correction contract, then retry ingest\.$"
)


class BankStatementDetailProprietaryAgentPostalAddressEvidenceRedTests(unittest.TestCase):
    """Retain institution and branch PostalAddress27 inside repeated ProprietaryAgent5."""

    @classmethod
    def setUpClass(cls) -> None:
        """Reuse the canonical real-PostgreSQL integration fixture."""
        posting.PostgresPostingTests.setUpClass()

    def setUp(self) -> None:
        """Prepare source-real repeated proprietary-agent postal variants."""
        self.case = posting.PostgresPostingTests("setUp")
        self.addCleanup(self.case.doCleanups)
        self.case.setUp()
        self.addCleanup(self.case.tearDown)

        fixture = proprietary.load_canonical_statement_fixture().decode("utf-8")
        marker = "            </RltdPties>\n            <RmtInf>"
        self.assertEqual(fixture.count(marker), 1)

        institution_address = payment_postal.BankStatementDetailPaymentAgentPostalAddressEvidenceRedTests._institution_address()
        branch_address = payment_postal.BankStatementDetailPaymentAgentPostalAddressEvidenceRedTests._branch_address()
        second_institution_address = copy.deepcopy(institution_address)
        second_institution_address.update(
            {
                "care_of": "Custody Operations",
                "department": "Securities Services",
                "sub_department": "Custody",
                "street_name": "Rue de la Paix",
                "building_number": "8",
                "building_name": "Custody House",
                "floor": "4",
                "unit_number": "402",
                "post_box": "3003",
                "room": "Custody",
                "post_code": "75002",
                "town_name": "Paris",
                "town_location_name": "Quartier Gaillon",
                "district_name": "2e arrondissement",
                "country_subdivision": "IDF",
                "country": "FR",
                "address_lines": ["8 Rue de la Paix", "75002 Paris"],
            }
        )
        second_branch_address = copy.deepcopy(branch_address)
        second_branch_address.update(
            {
                "care_of": "Custody Desk",
                "department": "Global Custody",
                "sub_department": "Settlement",
                "street_name": "Boulevard des Italiens",
                "building_number": "16",
                "building_name": "Custody Annex",
                "floor": "2",
                "unit_number": "204",
                "post_box": "4004",
                "room": "Settlement",
                "post_code": "75009",
                "town_name": "Paris",
                "town_location_name": "Opéra",
                "district_name": "9e arrondissement",
                "country_subdivision": "IDF",
                "country": "FR",
                "address_lines": ["16 Boulevard des Italiens", "75009 Paris"],
            }
        )

        self.base = [
            {
                "type": "BROKER_AGENT",
                "agent": {
                    "bicfi": "DEUTDEFF",
                    "name": "Execution Broker Frankfurt",
                    "postal_address": institution_address,
                    "branch": {
                        "id": "BROKER-FRA-001",
                        "name": "Frankfurt Execution Branch",
                        "postal_address": branch_address,
                    },
                },
            },
            {
                "type": "CUSTODY_AGENT",
                "agent": {
                    "bicfi": "BNPAFRPP",
                    "name": "Custody Agent Paris",
                    "postal_address": second_institution_address,
                    "branch": {
                        "id": "CUSTODY-PAR-001",
                        "name": "Paris Custody Branch",
                        "postal_address": second_branch_address,
                    },
                },
            },
        ]
        self.variants = self._variants(self.base)
        self.base_payload = self._with_agents(fixture, marker, self.base)
        self.base_statement = self._statement(self.base_payload)
        self.variant_payloads = {
            name: self._with_agents(fixture, marker, value)
            for name, value in self.variants.items()
        }
        self.variant_statements = {
            name: self._statement(payload)
            for name, payload in self.variant_payloads.items()
        }

        agent_xml = self._agents_xml(self.base)
        self.assertEqual(self.base_payload.count(agent_xml.encode("utf-8")), 1)
        first_agent_xml = self._agent_xml(self.base[0])
        self.assertEqual(first_agent_xml.count("<PstlAdr>"), 2)
        institution_whitespace = first_agent_xml.replace(
            "                    <PstlAdr>\n",
            "                    <PstlAdr>\n                      \n",
            1,
        )
        first_postal_end = first_agent_xml.find("</PstlAdr>")
        second_postal_start = first_agent_xml.find("<PstlAdr>", first_postal_end)
        if first_postal_end < 0 or second_postal_start < 0:
            raise AssertionError("focused proprietary agent requires institution and branch PstlAdr")
        branch_whitespace = (
            first_agent_xml[:second_postal_start]
            + first_agent_xml[second_postal_start:].replace(
                "<PstlAdr>\n",
                "<PstlAdr>\n                      \n",
                1,
            )
        )
        self.reformatted_statements = {
            "institution-whitespace": self._statement(
                self.base_payload.replace(
                    first_agent_xml.encode("utf-8"),
                    institution_whitespace.encode("utf-8"),
                    1,
                )
            ),
            "branch-whitespace": self._statement(
                self.base_payload.replace(
                    first_agent_xml.encode("utf-8"),
                    branch_whitespace.encode("utf-8"),
                    1,
                )
            ),
        }

        self.bank_account_reference = self._register_bank_account(self.base_statement)

    def test_proprietary_agent_postal_address_is_material_at_each_position(self) -> None:
        """Institution/branch postal facts and repeated positions affect canonical evidence."""
        base_entry = self.base_statement.entries[0]
        base_detail = base_entry.entry_details[0]
        base_digest = getattr(base_detail, "proprietary_agents_evidence_hash", None)
        self._assert_digest(base_digest)
        self.assertEqual(
            base_digest,
            proprietary.BankStatementDetailProprietaryAgentIdentityEvidenceRedTests._expected_hash(
                self.base
            ),
        )
        self._assert_exact_amount(base_entry, base_detail)

        for name, statement in self.variant_statements.items():
            with self.subTest(name=name):
                entry = statement.entries[0]
                detail = entry.entry_details[0]
                digest = getattr(detail, "proprietary_agents_evidence_hash", None)
                self._assert_digest(digest)
                self.assertEqual(
                    digest,
                    proprietary.BankStatementDetailProprietaryAgentIdentityEvidenceRedTests._expected_hash(
                        self.variants[name]
                    ),
                )
                self.assertNotEqual(base_digest, digest)
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
                self._assert_exact_amount(entry, detail)

    def test_postal_layout_is_representation_only_for_institution_and_branch(self) -> None:
        """Whitespace in either PostalAddress27 changes bytes but not admitted semantics."""
        base_entry = self.base_statement.entries[0]
        base_detail = base_entry.entry_details[0]
        base_digest = getattr(base_detail, "proprietary_agents_evidence_hash", None)
        self._assert_digest(base_digest)
        expected_digest = (
            proprietary.BankStatementDetailProprietaryAgentIdentityEvidenceRedTests._expected_hash(
                self.base
            )
        )
        self.assertEqual(base_digest, expected_digest)

        for name, statement in self.reformatted_statements.items():
            with self.subTest(name=name):
                entry = statement.entries[0]
                detail = entry.entry_details[0]
                digest = getattr(detail, "proprietary_agents_evidence_hash", None)
                self._assert_digest(digest)
                self.assertEqual(digest, expected_digest)
                self.assertNotEqual(
                    self.base_statement.source_artifact_hash,
                    statement.source_artifact_hash,
                )
                self.assertEqual(base_digest, digest)
                self.assertEqual(base_detail.source_detail_hash, detail.source_detail_hash)
                self.assertEqual(base_entry.source_entry_hash, entry.source_entry_hash)
                self.assertEqual(
                    self.base_statement.normalized_payload_hash,
                    statement.normalized_payload_hash,
                )
                self._assert_exact_amount(entry, detail)

    def test_material_postal_change_requires_explicit_correction(self) -> None:
        """Accepted proprietary-agent postal provenance cannot be silently replaced."""
        store = proprietary.MemoryArtifactStore()
        accepted = proprietary.accept_bank_statement_evidence(
            self._command(self.base_payload, self.bank_account_reference, "base"),
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
            artifact_store=store,
        )
        self.assertFalse(accepted["replayed"])

        changed_payload = self.variant_payloads["first-institution-town-name"]
        with self.assertRaisesRegex(proprietary.AccountingValidationError, _CORRECTION_ERROR):
            proprietary.accept_bank_statement_evidence(
                self._command(
                    changed_payload,
                    self.bank_account_reference,
                    "changed",
                ),
                posting.DATABASE_URL,
                self.case.policy.tenant_reference,
                artifact_store=store,
            )

    def test_buyer_read_preserves_structured_postal_provenance_without_amount_authority(self) -> None:
        """Buyer reconciliation reads retain postal structure while 25000 KRW stays exact."""
        changed_payload = self.variant_payloads["second-branch-post-code"]
        changed_statement = self.variant_statements["second-branch-post-code"]
        changed_reference = self._register_bank_account(changed_statement)

        base_detail = self._ingest_and_read(
            self.base_payload,
            self.bank_account_reference,
            "buyer-base",
        )
        changed_detail = self._ingest_and_read(
            changed_payload,
            changed_reference,
            "buyer-changed",
        )

        for detail in (base_detail, changed_detail):
            self._assert_digest(detail.get("proprietary_agents_evidence_hash"))
            self._assert_digest(detail.get("source_detail_hash"))
            self.assertEqual(detail["detail_amount"], "25000")
            self.assertEqual(detail["detail_currency_code"], "KRW")
            self.assertIsInstance(detail.get("proprietary_agents"), list)

        self.assertEqual(base_detail["proprietary_agents"], self.base)
        self.assertEqual(
            changed_detail["proprietary_agents"],
            self.variants["second-branch-post-code"],
        )
        self.assertNotEqual(
            base_detail["proprietary_agents_evidence_hash"],
            changed_detail["proprietary_agents_evidence_hash"],
        )
        self.assertNotEqual(
            base_detail["source_detail_hash"],
            changed_detail["source_detail_hash"],
        )

    @classmethod
    def _variants(
        cls,
        base: list[dict[str, object]],
    ) -> dict[str, list[dict[str, object]]]:
        """Change each admitted postal field and both proprietary-agent positions."""
        variants: dict[str, list[dict[str, object]]] = {}
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

        for owner, label in (("institution", "first-institution"), ("branch", "first-branch")):
            for field, replacement in scalar_changes.items():
                variant = copy.deepcopy(base)
                address = cls._address(variant, 0, owner)
                address[field] = copy.deepcopy(replacement)
                variants[f"{label}-{field.replace('_', '-')}"] = variant

            changed_line = copy.deepcopy(base)
            address = cls._address(changed_line, 0, owner)
            lines = address.get("address_lines")
            if not isinstance(lines, list) or len(lines) < 2:
                raise AssertionError("focused postal address requires source-ordered AdrLine")
            lines[0] = "Alternate address line"
            variants[f"{label}-address-line-value"] = changed_line

            reordered = copy.deepcopy(base)
            address = cls._address(reordered, 0, owner)
            lines = address.get("address_lines")
            if not isinstance(lines, list) or len(lines) < 2:
                raise AssertionError("focused postal address requires source-ordered AdrLine")
            lines.reverse()
            variants[f"{label}-address-line-order"] = reordered

            absent = copy.deepcopy(base)
            if owner == "institution":
                agent = cls._agent(absent, 0)
                agent.pop("postal_address", None)
            else:
                branch = cls._branch(absent, 0)
                branch.pop("postal_address", None)
            variants[f"{label}-address-absent"] = absent

        second_institution = copy.deepcopy(base)
        cls._address(second_institution, 1, "institution")["town_name"] = "Lyon"
        variants["second-institution-town-name"] = second_institution

        second_branch = copy.deepcopy(base)
        cls._address(second_branch, 1, "branch")["post_code"] = "69002"
        variants["second-branch-post-code"] = second_branch
        return variants

    @staticmethod
    def _agent(value: list[dict[str, object]], index: int) -> dict[str, object]:
        agent = value[index].get("agent")
        if not isinstance(agent, dict):
            raise AssertionError("proprietary agent requires Agt")
        return agent

    @classmethod
    def _branch(cls, value: list[dict[str, object]], index: int) -> dict[str, object]:
        branch = cls._agent(value, index).get("branch")
        if not isinstance(branch, dict):
            raise AssertionError("focused proprietary agent requires BrnchId")
        return branch

    @classmethod
    def _address(
        cls,
        value: list[dict[str, object]],
        index: int,
        owner: str,
    ) -> dict[str, object]:
        container = cls._agent(value, index) if owner == "institution" else cls._branch(value, index)
        address = container.get("postal_address")
        if not isinstance(address, dict):
            raise AssertionError("focused proprietary agent requires PostalAddress27")
        return address

    @classmethod
    def _with_agents(
        cls,
        fixture: str,
        marker: str,
        agents: list[dict[str, object]],
    ) -> bytes:
        items = cls._agents_xml(agents)
        replacement = "            </RltdPties>\n" + items + "            <RmtInf>"
        changed = fixture.replace(marker, replacement, 1)
        if changed == fixture:
            raise AssertionError("proprietary postal insertion must change XML")
        return changed.encode("utf-8")

    @classmethod
    def _agents_xml(cls, agents: list[dict[str, object]]) -> str:
        """Serialize source-ordered proprietary agents."""
        return (
            "            <RltdAgts>\n"
            + "".join(cls._agent_xml(agent) for agent in agents)
            + "            </RltdAgts>\n"
        )

    @classmethod
    def _agent_xml(cls, value: dict[str, object]) -> str:
        """Serialize ProprietaryAgent5 with institution and branch PostalAddress27."""
        agent_type = value.get("type")
        agent = value.get("agent")
        if not isinstance(agent_type, str) or not agent_type:
            raise AssertionError("ProprietaryAgent5 requires Tp")
        if not isinstance(agent, dict):
            raise AssertionError("ProprietaryAgent5 requires Agt")
        bicfi = agent.get("bicfi")
        if not isinstance(bicfi, str) or not bicfi:
            raise AssertionError("focused proprietary agent requires BICFI")

        parts = [
            "              <Prtry>\n",
            f"                <Tp>{agent_type}</Tp>\n",
            "                <Agt>\n",
            "                  <FinInstnId>\n",
            f"                    <BICFI>{bicfi}</BICFI>\n",
        ]
        if isinstance(agent.get("name"), str):
            parts.append(f"                    <Nm>{agent['name']}</Nm>\n")
        institution_address = agent.get("postal_address")
        if institution_address is not None:
            if not isinstance(institution_address, dict):
                raise AssertionError("institution postal_address must be a mapping")
            parts.append(cls._postal_xml(institution_address, "                    "))
        parts.append("                  </FinInstnId>\n")

        branch = agent.get("branch")
        if branch is not None:
            if not isinstance(branch, dict):
                raise AssertionError("branch must be a mapping")
            parts.append("                  <BrnchId>\n")
            if isinstance(branch.get("id"), str):
                parts.append(f"                    <Id>{branch['id']}</Id>\n")
            if isinstance(branch.get("name"), str):
                parts.append(f"                    <Nm>{branch['name']}</Nm>\n")
            branch_address = branch.get("postal_address")
            if branch_address is not None:
                if not isinstance(branch_address, dict):
                    raise AssertionError("branch postal_address must be a mapping")
                parts.append(cls._postal_xml(branch_address, "                    "))
            parts.append("                  </BrnchId>\n")
        parts.extend(["                </Agt>\n", "              </Prtry>\n"])
        return "".join(parts)

    @staticmethod
    def _postal_xml(address: dict[str, object], indent: str) -> str:
        return payment_postal.BankStatementDetailPaymentAgentPostalAddressEvidenceRedTests._postal_xml(
            address,
            indent,
        )

    @staticmethod
    def _statement(payload: bytes) -> object:
        return proprietary.parse_bank_statement_payload(
            payload,
            proprietary.CAMT053_MESSAGE_DEFINITION,
        )

    def _register_bank_account(self, statement: object) -> str:
        reference = f"urn:cwl:bank_account:{uuid.uuid4().hex}"
        proprietary.accept_bank_account_record(
            {
                "tenant_reference": self.case.policy.tenant_reference,
                "bank_account_reference": reference,
                "account_currency_code": getattr(statement, "account_currency_code"),
                "account_identifier_hash": getattr(statement, "account_identifier_hash"),
            },
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
        )
        return reference

    def _command(
        self,
        payload: bytes,
        account_reference: str,
        suffix: str,
    ) -> dict[str, object]:
        return {
            "tenant_reference": self.case.policy.tenant_reference,
            "bank_account_reference": account_reference,
            "ingestion_idempotency_key": (
                f"proprietary-agent-postal-{suffix}-{uuid.uuid4().hex}"
            ),
            "message_definition_identifier": proprietary.CAMT053_MESSAGE_DEFINITION,
            "statement_payload": payload.decode("utf-8"),
        }

    def _ingest_and_read(
        self,
        payload: bytes,
        account_reference: str,
        suffix: str,
    ) -> dict[str, object]:
        accepted = proprietary.accept_bank_statement_evidence(
            self._command(payload, account_reference, suffix),
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
            artifact_store=proprietary.MemoryArtifactStore(),
        )
        document = proprietary.lookup_bank_statement_entries(
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
            str(accepted["bank_statement_record_id"]),
        )
        return document["bank_statement_entries"][0]["entry_details"][0]

    @staticmethod
    def _assert_digest(value: object) -> None:
        if not isinstance(value, str) or _HASH_RE.fullmatch(value) is None:
            raise AssertionError("proprietary-agent evidence digest must be sha256:<64 hex>")

    @staticmethod
    def _assert_exact_amount(entry: object, detail: object) -> None:
        if getattr(entry, "entry_amount", None) != Decimal("25000.00"):
            raise AssertionError("proprietary-agent postal evidence must retain 25000.00")
        if getattr(entry, "entry_currency_code", None) != "KRW":
            raise AssertionError("proprietary-agent postal evidence must retain KRW")
        if getattr(detail, "detail_amount", None) != Decimal("25000.00"):
            raise AssertionError("proprietary-agent postal detail must retain 25000.00")
        if getattr(detail, "detail_currency_code", None) != "KRW":
            raise AssertionError("proprietary-agent postal detail must retain KRW")


if __name__ == "__main__":
    unittest.main()
