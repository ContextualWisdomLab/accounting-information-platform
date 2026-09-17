"""PostgreSQL REDs for TransactionAgents6 repeated proprietary-agent evidence."""

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


class BankStatementDetailProprietaryAgentsEvidenceRedTests(unittest.TestCase):
    """Retain repeated ProprietaryAgent5 evidence without promoting accounting authority."""

    @classmethod
    def setUpClass(cls) -> None:
        """Reuse the canonical real-PostgreSQL integration fixture."""
        posting.PostgresPostingTests.setUpClass()

    def setUp(self) -> None:
        """Prepare value, population, order, presence, and representation controls."""
        self.case = posting.PostgresPostingTests("setUp")
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.addCleanup(self.case.tearDown)

        fixture = load_canonical_statement_fixture().decode("utf-8")
        marker = "            </RltdPties>\n            <RmtInf>"
        self.assertEqual(fixture.count(marker), 1)

        self.base = [
            {
                "type": "BROKER_AGENT",
                "agent": {"bicfi": "DEUTDEFF"},
            },
            {
                "type": "CUSTODY_AGENT",
                "agent": {"bicfi": "BNPAFRPP"},
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
        self.absent_payload = self._with_proprietary_agents(fixture, marker, [])
        self.absent_statement = parse_bank_statement_payload(
            self.absent_payload, CAMT053_MESSAGE_DEFINITION
        )

        proprietary_xml = self._proprietary_agents_xml(self.base)
        self.assertEqual(self.base_payload.count(proprietary_xml.encode("utf-8")), 1)
        reformatted_xml = proprietary_xml.replace(
            "              <Prtry>\n",
            "              <Prtry>\n                \n",
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

    def test_proprietary_agent_value_population_and_order_are_material(self) -> None:
        """Required type, agent identity, repeated population, and order stay material."""
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

    def test_whole_proprietary_agent_population_absence_is_material(self) -> None:
        """Removing all optional Prtry agents removes evidence and changes source identity."""
        base_entry = self.base_statement.entries[0]
        base_detail = base_entry.entry_details[0]
        absent_entry = self.absent_statement.entries[0]
        absent_detail = absent_entry.entry_details[0]

        self.assertIsNone(
            getattr(absent_detail, "proprietary_agents_evidence_hash", None)
        )
        self._assert_exact_accounting_amount(absent_entry, absent_detail)
        self.assertEqual(
            self.base_statement.account_identifier_hash,
            self.absent_statement.account_identifier_hash,
        )
        self.assertNotEqual(base_detail.source_detail_hash, absent_detail.source_detail_hash)
        self.assertNotEqual(base_entry.source_entry_hash, absent_entry.source_entry_hash)
        self.assertNotEqual(
            self.base_statement.normalized_payload_hash,
            self.absent_statement.normalized_payload_hash,
        )
        self.assertEqual(
            self.base_statement.entries[1].source_entry_hash,
            self.absent_statement.entries[1].source_entry_hash,
        )

    def test_xml_layout_does_not_change_proprietary_agent_semantics(self) -> None:
        """Whitespace inside repeated Prtry changes raw bytes, not admitted semantics."""
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
            getattr(base_detail, "proprietary_agents_evidence_hash", None),
            expected,
        )
        self.assertEqual(
            getattr(reformatted_detail, "proprietary_agents_evidence_hash", None),
            expected,
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

    def test_changed_proprietary_agents_require_explicit_correction(self) -> None:
        """Accepted proprietary-agent provenance cannot be replaced by silent replay."""
        accepted = accept_bank_statement_evidence(
            self._command(self.base_payload, "base"),
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
            artifact_store=self.store,
        )
        self.assertFalse(accepted["replayed"])

        payloads = dict(self.variant_payloads)
        payloads["population-absent"] = self.absent_payload
        for name, payload in payloads.items():
            with self.subTest(name=name):
                with self.assertRaisesRegex(AccountingValidationError, _CORRECTION_ERROR):
                    accept_bank_statement_evidence(
                        self._command(payload, name),
                        posting.DATABASE_URL,
                        self.case.policy.tenant_reference,
                        artifact_store=self.store,
                    )

    def test_buyer_read_preserves_proprietary_agents_without_changing_amount(self) -> None:
        """Tenant reads expose proprietary-agent provenance while 25000 KRW stays authoritative."""
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

        self.assertEqual(
            detail.get("proprietary_agents_evidence_hash"),
            expected_hash,
        )
        self.assertEqual(detail.get("proprietary_agents"), self.base)
        self.assertEqual(entry["entry_amount"], "25000")
        self.assertEqual(entry["entry_currency_code"], "KRW")
        self.assertEqual(detail["detail_amount"], "25000")
        self.assertEqual(detail["detail_currency_code"], "KRW")

    @staticmethod
    def _variants(
        base: list[dict[str, object]],
    ) -> dict[str, list[dict[str, object]]]:
        """Change required type, agent identity, repeated population, and order independently."""
        variants: dict[str, list[dict[str, object]]] = {}

        first_type = copy.deepcopy(base)
        first_type[0]["type"] = "EXECUTION_AGENT"
        variants["first-type"] = first_type

        first_bicfi = copy.deepcopy(base)
        first_agent = first_bicfi[0].get("agent")
        if not isinstance(first_agent, dict):
            raise AssertionError("first proprietary agent requires Agt")
        first_agent["bicfi"] = "COBADEFF"
        variants["first-agent-bicfi"] = first_bicfi

        second_bicfi = copy.deepcopy(base)
        second_agent = second_bicfi[1].get("agent")
        if not isinstance(second_agent, dict):
            raise AssertionError("second proprietary agent requires Agt")
        second_agent["bicfi"] = "SOGEFRPP"
        variants["second-agent-bicfi"] = second_bicfi

        second_removed = copy.deepcopy(base)
        second_removed.pop()
        variants["second-item-removed"] = second_removed

        reversed_order = copy.deepcopy(base)
        reversed_order.reverse()
        variants["source-order"] = reversed_order
        return variants

    @staticmethod
    def _assert_exact_accounting_amount(entry: object, detail: object) -> None:
        """Pin accounting truth independently from proprietary-agent provenance."""
        if getattr(entry, "entry_amount", None) != Decimal("25000.00"):
            raise AssertionError(
                "proprietary-agent evidence must retain exact 25000.00 entry amount"
            )
        if getattr(entry, "entry_currency_code", None) != "KRW":
            raise AssertionError("proprietary-agent evidence must retain KRW entry currency")
        if getattr(detail, "detail_amount", None) != Decimal("25000.00"):
            raise AssertionError(
                "proprietary-agent evidence must retain exact 25000.00 detail amount"
            )
        if getattr(detail, "detail_currency_code", None) != "KRW":
            raise AssertionError("proprietary-agent evidence must retain KRW detail currency")

    @staticmethod
    def _assert_entry_hash_binding(entry: object, expected_hash: str) -> None:
        """Require source_entry_hash to digest the proprietary-agents evidence hash."""
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
                "source_entry_hash must digest the canonical entry projection containing "
                "proprietary_agents_evidence_hash"
            )

    @staticmethod
    def _expected_hash(value: list[dict[str, object]]) -> str:
        """Digest the complete source-ordered ProprietaryAgent5 population."""
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
        """Serialize one optional TransactionAgents6 carrying source-ordered Prtry items."""
        if not value:
            return ""
        items = "".join(cls._proprietary_agent_xml(item) for item in value)
        return "            <RltdAgts>\n" + items + "            </RltdAgts>\n"

    @staticmethod
    def _proprietary_agent_xml(value: dict[str, object]) -> str:
        """Serialize one valid ProprietaryAgent5 with required Tp then required Agt."""
        agent_type = value.get("type")
        agent = value.get("agent")
        if not isinstance(agent_type, str) or not agent_type:
            raise AssertionError("ProprietaryAgent5 requires Tp")
        if not isinstance(agent, dict):
            raise AssertionError("ProprietaryAgent5 requires Agt")
        bicfi = agent.get("bicfi")
        if not isinstance(bicfi, str) or not bicfi:
            raise AssertionError("test proprietary agent requires a non-empty BICFI")

        return (
            "              <Prtry>\n"
            f"                <Tp>{agent_type}</Tp>\n"
            "                <Agt>\n"
            "                  <FinInstnId>\n"
            f"                    <BICFI>{bicfi}</BICFI>\n"
            "                  </FinInstnId>\n"
            "                </Agt>\n"
            "              </Prtry>\n"
        )

    @classmethod
    def _with_proprietary_agents(
        cls,
        fixture: str,
        marker: str,
        value: list[dict[str, object]],
    ) -> bytes:
        """Insert related-agent provenance after related parties and before remittance."""
        replacement = (
            "            </RltdPties>\n"
            + cls._proprietary_agents_xml(value)
            + "            <RmtInf>"
        )
        changed = fixture.replace(marker, replacement, 1)
        if value and changed == fixture:
            raise AssertionError("proprietary-agent fixture insertion must change XML")
        return changed.encode("utf-8")

    def _command(self, payload: bytes, suffix: str) -> dict[str, object]:
        """Build one isolated supported ingest command for the current bank account."""
        return {
            "tenant_reference": self.case.policy.tenant_reference,
            "bank_account_reference": self.bank_account_reference,
            "ingestion_idempotency_key": (
                f"proprietary-agents-{suffix}-{uuid.uuid4().hex}"
            ),
            "message_definition_identifier": CAMT053_MESSAGE_DEFINITION,
            "statement_payload": payload.decode("utf-8"),
        }


if __name__ == "__main__":
    unittest.main()
