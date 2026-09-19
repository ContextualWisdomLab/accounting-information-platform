"""PostgreSQL REDs for deep identity inside repeated proprietary agents."""

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
_PROPRIETARY_AGENTS_PURPOSE = (
    "camt.053.001.14/Stmt/Ntry/NtryDtls/TxDtls/RltdAgts/Prtry"
)
_CORRECTION_ERROR = (
    r"^statement identity already exists with different entry evidence\. "
    r"Use an explicit correction contract, then retry ingest\.$"
)


class BankStatementDetailProprietaryAgentIdentityEvidenceRedTests(unittest.TestCase):
    """Retain institution and branch identity inside every ProprietaryAgent5."""

    @classmethod
    def setUpClass(cls) -> None:
        """Reuse the canonical real-PostgreSQL integration fixture."""
        posting.PostgresPostingTests.setUpClass()

    def setUp(self) -> None:
        """Prepare deep-identity materiality and representation controls."""
        self.case = posting.PostgresPostingTests("setUp")
        self.addCleanup(self.case.doCleanups)
        self.case.setUp()
        self.addCleanup(self.case.tearDown)

        fixture = load_canonical_statement_fixture().decode("utf-8")
        marker = "            </RltdPties>\n            <RmtInf>"
        self.assertEqual(fixture.count(marker), 1)

        self.base = [
            {
                "type": "BROKER_AGENT",
                "agent": {
                    "bicfi": "DEUTDEFF",
                    "clearing_system_member": {"member_id": "DE-CLEAR-001"},
                    "lei": "7LTWFZYICNSX8D621K86",
                    "name": "Execution Broker Frankfurt",
                    "branch": {
                        "id": "BROKER-FRA-001",
                        "lei": "529900Z6KVD8Y83D7K60",
                        "name": "Frankfurt Execution Branch",
                    },
                },
            },
            {
                "type": "CUSTODY_AGENT",
                "agent": {
                    "bicfi": "BNPAFRPP",
                    "name": "Custody Agent Paris",
                    "branch": {"id": "CUSTODY-PAR-001"},
                },
            },
        ]
        self.variants = self._variants(self.base)
        self.base_payload = self._with_proprietary_agents(fixture, marker, self.base)
        self.base_statement = parse_bank_statement_payload(
            self.base_payload, CAMT053_MESSAGE_DEFINITION
        )
        self.variant_payloads = {
            name: self._with_proprietary_agents(fixture, marker, value)
            for name, value in self.variants.items()
        }
        self.variant_statements = {
            name: parse_bank_statement_payload(payload, CAMT053_MESSAGE_DEFINITION)
            for name, payload in self.variant_payloads.items()
        }

        proprietary_xml = self._proprietary_agents_xml(self.base)
        self.assertEqual(self.base_payload.count(proprietary_xml.encode("utf-8")), 1)
        reformatted_xml = proprietary_xml.replace(
            "                    <ClrSysMmbId>\n",
            "                    <ClrSysMmbId>\n                      \n",
            1,
        )
        self.assertNotEqual(reformatted_xml, proprietary_xml)
        self.reformatted_payload = self.base_payload.replace(
            proprietary_xml.encode("utf-8"),
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

    def test_nested_institution_and_branch_identity_are_material_at_each_position(self) -> None:
        """Deep institution/branch fields must survive the proprietary-agent path."""
        base_hash = self._expected_hash(self.base)
        base_entry = self.base_statement.entries[0]
        base_detail = base_entry.entry_details[0]
        self.assertRegex(base_hash, _HASH_PATTERN)
        self.assertEqual(
            getattr(base_detail, "proprietary_agents_evidence_hash", None),
            base_hash,
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
                    getattr(detail, "proprietary_agents_evidence_hash", None),
                    expected_hash,
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

    def test_xml_layout_does_not_change_nested_agent_semantics(self) -> None:
        """Whitespace in nested identity changes raw bytes but not semantic evidence."""
        expected_hash = self._expected_hash(self.base)
        base_entry = self.base_statement.entries[0]
        base_detail = base_entry.entry_details[0]
        reformatted_entry = self.reformatted_statement.entries[0]
        reformatted_detail = reformatted_entry.entry_details[0]

        self.assertNotEqual(
            self.base_statement.source_artifact_hash,
            self.reformatted_statement.source_artifact_hash,
        )
        self.assertEqual(
            getattr(base_detail, "proprietary_agents_evidence_hash", None),
            expected_hash,
        )
        self.assertEqual(
            getattr(reformatted_detail, "proprietary_agents_evidence_hash", None),
            expected_hash,
        )
        self._assert_entry_hash_binding(base_entry, expected_hash)
        self._assert_entry_hash_binding(reformatted_entry, expected_hash)
        self._assert_exact_accounting_amount(reformatted_entry, reformatted_detail)
        self.assertEqual(base_detail.source_detail_hash, reformatted_detail.source_detail_hash)
        self.assertEqual(base_entry.source_entry_hash, reformatted_entry.source_entry_hash)
        self.assertEqual(
            self.base_statement.normalized_payload_hash,
            self.reformatted_statement.normalized_payload_hash,
        )

    def test_nested_agent_changes_require_explicit_correction(self) -> None:
        """Accepted nested agent provenance cannot be silently replaced on replay."""
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

    def test_buyer_read_preserves_deep_proprietary_agent_identity(self) -> None:
        """Tenant reads retain deep provenance while exact 25000 KRW stays authoritative."""
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

        self.assertEqual(detail.get("proprietary_agents_evidence_hash"), expected_hash)
        self.assertEqual(detail.get("proprietary_agents"), self.base)
        self.assertEqual(entry["entry_amount"], "25000")
        self.assertEqual(entry["entry_currency_code"], "KRW")
        self.assertEqual(detail["detail_amount"], "25000")
        self.assertEqual(detail["detail_currency_code"], "KRW")

    @staticmethod
    def _variants(
        base: list[dict[str, object]],
    ) -> dict[str, list[dict[str, object]]]:
        """Change one nested financial-institution or branch fact at a time."""
        variants: dict[str, list[dict[str, object]]] = {}

        def first_agent(value: list[dict[str, object]]) -> dict[str, object]:
            agent = value[0].get("agent")
            if not isinstance(agent, dict):
                raise AssertionError("first proprietary agent requires Agt")
            return agent

        clearing_member = copy.deepcopy(base)
        clearing = first_agent(clearing_member).get("clearing_system_member")
        if not isinstance(clearing, dict):
            raise AssertionError("first proprietary agent requires clearing member")
        clearing["member_id"] = "DE-CLEAR-002"
        variants["first-clearing-member-id"] = clearing_member

        institution_lei = copy.deepcopy(base)
        first_agent(institution_lei)["lei"] = "529900Z6KVD8Y83D7K60"
        variants["first-institution-lei"] = institution_lei

        institution_name = copy.deepcopy(base)
        first_agent(institution_name)["name"] = "Execution Broker Frankfurt Updated"
        variants["first-institution-name"] = institution_name

        branch_id = copy.deepcopy(base)
        branch = first_agent(branch_id).get("branch")
        if not isinstance(branch, dict):
            raise AssertionError("first proprietary agent requires branch")
        branch["id"] = "BROKER-FRA-002"
        variants["first-branch-id"] = branch_id

        branch_lei = copy.deepcopy(base)
        branch = first_agent(branch_lei).get("branch")
        if not isinstance(branch, dict):
            raise AssertionError("first proprietary agent requires branch")
        branch["lei"] = "7LTWFZYICNSX8D621K86"
        variants["first-branch-lei"] = branch_lei

        branch_name = copy.deepcopy(base)
        branch = first_agent(branch_name).get("branch")
        if not isinstance(branch, dict):
            raise AssertionError("first proprietary agent requires branch")
        branch["name"] = "Frankfurt Execution Branch Updated"
        variants["first-branch-name"] = branch_name

        second_name = copy.deepcopy(base)
        second_agent = second_name[1].get("agent")
        if not isinstance(second_agent, dict):
            raise AssertionError("second proprietary agent requires Agt")
        second_agent["name"] = "Custody Agent Paris Updated"
        variants["second-institution-name"] = second_name

        second_branch_id = copy.deepcopy(base)
        second_agent = second_branch_id[1].get("agent")
        if not isinstance(second_agent, dict):
            raise AssertionError("second proprietary agent requires Agt")
        second_branch = second_agent.get("branch")
        if not isinstance(second_branch, dict):
            raise AssertionError("second proprietary agent requires branch")
        second_branch["id"] = "CUSTODY-PAR-002"
        variants["second-branch-id"] = second_branch_id
        return variants

    @staticmethod
    def _assert_exact_accounting_amount(entry: object, detail: object) -> None:
        """Pin journal-facing amount independently from proprietary-agent provenance."""
        if getattr(entry, "entry_amount", None) != Decimal("25000.00"):
            raise AssertionError("nested proprietary-agent evidence must retain 25000.00")
        if getattr(entry, "entry_currency_code", None) != "KRW":
            raise AssertionError("nested proprietary-agent evidence must retain KRW")
        if getattr(detail, "detail_amount", None) != Decimal("25000.00"):
            raise AssertionError("nested proprietary-agent detail must retain 25000.00")
        if getattr(detail, "detail_currency_code", None) != "KRW":
            raise AssertionError("nested proprietary-agent detail must retain KRW")

    @staticmethod
    def _assert_entry_hash_binding(entry: object, expected_hash: str) -> None:
        """Require source_entry_hash to digest the deep proprietary-agent evidence hash."""
        projection = dict(bank_statement._entry_payload(entry))
        details = projection.get("details")
        if not isinstance(details, list) or not details:
            raise AssertionError("canonical entry projection must retain transaction details")
        first_detail = details[0]
        if not isinstance(first_detail, dict):
            raise AssertionError("canonical entry detail projection must be a mapping")
        if first_detail.get("proprietary_agents_evidence_hash") != expected_hash:
            raise AssertionError(
                "canonical entry projection must carry exact proprietary_agents_evidence_hash"
            )
        preimage = json.dumps(
            projection, separators=(",", ":"), sort_keys=True
        ).encode("utf-8")
        expected_entry_hash = f"sha256:{hashlib.sha256(preimage).hexdigest()}"
        if getattr(entry, "source_entry_hash", None) != expected_entry_hash:
            raise AssertionError(
                "source_entry_hash must digest canonical deep proprietary-agent evidence"
            )

    @staticmethod
    def _expected_hash(value: list[dict[str, object]]) -> str:
        """Digest the complete source-ordered proprietary-agent projection."""
        preimage = json.dumps(
            {
                "evidence_type": _PROPRIETARY_AGENTS_PURPOSE,
                "proprietary_agents": value,
            },
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return f"sha256:{hashlib.sha256(preimage).hexdigest()}"

    @classmethod
    def _proprietary_agents_xml(cls, value: list[dict[str, object]]) -> str:
        """Serialize one TransactionAgents6 source-ordered Prtry population."""
        items = "".join(cls._proprietary_agent_xml(item) for item in value)
        return "            <RltdAgts>\n" + items + "            </RltdAgts>\n"

    @staticmethod
    def _proprietary_agent_xml(value: dict[str, object]) -> str:
        """Serialize ProprietaryAgent5 with deep BranchAndFinancialInstitutionIdentification8."""
        agent_type = value.get("type")
        agent = value.get("agent")
        if not isinstance(agent_type, str) or not agent_type:
            raise AssertionError("ProprietaryAgent5 requires Tp")
        if not isinstance(agent, dict):
            raise AssertionError("ProprietaryAgent5 requires Agt")
        bicfi = agent.get("bicfi")
        if not isinstance(bicfi, str) or not bicfi:
            raise AssertionError("focused proprietary agent requires BICFI")

        fin_lines = [f"                    <BICFI>{bicfi}</BICFI>\n"]
        clearing = agent.get("clearing_system_member")
        if clearing is not None:
            if not isinstance(clearing, dict) or not isinstance(clearing.get("member_id"), str):
                raise AssertionError("clearing_system_member requires member_id")
            fin_lines.extend(
                [
                    "                    <ClrSysMmbId>\n",
                    f"                      <MmbId>{clearing['member_id']}</MmbId>\n",
                    "                    </ClrSysMmbId>\n",
                ]
            )
        if isinstance(agent.get("lei"), str):
            fin_lines.append(f"                    <LEI>{agent['lei']}</LEI>\n")
        if isinstance(agent.get("name"), str):
            fin_lines.append(f"                    <Nm>{agent['name']}</Nm>\n")

        branch_xml = ""
        branch = agent.get("branch")
        if branch is not None:
            if not isinstance(branch, dict):
                raise AssertionError("branch must be a mapping")
            branch_lines = ["                  <BrnchId>\n"]
            if isinstance(branch.get("id"), str):
                branch_lines.append(f"                    <Id>{branch['id']}</Id>\n")
            if isinstance(branch.get("lei"), str):
                branch_lines.append(f"                    <LEI>{branch['lei']}</LEI>\n")
            if isinstance(branch.get("name"), str):
                branch_lines.append(f"                    <Nm>{branch['name']}</Nm>\n")
            branch_lines.append("                  </BrnchId>\n")
            branch_xml = "".join(branch_lines)

        return (
            "              <Prtry>\n"
            f"                <Tp>{agent_type}</Tp>\n"
            "                <Agt>\n"
            "                  <FinInstnId>\n"
            + "".join(fin_lines)
            + "                  </FinInstnId>\n"
            + branch_xml
            + "                </Agt>\n"
            "              </Prtry>\n"
        )

    @classmethod
    def _with_proprietary_agents(
        cls,
        fixture: str,
        marker: str,
        value: list[dict[str, object]],
    ) -> bytes:
        """Insert proprietary agents after related parties and before remittance."""
        replacement = (
            "            </RltdPties>\n"
            + cls._proprietary_agents_xml(value)
            + "            <RmtInf>"
        )
        changed = fixture.replace(marker, replacement, 1)
        if changed == fixture:
            raise AssertionError("deep proprietary-agent fixture insertion must change XML")
        return changed.encode("utf-8")

    def _command(self, payload: bytes, suffix: str) -> dict[str, object]:
        """Build an isolated supported ingest command for the registered bank account."""
        return {
            "tenant_reference": self.case.policy.tenant_reference,
            "bank_account_reference": self.bank_account_reference,
            "ingestion_idempotency_key": (
                f"proprietary-agent-identity-{suffix}-{uuid.uuid4().hex}"
            ),
            "message_definition_identifier": CAMT053_MESSAGE_DEFINITION,
            "statement_payload": payload.decode("utf-8"),
        }


if __name__ == "__main__":
    unittest.main()
