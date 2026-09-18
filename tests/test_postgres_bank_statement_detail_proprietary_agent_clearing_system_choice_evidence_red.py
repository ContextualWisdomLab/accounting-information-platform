"""PostgreSQL REDs for clearing-system choice evidence inside proprietary agents."""

from __future__ import annotations

import copy
import re
import uuid
import unittest
from decimal import Decimal

from tests import test_postgres_bank_statement_detail_proprietary_agent_identity_evidence_red as proprietary
from tests import test_postgres_posting as posting

_HASH_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_CORRECTION_ERROR = (
    r"^statement identity already exists with different entry evidence\. "
    r"Use an explicit correction contract, then retry ingest\.$"
)


class BankStatementDetailProprietaryAgentClearingSystemChoiceEvidenceRedTests(
    unittest.TestCase
):
    """Retain ClrSysId choice provenance inside repeated ProprietaryAgent5 values."""

    @classmethod
    def setUpClass(cls) -> None:
        """Reuse the canonical real-PostgreSQL integration fixture."""
        posting.PostgresPostingTests.setUpClass()

    def setUp(self) -> None:
        """Prepare source-real proprietary-agent variants with safe nested cleanup."""
        self.case = posting.PostgresPostingTests("setUp")
        self.addCleanup(self.case.doCleanups)
        self.case.setUp()
        self.addCleanup(self.case.tearDown)

        fixture = proprietary.load_canonical_statement_fixture().decode("utf-8")
        marker = "            </RltdPties>\n            <RmtInf>"
        self.assertEqual(fixture.count(marker), 1)

        self.base = [
            {
                "type": "BROKER_AGENT",
                "agent": {
                    "bicfi": "DEUTDEFF",
                    "clearing_system_member": {
                        "system": {"proprietary": "USABA"},
                        "member_id": "DE-CLEAR-001",
                    },
                    "name": "Execution Broker Frankfurt",
                },
            },
            {
                "type": "CUSTODY_AGENT",
                "agent": {
                    "bicfi": "BNPAFRPP",
                    "name": "Custody Agent Paris",
                },
            },
        ]
        self.base_payload = self._with_agents(fixture, marker, self.base)
        self.base_statement = self._statement(self.base_payload)
        self.variants = self._variants(self.base)
        self.variant_payloads = {
            name: self._with_agents(fixture, marker, value)
            for name, value in self.variants.items()
        }
        self.variant_statements = {
            name: self._statement(payload)
            for name, payload in self.variant_payloads.items()
        }

        needle = b"                    <ClrSysId>\n"
        self.assertEqual(self.base_payload.count(needle), 1)
        self.reformatted_payload = self.base_payload.replace(
            needle,
            needle + b"                      \n",
            1,
        )
        self.assertNotEqual(self.base_payload, self.reformatted_payload)
        self.reformatted_statement = self._statement(self.reformatted_payload)

        self.bank_account_reference = self._register_bank_account(self.base_statement)

    def test_choice_value_discriminator_and_absence_are_material(self) -> None:
        """ClrSysId value, Cd|Prtry discriminator and absence affect evidence identity."""
        base_entry = self.base_statement.entries[0]
        base_detail = base_entry.entry_details[0]
        base_digest = getattr(base_detail, "proprietary_agents_evidence_hash", None)
        self._assert_digest(base_digest)
        self._assert_exact_amount(base_entry, base_detail)

        for name, statement in self.variant_statements.items():
            with self.subTest(name=name):
                entry = statement.entries[0]
                detail = entry.entry_details[0]
                digest = getattr(detail, "proprietary_agents_evidence_hash", None)
                self._assert_digest(digest)
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

    def test_clearing_system_layout_is_representation_only(self) -> None:
        """Whitespace inside ClrSysId changes raw bytes but not admitted semantics."""
        base_entry = self.base_statement.entries[0]
        base_detail = base_entry.entry_details[0]
        changed_entry = self.reformatted_statement.entries[0]
        changed_detail = changed_entry.entry_details[0]
        base_digest = getattr(base_detail, "proprietary_agents_evidence_hash", None)
        changed_digest = getattr(
            changed_detail,
            "proprietary_agents_evidence_hash",
            None,
        )
        self._assert_digest(base_digest)
        self._assert_digest(changed_digest)

        self.assertNotEqual(
            self.base_statement.source_artifact_hash,
            self.reformatted_statement.source_artifact_hash,
        )
        self.assertEqual(base_digest, changed_digest)
        self.assertEqual(base_detail.source_detail_hash, changed_detail.source_detail_hash)
        self.assertEqual(base_entry.source_entry_hash, changed_entry.source_entry_hash)
        self.assertEqual(
            self.base_statement.normalized_payload_hash,
            self.reformatted_statement.normalized_payload_hash,
        )
        self._assert_exact_amount(changed_entry, changed_detail)

    def test_choice_change_requires_explicit_correction(self) -> None:
        """Accepted proprietary-agent clearing provenance cannot be silently replaced."""
        store = proprietary.MemoryArtifactStore()
        accepted = proprietary.accept_bank_statement_evidence(
            self._command(self.base_payload, self.bank_account_reference, "base"),
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
            artifact_store=store,
        )
        self.assertFalse(accepted["replayed"])

        changed_payload = self.variant_payloads["same-scalar-choice-discriminator"]
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

    def test_buyer_read_retains_choice_provenance_without_accounting_authority(self) -> None:
        """Buyer evidence distinguishes choice provenance while 25000 KRW stays exact."""
        coded_payload = self.variant_payloads["same-scalar-choice-discriminator"]
        coded_statement = self.variant_statements["same-scalar-choice-discriminator"]
        coded_reference = self._register_bank_account(coded_statement)

        base_detail = self._ingest_and_read(
            self.base_payload,
            self.bank_account_reference,
            "buyer-proprietary",
        )
        coded_detail = self._ingest_and_read(
            coded_payload,
            coded_reference,
            "buyer-coded",
        )

        for detail in (base_detail, coded_detail):
            self._assert_digest(detail.get("proprietary_agents_evidence_hash"))
            self._assert_digest(detail.get("source_detail_hash"))
            self.assertEqual(detail["detail_amount"], "25000")
            self.assertEqual(detail["detail_currency_code"], "KRW")
            self.assertIsInstance(detail.get("proprietary_agents"), list)

        self.assertNotEqual(
            base_detail["proprietary_agents_evidence_hash"],
            coded_detail["proprietary_agents_evidence_hash"],
        )
        self.assertNotEqual(
            base_detail["source_detail_hash"],
            coded_detail["source_detail_hash"],
        )
        self.assertNotEqual(
            base_detail["proprietary_agents"],
            coded_detail["proprietary_agents"],
        )

    @staticmethod
    def _variants(
        base: list[dict[str, object]],
    ) -> dict[str, list[dict[str, object]]]:
        """Change only the first proprietary agent's clearing-system choice."""
        variants: dict[str, list[dict[str, object]]] = {}

        def clearing(value: list[dict[str, object]]) -> dict[str, object]:
            agent = value[0].get("agent")
            if not isinstance(agent, dict):
                raise AssertionError("first proprietary agent requires Agt")
            member = agent.get("clearing_system_member")
            if not isinstance(member, dict):
                raise AssertionError("first proprietary agent requires ClrSysMmbId")
            return member

        changed_value = copy.deepcopy(base)
        clearing(changed_value)["system"] = {"proprietary": "ALTCLR"}
        variants["choice-value"] = changed_value

        changed_kind = copy.deepcopy(base)
        clearing(changed_kind)["system"] = {"code": "USABA"}
        variants["same-scalar-choice-discriminator"] = changed_kind

        absent = copy.deepcopy(base)
        clearing(absent).pop("system")
        variants["choice-absent"] = absent
        return variants

    @classmethod
    def _with_agents(
        cls,
        fixture: str,
        marker: str,
        agents: list[dict[str, object]],
    ) -> bytes:
        """Insert proprietary agents after related parties and before remittance."""
        items = "".join(cls._agent_xml(value) for value in agents)
        related_agents = "            <RltdAgts>\n" + items + "            </RltdAgts>\n"
        replacement = "            </RltdPties>\n" + related_agents + "            <RmtInf>"
        changed = fixture.replace(marker, replacement, 1)
        if changed == fixture:
            raise AssertionError("proprietary clearing-system insertion must change XML")
        return changed.encode("utf-8")

    @staticmethod
    def _agent_xml(value: dict[str, object]) -> str:
        """Serialize ProprietaryAgent5 and ClearingSystemMemberIdentification2 in V14 order."""
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
        member = agent.get("clearing_system_member")
        if member is not None:
            if not isinstance(member, dict):
                raise AssertionError("clearing_system_member must be a mapping")
            member_id = member.get("member_id")
            if not isinstance(member_id, str) or not member_id:
                raise AssertionError("ClrSysMmbId requires MmbId")
            fin_lines.append("                    <ClrSysMmbId>\n")
            system = member.get("system")
            if system is not None:
                if not isinstance(system, dict) or len(system) != 1:
                    raise AssertionError("ClrSysId requires exactly one code|proprietary choice")
                kind, scalar = next(iter(system.items()))
                if kind not in {"code", "proprietary"} or not isinstance(scalar, str) or not scalar:
                    raise AssertionError("ClrSysId requires a non-empty code|proprietary value")
                tag = "Cd" if kind == "code" else "Prtry"
                fin_lines.extend(
                    [
                        "                      <ClrSysId>\n",
                        f"                        <{tag}>{scalar}</{tag}>\n",
                        "                      </ClrSysId>\n",
                    ]
                )
            fin_lines.extend(
                [
                    f"                      <MmbId>{member_id}</MmbId>\n",
                    "                    </ClrSysMmbId>\n",
                ]
            )
        if isinstance(agent.get("name"), str):
            fin_lines.append(f"                    <Nm>{agent['name']}</Nm>\n")

        return (
            "              <Prtry>\n"
            f"                <Tp>{agent_type}</Tp>\n"
            "                <Agt>\n"
            "                  <FinInstnId>\n"
            + "".join(fin_lines)
            + "                  </FinInstnId>\n"
            "                </Agt>\n"
            "              </Prtry>\n"
        )

    @staticmethod
    def _statement(payload: bytes) -> object:
        """Parse one exact camt.053.001.14 statement through the supported adapter."""
        return proprietary.parse_bank_statement_payload(
            payload,
            proprietary.CAMT053_MESSAGE_DEFINITION,
        )

    def _register_bank_account(self, statement: object) -> str:
        """Register an isolated tenant-scoped bank account for one statement variant."""
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
        """Build one isolated supported ingest command."""
        return {
            "tenant_reference": self.case.policy.tenant_reference,
            "bank_account_reference": account_reference,
            "ingestion_idempotency_key": (
                f"proprietary-agent-clearing-choice-{suffix}-{uuid.uuid4().hex}"
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
        """Accept one statement and return its first transaction-detail projection."""
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
        """Reject missing evidence identities and require canonical sha256 syntax."""
        if not isinstance(value, str) or _HASH_RE.fullmatch(value) is None:
            raise AssertionError("canonical digest must match sha256:<64 lowercase hex>")

    @staticmethod
    def _assert_exact_amount(entry: object, detail: object) -> None:
        """Keep proprietary-agent provenance separate from exact accounting facts."""
        if getattr(entry, "entry_amount", None) != Decimal("25000.00"):
            raise AssertionError("proprietary-agent choice must retain entry 25000.00")
        if getattr(entry, "entry_currency_code", None) != "KRW":
            raise AssertionError("proprietary-agent choice must retain entry KRW")
        if getattr(detail, "detail_amount", None) != Decimal("25000.00"):
            raise AssertionError("proprietary-agent choice must retain detail 25000.00")
        if getattr(detail, "detail_currency_code", None) != "KRW":
            raise AssertionError("proprietary-agent choice must retain detail KRW")


if __name__ == "__main__":
    unittest.main()
