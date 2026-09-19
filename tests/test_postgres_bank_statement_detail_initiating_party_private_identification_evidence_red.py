"""PostgreSQL REDs for complete initiating-party private identification evidence."""

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
from tests import test_postgres_bank_statement_detail_initiating_party_evidence_red as initiating
from tests import (
    test_postgres_bank_statement_detail_initiating_party_identification_evidence_red
    as initiating_identity,
)
from tests import (
    test_postgres_bank_statement_detail_ultimate_party_private_identification_evidence_red
    as ultimate_private,
)
from tests import test_postgres_posting as posting

_CORRECTION_ERROR = (
    r"^statement identity already exists with different entry evidence\. "
    r"Use an explicit correction contract, then retry ingest\.$"
)


class BankStatementDetailInitiatingPartyPrivateIdentificationEvidenceRedTests(
    unittest.TestCase
):
    """Retain InitgPty PersonIdentification18 as non-reversible provenance only."""

    @classmethod
    def setUpClass(cls) -> None:
        """Reuse the canonical real-PostgreSQL integration fixture."""
        initiating_identity.BankStatementDetailInitiatingPartyIdentificationEvidenceRedTests.setUpClass()

    def setUp(self) -> None:
        """Prepare one exception-safe initiating-party fixture and private variants."""
        self.case = (
            initiating_identity.BankStatementDetailInitiatingPartyIdentificationEvidenceRedTests(
                "setUp"
            )
        )
        self.addCleanup(self.case.doCleanups)
        self.case.setUp()
        self.addCleanup(self.case.tearDown)
        self.party_name = "Initiating Private Person"
        self.base_private: dict[str, object] = {
            "date_and_place_of_birth": {
                "birth_date": "1980-05-17",
                "province_of_birth": "Seoul",
                "city_of_birth": "Seoul",
                "country_of_birth": "KR",
            },
            "identifiers": [
                {
                    "id": "INITIATING-PERSON-PRIMARY-001",
                    "scheme": {"code": "CCPT"},
                    "issuer": "Initiating Passport Authority",
                },
                {
                    "id": "INITIATING-PERSON-ADDITIONAL-001",
                    "scheme": {"code": "NIDN"},
                    "issuer": "Initiating National Identity Registry",
                },
            ],
        }
        contract = (
            ultimate_private.BankStatementDetailUltimatePartyPrivateIdentificationEvidenceRedTests
        )
        self.variants = contract._variants(self.base_private)

    def test_private_fields_choices_presence_and_repetition_are_material(self) -> None:
        """Birth and repeated private identifiers must bind initiating-party evidence."""
        baseline = parse_bank_statement_payload(
            self._with_private(self.base_private),
            CAMT053_MESSAGE_DEFINITION,
        )
        baseline_entry = baseline.entries[0]
        baseline_detail = baseline_entry.entry_details[0]
        self.case._assert_financial_truth(baseline_entry, baseline_detail)
        self._assert_semantic_hashes(baseline, baseline_entry, baseline_detail)

        for semantic, private in self.variants.items():
            with self.subTest(semantic=semantic):
                changed = parse_bank_statement_payload(
                    self._with_private(private),
                    CAMT053_MESSAGE_DEFINITION,
                )
                changed_entry = changed.entries[0]
                changed_detail = changed_entry.entry_details[0]
                self.case._assert_financial_truth(changed_entry, changed_detail)
                self._assert_semantic_hashes(changed, changed_entry, changed_detail)

                self.assertNotEqual(
                    baseline_detail.initiating_party_evidence_hash,
                    changed_detail.initiating_party_evidence_hash,
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

    def test_private_scheme_whitespace_is_representation_only(self) -> None:
        """Whitespace inside InitgPty private SchmeNm changes bytes, not semantics."""
        baseline_payload = self._with_private(self.base_private)
        needle = b"                        <SchmeNm>\n"
        self.assertGreaterEqual(baseline_payload.count(needle), 1)
        formatted_payload = baseline_payload.replace(
            needle,
            needle + b"                          \n",
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
        self.case._assert_financial_truth(baseline_entry, baseline_detail)
        self.case._assert_financial_truth(formatted_entry, formatted_detail)

        for value in (
            baseline.source_artifact_hash,
            formatted.source_artifact_hash,
            baseline_detail.initiating_party_evidence_hash,
            formatted_detail.initiating_party_evidence_hash,
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
            self.case._assert_sha256(value)

        self.assertNotEqual(baseline.source_artifact_hash, formatted.source_artifact_hash)
        self.assertEqual(
            baseline_detail.initiating_party_evidence_hash,
            formatted_detail.initiating_party_evidence_hash,
        )
        self.assertEqual(
            baseline_detail.source_detail_hash,
            formatted_detail.source_detail_hash,
        )
        self.assertEqual(
            baseline_entry.source_entry_hash,
            formatted_entry.source_entry_hash,
        )
        self.assertEqual(
            baseline.normalized_payload_hash,
            formatted.normalized_payload_hash,
        )
        self.assertEqual(
            baseline.account_identifier_hash,
            formatted.account_identifier_hash,
        )
        self.assertEqual(
            baseline.entries[1].source_entry_hash,
            formatted.entries[1].source_entry_hash,
        )

    def test_every_private_material_variant_reaches_correction_boundary(self) -> None:
        """Accepted initiating-person provenance cannot be silently replaced by replay."""
        baseline_payload = self._with_private(self.base_private)
        for semantic, private in self.variants.items():
            with self.subTest(semantic=semantic):
                bank_account_reference = f"urn:cwl:bank_account:{uuid.uuid4().hex}"
                self.case.helper._register_bank_account(bank_account_reference)
                store = MemoryArtifactStore()
                initiating.accept_bank_statement_evidence(
                    self.case.helper._command(
                        baseline_payload,
                        f"initiating-private-{semantic}-baseline",
                        bank_account_reference,
                    ),
                    posting.DATABASE_URL,
                    self.case.helper.case.policy.tenant_reference,
                    artifact_store=store,
                )
                with self.assertRaisesRegex(
                    AccountingValidationError,
                    _CORRECTION_ERROR,
                ):
                    initiating.accept_bank_statement_evidence(
                        self.case.helper._command(
                            self._with_private(private),
                            f"initiating-private-{semantic}-changed",
                            bank_account_reference,
                        ),
                        posting.DATABASE_URL,
                        self.case.helper.case.policy.tenant_reference,
                        artifact_store=store,
                    )

    def test_buyer_projection_keeps_private_source_values_non_reversible(self) -> None:
        """Private source values may alter evidence identity but never buyer fields."""
        sensitive = copy.deepcopy(self.base_private)
        birth = sensitive.get("date_and_place_of_birth")
        if not isinstance(birth, dict):
            raise AssertionError("date_and_place_of_birth must be a mapping")
        birth.update(
            {
                "birth_date": "1977-11-03",
                "province_of_birth": "Canterbury",
                "city_of_birth": "Christchurch",
                "country_of_birth": "NZ",
            }
        )
        identifiers = sensitive.get("identifiers")
        if not isinstance(identifiers, list) or len(identifiers) < 2:
            raise AssertionError("expected two private identifiers")
        first = identifiers[0]
        second = identifiers[1]
        if not isinstance(first, dict) or not isinstance(second, dict):
            raise AssertionError("private identifiers must be mappings")
        first.update(
            {
                "id": "INITIATING-PRIVATE-PASSPORT-7781",
                "scheme": {"proprietary": "INITIATING_PRIVATE_SCHEME_ALPHA"},
                "issuer": "Initiating Private Passport Registry",
            }
        )
        second.update(
            {
                "id": "INITIATING-PRIVATE-NATIONAL-7782",
                "scheme": {"proprietary": "INITIATING_PRIVATE_SCHEME_BETA"},
                "issuer": "Initiating Private National Registry",
            }
        )
        sensitive_values = set(self.case._scalar_leaves(sensitive))

        baseline = self.case._ingest_and_read_first_entry(
            self._with_private(self.base_private),
            f"initiating-private-public-baseline-{uuid.uuid4().hex}",
        )
        private = self.case._ingest_and_read_first_entry(
            self._with_private(sensitive),
            f"initiating-private-sensitive-{uuid.uuid4().hex}",
        )
        evidence_key = "initiating_party_evidence_hash"
        for entry in (baseline, private):
            self.case._assert_uuid(entry["bank_statement_entry_id"])
            self.case._assert_sha256(entry["source_entry_hash"])
            self.assertEqual(Decimal(str(entry["entry_amount"])), Decimal("25000.00"))
            self.assertEqual(entry["entry_currency_code"], "KRW")
            details = entry.get("entry_details")
            if not isinstance(details, list) or not details:
                raise AssertionError("expected buyer entry to expose first detail")
            detail = details[0]
            if not isinstance(detail, dict):
                raise AssertionError("expected buyer entry detail mapping")
            self.case._assert_sha256(detail[evidence_key])
            self.case._assert_sha256(detail["source_detail_hash"])
            self.assertEqual(Decimal(str(detail["detail_amount"])), Decimal("25000.00"))
            self.assertEqual(detail["detail_currency_code"], "KRW")

        baseline_detail = baseline["entry_details"][0]
        private_detail = private["entry_details"][0]
        self.assertNotEqual(baseline_detail[evidence_key], private_detail[evidence_key])
        self.assertNotEqual(
            baseline_detail["source_detail_hash"],
            private_detail["source_detail_hash"],
        )
        self.assertNotEqual(baseline["source_entry_hash"], private["source_entry_hash"])
        baseline_public = self.case._public_entry_projection(baseline, evidence_key)
        private_public = self.case._public_entry_projection(private, evidence_key)
        self.assertEqual(baseline_public, private_public)
        buyer_values = set(self.case._scalar_leaves(private_public))
        self.assertTrue(sensitive_values.isdisjoint(buyer_values))

    def _with_private(self, private: dict[str, object]) -> bytes:
        """Insert one schema-shaped InitgPty/Pty carrying PersonIdentification18."""
        marker = "            <RltdPties>\n              <Dbtr>"
        if self.case.fixture.count(marker) != 1:
            raise AssertionError("target transaction-party marker must be unique")
        contract = (
            ultimate_private.BankStatementDetailUltimatePartyPrivateIdentificationEvidenceRedTests
        )
        replacement = (
            "            <RltdPties>\n"
            "              <InitgPty>\n"
            "                <Pty>\n"
            f"                  <Nm>{self.party_name}</Nm>\n"
            "                  <Id>\n"
            f"{contract._private_xml(private)}"
            "                  </Id>\n"
            "                </Pty>\n"
            "              </InitgPty>\n"
            "              <Dbtr>"
        )
        return self.case.fixture.replace(marker, replacement, 1).encode("utf-8")

    def _assert_semantic_hashes(self, statement: object, entry: object, detail: object) -> None:
        """Require canonical identities before materiality or stability comparisons."""
        for value in (
            getattr(detail, "initiating_party_evidence_hash"),
            getattr(detail, "source_detail_hash"),
            getattr(entry, "source_entry_hash"),
            getattr(statement, "normalized_payload_hash"),
            getattr(statement, "account_identifier_hash"),
            getattr(statement.entries[1], "source_entry_hash"),
        ):
            self.case._assert_sha256(value)


if __name__ == "__main__":
    unittest.main()
