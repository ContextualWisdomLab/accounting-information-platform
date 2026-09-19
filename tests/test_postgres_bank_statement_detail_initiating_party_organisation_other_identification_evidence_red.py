"""PostgreSQL REDs for initiating-party organisation other-identification evidence."""

from __future__ import annotations

import copy
import unittest
import uuid
from decimal import Decimal

from accounting_information_platform import (
    AccountingValidationError,
    CAMT053_MESSAGE_DEFINITION,
    MemoryArtifactStore,
    parse_bank_statement_payload,
)
from tests import (
    test_postgres_bank_statement_detail_initiating_party_identification_evidence_red as identity,
)
from tests import test_postgres_bank_statement_detail_initiating_party_evidence_red as initiating
from tests import test_postgres_posting as posting

_CORRECTION_ERROR = (
    r"^statement identity already exists with different entry evidence\. "
    r"Use an explicit correction contract, then retry ingest\.$"
)


class BankStatementDetailInitiatingPartyOrganisationOtherIdentificationEvidenceRedTests(
    unittest.TestCase
):
    """Retain repeated InitgPty OrgId/Othr semantics as private reconciliation evidence."""

    @classmethod
    def setUpClass(cls) -> None:
        """Reuse the canonical real-PostgreSQL integration fixture."""
        identity.BankStatementDetailInitiatingPartyIdentificationEvidenceRedTests.setUpClass()

    def setUp(self) -> None:
        """Prepare one isolated initiating-party helper and repeated Othr variants."""
        self.helper = identity.BankStatementDetailInitiatingPartyIdentificationEvidenceRedTests(
            "setUp"
        )
        self.addCleanup(self.helper.doCleanups)
        self.helper.setUp()
        self.addCleanup(self.helper.tearDown)
        self.base_identifiers = [
            {
                "id": "INITIATING-ORG-PRIMARY-001",
                "scheme": {"code": "BANK"},
                "issuer": "Initiating Relationship Registry",
            },
            {
                "id": "INITIATING-ORG-ADDITIONAL-001",
                "scheme": {"code": "BANK"},
                "issuer": "Initiating Tax Registry",
            },
        ]
        self.variants = self._variants(self.base_identifiers)

    def test_other_identification_fields_and_population_are_material_to_evidence(self) -> None:
        """Othr ID, scheme choice/value, issuer, and repeated population alter evidence."""
        baseline = parse_bank_statement_payload(
            self._payload(self.base_identifiers),
            CAMT053_MESSAGE_DEFINITION,
        )
        baseline_entry = baseline.entries[0]
        baseline_detail = baseline_entry.entry_details[0]
        self.helper._assert_financial_truth(baseline_entry, baseline_detail)
        for value in (
            getattr(baseline_detail, "initiating_party_evidence_hash"),
            baseline_detail.source_detail_hash,
            baseline_entry.source_entry_hash,
            baseline.normalized_payload_hash,
            baseline.account_identifier_hash,
            baseline.entries[1].source_entry_hash,
        ):
            self.helper._assert_sha256(value)

        for semantic, identifiers in self.variants.items():
            with self.subTest(semantic=semantic):
                changed = parse_bank_statement_payload(
                    self._payload(identifiers),
                    CAMT053_MESSAGE_DEFINITION,
                )
                changed_entry = changed.entries[0]
                changed_detail = changed_entry.entry_details[0]
                self.helper._assert_financial_truth(changed_entry, changed_detail)
                for value in (
                    getattr(changed_detail, "initiating_party_evidence_hash"),
                    changed_detail.source_detail_hash,
                    changed_entry.source_entry_hash,
                    changed.normalized_payload_hash,
                    changed.account_identifier_hash,
                    changed.entries[1].source_entry_hash,
                ):
                    self.helper._assert_sha256(value)
                self.assertNotEqual(
                    getattr(baseline_detail, "initiating_party_evidence_hash"),
                    getattr(changed_detail, "initiating_party_evidence_hash"),
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
        """Cd=BANK and Prtry=BANK remain distinct scheme-choice semantics at each Othr."""
        for position in (0, 1):
            with self.subTest(position=position):
                coded = copy.deepcopy(self.base_identifiers)
                proprietary = copy.deepcopy(self.base_identifiers)
                coded[position]["scheme"] = {"code": "BANK"}
                proprietary[position]["scheme"] = {"proprietary": "BANK"}
                left = parse_bank_statement_payload(
                    self._payload(coded),
                    CAMT053_MESSAGE_DEFINITION,
                )
                right = parse_bank_statement_payload(
                    self._payload(proprietary),
                    CAMT053_MESSAGE_DEFINITION,
                )
                left_entry = left.entries[0]
                right_entry = right.entries[0]
                left_detail = left_entry.entry_details[0]
                right_detail = right_entry.entry_details[0]
                self.helper._assert_financial_truth(left_entry, left_detail)
                self.helper._assert_financial_truth(right_entry, right_detail)
                for value in (
                    getattr(left_detail, "initiating_party_evidence_hash"),
                    getattr(right_detail, "initiating_party_evidence_hash"),
                    left_detail.source_detail_hash,
                    right_detail.source_detail_hash,
                    left_entry.source_entry_hash,
                    right_entry.source_entry_hash,
                    left.normalized_payload_hash,
                    right.normalized_payload_hash,
                ):
                    self.helper._assert_sha256(value)
                self.assertNotEqual(
                    getattr(left_detail, "initiating_party_evidence_hash"),
                    getattr(right_detail, "initiating_party_evidence_hash"),
                )
                self.assertNotEqual(left_detail.source_detail_hash, right_detail.source_detail_hash)
                self.assertNotEqual(left_entry.source_entry_hash, right_entry.source_entry_hash)
                self.assertNotEqual(left.normalized_payload_hash, right.normalized_payload_hash)

    def test_other_identification_layout_is_representation_only(self) -> None:
        """Whitespace inside SchmeNm changes source bytes, not admitted semantics."""
        baseline_payload = self._payload(self.base_identifiers)
        needle = b"                        <SchmeNm>\n"
        self.assertGreaterEqual(baseline_payload.count(needle), 2)
        formatted_payload = baseline_payload.replace(
            needle,
            b"                        <SchmeNm>\n                          \n",
            1,
        )
        self.assertNotEqual(baseline_payload, formatted_payload)

        baseline = parse_bank_statement_payload(
            baseline_payload,
            CAMT053_MESSAGE_DEFINITION,
        )
        formatted = parse_bank_statement_payload(
            formatted_payload,
            CAMT053_MESSAGE_DEFINITION,
        )
        baseline_entry = baseline.entries[0]
        formatted_entry = formatted.entries[0]
        baseline_detail = baseline_entry.entry_details[0]
        formatted_detail = formatted_entry.entry_details[0]
        for value in (
            baseline.source_artifact_hash,
            formatted.source_artifact_hash,
            getattr(baseline_detail, "initiating_party_evidence_hash"),
            getattr(formatted_detail, "initiating_party_evidence_hash"),
            baseline_detail.source_detail_hash,
            formatted_detail.source_detail_hash,
            baseline_entry.source_entry_hash,
            formatted_entry.source_entry_hash,
            baseline.normalized_payload_hash,
            formatted.normalized_payload_hash,
            baseline.account_identifier_hash,
            formatted.account_identifier_hash,
            baseline.entries[1].source_entry_hash,
            formatted.entries[1].source_entry_hash,
        ):
            self.helper._assert_sha256(value)
        self.assertNotEqual(baseline.source_artifact_hash, formatted.source_artifact_hash)
        self.assertEqual(
            getattr(baseline_detail, "initiating_party_evidence_hash"),
            getattr(formatted_detail, "initiating_party_evidence_hash"),
        )
        self.assertEqual(baseline_detail.source_detail_hash, formatted_detail.source_detail_hash)
        self.assertEqual(baseline_entry.source_entry_hash, formatted_entry.source_entry_hash)
        self.assertEqual(baseline.normalized_payload_hash, formatted.normalized_payload_hash)
        self.assertEqual(baseline.account_identifier_hash, formatted.account_identifier_hash)
        self.assertEqual(
            baseline.entries[1].source_entry_hash,
            formatted.entries[1].source_entry_hash,
        )

    def test_all_other_identification_variants_reach_correction_boundary(self) -> None:
        """Accepted repeated Othr evidence cannot be silently replaced by replay."""
        baseline_payload = self._payload(self.base_identifiers)
        for semantic, identifiers in self.variants.items():
            with self.subTest(semantic=semantic):
                reference = f"urn:cwl:bank_account:{uuid.uuid4().hex}"
                self.helper.helper._register_bank_account(reference)
                store = MemoryArtifactStore()
                initiating.accept_bank_statement_evidence(
                    self.helper.helper._command(
                        baseline_payload,
                        f"initiating-org-other-{semantic}-baseline",
                        reference,
                    ),
                    posting.DATABASE_URL,
                    self.helper.helper.case.policy.tenant_reference,
                    artifact_store=store,
                )
                with self.assertRaisesRegex(
                    AccountingValidationError,
                    _CORRECTION_ERROR,
                ):
                    initiating.accept_bank_statement_evidence(
                        self.helper.helper._command(
                            self._payload(identifiers),
                            f"initiating-org-other-{semantic}-changed",
                            reference,
                        ),
                        posting.DATABASE_URL,
                        self.helper.helper.case.policy.tenant_reference,
                        artifact_store=store,
                    )

    def test_buyer_projection_keeps_other_identification_non_reversible(self) -> None:
        """Buyer reads preserve Othr-sensitive hashes without exposing source semantics."""
        private_identifiers = copy.deepcopy(self.base_identifiers)
        private_identifiers[0] = {
            "id": "INITIATING-PRIVATE-ID-7781",
            "scheme": {"proprietary": "INITIATING_PRIVATE_SCHEME_ALPHA"},
            "issuer": "Initiating Private Registry Alpha",
        }
        private_identifiers[1] = {
            "id": "INITIATING-PRIVATE-ID-7782",
            "scheme": {"proprietary": "INITIATING_PRIVATE_SCHEME_BETA"},
            "issuer": "Initiating Private Registry Beta",
        }
        one_identifier = copy.deepcopy(self.base_identifiers[:1])
        payloads = {
            "baseline": self._payload(self.base_identifiers),
            "private": self._payload(private_identifiers),
            "single": self._payload(one_identifier),
        }
        entries = {
            label: self.helper._ingest_and_read_first_entry(payload, label)
            for label, payload in payloads.items()
        }
        evidence_key = "initiating_party_evidence_hash"
        for entry in entries.values():
            self.helper._assert_uuid(entry["bank_statement_entry_id"])
            self.helper._assert_sha256(entry["source_entry_hash"])
            self.assertEqual(Decimal(str(entry["entry_amount"])), Decimal("25000.00"))
            self.assertEqual(entry["entry_currency_code"], "KRW")
            details = entry["entry_details"]
            if not isinstance(details, list) or not details:
                raise AssertionError("expected the buyer entry to expose its first detail")
            detail = details[0]
            if not isinstance(detail, dict):
                raise AssertionError("expected the buyer detail to be a mapping")
            self.helper._assert_sha256(detail[evidence_key])
            self.helper._assert_sha256(detail["source_detail_hash"])
            self.assertEqual(Decimal(str(detail["detail_amount"])), Decimal("25000.00"))
            self.assertEqual(detail["detail_currency_code"], "KRW")

        baseline = entries["baseline"]
        baseline_detail = baseline["entry_details"][0]
        baseline_public = self.helper._public_entry_projection(baseline, evidence_key)
        for label in ("private", "single"):
            variant = entries[label]
            variant_detail = variant["entry_details"][0]
            self.assertNotEqual(
                baseline_detail[evidence_key],
                variant_detail[evidence_key],
            )
            self.assertNotEqual(
                baseline_detail["source_detail_hash"],
                variant_detail["source_detail_hash"],
            )
            self.assertNotEqual(baseline["source_entry_hash"], variant["source_entry_hash"])
            self.assertEqual(
                baseline_public,
                self.helper._public_entry_projection(variant, evidence_key),
            )

        buyer_values: set[object] = set()
        for entry in entries.values():
            buyer_values.update(
                self.helper._scalar_leaves(
                    self.helper._public_entry_projection(entry, evidence_key)
                )
            )
        for source_value in (
            "INITIATING-ORG-PRIMARY-001",
            "INITIATING-ORG-ADDITIONAL-001",
            "Initiating Relationship Registry",
            "Initiating Tax Registry",
            "INITIATING-PRIVATE-ID-7781",
            "INITIATING_PRIVATE_SCHEME_ALPHA",
            "Initiating Private Registry Alpha",
            "INITIATING-PRIVATE-ID-7782",
            "INITIATING_PRIVATE_SCHEME_BETA",
            "Initiating Private Registry Beta",
        ):
            self.assertNotIn(source_value, buyer_values)

    @classmethod
    def _variants(
        cls,
        base: list[dict[str, object]],
    ) -> dict[str, list[dict[str, object]]]:
        """Return independent first/repeated identifier, scheme, issuer, and population variants."""
        variants: dict[str, list[dict[str, object]]] = {}
        for position, prefix in ((0, "first"), (1, "additional")):
            changed_id = copy.deepcopy(base)
            changed_id[position]["id"] = f"INITIATING-ORG-{prefix.upper()}-002"
            variants[f"{prefix}-id-value"] = changed_id

            changed_scheme = copy.deepcopy(base)
            changed_scheme[position]["scheme"] = {"code": "DUNS"}
            variants[f"{prefix}-scheme-value"] = changed_scheme

            changed_choice = copy.deepcopy(base)
            changed_choice[position]["scheme"] = {"proprietary": "BANK"}
            variants[f"{prefix}-scheme-choice"] = changed_choice

            proprietary = copy.deepcopy(base)
            proprietary[position]["scheme"] = {
                "proprietary": f"INITIATING_ORG_SCHEME_{prefix.upper()}"
            }
            variants[f"{prefix}-scheme-proprietary-value"] = proprietary

            scheme_absent = copy.deepcopy(base)
            scheme_absent[position].pop("scheme")
            variants[f"{prefix}-scheme-absent"] = scheme_absent

            issuer_value = copy.deepcopy(base)
            issuer_value[position]["issuer"] = f"Alternate Initiating {prefix.title()} Registry"
            variants[f"{prefix}-issuer-value"] = issuer_value

            issuer_absent = copy.deepcopy(base)
            issuer_absent[position].pop("issuer")
            variants[f"{prefix}-issuer-absent"] = issuer_absent

        additional_removed = copy.deepcopy(base)
        additional_removed.pop()
        variants["additional-identifier-removed"] = additional_removed
        return variants

    def _payload(self, identifiers: list[dict[str, object]]) -> bytes:
        """Insert schema-shaped repeated InitgPty/Pty/Id/OrgId/Othr evidence."""
        if not identifiers:
            raise AssertionError("initiating organisation Othr RED requires an identifier")
        marker = (
            "            <RltdPties>\n"
            "              <Dbtr>"
        )
        self.assertEqual(self.helper.fixture.count(marker), 1)
        other_xml = "".join(self._other_xml(value) for value in identifiers)
        replacement = (
            "            <RltdPties>\n"
            "              <InitgPty>\n"
            "                <Pty>\n"
            f"                  <Nm>{self.helper.party_name}</Nm>\n"
            "                  <Id>\n"
            "                    <OrgId>\n"
            + other_xml
            + "                    </OrgId>\n"
            "                  </Id>\n"
            "                </Pty>\n"
            "              </InitgPty>\n"
            "              <Dbtr>"
        )
        return self.helper.fixture.replace(marker, replacement, 1).encode("utf-8")

    @classmethod
    def _other_xml(cls, value: dict[str, object]) -> str:
        """Serialize one GenericOrganisationIdentification3 in schema order."""
        identifier = str(value["id"])
        scheme = value.get("scheme")
        issuer = value.get("issuer")
        scheme_xml = "" if scheme is None else cls._scheme_xml(scheme)
        issuer_xml = "" if issuer is None else f"                        <Issr>{issuer}</Issr>\n"
        return (
            "                      <Othr>\n"
            f"                        <Id>{identifier}</Id>\n"
            + scheme_xml
            + issuer_xml
            + "                      </Othr>\n"
        )

    @staticmethod
    def _scheme_xml(scheme: object) -> str:
        """Serialize one OrganisationIdentificationSchemeName1Choice."""
        if not isinstance(scheme, dict):
            raise AssertionError(f"scheme must be a mapping, got {scheme!r}")
        if set(scheme) == {"code"}:
            return (
                "                        <SchmeNm>\n"
                f"                          <Cd>{scheme['code']}</Cd>\n"
                "                        </SchmeNm>\n"
            )
        if set(scheme) == {"proprietary"}:
            return (
                "                        <SchmeNm>\n"
                f"                          <Prtry>{scheme['proprietary']}</Prtry>\n"
                "                        </SchmeNm>\n"
            )
        raise AssertionError(f"unsupported scheme choice: {scheme!r}")


if __name__ == "__main__":
    unittest.main()
