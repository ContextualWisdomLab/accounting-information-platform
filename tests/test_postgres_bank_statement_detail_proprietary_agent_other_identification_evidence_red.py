"""PostgreSQL REDs for alternate financial IDs inside repeated proprietary agents."""

from __future__ import annotations

import copy
import re
import unittest
import uuid
from decimal import Decimal

from tests import test_postgres_bank_statement_detail_proprietary_agent_identity_evidence_red as proprietary
from tests import test_postgres_posting as posting

_HASH_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_CORRECTION_ERROR = (
    r"^statement identity already exists with different entry evidence\. "
    r"Use an explicit correction contract, then retry ingest\.$"
)


class BankStatementDetailProprietaryAgentOtherIdentificationEvidenceRedTests(
    unittest.TestCase
):
    """Retain GenericFinancialIdentification1 inside every ProprietaryAgent5 value."""

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
                    "name": "Execution Broker Frankfurt",
                    "other": {
                        "id": "DE-BROKER-ALT-001",
                        "scheme": {"proprietary": "BANK"},
                        "issuer": "Bundesbank Registry",
                    },
                },
            },
            {
                "type": "CUSTODY_AGENT",
                "agent": {
                    "bicfi": "BNPAFRPP",
                    "name": "Custody Agent Paris",
                    "other": {
                        "id": "FR-CUSTODY-ALT-001",
                        "scheme": {"proprietary": "CUSTODY"},
                        "issuer": "Banque de France Registry",
                    },
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

        needle = b"                  <Othr>\n"
        self.assertEqual(self.base_payload.count(needle), 2)
        first_offset = self.base_payload.index(needle)
        reformatted = (
            self.base_payload[: first_offset + len(needle)]
            + b"                    \n"
            + self.base_payload[first_offset + len(needle) :]
        )
        self.assertNotEqual(self.base_payload, reformatted)
        self.reformatted_payload = reformatted
        self.reformatted_statement = self._statement(self.reformatted_payload)

        self.bank_account_reference = self._register_bank_account(self.base_statement)

    def test_other_identification_value_choice_optionality_and_position_are_material(self) -> None:
        """Othr Id, scheme choice/optionality, issuer and repeated position affect identity."""
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

    def test_same_scalar_scheme_choice_keeps_discriminator_material(self) -> None:
        """FinancialIdentificationSchemeName1Choice Cd|Prtry remains material for BANK."""
        coded = self.variant_statements["first-scheme-coded-choice"]
        base_entry = self.base_statement.entries[0]
        base_detail = base_entry.entry_details[0]
        coded_entry = coded.entries[0]
        coded_detail = coded_entry.entry_details[0]
        base_digest = getattr(base_detail, "proprietary_agents_evidence_hash", None)
        coded_digest = getattr(coded_detail, "proprietary_agents_evidence_hash", None)
        self._assert_digest(base_digest)
        self._assert_digest(coded_digest)
        self.assertNotEqual(base_digest, coded_digest)
        self.assertNotEqual(base_detail.source_detail_hash, coded_detail.source_detail_hash)
        self.assertNotEqual(base_entry.source_entry_hash, coded_entry.source_entry_hash)
        self.assertNotEqual(
            self.base_statement.normalized_payload_hash,
            coded.normalized_payload_hash,
        )
        self._assert_exact_amount(coded_entry, coded_detail)

    def test_other_identification_layout_is_representation_only(self) -> None:
        """Whitespace inside Othr changes raw bytes but not admitted semantics."""
        base_entry = self.base_statement.entries[0]
        base_detail = base_entry.entry_details[0]
        changed_entry = self.reformatted_statement.entries[0]
        changed_detail = changed_entry.entry_details[0]
        base_digest = getattr(base_detail, "proprietary_agents_evidence_hash", None)
        changed_digest = getattr(changed_detail, "proprietary_agents_evidence_hash", None)
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

    def test_other_identification_change_requires_explicit_correction(self) -> None:
        """Accepted proprietary-agent alternate identity cannot be silently replaced."""
        store = proprietary.MemoryArtifactStore()
        accepted = proprietary.accept_bank_statement_evidence(
            self._command(self.base_payload, self.bank_account_reference, "base"),
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
            artifact_store=store,
        )
        self.assertFalse(accepted["replayed"])

        changed_payload = self.variant_payloads["first-scheme-coded-choice"]
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

    def test_buyer_read_retains_other_identification_without_accounting_authority(self) -> None:
        """Buyer reconciliation evidence retains Othr while 25000 KRW stays exact."""
        coded_payload = self.variant_payloads["first-scheme-coded-choice"]
        coded_statement = self.variant_statements["first-scheme-coded-choice"]
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

        self.assertEqual(base_detail["proprietary_agents"], self.base)
        self.assertEqual(
            coded_detail["proprietary_agents"],
            self.variants["first-scheme-coded-choice"],
        )
        self.assertNotEqual(
            base_detail["proprietary_agents_evidence_hash"],
            coded_detail["proprietary_agents_evidence_hash"],
        )
        self.assertNotEqual(
            base_detail["source_detail_hash"],
            coded_detail["source_detail_hash"],
        )

    @staticmethod
    def _variants(
        base: list[dict[str, object]],
    ) -> dict[str, list[dict[str, object]]]:
        """Change one GenericFinancialIdentification1 fact or repeated position at a time."""
        variants: dict[str, list[dict[str, object]]] = {}

        def other(value: list[dict[str, object]], index: int = 0) -> dict[str, object]:
            agent = value[index].get("agent")
            if not isinstance(agent, dict):
                raise AssertionError("proprietary agent requires Agt")
            result = agent.get("other")
            if not isinstance(result, dict):
                raise AssertionError("focused proprietary agent requires Othr")
            return result

        changed_id = copy.deepcopy(base)
        other(changed_id)["id"] = "DE-BROKER-ALT-002"
        variants["first-other-id"] = changed_id

        changed_scheme = copy.deepcopy(base)
        other(changed_scheme)["scheme"] = {"proprietary": "NATIONAL_BANK_ID"}
        variants["first-scheme-proprietary"] = changed_scheme

        coded = copy.deepcopy(base)
        other(coded)["scheme"] = {"code": "BANK"}
        variants["first-scheme-coded-choice"] = coded

        scheme_absent = copy.deepcopy(base)
        other(scheme_absent).pop("scheme")
        variants["first-scheme-absent"] = scheme_absent

        issuer = copy.deepcopy(base)
        other(issuer)["issuer"] = "BaFin Registry"
        variants["first-issuer"] = issuer

        issuer_absent = copy.deepcopy(base)
        other(issuer_absent).pop("issuer")
        variants["first-issuer-absent"] = issuer_absent

        other_absent = copy.deepcopy(base)
        first_agent = other_absent[0].get("agent")
        if not isinstance(first_agent, dict):
            raise AssertionError("first proprietary agent requires Agt")
        first_agent.pop("other")
        variants["first-other-absent"] = other_absent

        second_id = copy.deepcopy(base)
        other(second_id, 1)["id"] = "FR-CUSTODY-ALT-002"
        variants["second-other-id"] = second_id
        return variants

    @classmethod
    def _with_agents(
        cls,
        fixture: str,
        marker: str,
        agents: list[dict[str, object]],
    ) -> bytes:
        """Insert source-ordered proprietary agents after related parties."""
        items = "".join(cls._agent_xml(value) for value in agents)
        related_agents = "            <RltdAgts>\n" + items + "            </RltdAgts>\n"
        replacement = "            </RltdPties>\n" + related_agents + "            <RmtInf>"
        changed = fixture.replace(marker, replacement, 1)
        if changed == fixture:
            raise AssertionError("proprietary alternate-ID insertion must change XML")
        return changed.encode("utf-8")

    @staticmethod
    def _agent_xml(value: dict[str, object]) -> str:
        """Serialize ProprietaryAgent5 and FinancialInstitutionIdentification23.Othr."""
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
        if isinstance(agent.get("name"), str):
            fin_lines.append(f"                    <Nm>{agent['name']}</Nm>\n")
        other = agent.get("other")
        if other is not None:
            if not isinstance(other, dict) or not isinstance(other.get("id"), str):
                raise AssertionError("Othr requires Id")
            fin_lines.extend(
                [
                    "                    <Othr>\n",
                    f"                      <Id>{other['id']}</Id>\n",
                ]
            )
            scheme = other.get("scheme")
            if scheme is not None:
                if not isinstance(scheme, dict) or len(scheme) != 1:
                    raise AssertionError("SchmeNm requires exactly one Cd|Prtry choice")
                kind, scalar = next(iter(scheme.items()))
                if kind not in {"code", "proprietary"} or not isinstance(scalar, str) or not scalar:
                    raise AssertionError("SchmeNm requires a non-empty Cd|Prtry value")
                tag = "Cd" if kind == "code" else "Prtry"
                fin_lines.extend(
                    [
                        "                      <SchmeNm>\n",
                        f"                        <{tag}>{scalar}</{tag}>\n",
                        "                      </SchmeNm>\n",
                    ]
                )
            if isinstance(other.get("issuer"), str):
                fin_lines.append(f"                      <Issr>{other['issuer']}</Issr>\n")
            fin_lines.append("                    </Othr>\n")

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
                f"proprietary-agent-other-id-{suffix}-{uuid.uuid4().hex}"
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
            raise AssertionError("proprietary alternate-ID evidence must retain entry 25000.00")
        if getattr(entry, "entry_currency_code", None) != "KRW":
            raise AssertionError("proprietary alternate-ID evidence must retain entry KRW")
        if getattr(detail, "detail_amount", None) != Decimal("25000.00"):
            raise AssertionError("proprietary alternate-ID evidence must retain detail 25000.00")
        if getattr(detail, "detail_currency_code", None) != "KRW":
            raise AssertionError("proprietary alternate-ID evidence must retain detail KRW")


if __name__ == "__main__":
    unittest.main()
