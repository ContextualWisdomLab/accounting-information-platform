"""PostgreSQL REDs for alternate financial-identification evidence on standard agents."""

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


class BankStatementDetailPaymentAgentOtherIdentificationEvidenceRedTests(
    unittest.TestCase
):
    """Retain GenericFinancialIdentification1 without exposing reversible buyer PII."""

    @classmethod
    def setUpClass(cls) -> None:
        """Reuse the canonical real-PostgreSQL integration fixture."""
        posting.PostgresPostingTests.setUpClass()

    def setUp(self) -> None:
        """Prepare standard-agent alternate-ID materiality and privacy controls."""
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
            "other": {
                "id": "DE-BANK-ALT-001",
                "scheme": {"proprietary": "BANK"},
                "issuer": "Bundesbank Registry",
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
            "                  <Othr>\n",
            "                  <Othr>\n                    \n",
            1,
        )
        self.assertNotEqual(reformatted_xml, agent_xml)
        self.reformatted_payload = self.base_payload.replace(
            agent_xml.encode("utf-8"), reformatted_xml.encode("utf-8"), 1
        )
        self.assertNotEqual(self.reformatted_payload, self.base_payload)
        self.reformatted_statement = parse_bank_statement_payload(
            self.reformatted_payload, CAMT053_MESSAGE_DEFINITION
        )

        self.bank_account_reference = self._register_bank_account(self.base_statement)
        self.store = MemoryArtifactStore()

    def test_alternate_financial_identification_is_material(self) -> None:
        """Id, scheme choice/optionality, issuer, and Othr presence affect evidence identity."""
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
                    "BICFI is fixed; the existing purpose digest must stay stable",
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

    def test_same_scalar_scheme_choice_keeps_discriminator_material(self) -> None:
        """FinancialIdentificationSchemeName1Choice Cd|Prtry remains material for BANK."""
        coded_statement = self.variant_statements["scheme-coded-choice"]
        proprietary_statement = self.base_statement
        coded_entry = coded_statement.entries[1]
        proprietary_entry = proprietary_statement.entries[1]
        coded_detail = coded_entry.entry_details[0]
        proprietary_detail = proprietary_entry.entry_details[0]

        self.assertEqual(
            getattr(coded_detail, "instructing_agent_evidence_hash", None),
            getattr(proprietary_detail, "instructing_agent_evidence_hash", None),
        )
        self.assertNotEqual(
            coded_detail.source_detail_hash, proprietary_detail.source_detail_hash
        )
        self.assertNotEqual(coded_entry.source_entry_hash, proprietary_entry.source_entry_hash)
        self.assertNotEqual(
            coded_statement.normalized_payload_hash,
            proprietary_statement.normalized_payload_hash,
        )
        self._assert_exact_accounting_amount(coded_entry, coded_detail)
        self._assert_exact_accounting_amount(proprietary_entry, proprietary_detail)

    def test_xml_layout_does_not_change_alternate_identification_semantics(self) -> None:
        """Whitespace inside Othr changes raw bytes but not admitted semantics."""
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

    def test_changed_alternate_identification_requires_explicit_correction(self) -> None:
        """Accepted alternate financial identity cannot be replaced by silent replay."""
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

    def test_buyer_read_does_not_expose_reversible_alternate_identification(self) -> None:
        """Canonical evidence may change while the purpose-limited buyer projection stays stable."""
        base_detail = self._ingest_and_read(self.base_payload, "privacy-base")
        coded_statement = self.variant_statements["scheme-coded-choice"]
        coded_reference = self._register_bank_account(coded_statement)
        coded_detail = self._ingest_and_read(
            self.variant_payloads["scheme-coded-choice"],
            "privacy-coded",
            bank_account_reference=coded_reference,
        )

        self.assertEqual(
            base_detail.get("instructing_agent_evidence_hash"),
            coded_detail.get("instructing_agent_evidence_hash"),
        )
        self.assertNotEqual(
            base_detail.get("source_detail_hash"), coded_detail.get("source_detail_hash")
        )
        base_visible = dict(base_detail)
        coded_visible = dict(coded_detail)
        base_visible.pop("source_detail_hash", None)
        coded_visible.pop("source_detail_hash", None)
        self.assertEqual(base_visible, coded_visible)
        self.assertEqual(base_detail["detail_amount"], "6000")
        self.assertEqual(base_detail["detail_currency_code"], "KRW")
        self.assertEqual(coded_detail["detail_amount"], "6000")
        self.assertEqual(coded_detail["detail_currency_code"], "KRW")

    @staticmethod
    def _variants(base: dict[str, object]) -> dict[str, dict[str, object]]:
        """Change one GenericFinancialIdentification1 fact at a time."""
        variants: dict[str, dict[str, object]] = {}

        other_id = copy.deepcopy(base)
        other = other_id.get("other")
        if not isinstance(other, dict):
            raise AssertionError("base instructing agent requires Othr")
        other["id"] = "DE-BANK-ALT-002"
        variants["other-id"] = other_id

        scheme = copy.deepcopy(base)
        other = scheme.get("other")
        if not isinstance(other, dict):
            raise AssertionError("base instructing agent requires Othr")
        other["scheme"] = {"proprietary": "NATIONAL_BANK_ID"}
        variants["scheme-proprietary"] = scheme

        coded = copy.deepcopy(base)
        other = coded.get("other")
        if not isinstance(other, dict):
            raise AssertionError("base instructing agent requires Othr")
        other["scheme"] = {"code": "BANK"}
        variants["scheme-coded-choice"] = coded

        scheme_absent = copy.deepcopy(base)
        other = scheme_absent.get("other")
        if not isinstance(other, dict):
            raise AssertionError("base instructing agent requires Othr")
        other.pop("scheme")
        variants["scheme-absent"] = scheme_absent

        issuer = copy.deepcopy(base)
        other = issuer.get("other")
        if not isinstance(other, dict):
            raise AssertionError("base instructing agent requires Othr")
        other["issuer"] = "BaFin Registry"
        variants["issuer"] = issuer

        issuer_absent = copy.deepcopy(base)
        other = issuer_absent.get("other")
        if not isinstance(other, dict):
            raise AssertionError("base instructing agent requires Othr")
        other.pop("issuer")
        variants["issuer-absent"] = issuer_absent

        other_absent = copy.deepcopy(base)
        other_absent.pop("other")
        variants["other-absent"] = other_absent
        return variants

    def _with_instructing_agent(self, value: dict[str, object]) -> bytes:
        """Insert one InstgAgt alternate identifier into the outgoing-payment detail."""
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
        """Serialize FinancialInstitutionIdentification23.Othr in XSD sequence order."""
        bicfi = value.get("bicfi")
        if not isinstance(bicfi, str) or not bicfi:
            raise AssertionError("focused instructing agent requires BICFI")
        lines = [
            "              <InstgAgt>\n",
            "                <FinInstnId>\n",
            f"                  <BICFI>{bicfi}</BICFI>\n",
        ]
        other = value.get("other")
        if other is not None:
            if not isinstance(other, dict) or not isinstance(other.get("id"), str):
                raise AssertionError("Othr requires Id")
            lines.extend(
                [
                    "                  <Othr>\n",
                    f"                    <Id>{other['id']}</Id>\n",
                ]
            )
            scheme = other.get("scheme")
            if scheme is not None:
                if not isinstance(scheme, dict):
                    raise AssertionError("SchmeNm must be a structured choice")
                lines.append("                    <SchmeNm>\n")
                if isinstance(scheme.get("code"), str):
                    lines.append(f"                      <Cd>{scheme['code']}</Cd>\n")
                elif isinstance(scheme.get("proprietary"), str):
                    lines.append(
                        f"                      <Prtry>{scheme['proprietary']}</Prtry>\n"
                    )
                else:
                    raise AssertionError("SchmeNm requires Cd or Prtry")
                lines.append("                    </SchmeNm>\n")
            if isinstance(other.get("issuer"), str):
                lines.append(f"                    <Issr>{other['issuer']}</Issr>\n")
            lines.append("                  </Othr>\n")
        lines.extend(["                </FinInstnId>\n", "              </InstgAgt>\n"])
        return "".join(lines)

    @staticmethod
    def _assert_exact_accounting_amount(entry: object, detail: object) -> None:
        """Keep accounting amount/currency independent from agent identification provenance."""
        if getattr(entry, "entry_amount", None) != Decimal("6000.00"):
            raise AssertionError("alternate-ID evidence must retain 6000.00 entry amount")
        if getattr(entry, "entry_currency_code", None) != "KRW":
            raise AssertionError("alternate-ID evidence must retain KRW entry currency")
        if getattr(detail, "detail_amount", None) != Decimal("6000.00"):
            raise AssertionError("alternate-ID evidence must retain 6000.00 detail amount")
        if getattr(detail, "detail_currency_code", None) != "KRW":
            raise AssertionError("alternate-ID evidence must retain KRW detail currency")

    def _register_bank_account(self, statement: object) -> str:
        """Register an isolated bank account reference for one accepted statement shape."""
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
                f"payment-agent-other-id-{suffix}-{uuid.uuid4().hex}"
            ),
            "message_definition_identifier": CAMT053_MESSAGE_DEFINITION,
            "statement_payload": payload.decode("utf-8"),
        }


if __name__ == "__main__":
    unittest.main()
