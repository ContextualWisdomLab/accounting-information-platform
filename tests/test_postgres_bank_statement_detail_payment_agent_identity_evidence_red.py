"""PostgreSQL REDs for deep identity inside standard transaction-agent roles."""

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


class BankStatementDetailPaymentAgentIdentityEvidenceRedTests(unittest.TestCase):
    """Retain non-BICFI identity facts inside one standard TransactionAgents6 role."""

    @classmethod
    def setUpClass(cls) -> None:
        """Reuse the canonical real-PostgreSQL integration fixture."""
        posting.PostgresPostingTests.setUpClass()

    def setUp(self) -> None:
        """Prepare instructing-agent deep-identity materiality controls."""
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
            "clearing_system_member": {"member_id": "DE-CLEAR-001"},
            "lei": "7LTWFZYICNSX8D621K86",
            "name": "Instructing Bank Frankfurt",
            "branch": {
                "id": "INSTG-FRA-001",
                "lei": "529900Z6KVD8Y83D7K60",
                "name": "Frankfurt Payments Branch",
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
            "                  <ClrSysMmbId>\n",
            "                  <ClrSysMmbId>\n                    \n",
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

    def test_non_bicfi_instructing_agent_identity_is_material(self) -> None:
        """Every admitted institution/branch fact must affect canonical evidence identity."""
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
                    "BICFI is intentionally unchanged; the existing purpose digest stays stable",
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

    def test_xml_layout_does_not_change_instructing_agent_semantics(self) -> None:
        """Whitespace in deep agent identity changes raw bytes but not admitted semantics."""
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

    def test_deep_instructing_agent_change_requires_explicit_correction(self) -> None:
        """Accepted non-BICFI provenance cannot be silently replaced on replay."""
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

    def test_buyer_read_keeps_role_digest_and_changed_source_identity(self) -> None:
        """Buyer read remains purpose-digest-only while deep provenance stays material."""
        base_detail = self._ingest_and_read(self.base_payload, "lookup-base")
        self.assertIsInstance(base_detail.get("instructing_agent_evidence_hash"), str)
        self.assertRegex(
            str(base_detail["instructing_agent_evidence_hash"]),
            r"\Asha256:[0-9a-f]{64}\Z",
        )
        self.assertNotIn("instructing_agent", base_detail)

        variant_name = "institution-name"
        variant_statement = self.variant_statements[variant_name]
        variant_reference = f"urn:cwl:bank_account:{uuid.uuid4().hex}"
        accept_bank_account_record(
            {
                "tenant_reference": self.case.policy.tenant_reference,
                "bank_account_reference": variant_reference,
                "account_currency_code": variant_statement.account_currency_code,
                "account_identifier_hash": variant_statement.account_identifier_hash,
            },
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
        )
        variant_detail = self._ingest_and_read(
            self.variant_payloads[variant_name],
            "lookup-variant",
            bank_account_reference=variant_reference,
        )
        self.assertEqual(
            base_detail["instructing_agent_evidence_hash"],
            variant_detail["instructing_agent_evidence_hash"],
        )
        self.assertNotEqual(
            base_detail["source_detail_hash"],
            variant_detail["source_detail_hash"],
        )
        self.assertEqual(base_detail["detail_amount"], "6000")
        self.assertEqual(base_detail["detail_currency_code"], "KRW")
        self.assertEqual(variant_detail["detail_amount"], "6000")
        self.assertEqual(variant_detail["detail_currency_code"], "KRW")

    @staticmethod
    def _variants(base: dict[str, object]) -> dict[str, dict[str, object]]:
        """Change one non-BICFI BranchAndFinancialInstitutionIdentification8 fact at a time."""
        variants: dict[str, dict[str, object]] = {}

        clearing_member = copy.deepcopy(base)
        clearing = clearing_member.get("clearing_system_member")
        if not isinstance(clearing, dict):
            raise AssertionError("base instructing agent requires clearing member")
        clearing["member_id"] = "DE-CLEAR-002"
        variants["clearing-member-id"] = clearing_member

        institution_lei = copy.deepcopy(base)
        institution_lei["lei"] = "529900Z6KVD8Y83D7K60"
        variants["institution-lei"] = institution_lei

        institution_name = copy.deepcopy(base)
        institution_name["name"] = "Instructing Bank Frankfurt Updated"
        variants["institution-name"] = institution_name

        branch_id = copy.deepcopy(base)
        branch = branch_id.get("branch")
        if not isinstance(branch, dict):
            raise AssertionError("base instructing agent requires branch")
        branch["id"] = "INSTG-FRA-002"
        variants["branch-id"] = branch_id

        branch_lei = copy.deepcopy(base)
        branch = branch_lei.get("branch")
        if not isinstance(branch, dict):
            raise AssertionError("base instructing agent requires branch")
        branch["lei"] = "7LTWFZYICNSX8D621K86"
        variants["branch-lei"] = branch_lei

        branch_name = copy.deepcopy(base)
        branch = branch_name.get("branch")
        if not isinstance(branch, dict):
            raise AssertionError("base instructing agent requires branch")
        branch["name"] = "Frankfurt Payments Branch Updated"
        variants["branch-name"] = branch_name
        return variants

    def _with_instructing_agent(self, value: dict[str, object]) -> bytes:
        """Insert one deep InstgAgt into the outgoing-payment transaction detail."""
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
    def _agent_xml(value: dict[str, object]) -> str:
        """Serialize InstgAgt with deep BranchAndFinancialInstitutionIdentification8."""
        bicfi = value.get("bicfi")
        if not isinstance(bicfi, str) or not bicfi:
            raise AssertionError("focused instructing agent requires BICFI")
        lines = [
            "              <InstgAgt>\n",
            "                <FinInstnId>\n",
            f"                  <BICFI>{bicfi}</BICFI>\n",
        ]
        clearing = value.get("clearing_system_member")
        if clearing is not None:
            if not isinstance(clearing, dict) or not isinstance(clearing.get("member_id"), str):
                raise AssertionError("clearing_system_member requires member_id")
            lines.extend(
                [
                    "                  <ClrSysMmbId>\n",
                    f"                    <MmbId>{clearing['member_id']}</MmbId>\n",
                    "                  </ClrSysMmbId>\n",
                ]
            )
        if isinstance(value.get("lei"), str):
            lines.append(f"                  <LEI>{value['lei']}</LEI>\n")
        if isinstance(value.get("name"), str):
            lines.append(f"                  <Nm>{value['name']}</Nm>\n")
        lines.append("                </FinInstnId>\n")

        branch = value.get("branch")
        if branch is not None:
            if not isinstance(branch, dict):
                raise AssertionError("branch must be a mapping")
            lines.append("                <BrnchId>\n")
            if isinstance(branch.get("id"), str):
                lines.append(f"                  <Id>{branch['id']}</Id>\n")
            if isinstance(branch.get("lei"), str):
                lines.append(f"                  <LEI>{branch['lei']}</LEI>\n")
            if isinstance(branch.get("name"), str):
                lines.append(f"                  <Nm>{branch['name']}</Nm>\n")
            lines.append("                </BrnchId>\n")
        lines.append("              </InstgAgt>\n")
        return "".join(lines)

    @staticmethod
    def _assert_exact_accounting_amount(entry: object, detail: object) -> None:
        """Keep accounting amounts independent from transaction-agent provenance."""
        if getattr(entry, "entry_amount", None) != Decimal("6000.00"):
            raise AssertionError("instructing-agent evidence must retain 6000.00")
        if getattr(entry, "entry_currency_code", None) != "KRW":
            raise AssertionError("instructing-agent evidence must retain KRW")
        if getattr(detail, "detail_amount", None) != Decimal("6000.00"):
            raise AssertionError("instructing-agent detail must retain 6000.00")
        if getattr(detail, "detail_currency_code", None) != "KRW":
            raise AssertionError("instructing-agent detail must retain KRW")

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
            "ingestion_idempotency_key": f"payment-agent-identity-{suffix}-{uuid.uuid4().hex}",
            "message_definition_identifier": CAMT053_MESSAGE_DEFINITION,
            "statement_payload": payload.decode("utf-8"),
        }


if __name__ == "__main__":
    unittest.main()
