"""PostgreSQL REDs for alternate financial IDs across standard payment-agent roles."""

from __future__ import annotations

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


class BankStatementDetailPaymentAgentOtherRoleIdentificationEvidenceRedTests(
    unittest.TestCase
):
    """Retain GenericFinancialIdentification1 on every non-instructing standard role."""

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
        """Prepare role-specific alternate-ID materiality and privacy controls."""
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

    def test_other_identification_is_material_for_every_remaining_standard_role(self) -> None:
        """Role-specific alternate-ID facts change canonical evidence, not accounting truth."""
        for element_name, (digest_key, bicfi) in self.ROLES.items():
            with self.subTest(element_name=element_name):
                base = self._statement(
                    self._with_agent(
                        element_name,
                        bicfi,
                        other_id=f"{element_name}-ALT-001",
                        scheme_kind="proprietary",
                        scheme_value="BANK",
                        issuer="CWL Registry",
                    )
                )
                variants = {
                    "other-id": self._statement(
                        self._with_agent(
                            element_name,
                            bicfi,
                            other_id=f"{element_name}-ALT-002",
                            scheme_kind="proprietary",
                            scheme_value="BANK",
                            issuer="CWL Registry",
                        )
                    ),
                    "same-scalar-scheme-choice": self._statement(
                        self._with_agent(
                            element_name,
                            bicfi,
                            other_id=f"{element_name}-ALT-001",
                            scheme_kind="code",
                            scheme_value="BANK",
                            issuer="CWL Registry",
                        )
                    ),
                    "issuer": self._statement(
                        self._with_agent(
                            element_name,
                            bicfi,
                            other_id=f"{element_name}-ALT-001",
                            scheme_kind="proprietary",
                            scheme_value="BANK",
                            issuer="CWL Registry Updated",
                        )
                    ),
                    "other-absent": self._statement(
                        self._with_agent(element_name, bicfi)
                    ),
                }
                base_entry = base.entries[1]
                base_detail = base_entry.entry_details[0]
                self._assert_exact_accounting_amount(base_entry, base_detail)
                self.assertIsInstance(getattr(base_detail, digest_key, None), str)

                for variant_name, statement in variants.items():
                    with self.subTest(
                        element_name=element_name,
                        variant_name=variant_name,
                    ):
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

    def test_other_identification_xml_layout_is_not_semantic_for_each_role(self) -> None:
        """Whitespace inside Othr changes raw bytes but not admitted evidence semantics."""
        for element_name, (digest_key, bicfi) in self.ROLES.items():
            with self.subTest(element_name=element_name):
                payload = self._with_agent(
                    element_name,
                    bicfi,
                    other_id=f"{element_name}-ALT-001",
                    scheme_kind="proprietary",
                    scheme_value="BANK",
                    issuer="CWL Registry",
                )
                reformatted = payload.replace(
                    b"                  <Othr>\n",
                    b"                  <Othr>\n                    \n",
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

    def test_other_identification_change_requires_explicit_correction_for_each_role(self) -> None:
        """Accepted role-specific alternate IDs cannot be replaced by silent replay."""
        for element_name, (_, bicfi) in self.ROLES.items():
            with self.subTest(element_name=element_name):
                base_payload = self._with_agent(
                    element_name,
                    bicfi,
                    other_id=f"{element_name}-ALT-001",
                    scheme_kind="proprietary",
                    scheme_value="BANK",
                    issuer="CWL Registry",
                )
                changed_payload = self._with_agent(
                    element_name,
                    bicfi,
                    other_id=f"{element_name}-ALT-002",
                    scheme_kind="proprietary",
                    scheme_value="BANK",
                    issuer="CWL Registry",
                )
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

    def test_other_identification_stays_private_on_buyer_projection_for_each_role(self) -> None:
        """Buyer reads keep the role digest but do not expose reversible alternate IDs."""
        for element_name, (digest_key, bicfi) in self.ROLES.items():
            with self.subTest(element_name=element_name):
                proprietary_payload = self._with_agent(
                    element_name,
                    bicfi,
                    other_id=f"{element_name}-ALT-001",
                    scheme_kind="proprietary",
                    scheme_value="BANK",
                    issuer="CWL Registry",
                )
                coded_payload = self._with_agent(
                    element_name,
                    bicfi,
                    other_id=f"{element_name}-ALT-001",
                    scheme_kind="code",
                    scheme_value="BANK",
                    issuer="CWL Registry",
                )
                proprietary_statement = self._statement(proprietary_payload)
                coded_statement = self._statement(coded_payload)
                proprietary_reference = self._register_bank_account(proprietary_statement)
                coded_reference = self._register_bank_account(coded_statement)
                proprietary_detail = self._ingest_and_read(
                    proprietary_payload,
                    proprietary_reference,
                    f"privacy-proprietary-{element_name}",
                )
                coded_detail = self._ingest_and_read(
                    coded_payload,
                    coded_reference,
                    f"privacy-coded-{element_name}",
                )

                self.assertEqual(
                    proprietary_detail.get(digest_key),
                    coded_detail.get(digest_key),
                )
                self.assertNotEqual(
                    proprietary_detail.get("source_detail_hash"),
                    coded_detail.get("source_detail_hash"),
                )
                proprietary_visible = dict(proprietary_detail)
                coded_visible = dict(coded_detail)
                proprietary_visible.pop("source_detail_hash", None)
                coded_visible.pop("source_detail_hash", None)
                self.assertEqual(proprietary_visible, coded_visible)
                self.assertEqual(proprietary_detail["detail_amount"], "6000")
                self.assertEqual(proprietary_detail["detail_currency_code"], "KRW")
                self.assertEqual(coded_detail["detail_amount"], "6000")
                self.assertEqual(coded_detail["detail_currency_code"], "KRW")

    def _with_agent(
        self,
        element_name: str,
        bicfi: str,
        *,
        other_id: str | None = None,
        scheme_kind: str | None = None,
        scheme_value: str | None = None,
        issuer: str | None = None,
    ) -> bytes:
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
            + self._agent_xml(
                element_name,
                bicfi,
                other_id=other_id,
                scheme_kind=scheme_kind,
                scheme_value=scheme_value,
                issuer=issuer,
            )
            + "            </RltdAgts>\n"
            "            <RmtInf>"
        )
        return self.fixture.replace(self.marker, replacement, 1).encode("utf-8")

    @staticmethod
    def _agent_xml(
        element_name: str,
        bicfi: str,
        *,
        other_id: str | None,
        scheme_kind: str | None,
        scheme_value: str | None,
        issuer: str | None,
    ) -> str:
        """Serialize the focused standard-role financial institution in XSD sequence order."""
        lines = [
            f"              <{element_name}>\n",
            "                <FinInstnId>\n",
            f"                  <BICFI>{bicfi}</BICFI>\n",
        ]
        if other_id is not None:
            lines.extend(
                [
                    "                  <Othr>\n",
                    f"                    <Id>{other_id}</Id>\n",
                ]
            )
            if scheme_kind is not None or scheme_value is not None:
                if scheme_kind not in {"code", "proprietary"} or not scheme_value:
                    raise AssertionError("SchmeNm requires code|proprietary plus a value")
                lines.append("                    <SchmeNm>\n")
                if scheme_kind == "code":
                    lines.append(f"                      <Cd>{scheme_value}</Cd>\n")
                else:
                    lines.append(f"                      <Prtry>{scheme_value}</Prtry>\n")
                lines.append("                    </SchmeNm>\n")
            if issuer is not None:
                lines.append(f"                    <Issr>{issuer}</Issr>\n")
            lines.append("                  </Othr>\n")
        elif any(value is not None for value in (scheme_kind, scheme_value, issuer)):
            raise AssertionError("scheme and issuer require Othr/Id")
        lines.extend(
            [
                "                </FinInstnId>\n",
                f"              </{element_name}>\n",
            ]
        )
        return "".join(lines)

    @staticmethod
    def _statement(payload: bytes):
        """Parse one source-real camt.053 payload through the supported adapter."""
        return parse_bank_statement_payload(payload, CAMT053_MESSAGE_DEFINITION)

    @staticmethod
    def _assert_exact_accounting_amount(entry: object, detail: object) -> None:
        """Keep agent evidence independent from the exact accounting amount and currency."""
        if getattr(entry, "entry_amount", None) != Decimal("6000.00"):
            raise AssertionError("agent evidence must retain 6000.00 entry amount")
        if getattr(entry, "entry_currency_code", None) != "KRW":
            raise AssertionError("agent evidence must retain KRW entry currency")
        if getattr(detail, "detail_amount", None) != Decimal("6000.00"):
            raise AssertionError("agent evidence must retain 6000.00 detail amount")
        if getattr(detail, "detail_currency_code", None) != "KRW":
            raise AssertionError("agent evidence must retain KRW detail currency")

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
                f"payment-agent-other-role-{suffix}-{uuid.uuid4().hex}"
            ),
            "message_definition_identifier": CAMT053_MESSAGE_DEFINITION,
            "statement_payload": payload.decode("utf-8"),
        }


if __name__ == "__main__":
    unittest.main()
