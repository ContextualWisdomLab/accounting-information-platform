"""PostgreSQL REDs for initiating-party organisation identifier evidence."""

from __future__ import annotations

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


class BankStatementDetailInitiatingPartyOrganisationIdentifiersEvidenceRedTests(
    unittest.TestCase
):
    """Retain InitgPty AnyBIC/LEI as provenance, never identity-master authority."""

    @classmethod
    def setUpClass(cls) -> None:
        """Reuse the canonical real-PostgreSQL integration fixture."""
        identity.BankStatementDetailInitiatingPartyIdentificationEvidenceRedTests.setUpClass()

    def setUp(self) -> None:
        """Prepare one isolated initiating-party helper and identifier variants."""
        self.helper = identity.BankStatementDetailInitiatingPartyIdentificationEvidenceRedTests(
            "setUp"
        )
        self.addCleanup(self.helper.doCleanups)
        self.helper.setUp()
        self.addCleanup(self.helper.tearDown)
        self.base_bic = "DEUTDEFFXXX"
        self.changed_bic = "COBADEFFXXX"
        self.base_lei = "7LTWFZYICNSX8D621K86"
        self.changed_lei = "529900Z6KVD8Y83D7K60"

    def test_any_bic_and_lei_value_and_presence_are_material_initiating_evidence(self) -> None:
        """AnyBIC/LEI values and optional presence independently alter evidence identity."""
        variants = (
            (
                "any-bic-value",
                self.base_bic,
                self.base_lei,
                self.changed_bic,
                self.base_lei,
            ),
            ("any-bic-absent", self.base_bic, self.base_lei, None, self.base_lei),
            (
                "lei-value",
                self.base_bic,
                self.base_lei,
                self.base_bic,
                self.changed_lei,
            ),
            ("lei-absent", self.base_bic, self.base_lei, self.base_bic, None),
        )
        for semantic, left_bic, left_lei, right_bic, right_lei in variants:
            with self.subTest(semantic=semantic):
                left = parse_bank_statement_payload(
                    self._payload(left_bic, left_lei),
                    CAMT053_MESSAGE_DEFINITION,
                )
                right = parse_bank_statement_payload(
                    self._payload(right_bic, right_lei),
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
                    left.account_identifier_hash,
                    right.account_identifier_hash,
                    left.entries[1].source_entry_hash,
                    right.entries[1].source_entry_hash,
                ):
                    self.helper._assert_sha256(value)

                self.assertNotEqual(
                    getattr(left_detail, "initiating_party_evidence_hash"),
                    getattr(right_detail, "initiating_party_evidence_hash"),
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
                self.assertEqual(left.account_identifier_hash, right.account_identifier_hash)
                self.assertEqual(
                    left.entries[1].source_entry_hash,
                    right.entries[1].source_entry_hash,
                )

    def test_organisation_identifier_layout_is_representation_only(self) -> None:
        """Whitespace beside AnyBIC changes source bytes, not semantic evidence."""
        baseline_payload = self._payload(self.base_bic, self.base_lei)
        needle = f"                      <AnyBIC>{self.base_bic}</AnyBIC>\n".encode()
        self.assertEqual(baseline_payload.count(needle), 1)
        formatted_payload = baseline_payload.replace(
            needle,
            needle + b"                      \n",
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
        self.assertEqual(
            baseline_detail.source_detail_hash,
            formatted_detail.source_detail_hash,
        )
        self.assertEqual(baseline_entry.source_entry_hash, formatted_entry.source_entry_hash)
        self.assertEqual(
            baseline.normalized_payload_hash,
            formatted.normalized_payload_hash,
        )
        self.assertEqual(baseline.account_identifier_hash, formatted.account_identifier_hash)
        self.assertEqual(
            baseline.entries[1].source_entry_hash,
            formatted.entries[1].source_entry_hash,
        )
        self.helper._assert_financial_truth(baseline_entry, baseline_detail)
        self.helper._assert_financial_truth(formatted_entry, formatted_detail)

    def test_all_organisation_identifier_variants_reach_correction_boundary(self) -> None:
        """Accepted AnyBIC/LEI evidence cannot be silently replaced by replay."""
        baseline_payload = self._payload(self.base_bic, self.base_lei)
        variants = (
            ("any-bic-value", self.changed_bic, self.base_lei),
            ("any-bic-absent", None, self.base_lei),
            ("lei-value", self.base_bic, self.changed_lei),
            ("lei-absent", self.base_bic, None),
        )
        for semantic, changed_bic, changed_lei in variants:
            with self.subTest(semantic=semantic):
                reference = f"urn:cwl:bank_account:{uuid.uuid4().hex}"
                self.helper.helper._register_bank_account(reference)
                store = MemoryArtifactStore()
                initiating.accept_bank_statement_evidence(
                    self.helper.helper._command(
                        baseline_payload,
                        f"initiating-org-identifiers-{semantic}-baseline",
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
                            self._payload(changed_bic, changed_lei),
                            f"initiating-org-identifiers-{semantic}-changed",
                            reference,
                        ),
                        posting.DATABASE_URL,
                        self.helper.helper.case.policy.tenant_reference,
                        artifact_store=store,
                    )

    def test_buyer_projection_keeps_organisation_identifiers_non_reversible(self) -> None:
        """Buyer entries preserve identifier-sensitive hashes without exposing source IDs."""
        payloads = {
            "baseline": self._payload(self.base_bic, self.base_lei),
            "changed_bic": self._payload(self.changed_bic, self.base_lei),
            "absent_bic": self._payload(None, self.base_lei),
            "changed_lei": self._payload(self.base_bic, self.changed_lei),
            "absent_lei": self._payload(self.base_bic, None),
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
            self.assertIsInstance(details, list)
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
        for label in ("changed_bic", "absent_bic", "changed_lei", "absent_lei"):
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
            self.assertNotEqual(
                baseline["source_entry_hash"],
                variant["source_entry_hash"],
            )
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
            self.helper.base_identifier,
            self.base_bic,
            self.changed_bic,
            self.base_lei,
            self.changed_lei,
        ):
            self.assertNotIn(source_value, buyer_values)

    def _payload(self, any_bic: str | None, lei: str | None) -> bytes:
        """Insert schema-shaped InitgPty/Pty/Id/OrgId identifiers in the first detail."""
        marker = (
            "            <RltdPties>\n"
            "              <Dbtr>"
        )
        self.assertEqual(self.helper.fixture.count(marker), 1)
        any_bic_xml = (
            f"                      <AnyBIC>{any_bic}</AnyBIC>\n"
            if any_bic is not None
            else ""
        )
        lei_xml = (
            f"                      <LEI>{lei}</LEI>\n"
            if lei is not None
            else ""
        )
        replacement = (
            "            <RltdPties>\n"
            "              <InitgPty>\n"
            "                <Pty>\n"
            f"                  <Nm>{self.helper.party_name}</Nm>\n"
            "                  <Id>\n"
            "                    <OrgId>\n"
            + any_bic_xml
            + lei_xml
            + "                      <Othr>\n"
            f"                        <Id>{self.helper.base_identifier}</Id>\n"
            "                      </Othr>\n"
            "                    </OrgId>\n"
            "                  </Id>\n"
            "                </Pty>\n"
            "              </InitgPty>\n"
            "              <Dbtr>"
        )
        return self.helper.fixture.replace(marker, replacement, 1).encode("utf-8")


if __name__ == "__main__":
    unittest.main()
