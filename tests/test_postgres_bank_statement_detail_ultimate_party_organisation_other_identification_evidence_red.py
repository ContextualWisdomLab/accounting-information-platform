"""PostgreSQL REDs for ultimate-party organisation Othr evidence in camt.053."""

from __future__ import annotations

import copy
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
from tests import test_postgres_posting as posting

_HASH_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
_CORRECTION_ERROR = (
    r"^statement identity already exists with different entry evidence\. "
    r"Use an explicit correction contract, then retry ingest\.$"
)


class BankStatementDetailUltimatePartyOrganisationOtherIdentificationEvidenceRedTests(
    unittest.TestCase
):
    """Retain ultimate-party GenericOrganisationIdentification3 as private evidence."""

    @classmethod
    def setUpClass(cls) -> None:
        """Reuse the canonical real-PostgreSQL integration fixture."""
        posting.PostgresPostingTests.setUpClass()

    def setUp(self) -> None:
        """Prepare repeated Othr variants and one isolated PostgreSQL fixture."""
        self.case = posting.PostgresPostingTests("setUp")
        self.addCleanup(self.case.doCleanups)
        self.case.setUp()
        self.addCleanup(self.case.tearDown)
        self.fixture = load_canonical_statement_fixture().decode("utf-8")
        self.party_name = "Ultimate Organisation Other Party"
        self.base_identifiers = [
            {
                "id": "ULTIMATE-ORG-PRIMARY-001",
                "scheme": {"code": "BANK"},
                "issuer": "Ultimate Relationship Registry",
            },
            {
                "id": "ULTIMATE-ORG-ADDITIONAL-001",
                "scheme": {"code": "BANK"},
                "issuer": "Ultimate Tax Registry",
            },
        ]
        self.variants = self._variants(self.base_identifiers)

    def test_other_identification_fields_and_population_are_material_to_identity(self) -> None:
        """Othr ID, scheme choice/value, issuer, and repeated population change evidence."""
        for role in ("ultimate_debtor", "ultimate_creditor"):
            baseline = parse_bank_statement_payload(
                self._with_ultimate_party_identifiers(role, self.base_identifiers),
                CAMT053_MESSAGE_DEFINITION,
            )
            baseline_entry = baseline.entries[0]
            baseline_detail = baseline_entry.entry_details[0]
            evidence_key = self._evidence_key(role)
            self._assert_financial_truth(baseline_entry, baseline_detail)
            for value in (
                getattr(baseline_detail, evidence_key),
                baseline_detail.source_detail_hash,
                baseline_entry.source_entry_hash,
                baseline.normalized_payload_hash,
                baseline.account_identifier_hash,
                baseline.entries[1].source_entry_hash,
            ):
                self._assert_sha256(value)

            for semantic, identifiers in self.variants.items():
                with self.subTest(role=role, semantic=semantic):
                    changed = parse_bank_statement_payload(
                        self._with_ultimate_party_identifiers(role, identifiers),
                        CAMT053_MESSAGE_DEFINITION,
                    )
                    changed_entry = changed.entries[0]
                    changed_detail = changed_entry.entry_details[0]
                    self._assert_financial_truth(changed_entry, changed_detail)
                    for value in (
                        getattr(changed_detail, evidence_key),
                        changed_detail.source_detail_hash,
                        changed_entry.source_entry_hash,
                        changed.normalized_payload_hash,
                        changed.account_identifier_hash,
                        changed.entries[1].source_entry_hash,
                    ):
                        self._assert_sha256(value)

                    self.assertNotEqual(
                        getattr(baseline_detail, evidence_key),
                        getattr(changed_detail, evidence_key),
                    )
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

    def test_scheme_choice_discriminator_is_material_for_same_scalar(self) -> None:
        """Cd=BANK and Prtry=BANK remain distinct scheme-name choice values."""
        for role in ("ultimate_debtor", "ultimate_creditor"):
            for position in (0, 1):
                with self.subTest(role=role, position=position):
                    coded = copy.deepcopy(self.base_identifiers)
                    proprietary = copy.deepcopy(self.base_identifiers)
                    coded[position]["scheme"] = {"code": "BANK"}
                    proprietary[position]["scheme"] = {"proprietary": "BANK"}
                    left = parse_bank_statement_payload(
                        self._with_ultimate_party_identifiers(role, coded),
                        CAMT053_MESSAGE_DEFINITION,
                    )
                    right = parse_bank_statement_payload(
                        self._with_ultimate_party_identifiers(role, proprietary),
                        CAMT053_MESSAGE_DEFINITION,
                    )
                    left_entry = left.entries[0]
                    right_entry = right.entries[0]
                    left_detail = left_entry.entry_details[0]
                    right_detail = right_entry.entry_details[0]
                    evidence_key = self._evidence_key(role)
                    self._assert_financial_truth(left_entry, left_detail)
                    self._assert_financial_truth(right_entry, right_detail)
                    for value in (
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
                    self.assertNotEqual(
                        getattr(left_detail, evidence_key),
                        getattr(right_detail, evidence_key),
                    )
                    self.assertNotEqual(
                        left_detail.source_detail_hash,
                        right_detail.source_detail_hash,
                    )
                    self.assertNotEqual(
                        left_entry.source_entry_hash,
                        right_entry.source_entry_hash,
                    )
                    self.assertNotEqual(
                        left.normalized_payload_hash,
                        right.normalized_payload_hash,
                    )

    def test_other_identification_xml_layout_is_representation_only(self) -> None:
        """Whitespace inside SchmeNm changes source bytes, not admitted semantics."""
        for role in ("ultimate_debtor", "ultimate_creditor"):
            with self.subTest(role=role):
                baseline = self._with_ultimate_party_identifiers(
                    role, self.base_identifiers
                )
                formatted = baseline.replace(
                    b"                        <SchmeNm>\n",
                    b"                        <SchmeNm>\n                          \n",
                    1,
                )
                self.assertNotEqual(baseline, formatted)

                left = parse_bank_statement_payload(
                    baseline, CAMT053_MESSAGE_DEFINITION
                )
                right = parse_bank_statement_payload(
                    formatted, CAMT053_MESSAGE_DEFINITION
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

                self.assertNotEqual(
                    left.source_artifact_hash,
                    right.source_artifact_hash,
                )
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

    def test_other_identification_changes_reach_complete_correction_boundary(self) -> None:
        """Accepted ultimate-party Othr evidence cannot be silently replaced by replay."""
        for role in ("ultimate_debtor", "ultimate_creditor"):
            baseline = self._with_ultimate_party_identifiers(
                role, self.base_identifiers
            )
            reference = self._register_statement_account(baseline)
            store = MemoryArtifactStore()
            accept_bank_statement_evidence(
                self._command(
                    baseline,
                    reference,
                    f"{role}-organisation-other-baseline",
                ),
                posting.DATABASE_URL,
                self.case.policy.tenant_reference,
                artifact_store=store,
            )
            for semantic, identifiers in self.variants.items():
                with self.subTest(role=role, semantic=semantic):
                    changed = self._with_ultimate_party_identifiers(
                        role, identifiers
                    )
                    with self.assertRaisesRegex(
                        AccountingValidationError, _CORRECTION_ERROR
                    ):
                        accept_bank_statement_evidence(
                            self._command(
                                changed,
                                reference,
                                f"{role}-organisation-other-{semantic}",
                            ),
                            posting.DATABASE_URL,
                            self.case.policy.tenant_reference,
                            artifact_store=store,
                        )

    def test_buyer_projection_keeps_other_identification_non_reversible(self) -> None:
        """Rich Othr provenance changes internal evidence without leaking source IDs."""
        privacy_identifiers = copy.deepcopy(self.base_identifiers)
        privacy_identifiers[0]["id"] = "ULTIMATE-PRIVATE-ID-7781"
        privacy_identifiers[0]["scheme"] = {
            "proprietary": "ULTIMATE_PRIVATE_SCHEME_ALPHA"
        }
        privacy_identifiers[0]["issuer"] = "Ultimate Private Registry Alpha"
        privacy_identifiers[1]["id"] = "ULTIMATE-PRIVATE-ID-7782"
        privacy_identifiers[1]["scheme"] = {
            "proprietary": "ULTIMATE_PRIVATE_SCHEME_BETA"
        }
        privacy_identifiers[1]["issuer"] = "Ultimate Private Registry Beta"

        for role in ("ultimate_debtor", "ultimate_creditor"):
            with self.subTest(role=role):
                baseline = self._ingest_and_read_first_detail(
                    self._with_ultimate_party_identifiers(
                        role, self.base_identifiers
                    ),
                    f"{role}-organisation-other-public-baseline",
                )
                private = self._ingest_and_read_first_detail(
                    self._with_ultimate_party_identifiers(
                        role, privacy_identifiers
                    ),
                    f"{role}-organisation-other-private",
                )
                evidence_key = self._evidence_key(role)
                for projection in (baseline, private):
                    self._assert_sha256(projection[evidence_key])
                    self._assert_sha256(projection["source_detail_hash"])
                    self.assertEqual(
                        Decimal(str(projection["detail_amount"])),
                        Decimal("25000.00"),
                    )
                    self.assertEqual(projection["detail_currency_code"], "KRW")

                self.assertNotEqual(
                    baseline[evidence_key],
                    private[evidence_key],
                )
                self.assertNotEqual(
                    baseline["source_detail_hash"],
                    private["source_detail_hash"],
                )
                self.assertEqual(
                    self._public_projection(baseline, evidence_key),
                    self._public_projection(private, evidence_key),
                )

                serialized = json.dumps(private, sort_keys=True, default=str)
                for source_value in (
                    "ULTIMATE-PRIVATE-ID-7781",
                    "ULTIMATE_PRIVATE_SCHEME_ALPHA",
                    "Ultimate Private Registry Alpha",
                    "ULTIMATE-PRIVATE-ID-7782",
                    "ULTIMATE_PRIVATE_SCHEME_BETA",
                    "Ultimate Private Registry Beta",
                ):
                    self.assertNotIn(source_value, serialized)

    @classmethod
    def _variants(
        cls, base: list[dict[str, object]]
    ) -> dict[str, list[dict[str, object]]]:
        """Return independent first/repeated Othr value, choice, and presence variants."""
        variants: dict[str, list[dict[str, object]]] = {}

        first_id = copy.deepcopy(base)
        first_id[0]["id"] = "ULTIMATE-ORG-PRIMARY-002"
        variants["first-id-value"] = first_id

        first_scheme_value = copy.deepcopy(base)
        first_scheme_value[0]["scheme"] = {"code": "DUNS"}
        variants["first-scheme-value"] = first_scheme_value

        first_scheme_choice = copy.deepcopy(base)
        first_scheme_choice[0]["scheme"] = {"proprietary": "BANK"}
        variants["first-scheme-choice"] = first_scheme_choice

        first_scheme_proprietary = copy.deepcopy(base)
        first_scheme_proprietary[0]["scheme"] = {
            "proprietary": "ULTIMATE_ORG_SCHEME_ALPHA"
        }
        variants["first-scheme-proprietary-value"] = first_scheme_proprietary

        first_scheme_absent = copy.deepcopy(base)
        first_scheme_absent[0].pop("scheme")
        variants["first-scheme-absent"] = first_scheme_absent

        first_issuer_value = copy.deepcopy(base)
        first_issuer_value[0]["issuer"] = "Alternate Ultimate Relationship Registry"
        variants["first-issuer-value"] = first_issuer_value

        first_issuer_absent = copy.deepcopy(base)
        first_issuer_absent[0].pop("issuer")
        variants["first-issuer-absent"] = first_issuer_absent

        additional_id = copy.deepcopy(base)
        additional_id[1]["id"] = "ULTIMATE-ORG-ADDITIONAL-002"
        variants["additional-id-value"] = additional_id

        additional_scheme_value = copy.deepcopy(base)
        additional_scheme_value[1]["scheme"] = {"code": "DUNS"}
        variants["additional-scheme-value"] = additional_scheme_value

        additional_scheme_choice = copy.deepcopy(base)
        additional_scheme_choice[1]["scheme"] = {"proprietary": "BANK"}
        variants["additional-scheme-choice"] = additional_scheme_choice

        additional_scheme_proprietary = copy.deepcopy(base)
        additional_scheme_proprietary[1]["scheme"] = {
            "proprietary": "ULTIMATE_ORG_SCHEME_BETA"
        }
        variants["additional-scheme-proprietary-value"] = additional_scheme_proprietary

        additional_scheme_absent = copy.deepcopy(base)
        additional_scheme_absent[1].pop("scheme")
        variants["additional-scheme-absent"] = additional_scheme_absent

        additional_issuer_value = copy.deepcopy(base)
        additional_issuer_value[1]["issuer"] = "Alternate Ultimate Tax Registry"
        variants["additional-issuer-value"] = additional_issuer_value

        additional_issuer_absent = copy.deepcopy(base)
        additional_issuer_absent[1].pop("issuer")
        variants["additional-issuer-absent"] = additional_issuer_absent

        additional_removed = copy.deepcopy(base)
        additional_removed.pop()
        variants["additional-identifier-removed"] = additional_removed
        return variants

    def _with_ultimate_party_identifiers(
        self,
        role: str,
        identifiers: list[dict[str, object]],
    ) -> bytes:
        """Insert one ultimate role with repeated GenericOrganisationIdentification3."""
        marker = "              </Dbtr>\n            </RltdPties>"
        self.assertEqual(self.fixture.count(marker), 1)
        tag = self._xml_tag(role)
        other_xml = "".join(self._other_xml(identifier) for identifier in identifiers)
        replacement = (
            "              </Dbtr>\n"
            f"              <{tag}>\n"
            "                <Pty>\n"
            f"                  <Nm>{self.party_name}</Nm>\n"
            "                  <Id>\n"
            "                    <OrgId>\n"
            + other_xml
            + "                    </OrgId>\n"
            "                  </Id>\n"
            "                </Pty>\n"
            f"              </{tag}>\n"
            "            </RltdPties>"
        )
        return self.fixture.replace(marker, replacement, 1).encode("utf-8")

    @staticmethod
    def _other_xml(identifier: dict[str, object]) -> str:
        """Serialize one GenericOrganisationIdentification3 in schema order."""
        lines = [
            "                      <Othr>",
            f"                        <Id>{identifier['id']}</Id>",
        ]
        scheme = identifier.get("scheme")
        if scheme is not None:
            lines.append("                        <SchmeNm>")
            assert isinstance(scheme, dict)
            if "code" in scheme:
                lines.append(f"                          <Cd>{scheme['code']}</Cd>")
            elif "proprietary" in scheme:
                lines.append(
                    f"                          <Prtry>{scheme['proprietary']}</Prtry>"
                )
            else:
                raise AssertionError(f"unsupported scheme: {scheme!r}")
            lines.append("                        </SchmeNm>")
        issuer = identifier.get("issuer")
        if issuer is not None:
            lines.append(f"                        <Issr>{issuer}</Issr>")
        lines.append("                      </Othr>")
        return "\n".join(lines) + "\n"

    def _register_statement_account(self, payload: bytes) -> str:
        """Register only the statement-owner account required by supported ingest."""
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
        """Ingest on an isolated account and return the first transaction detail."""
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
        detail: dict[str, object], evidence_key: str
    ) -> dict[str, object]:
        """Remove only internal evidence identities before buyer-visible comparison."""
        projection = dict(detail)
        projection.pop(evidence_key)
        projection.pop("source_detail_hash")
        return projection

    def _command(
        self,
        payload: bytes,
        bank_account_reference: str,
        suffix: str,
    ) -> dict[str, object]:
        """Return one supported ingest command with a unique tenant replay key."""
        return {
            "tenant_reference": self.case.policy.tenant_reference,
            "bank_account_reference": bank_account_reference,
            "ingestion_idempotency_key": (
                f"ultimate-party-org-other-{suffix}-{uuid.uuid4().hex}"
            ),
            "message_definition_identifier": CAMT053_MESSAGE_DEFINITION,
            "statement_payload": payload.decode("utf-8"),
        }

    @staticmethod
    def _evidence_key(role: str) -> str:
        """Return the existing role-specific ultimate-party digest field."""
        if role == "ultimate_debtor":
            return "ultimate_debtor_evidence_hash"
        if role == "ultimate_creditor":
            return "ultimate_creditor_evidence_hash"
        raise AssertionError(f"unsupported ultimate role: {role}")

    @staticmethod
    def _xml_tag(role: str) -> str:
        """Map the test role to the registered camt.053 ultimate-party element."""
        if role == "ultimate_debtor":
            return "UltmtDbtr"
        if role == "ultimate_creditor":
            return "UltmtCdtr"
        raise AssertionError(f"unsupported ultimate role: {role}")

    def _assert_financial_truth(self, entry: object, detail: object) -> None:
        """Keep organisation identifiers independent from exact accounting values."""
        self.assertEqual(getattr(entry, "entry_amount"), Decimal("25000.00"))
        self.assertEqual(getattr(entry, "entry_currency_code"), "KRW")
        self.assertEqual(getattr(detail, "detail_amount"), Decimal("25000.00"))
        self.assertEqual(getattr(detail, "detail_currency_code"), "KRW")

    def _assert_sha256(self, value: object) -> None:
        """Require one canonical SHA-256 identity before equality comparisons."""
        self.assertIsInstance(value, str)
        self.assertRegex(value, _HASH_PATTERN)


if __name__ == "__main__":
    unittest.main()
