"""PostgreSQL REDs for initiating-party PartyIdentification272 identity evidence."""

from __future__ import annotations

import re
import unittest
import uuid
from decimal import Decimal

from accounting_information_platform import (
    AccountingValidationError,
    CAMT053_MESSAGE_DEFINITION,
    MemoryArtifactStore,
    load_canonical_statement_fixture,
    parse_bank_statement_payload,
)
from tests import test_postgres_bank_statement_detail_initiating_party_evidence_red as initiating
from tests import test_postgres_posting as posting

_CORRECTION_ERROR = (
    r"^statement identity already exists with different entry evidence\. "
    r"Use an explicit correction contract, then retry ingest\.$"
)


class BankStatementDetailInitiatingPartyIdentificationEvidenceRedTests(unittest.TestCase):
    """Preserve InitgPty/Pty/Id evidence without promoting it to identity-master truth."""

    @classmethod
    def setUpClass(cls) -> None:
        """Reuse the canonical real-PostgreSQL integration fixture."""
        initiating.BankStatementDetailInitiatingPartyEvidenceRedTests.setUpClass()

    def setUp(self) -> None:
        """Prepare one isolated helper and identity variants on the canonical fixture."""
        self.helper = initiating.BankStatementDetailInitiatingPartyEvidenceRedTests("setUp")
        self.addCleanup(self.helper.doCleanups)
        self.helper.setUp()
        self.addCleanup(self.helper.tearDown)
        self.fixture = load_canonical_statement_fixture().decode("utf-8")
        self.party_name = "Initiating Identified Party"
        self.base_identifier = "INITIATING-PARTY-ID-001"
        self.changed_identifier = "INITIATING-PARTY-ID-002"

    def test_identifier_value_and_party52_choice_are_material_initiating_evidence(self) -> None:
        """Identifier value and OrgId|PrvtId discriminator alter only initiating provenance."""
        variants = (
            (
                "identifier-value",
                ("organisation", self.base_identifier),
                ("organisation", self.changed_identifier),
            ),
            (
                "identity-choice",
                ("organisation", self.base_identifier),
                ("person", self.base_identifier),
            ),
        )
        for semantic, left_identity, right_identity in variants:
            with self.subTest(semantic=semantic):
                left = parse_bank_statement_payload(
                    self._with_identified_party(*left_identity),
                    CAMT053_MESSAGE_DEFINITION,
                )
                right = parse_bank_statement_payload(
                    self._with_identified_party(*right_identity),
                    CAMT053_MESSAGE_DEFINITION,
                )
                left_entry = left.entries[0]
                right_entry = right.entries[0]
                left_detail = left_entry.entry_details[0]
                right_detail = right_entry.entry_details[0]

                self._assert_financial_truth(left_entry, left_detail)
                self._assert_financial_truth(right_entry, right_detail)
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
                    self._assert_sha256(value)

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
                self.assertEqual(
                    left.account_identifier_hash,
                    right.account_identifier_hash,
                )
                self.assertEqual(
                    left.entries[1].source_entry_hash,
                    right.entries[1].source_entry_hash,
                )

    def test_identifier_whitespace_is_representation_only(self) -> None:
        """Layout whitespace inside InitgPty/Pty/Id changes bytes, not semantic identity."""
        baseline_payload = self._with_identified_party(
            "organisation",
            self.base_identifier,
        )
        needle = (
            "                  <Id>\n"
            "                    <OrgId>\n"
        ).encode("utf-8")
        self.assertEqual(baseline_payload.count(needle), 1)
        formatted_payload = baseline_payload.replace(
            needle,
            (
                "                  <Id>\n"
                "                    \n"
                "                    <OrgId>\n"
            ).encode("utf-8"),
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
            self._assert_sha256(value)

        self.assertNotEqual(
            baseline.source_artifact_hash,
            formatted.source_artifact_hash,
        )
        self.assertEqual(
            getattr(baseline_detail, "initiating_party_evidence_hash"),
            getattr(formatted_detail, "initiating_party_evidence_hash"),
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
        self._assert_financial_truth(baseline_entry, baseline_detail)
        self._assert_financial_truth(formatted_entry, formatted_detail)

    def test_all_identity_variants_reach_explicit_correction_boundary(self) -> None:
        """Accepted initiating-party identity cannot be changed by silent replay."""
        baseline_payload = self._with_identified_party(
            "organisation",
            self.base_identifier,
        )
        variants = (
            (
                "identifier-value",
                self._with_identified_party(
                    "organisation",
                    self.changed_identifier,
                ),
            ),
            (
                "identity-choice",
                self._with_identified_party(
                    "person",
                    self.base_identifier,
                ),
            ),
        )
        for label, changed_payload in variants:
            with self.subTest(semantic=label):
                bank_account_reference = f"urn:cwl:bank_account:{uuid.uuid4().hex}"
                self.helper._register_bank_account(bank_account_reference)
                store = MemoryArtifactStore()
                initiating.accept_bank_statement_evidence(
                    self.helper._command(
                        baseline_payload,
                        f"initiating-identification-{label}-baseline",
                        bank_account_reference,
                    ),
                    posting.DATABASE_URL,
                    self.helper.case.policy.tenant_reference,
                    artifact_store=store,
                )
                with self.assertRaisesRegex(
                    AccountingValidationError,
                    _CORRECTION_ERROR,
                ):
                    initiating.accept_bank_statement_evidence(
                        self.helper._command(
                            changed_payload,
                            f"initiating-identification-{label}-changed",
                            bank_account_reference,
                        ),
                        posting.DATABASE_URL,
                        self.helper.case.policy.tenant_reference,
                        artifact_store=store,
                    )

    def test_buyer_projection_keeps_identity_non_reversible(self) -> None:
        """Buyer entries expose identity-sensitive digests without source identifiers."""
        payloads = {
            "organisation": self._with_identified_party(
                "organisation",
                self.base_identifier,
            ),
            "changed_identifier": self._with_identified_party(
                "organisation",
                self.changed_identifier,
            ),
            "person": self._with_identified_party(
                "person",
                self.base_identifier,
            ),
        }
        entries = {
            label: self._ingest_and_read_first_entry(payload, label)
            for label, payload in payloads.items()
        }
        evidence_key = "initiating_party_evidence_hash"

        for entry in entries.values():
            self._assert_sha256(entry["source_entry_hash"])
            self.assertEqual(
                Decimal(str(entry["entry_amount"])),
                Decimal("25000.00"),
            )
            self.assertEqual(entry["entry_currency_code"], "KRW")
            details = entry["entry_details"]
            self.assertIsInstance(details, list)
            if not isinstance(details, list) or not details:
                raise AssertionError("expected the buyer entry to expose its first detail")
            detail = details[0]
            self.assertIsInstance(detail, dict)
            if not isinstance(detail, dict):
                raise AssertionError("expected buyer entry detail to be a mapping")
            self._assert_sha256(detail[evidence_key])
            self._assert_sha256(detail["source_detail_hash"])
            self.assertEqual(
                Decimal(str(detail["detail_amount"])),
                Decimal("25000.00"),
            )
            self.assertEqual(detail["detail_currency_code"], "KRW")

        organisation = entries["organisation"]
        organisation_detail = organisation["entry_details"][0]
        for label in ("changed_identifier", "person"):
            variant = entries[label]
            variant_detail = variant["entry_details"][0]
            self.assertNotEqual(
                organisation_detail[evidence_key],
                variant_detail[evidence_key],
            )
            self.assertNotEqual(
                organisation_detail["source_detail_hash"],
                variant_detail["source_detail_hash"],
            )
            self.assertNotEqual(
                organisation["source_entry_hash"],
                variant["source_entry_hash"],
            )
            self.assertEqual(
                self._public_entry_projection(organisation, evidence_key),
                self._public_entry_projection(variant, evidence_key),
            )

        buyer_values: set[object] = set()
        for entry in entries.values():
            buyer_values.update(
                self._scalar_leaves(
                    self._public_entry_projection(entry, evidence_key)
                )
            )
        self.assertNotIn(self.base_identifier, buyer_values)
        self.assertNotIn(self.changed_identifier, buyer_values)

    def _with_identified_party(
        self,
        identity_choice: str,
        identifier: str,
    ) -> bytes:
        """Insert one schema-shaped InitgPty/Pty with PartyIdentification272/Id."""
        marker = (
            "            <RltdPties>\n"
            "              <Dbtr>"
        )
        self.assertEqual(self.fixture.count(marker), 1)
        replacement = (
            "            <RltdPties>\n"
            "              <InitgPty>\n"
            "                <Pty>\n"
            f"                  <Nm>{self.party_name}</Nm>\n"
            "                  <Id>\n"
            f"{self._identity_xml(identity_choice, identifier)}"
            "                  </Id>\n"
            "                </Pty>\n"
            "              </InitgPty>\n"
            "              <Dbtr>"
        )
        return self.fixture.replace(marker, replacement, 1).encode("utf-8")

    @staticmethod
    def _identity_xml(identity_choice: str, identifier: str) -> str:
        """Serialize one organisation-or-person Party52Choice identity."""
        if identity_choice == "organisation":
            return (
                "                    <OrgId>\n"
                "                      <Othr>\n"
                f"                        <Id>{identifier}</Id>\n"
                "                      </Othr>\n"
                "                    </OrgId>\n"
            )
        if identity_choice == "person":
            return (
                "                    <PrvtId>\n"
                "                      <Othr>\n"
                f"                        <Id>{identifier}</Id>\n"
                "                      </Othr>\n"
                "                    </PrvtId>\n"
            )
        raise AssertionError(f"unsupported Party52Choice: {identity_choice}")

    def _ingest_and_read_first_entry(
        self,
        payload: bytes,
        suffix: str,
    ) -> dict[str, object]:
        """Ingest on an isolated statement-owner account and return its first entry."""
        bank_account_reference = f"urn:cwl:bank_account:{uuid.uuid4().hex}"
        self.helper._register_bank_account(bank_account_reference)
        accepted = initiating.accept_bank_statement_evidence(
            self.helper._command(
                payload,
                f"initiating-identification-{suffix}",
                bank_account_reference,
            ),
            posting.DATABASE_URL,
            self.helper.case.policy.tenant_reference,
            artifact_store=MemoryArtifactStore(),
        )
        document = initiating.lookup_bank_statement_entries(
            posting.DATABASE_URL,
            self.helper.case.policy.tenant_reference,
            str(accepted["bank_statement_record_id"]),
        )
        entry = document["bank_statement_entries"][0]
        self.assertIsInstance(entry, dict)
        if not isinstance(entry, dict):
            raise AssertionError("expected the buyer read to expose an entry mapping")
        return entry

    @staticmethod
    def _public_entry_projection(
        entry: dict[str, object],
        evidence_key: str,
    ) -> dict[str, object]:
        """Remove only internal entry/detail evidence hashes before public comparison."""
        projection = dict(entry)
        projection.pop("source_entry_hash")
        details = projection.get("entry_details")
        if not isinstance(details, list):
            raise AssertionError("expected entry_details to be a list")
        public_details: list[dict[str, object]] = []
        for raw_detail in details:
            if not isinstance(raw_detail, dict):
                raise AssertionError("expected each entry detail to be a mapping")
            detail = dict(raw_detail)
            detail.pop(evidence_key)
            detail.pop("source_detail_hash")
            public_details.append(detail)
        projection["entry_details"] = public_details
        return projection

    @classmethod
    def _scalar_leaves(cls, value: object) -> tuple[object, ...]:
        """Collect scalar buyer values recursively across the complete entry projection."""
        if isinstance(value, dict):
            leaves: list[object] = []
            for child in value.values():
                leaves.extend(cls._scalar_leaves(child))
            return tuple(leaves)
        if isinstance(value, (list, tuple)):
            leaves = []
            for child in value:
                leaves.extend(cls._scalar_leaves(child))
            return tuple(leaves)
        if value is None:
            return ()
        return (value,)

    def _assert_financial_truth(self, entry: object, detail: object) -> None:
        """Keep identity mutations independent from exact transaction facts."""
        self.assertEqual(getattr(entry, "entry_amount"), Decimal("25000.00"))
        self.assertEqual(getattr(entry, "entry_currency_code"), "KRW")
        self.assertEqual(getattr(detail, "detail_amount"), Decimal("25000.00"))
        self.assertEqual(getattr(detail, "detail_currency_code"), "KRW")

    def _assert_sha256(self, value: object) -> None:
        """Require one canonical SHA-256 identity before equality comparisons."""
        self.assertIsInstance(value, str)
        if not isinstance(value, str) or re.fullmatch(
            r"sha256:[0-9a-f]{64}",
            value,
        ) is None:
            raise AssertionError(f"expected canonical sha256 digest, got {value!r}")


if __name__ == "__main__":
    unittest.main()
