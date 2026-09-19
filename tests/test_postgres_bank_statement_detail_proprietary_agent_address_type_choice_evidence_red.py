"""PostgreSQL REDs for AddressType3Choice inside repeated proprietary agents."""

from __future__ import annotations

import copy
import re
import unittest
import uuid
from decimal import Decimal

from tests import (
    test_postgres_bank_statement_detail_proprietary_agent_identity_evidence_red as proprietary,
)
from tests import test_postgres_posting as posting

_HASH_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_CORRECTION_ERROR = (
    r"^statement identity already exists with different entry evidence\. "
    r"Use an explicit correction contract, then retry ingest\.$"
)


class BankStatementDetailProprietaryAgentAddressTypeChoiceEvidenceRedTests(unittest.TestCase):
    """Retain AddressType3Choice semantics at every repeated proprietary-agent address."""

    @classmethod
    def setUpClass(cls) -> None:
        """Reuse the canonical real-PostgreSQL integration fixture."""
        posting.PostgresPostingTests.setUpClass()

    def setUp(self) -> None:
        """Prepare repeated proprietary agents with institution and branch address types."""
        self.case = posting.PostgresPostingTests("setUp")
        self.addCleanup(self.case.doCleanups)
        self.case.setUp()
        self.addCleanup(self.case.tearDown)

        fixture = proprietary.load_canonical_statement_fixture().decode("utf-8")
        self.marker = "            </RltdPties>\n            <RmtInf>"
        self.assertEqual(fixture.count(self.marker), 1)
        self.fixture = fixture

        self.base = [
            self._agent_value(
                "BROKER_AGENT",
                "DEUTDEFF",
                "Execution Broker Frankfurt",
                "BROKER-FRA-001",
                "Frankfurt Execution Branch",
                "CSTM",
                "CWL Address Registry",
                "ADDR_TYPE",
                "CSTB",
                "CWL Branch Registry",
                "BRANCH_ADDR_TYPE",
                "DE",
            ),
            self._agent_value(
                "CUSTODY_AGENT",
                "BNPAFRPP",
                "Custody Agent Paris",
                "CUSTODY-PAR-001",
                "Paris Custody Branch",
                "CSTC",
                "CWL Custody Address Registry",
                "CUSTODY_ADDR_TYPE",
                "CSTD",
                "CWL Custody Branch Registry",
                "CUSTODY_BRANCH_ADDR_TYPE",
                "FR",
            ),
        ]
        self.variants = self._variants(self.base)
        self.base_payload = self._with_agents(self.base)
        self.base_statement = self._statement(self.base_payload)
        self.variant_payloads = {
            name: self._with_agents(value) for name, value in self.variants.items()
        }
        self.variant_statements = {
            name: self._statement(payload)
            for name, payload in self.variant_payloads.items()
        }

        first_agent_xml = self._agent_xml(self.base[0])
        address_prtry = "                        <Prtry>\n"
        self.assertEqual(first_agent_xml.count(address_prtry), 2)
        institution_whitespace = first_agent_xml.replace(
            address_prtry,
            address_prtry + "                          \n",
            1,
        )
        first = first_agent_xml.find(address_prtry)
        second = first_agent_xml.find(address_prtry, first + len(address_prtry))
        if first < 0 or second < 0:
            raise AssertionError(
                "focused proprietary agent requires institution and branch AddressType3Choice"
            )
        branch_whitespace = (
            first_agent_xml[:second]
            + first_agent_xml[second:].replace(
                address_prtry,
                address_prtry + "                          \n",
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

    def test_address_type_choice_is_material_at_each_repeated_position(self) -> None:
        """Cd/Prtry and proprietary fields affect evidence at both agents and owners."""
        base_entry = self.base_statement.entries[0]
        base_detail = base_entry.entry_details[0]
        base_digest = getattr(base_detail, "proprietary_agents_evidence_hash", None)
        self._assert_digest(base_digest)
        self.assertEqual(base_digest, self._expected_hash(self.base))
        self._assert_exact_amount(base_entry, base_detail)

        for name, statement in self.variant_statements.items():
            with self.subTest(name=name):
                entry = statement.entries[0]
                detail = entry.entry_details[0]
                digest = getattr(detail, "proprietary_agents_evidence_hash", None)
                self._assert_digest(digest)
                self.assertEqual(digest, self._expected_hash(self.variants[name]))
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

    def test_proprietary_address_type_layout_is_representation_only(self) -> None:
        """Whitespace inside either Prtry branch changes bytes, not admitted semantics."""
        base_entry = self.base_statement.entries[0]
        base_detail = base_entry.entry_details[0]
        expected_digest = self._expected_hash(self.base)
        self.assertEqual(
            getattr(base_detail, "proprietary_agents_evidence_hash", None),
            expected_digest,
        )

        for name, statement in self.reformatted_statements.items():
            with self.subTest(name=name):
                entry = statement.entries[0]
                detail = entry.entry_details[0]
                self.assertNotEqual(
                    self.base_statement.source_artifact_hash,
                    statement.source_artifact_hash,
                )
                self.assertEqual(
                    getattr(detail, "proprietary_agents_evidence_hash", None),
                    expected_digest,
                )
                self.assertEqual(base_detail.source_detail_hash, detail.source_detail_hash)
                self.assertEqual(base_entry.source_entry_hash, entry.source_entry_hash)
                self.assertEqual(
                    self.base_statement.normalized_payload_hash,
                    statement.normalized_payload_hash,
                )
                self._assert_exact_amount(entry, detail)

    def test_changed_address_type_choice_requires_explicit_correction(self) -> None:
        """Accepted proprietary-agent address-type provenance cannot be silently replaced."""
        store = proprietary.MemoryArtifactStore()
        accepted = proprietary.accept_bank_statement_evidence(
            self._command(self.base_payload, self.bank_account_reference, "base"),
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
            artifact_store=store,
        )
        self.assertFalse(accepted["replayed"])

        changed = self.variant_payloads["first-institution-choice-code"]
        with self.assertRaisesRegex(proprietary.AccountingValidationError, _CORRECTION_ERROR):
            proprietary.accept_bank_statement_evidence(
                self._command(
                    changed,
                    self.bank_account_reference,
                    "changed-choice",
                ),
                posting.DATABASE_URL,
                self.case.policy.tenant_reference,
                artifact_store=store,
            )

    def test_buyer_read_preserves_complete_structured_address_type_provenance(self) -> None:
        """Buyer reconciliation reads retain the canonical proprietary-agent structures."""
        changed_name = "second-branch-choice-code"
        changed_payload = self.variant_payloads[changed_name]
        changed_statement = self.variant_statements[changed_name]
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
            self.variants[changed_name],
        )
        self.assertNotEqual(
            base_detail["proprietary_agents_evidence_hash"],
            changed_detail["proprietary_agents_evidence_hash"],
        )
        self.assertNotEqual(
            base_detail["source_detail_hash"],
            changed_detail["source_detail_hash"],
        )

    @staticmethod
    def _agent_value(
        agent_type: str,
        bicfi: str,
        name: str,
        branch_id: str,
        branch_name: str,
        institution_type_id: str,
        institution_type_issuer: str,
        institution_type_scheme: str,
        branch_type_id: str,
        branch_type_issuer: str,
        branch_type_scheme: str,
        country: str,
    ) -> dict[str, object]:
        return {
            "type": agent_type,
            "agent": {
                "bicfi": bicfi,
                "name": name,
                "postal_address": {
                    "address_type": {
                        "proprietary": {
                            "id": institution_type_id,
                            "issuer": institution_type_issuer,
                            "scheme_name": institution_type_scheme,
                        }
                    },
                    "country": country,
                },
                "branch": {
                    "id": branch_id,
                    "name": branch_name,
                    "postal_address": {
                        "address_type": {
                            "proprietary": {
                                "id": branch_type_id,
                                "issuer": branch_type_issuer,
                                "scheme_name": branch_type_scheme,
                            }
                        },
                        "country": country,
                    },
                },
            },
        }

    @classmethod
    def _variants(
        cls,
        base: list[dict[str, object]],
    ) -> dict[str, list[dict[str, object]]]:
        """Change every AddressType3Choice field at every repeated agent/owner position."""
        variants: dict[str, list[dict[str, object]]] = {}
        for index, position in ((0, "first"), (1, "second")):
            for owner in ("institution", "branch"):
                for field, replacement in (
                    ("id", f"ALT-{position}-{owner}-TYPE"),
                    ("issuer", f"Alternate {position} {owner} address registry"),
                    ("scheme_name", f"ALT_{position.upper()}_{owner.upper()}_ADDR_TYPE"),
                ):
                    variant = copy.deepcopy(base)
                    proprietary_type = cls._proprietary_type(variant, index, owner)
                    proprietary_type[field] = replacement
                    variants[f"{position}-{owner}-proprietary-{field}"] = variant

                without_scheme = copy.deepcopy(base)
                cls._proprietary_type(without_scheme, index, owner).pop(
                    "scheme_name", None
                )
                variants[f"{position}-{owner}-proprietary-scheme-absent"] = (
                    without_scheme
                )

                coded = copy.deepcopy(base)
                cls._address(coded, index, owner)["address_type"] = {"code": "BIZZ"}
                variants[f"{position}-{owner}-choice-code"] = coded
        return variants

    @classmethod
    def _proprietary_type(
        cls,
        value: list[dict[str, object]],
        index: int,
        owner: str,
    ) -> dict[str, object]:
        address_type = cls._address(value, index, owner).get("address_type")
        if not isinstance(address_type, dict):
            raise AssertionError("focused postal address requires AdrTp")
        proprietary_type = address_type.get("proprietary")
        if not isinstance(proprietary_type, dict):
            raise AssertionError("focused address type requires Prtry")
        return proprietary_type

    @staticmethod
    def _agent(value: list[dict[str, object]], index: int) -> dict[str, object]:
        agent = value[index].get("agent")
        if not isinstance(agent, dict):
            raise AssertionError("proprietary agent requires Agt")
        return agent

    @classmethod
    def _branch(
        cls,
        value: list[dict[str, object]],
        index: int,
    ) -> dict[str, object]:
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
        container = (
            cls._agent(value, index)
            if owner == "institution"
            else cls._branch(value, index)
        )
        address = container.get("postal_address")
        if not isinstance(address, dict):
            raise AssertionError("focused proprietary agent requires PostalAddress27")
        return address

    def _with_agents(self, agents: list[dict[str, object]]) -> bytes:
        items = self._agents_xml(agents)
        replacement = "            </RltdPties>\n" + items + "            <RmtInf>"
        changed = self.fixture.replace(self.marker, replacement, 1)
        if changed == self.fixture:
            raise AssertionError("proprietary address-type insertion must change XML")
        return changed.encode("utf-8")

    @classmethod
    def _agents_xml(cls, agents: list[dict[str, object]]) -> str:
        return (
            "            <RltdAgts>\n"
            + "".join(cls._agent_xml(agent) for agent in agents)
            + "            </RltdAgts>\n"
        )

    @classmethod
    def _agent_xml(cls, value: dict[str, object]) -> str:
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
        if not isinstance(institution_address, dict):
            raise AssertionError("focused proprietary agent requires institution PstlAdr")
        parts.append(cls._postal_xml(institution_address, "                    "))
        parts.append("                  </FinInstnId>\n")

        branch = agent.get("branch")
        if not isinstance(branch, dict):
            raise AssertionError("focused proprietary agent requires BrnchId")
        parts.append("                  <BrnchId>\n")
        if isinstance(branch.get("id"), str):
            parts.append(f"                    <Id>{branch['id']}</Id>\n")
        if isinstance(branch.get("name"), str):
            parts.append(f"                    <Nm>{branch['name']}</Nm>\n")
        branch_address = branch.get("postal_address")
        if not isinstance(branch_address, dict):
            raise AssertionError("focused proprietary agent requires branch PstlAdr")
        parts.append(cls._postal_xml(branch_address, "                    "))
        parts.extend(
            [
                "                  </BrnchId>\n",
                "                </Agt>\n",
                "              </Prtry>\n",
            ]
        )
        return "".join(parts)

    @staticmethod
    def _postal_xml(address: dict[str, object], indent: str) -> str:
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
            proprietary_type = address_type["proprietary"]
            if not isinstance(proprietary_type, dict):
                raise AssertionError("proprietary AdrTp must be structured")
            scheme_name = proprietary_type.get("scheme_name")
            scheme_xml = (
                f"{indent}      <SchmeNm>{scheme_name}</SchmeNm>\n"
                if scheme_name is not None
                else ""
            )
            address_type_xml = (
                f"{indent}  <AdrTp>\n"
                f"{indent}    <Prtry>\n"
                f"{indent}      <Id>{proprietary_type['id']}</Id>\n"
                f"{indent}      <Issr>{proprietary_type['issuer']}</Issr>\n"
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
                f"proprietary-agent-address-type-{suffix}-{uuid.uuid4().hex}"
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
    def _expected_hash(value: list[dict[str, object]]) -> str:
        return proprietary.BankStatementDetailProprietaryAgentIdentityEvidenceRedTests._expected_hash(
            value
        )

    @staticmethod
    def _assert_digest(value: object) -> None:
        if not isinstance(value, str) or _HASH_RE.fullmatch(value) is None:
            raise AssertionError(
                "proprietary-agent evidence digest must be sha256:<64 hex>"
            )

    @staticmethod
    def _assert_exact_amount(entry: object, detail: object) -> None:
        if getattr(entry, "entry_amount", None) != Decimal("25000.00"):
            raise AssertionError("address-type evidence must retain exact 25000.00")
        if getattr(entry, "entry_currency_code", None) != "KRW":
            raise AssertionError("address-type evidence must retain KRW")
        if getattr(detail, "detail_amount", None) != Decimal("25000.00"):
            raise AssertionError("address-type detail must retain exact 25000.00")
        if getattr(detail, "detail_currency_code", None) != "KRW":
            raise AssertionError("address-type detail must retain KRW")


if __name__ == "__main__":
    unittest.main()
