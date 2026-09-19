"""PostgreSQL REDs for initiating-party residence-country evidence in camt.053 details."""

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
from tests import test_postgres_bank_statement_detail_initiating_party_evidence_red as initiating
from tests import (
    test_postgres_bank_statement_detail_initiating_party_identification_evidence_red
    as initiating_identity,
)
from tests import test_postgres_posting as posting

_CORRECTION_ERROR = (
    r"^statement identity already exists with different entry evidence\. "
    r"Use an explicit correction contract, then retry ingest\.$"
)


class BankStatementDetailInitiatingPartyCountryOfResidenceEvidenceRedTests(
    unittest.TestCase
):
    """Retain InitgPty/Pty/CtryOfRes as purpose-bound, non-reversible evidence."""

    @classmethod
    def setUpClass(cls) -> None:
        """Reuse the canonical real-PostgreSQL integration fixture."""
        initiating_identity.BankStatementDetailInitiatingPartyIdentificationEvidenceRedTests.setUpClass()

    def setUp(self) -> None:
        """Prepare one exception-safe initiating-party helper."""
        self.case = (
            initiating_identity.BankStatementDetailInitiatingPartyIdentificationEvidenceRedTests(
                "setUp"
            )
        )
        self.addCleanup(self.case.doCleanups)
        self.case.setUp()
        self.addCleanup(self.case.tearDown)

    def test_country_value_and_presence_are_material_initiating_evidence(self) -> None:
        """Residence-country value and optional presence must change provenance."""
        baseline = parse_bank_statement_payload(
            self._with_country("KR"),
            CAMT053_MESSAGE_DEFINITION,
        )
        baseline_entry = baseline.entries[0]
        baseline_detail = baseline_entry.entry_details[0]
        self.case._assert_financial_truth(baseline_entry, baseline_detail)
        self._assert_semantic_hashes(baseline, baseline_entry, baseline_detail)

        for semantic, country in (("country-value", "DE"), ("country-absent", None)):
            with self.subTest(semantic=semantic):
                changed = parse_bank_statement_payload(
                    self._with_country(country),
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

    def test_country_xml_layout_is_representation_only(self) -> None:
        """Whitespace after CtryOfRes changes raw bytes without changing semantics."""
        baseline_payload = self._with_country("KR")
        needle = b"                  <CtryOfRes>KR</CtryOfRes>\n"
        self.assertEqual(baseline_payload.count(needle), 1)
        formatted_payload = baseline_payload.replace(
            needle,
            needle + b"                  \n",
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

    def test_country_changes_reach_complete_correction_boundary(self) -> None:
        """Accepted initiating-party residence provenance cannot be silently replaced."""
        baseline_payload = self._with_country("KR")
        for semantic, country in (("country-value", "DE"), ("country-absent", None)):
            with self.subTest(semantic=semantic):
                bank_account_reference = f"urn:cwl:bank_account:{uuid.uuid4().hex}"
                self.case.helper._register_bank_account(bank_account_reference)
                store = MemoryArtifactStore()
                initiating.accept_bank_statement_evidence(
                    self.case.helper._command(
                        baseline_payload,
                        f"initiating-country-{semantic}-baseline",
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
                            self._with_country(country),
                            f"initiating-country-{semantic}-changed",
                            bank_account_reference,
                        ),
                        posting.DATABASE_URL,
                        self.case.helper.case.policy.tenant_reference,
                        artifact_store=store,
                    )

    def test_buyer_projection_keeps_country_non_reversible(self) -> None:
        """Residence country changes internal evidence without changing buyer data."""
        absent = self.case._ingest_and_read_first_entry(
            self._with_country(None),
            f"initiating-country-none-{uuid.uuid4().hex}",
        )
        rich = self.case._ingest_and_read_first_entry(
            self._with_country("DE"),
            f"initiating-country-rich-{uuid.uuid4().hex}",
        )
        evidence_key = "initiating_party_evidence_hash"

        for entry in (absent, rich):
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

        absent_detail = absent["entry_details"][0]
        rich_detail = rich["entry_details"][0]
        self.assertNotEqual(absent_detail[evidence_key], rich_detail[evidence_key])
        self.assertNotEqual(
            absent_detail["source_detail_hash"],
            rich_detail["source_detail_hash"],
        )
        self.assertNotEqual(absent["source_entry_hash"], rich["source_entry_hash"])
        absent_public = self.case._public_entry_projection(absent, evidence_key)
        rich_public = self.case._public_entry_projection(rich, evidence_key)
        self.assertEqual(absent_public, rich_public)
        for projection in (absent_public, rich_public):
            buyer_values = set(self.case._scalar_leaves(projection))
            self.assertNotIn("DE", buyer_values)

    def _with_country(self, country: str | None) -> bytes:
        """Return one InitgPty/Pty branch with optional CtryOfRes after Id."""
        payload = self.case._with_identified_party(
            "organisation",
            self.case.base_identifier,
        )
        if country is None:
            return payload

        marker = b"                  </Id>\n                </Pty>"
        if payload.count(marker) != 1:
            raise AssertionError("target initiating-party Id/Pty sequence must be unique")
        return payload.replace(
            marker,
            (
                b"                  </Id>\n"
                + f"                  <CtryOfRes>{country}</CtryOfRes>\n".encode(
                    "utf-8"
                )
                + b"                </Pty>"
            ),
            1,
        )

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
