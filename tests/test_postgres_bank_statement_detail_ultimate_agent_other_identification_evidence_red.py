"""PostgreSQL REDs for ultimate-agent alternate financial-identification evidence."""

from __future__ import annotations

import copy
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
from tests import test_postgres_posting as posting

_HASH_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
_CORRECTION_ERROR = (
    r"^statement identity already exists with different entry evidence\. "
    r"Use an explicit correction contract, then retry ingest\.$"
)


class BankStatementDetailUltimateAgentOtherIdentificationEvidenceRedTests(
    unittest.TestCase
):
    """Retain GenericFinancialIdentification1 by ultimate role without disclosure."""

    @classmethod
    def setUpClass(cls) -> None:
        """Reuse the canonical real-PostgreSQL integration fixture."""
        posting.PostgresPostingTests.setUpClass()

    def setUp(self) -> None:
        """Prepare isolated ultimate-agent alternate-identification cases."""
        self.case = posting.PostgresPostingTests("setUp")
        self.addCleanup(self.case.doCleanups)
        self.case.setUp()
        self.addCleanup(self.case.tearDown)
        self.fixture = load_canonical_statement_fixture().decode("utf-8")
        self.base = {
            "bicfi": "DEUTDEFF",
            "other": {
                "id": "ULTIMATE-AGENT-ID-001",
                "scheme": {"proprietary": "BANK"},
                "issuer": "Ultimate Agent Registry",
            },
        }

    def test_other_identification_is_material_for_each_ultimate_agent(self) -> None:
        """Id, scheme choice/optionality, issuer, and Othr presence change evidence."""
        for role in self._roles():
            baseline = parse_bank_statement_payload(
                self._with_ultimate_agent(role, self.base),
                CAMT053_MESSAGE_DEFINITION,
            )
            baseline_entry = baseline.entries[0]
            baseline_detail = baseline_entry.entry_details[0]
            evidence_key = self._evidence_key(role)
            baseline_role_hash = getattr(baseline_detail, evidence_key)
            self._assert_financial_truth(baseline_entry, baseline_detail)
            self._assert_semantic_hashes(
                baseline,
                baseline_entry,
                baseline_detail,
                evidence_key,
            )

            for semantic, changed_agent in self._variants(self.base).items():
                with self.subTest(role=role, semantic=semantic):
                    changed = parse_bank_statement_payload(
                        self._with_ultimate_agent(role, changed_agent),
                        CAMT053_MESSAGE_DEFINITION,
                    )
                    changed_entry = changed.entries[0]
                    changed_detail = changed_entry.entry_details[0]
                    changed_role_hash = getattr(changed_detail, evidence_key)
                    self._assert_financial_truth(changed_entry, changed_detail)
                    self._assert_semantic_hashes(
                        changed,
                        changed_entry,
                        changed_detail,
                        evidence_key,
                    )

                    self.assertNotEqual(baseline_role_hash, changed_role_hash)
                    self.assertNotEqual(
                        baseline_detail.source_detail_hash,
                        changed_detail.source_detail_hash,
                    )
                    self.assertNotEqual(
                        baseline_entry.source_entry_hash,
                        changed_entry.source_entry_hash,
                    )
                    self.assertNotEqual(
                        baseline.normalized_payload_hash,
                        changed.normalized_payload_hash,
                    )
                    self.assertEqual(
                        baseline.account_identifier_hash,
                        changed.account_identifier_hash,
                    )
                    self.assertEqual(
                        baseline.entries[1].source_entry_hash,
                        changed.entries[1].source_entry_hash,
                    )

    def test_same_scalar_scheme_choice_keeps_discriminator_material(self) -> None:
        """FinancialIdentificationSchemeName1Choice Cd|Prtry remains material."""
        coded_agent = copy.deepcopy(self.base)
        other = coded_agent.get("other")
        if not isinstance(other, dict):
            raise AssertionError("ultimate-agent baseline requires Othr")
        other["scheme"] = {"code": "BANK"}

        for role in self._roles():
            with self.subTest(role=role):
                proprietary = parse_bank_statement_payload(
                    self._with_ultimate_agent(role, self.base),
                    CAMT053_MESSAGE_DEFINITION,
                )
                coded = parse_bank_statement_payload(
                    self._with_ultimate_agent(role, coded_agent),
                    CAMT053_MESSAGE_DEFINITION,
                )
                evidence_key = self._evidence_key(role)
                proprietary_entry = proprietary.entries[0]
                coded_entry = coded.entries[0]
                proprietary_detail = proprietary_entry.entry_details[0]
                coded_detail = coded_entry.entry_details[0]

                self.assertNotEqual(
                    getattr(proprietary_detail, evidence_key),
                    getattr(coded_detail, evidence_key),
                )
                self.assertNotEqual(
                    proprietary_detail.source_detail_hash,
                    coded_detail.source_detail_hash,
                )
                self.assertNotEqual(
                    proprietary_entry.source_entry_hash,
                    coded_entry.source_entry_hash,
                )
                self.assertNotEqual(
                    proprietary.normalized_payload_hash,
                    coded.normalized_payload_hash,
                )
                self.assertEqual(
                    proprietary.account_identifier_hash,
                    coded.account_identifier_hash,
                )
                self._assert_financial_truth(proprietary_entry, proprietary_detail)
                self._assert_financial_truth(coded_entry, coded_detail)

    def test_othr_layout_is_representation_only_for_each_ultimate_agent(self) -> None:
        """Whitespace inside Othr changes raw bytes without changing semantics."""
        for role in self._roles():
            with self.subTest(role=role):
                baseline = self._with_ultimate_agent(role, self.base)
                needle = b"                    <Othr>\n"
                self.assertEqual(baseline.count(needle), 1)
                formatted = baseline.replace(
                    needle,
                    needle + b"                      \n",
                    1,
                )
                self.assertNotEqual(baseline, formatted)

                left = parse_bank_statement_payload(
                    baseline,
                    CAMT053_MESSAGE_DEFINITION,
                )
                right = parse_bank_statement_payload(
                    formatted,
                    CAMT053_MESSAGE_DEFINITION,
                )
                left_entry = left.entries[0]
                right_entry = right.entries[0]
                left_detail = left_entry.entry_details[0]
                right_detail = right_entry.entry_details[0]
                evidence_key = self._evidence_key(role)

                for value in (
                    left.source_artifact_hash,
                    right.source_artifact_hash,
                    getattr(left_detail, evidence_key),
                    getattr(right_detail, evidence_key),
                    left_detail.source_detail_hash,
                    right_detail.source_detail_hash,
                    left_entry.source_entry_hash,
                    right_entry.source_entry_hash,
                    left.normalized_payload_hash,
                    right.normalized_payload_hash,
                ):
                    self._assert_sha256(value)

                self.assertNotEqual(left.source_artifact_hash, right.source_artifact_hash)
                self.assertEqual(
                    getattr(left_detail, evidence_key),
                    getattr(right_detail, evidence_key),
                )
                self.assertEqual(
                    left_detail.source_detail_hash,
                    right_detail.source_detail_hash,
                )
                self.assertEqual(
                    left_entry.source_entry_hash,
                    right_entry.source_entry_hash,
                )
                self.assertEqual(
                    left.normalized_payload_hash,
                    right.normalized_payload_hash,
                )

    def test_every_other_identification_variant_reaches_correction_boundary(self) -> None:
        """Accepted ultimate-agent alternate identity cannot be silently replaced."""
        for role in self._roles():
            baseline = self._with_ultimate_agent(role, self.base)
            reference = self._register_statement_account(baseline)
            store = MemoryArtifactStore()
            accepted = accept_bank_statement_evidence(
                self._command(baseline, reference, f"{role}-other-baseline"),
                posting.DATABASE_URL,
                self.case.policy.tenant_reference,
                artifact_store=store,
            )
            self.assertFalse(accepted["replayed"])

            for semantic, changed_agent in self._variants(self.base).items():
                with self.subTest(role=role, semantic=semantic):
                    changed = self._with_ultimate_agent(role, changed_agent)
                    with self.assertRaisesRegex(AccountingValidationError, _CORRECTION_ERROR):
                        accept_bank_statement_evidence(
                            self._command(
                                changed,
                                reference,
                                f"{role}-other-{semantic}",
                            ),
                            posting.DATABASE_URL,
                            self.case.policy.tenant_reference,
                            artifact_store=store,
                        )

    def test_buyer_projection_keeps_other_identification_non_reversible(self) -> None:
        """Ultimate-agent Othr affects digest identity without buyer disclosure."""
        for role in self._roles():
            with self.subTest(role=role):
                rich = self._ingest_and_read_first_detail(
                    self._with_ultimate_agent(role, self.base),
                    f"{role}-other-rich",
                )
                bic_only_agent = {"bicfi": self.base["bicfi"]}
                bic_only = self._ingest_and_read_first_detail(
                    self._with_ultimate_agent(role, bic_only_agent),
                    f"{role}-other-absent",
                )
                evidence_key = self._evidence_key(role)
                for projection in (rich, bic_only):
                    self._assert_sha256(projection[evidence_key])
                    self._assert_sha256(projection["source_detail_hash"])
                    self.assertEqual(
                        Decimal(str(projection["detail_amount"])),
                        Decimal("25000.00"),
                    )
                    self.assertEqual(projection["detail_currency_code"], "KRW")

                self.assertNotEqual(rich[evidence_key], bic_only[evidence_key])
                self.assertNotEqual(
                    rich["source_detail_hash"],
                    bic_only["source_detail_hash"],
                )
                rich_public = self._public_projection(rich, evidence_key)
                bic_public = self._public_projection(bic_only, evidence_key)
                self.assertEqual(rich_public, bic_public)

                source_values = {
                    "ULTIMATE-AGENT-ID-001",
                    "BANK",
                    "Ultimate Agent Registry",
                }
                buyer_values = set(self._scalar_leaves(rich_public))
                self.assertTrue(source_values.isdisjoint(buyer_values))

    @staticmethod
    def _variants(base: dict[str, object]) -> dict[str, dict[str, object]]:
        """Change one GenericFinancialIdentification1 fact at a time."""
        variants: dict[str, dict[str, object]] = {}

        other_id = copy.deepcopy(base)
        other = other_id.get("other")
        if not isinstance(other, dict):
            raise AssertionError("ultimate-agent baseline requires Othr")
        other["id"] = "ULTIMATE-AGENT-ID-002"
        variants["other-id-value"] = other_id

        scheme = copy.deepcopy(base)
        other = scheme.get("other")
        if not isinstance(other, dict):
            raise AssertionError("ultimate-agent baseline requires Othr")
        other["scheme"] = {"proprietary": "NATIONAL_BANK_ID"}
        variants["scheme-proprietary-value"] = scheme

        coded = copy.deepcopy(base)
        other = coded.get("other")
        if not isinstance(other, dict):
            raise AssertionError("ultimate-agent baseline requires Othr")
        other["scheme"] = {"code": "BANK"}
        variants["same-scalar-scheme-choice"] = coded

        scheme_absent = copy.deepcopy(base)
        other = scheme_absent.get("other")
        if not isinstance(other, dict):
            raise AssertionError("ultimate-agent baseline requires Othr")
        other.pop("scheme")
        variants["scheme-absent"] = scheme_absent

        issuer = copy.deepcopy(base)
        other = issuer.get("other")
        if not isinstance(other, dict):
            raise AssertionError("ultimate-agent baseline requires Othr")
        other["issuer"] = "Alternate Ultimate Agent Registry"
        variants["issuer-value"] = issuer

        issuer_absent = copy.deepcopy(base)
        other = issuer_absent.get("other")
        if not isinstance(other, dict):
            raise AssertionError("ultimate-agent baseline requires Othr")
        other.pop("issuer")
        variants["issuer-absent"] = issuer_absent

        other_absent = copy.deepcopy(base)
        other_absent.pop("other")
        variants["other-absent"] = other_absent
        return variants

    def _with_ultimate_agent(self, role: str, value: dict[str, object]) -> bytes:
        """Insert one ultimate Agt with FinancialInstitutionIdentification23.Othr."""
        marker = "              </Dbtr>\n            </RltdPties>"
        self.assertEqual(self.fixture.count(marker), 1)
        tag = self._xml_tag(role)
        replacement = (
            "              </Dbtr>\n"
            f"              <{tag}>\n"
            "                <Agt>\n"
            "                  <FinInstnId>\n"
            + self._financial_institution_xml(value)
            + "                  </FinInstnId>\n"
            "                </Agt>\n"
            f"              </{tag}>\n"
            "            </RltdPties>"
        )
        changed = self.fixture.replace(marker, replacement, 1)
        self.assertNotEqual(changed, self.fixture)
        return changed.encode("utf-8")

    @staticmethod
    def _financial_institution_xml(value: dict[str, object]) -> str:
        """Serialize BICFI and Othr in FinancialInstitutionIdentification23 order."""
        bicfi = value.get("bicfi")
        if not isinstance(bicfi, str) or not bicfi:
            raise AssertionError("focused ultimate agent requires BICFI")
        lines = [f"                    <BICFI>{bicfi}</BICFI>\n"]
        other = value.get("other")
        if other is not None:
            if not isinstance(other, dict) or not isinstance(other.get("id"), str):
                raise AssertionError("ultimate-agent Othr requires Id")
            lines.extend(
                [
                    "                    <Othr>\n",
                    f"                      <Id>{other['id']}</Id>\n",
                ]
            )
            scheme = other.get("scheme")
            if scheme is not None:
                if not isinstance(scheme, dict):
                    raise AssertionError("SchmeNm must be a structured choice")
                lines.append("                      <SchmeNm>\n")
                if isinstance(scheme.get("code"), str):
                    lines.append(f"                        <Cd>{scheme['code']}</Cd>\n")
                elif isinstance(scheme.get("proprietary"), str):
                    lines.append(
                        f"                        <Prtry>{scheme['proprietary']}</Prtry>\n"
                    )
                else:
                    raise AssertionError("SchmeNm requires Cd or Prtry")
                lines.append("                      </SchmeNm>\n")
            if isinstance(other.get("issuer"), str):
                lines.append(f"                      <Issr>{other['issuer']}</Issr>\n")
            lines.append("                    </Othr>\n")
        return "".join(lines)

    def _register_statement_account(self, payload: bytes) -> str:
        """Register the statement-owner account required by supported ingest."""
        parsed = parse_bank_statement_payload(payload, CAMT053_MESSAGE_DEFINITION)
        reference = f"urn:cwl:bank_account:{uuid.uuid4().hex}"
        accept_bank_account_record(
            {
                "tenant_reference": self.case.policy.tenant_reference,
                "bank_account_reference": reference,
                "account_currency_code": parsed.account_currency_code,
                "account_identifier_hash": parsed.account_identifier_hash,
            },
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
        )
        return reference

    def _ingest_and_read_first_detail(
        self,
        payload: bytes,
        suffix: str,
    ) -> dict[str, object]:
        """Ingest one isolated statement and return its first detail projection."""
        reference = self._register_statement_account(payload)
        accepted = accept_bank_statement_evidence(
            self._command(payload, reference, suffix),
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
            artifact_store=MemoryArtifactStore(),
        )
        document = lookup_bank_statement_entries(
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
            str(accepted["bank_statement_record_id"]),
        )
        return document["bank_statement_entries"][0]["entry_details"][0]

    @staticmethod
    def _public_projection(
        detail: dict[str, object],
        evidence_key: str,
    ) -> dict[str, object]:
        """Remove only internal evidence identities before buyer comparison."""
        projection = copy.deepcopy(detail)
        projection.pop(evidence_key, None)
        projection.pop("source_detail_hash", None)
        return projection

    @classmethod
    def _scalar_leaves(cls, value: object) -> list[str]:
        """Return exact scalar leaves so privacy checks avoid substring heuristics."""
        leaves: list[str] = []
        if isinstance(value, dict):
            for item in value.values():
                leaves.extend(cls._scalar_leaves(item))
        elif isinstance(value, list):
            for item in value:
                leaves.extend(cls._scalar_leaves(item))
        elif value is not None:
            leaves.append(str(value))
        return leaves

    def _command(
        self,
        payload: bytes,
        bank_account_reference: str,
        suffix: str,
    ) -> dict[str, object]:
        """Return one supported ingest command with an isolated replay key."""
        return {
            "tenant_reference": self.case.policy.tenant_reference,
            "bank_account_reference": bank_account_reference,
            "ingestion_idempotency_key": f"ultimate-agent-other-{suffix}-{uuid.uuid4().hex}",
            "message_definition_identifier": CAMT053_MESSAGE_DEFINITION,
            "statement_payload": payload.decode("utf-8"),
        }

    @staticmethod
    def _roles() -> tuple[str, str]:
        """Return the two ultimate roles owned by TransactionParties12."""
        return ("ultimate_debtor", "ultimate_creditor")

    @staticmethod
    def _evidence_key(role: str) -> str:
        """Return the role-specific ultimate-party evidence digest field."""
        if role == "ultimate_debtor":
            return "ultimate_debtor_evidence_hash"
        if role == "ultimate_creditor":
            return "ultimate_creditor_evidence_hash"
        raise AssertionError(f"unsupported ultimate role: {role}")

    @staticmethod
    def _xml_tag(role: str) -> str:
        """Map the logical role to the camt.053 ultimate-party tag."""
        if role == "ultimate_debtor":
            return "UltmtDbtr"
        if role == "ultimate_creditor":
            return "UltmtCdtr"
        raise AssertionError(f"unsupported ultimate role: {role}")

    def _assert_semantic_hashes(
        self,
        statement: object,
        entry: object,
        detail: object,
        evidence_key: str,
    ) -> None:
        """Require canonical identities for the entire retained evidence chain."""
        for value in (
            getattr(detail, evidence_key),
            getattr(detail, "source_detail_hash"),
            getattr(entry, "source_entry_hash"),
            getattr(statement, "normalized_payload_hash"),
            getattr(statement, "account_identifier_hash"),
            getattr(statement, "entries")[1].source_entry_hash,
        ):
            self._assert_sha256(value)

    @staticmethod
    def _assert_sha256(value: object) -> None:
        """Require one canonical SHA-256 identity."""
        if not isinstance(value, str) or _HASH_PATTERN.fullmatch(value) is None:
            raise AssertionError(f"expected canonical SHA-256 identity, got {value!r}")

    def _assert_financial_truth(self, entry: object, detail: object) -> None:
        """Keep ultimate-agent provenance independent from exact accounting facts."""
        self.assertEqual(getattr(entry, "entry_amount"), Decimal("25000.00"))
        self.assertEqual(getattr(entry, "entry_currency_code"), "KRW")
        self.assertEqual(getattr(detail, "detail_amount"), Decimal("25000.00"))
        self.assertEqual(getattr(detail, "detail_currency_code"), "KRW")


if __name__ == "__main__":
    unittest.main()
