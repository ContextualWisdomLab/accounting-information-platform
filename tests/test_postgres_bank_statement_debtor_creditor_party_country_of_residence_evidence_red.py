"""PostgreSQL REDs for direct debtor/creditor PartyIdentification272 residence-country evidence."""

from __future__ import annotations

import json
import unittest
import uuid
from decimal import Decimal

from accounting_information_platform import (
    AccountingValidationError,
    CAMT053_MESSAGE_DEFINITION,
    MemoryArtifactStore,
    accept_bank_statement_evidence,
    parse_bank_statement_payload,
)
from tests import (
    test_postgres_bank_statement_debtor_creditor_party_choice_evidence_red as party_choice,
)
from tests import test_postgres_posting as posting

_CORRECTION_ERROR = (
    r"^statement identity already exists with different entry evidence\. "
    r"Use an explicit correction contract, then retry ingest\.$"
)


class BankStatementDebtorCreditorPartyCountryOfResidenceEvidenceRedTests(
    unittest.TestCase
):
    """Retain direct-party CtryOfRes as purpose-bound non-reversible evidence."""

    @classmethod
    def setUpClass(cls) -> None:
        """Reuse the canonical real-PostgreSQL integration fixture."""
        party_choice.BankStatementDebtorCreditorPartyChoiceEvidenceRedTests.setUpClass()

    def setUp(self) -> None:
        """Prepare one direct-party helper with exception-safe nested cleanup."""
        self.case = party_choice.BankStatementDebtorCreditorPartyChoiceEvidenceRedTests(
            "setUp"
        )
        self.addCleanup(self.case.doCleanups)
        self.case.setUp()
        self.addCleanup(self.case.tearDown)
        self.party_name = "Direct Party Residence Evidence"

    def test_country_value_and_presence_are_material_for_both_roles(self) -> None:
        """CtryOfRes value and presence change direct-party evidence, not accounting facts."""
        for role in ("debtor", "creditor"):
            target_index = self.case._target_entry_index(role)
            untouched_index = 1 - target_index
            baseline = parse_bank_statement_payload(
                self._with_role_country(role, "KR"),
                CAMT053_MESSAGE_DEFINITION,
            )
            baseline_entry = baseline.entries[target_index]
            baseline_detail = baseline_entry.entry_details[0]
            self._assert_financial_truth(role, baseline_entry, baseline_detail)
            self._assert_semantic_hashes(
                baseline,
                baseline_entry,
                baseline_detail,
                baseline.entries[untouched_index],
            )

            for semantic, country in (("country-value", "DE"), ("country-absent", None)):
                with self.subTest(role=role, semantic=semantic):
                    changed = parse_bank_statement_payload(
                        self._with_role_country(role, country),
                        CAMT053_MESSAGE_DEFINITION,
                    )
                    changed_entry = changed.entries[target_index]
                    changed_detail = changed_entry.entry_details[0]
                    self._assert_financial_truth(role, changed_entry, changed_detail)
                    self._assert_semantic_hashes(
                        changed,
                        changed_entry,
                        changed_detail,
                        changed.entries[untouched_index],
                    )

                    self.assertNotEqual(
                        baseline_entry.counterparty_evidence_hash,
                        changed_entry.counterparty_evidence_hash,
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
                        baseline.entries[untouched_index].source_entry_hash,
                        changed.entries[untouched_index].source_entry_hash,
                    )

    def test_country_xml_layout_is_representation_only_for_both_roles(self) -> None:
        """Whitespace around CtryOfRes changes raw bytes without changing semantics."""
        needle = b"                  <CtryOfRes>KR</CtryOfRes>\n"
        whitespace = b"                  \n"

        for role in ("debtor", "creditor"):
            with self.subTest(role=role):
                baseline_payload = self._with_role_country(role, "KR")
                self.assertEqual(baseline_payload.count(needle), 1)
                formatted_payload = baseline_payload.replace(
                    needle,
                    needle + whitespace,
                    1,
                )
                self.assertNotEqual(baseline_payload, formatted_payload)

                left = parse_bank_statement_payload(
                    baseline_payload,
                    CAMT053_MESSAGE_DEFINITION,
                )
                right = parse_bank_statement_payload(
                    formatted_payload,
                    CAMT053_MESSAGE_DEFINITION,
                )
                target_index = self.case._target_entry_index(role)
                left_entry = left.entries[target_index]
                right_entry = right.entries[target_index]
                left_detail = left_entry.entry_details[0]
                right_detail = right_entry.entry_details[0]
                self._assert_financial_truth(role, left_entry, left_detail)
                self._assert_financial_truth(role, right_entry, right_detail)

                for value in (
                    left.source_artifact_hash,
                    right.source_artifact_hash,
                    left_entry.counterparty_evidence_hash,
                    right_entry.counterparty_evidence_hash,
                    left_detail.source_detail_hash,
                    right_detail.source_detail_hash,
                    left_entry.source_entry_hash,
                    right_entry.source_entry_hash,
                    left.normalized_payload_hash,
                    right.normalized_payload_hash,
                ):
                    self.case._assert_sha256(value)

                self.assertNotEqual(left.source_artifact_hash, right.source_artifact_hash)
                self.assertEqual(
                    left_entry.counterparty_evidence_hash,
                    right_entry.counterparty_evidence_hash,
                )
                self.assertEqual(
                    left_detail.source_detail_hash,
                    right_detail.source_detail_hash,
                )
                self.assertEqual(left_entry.source_entry_hash, right_entry.source_entry_hash)
                self.assertEqual(
                    left.normalized_payload_hash,
                    right.normalized_payload_hash,
                )

    def test_country_changes_reach_complete_correction_boundary(self) -> None:
        """Accepted direct-party residence provenance cannot be silently replaced."""
        for role in ("debtor", "creditor"):
            baseline_payload = self._with_role_country(role, "KR")
            reference = self.case._register_statement_account(baseline_payload)
            store = MemoryArtifactStore()
            accepted = accept_bank_statement_evidence(
                self.case._command(
                    baseline_payload,
                    reference,
                    f"{role}-party-country-baseline",
                ),
                posting.DATABASE_URL,
                self.case.case.policy.tenant_reference,
                artifact_store=store,
            )
            self.assertFalse(accepted["replayed"])

            for semantic, country in (("country-value", "DE"), ("country-absent", None)):
                with self.subTest(role=role, semantic=semantic):
                    with self.assertRaisesRegex(
                        AccountingValidationError,
                        _CORRECTION_ERROR,
                    ):
                        accept_bank_statement_evidence(
                            self.case._command(
                                self._with_role_country(role, country),
                                reference,
                                f"{role}-party-country-{semantic}",
                            ),
                            posting.DATABASE_URL,
                            self.case.case.policy.tenant_reference,
                            artifact_store=store,
                        )

    def test_buyer_projection_keeps_residence_country_non_reversible(self) -> None:
        """Residence country changes internal evidence without adding tenant-visible PII."""
        for role in ("debtor", "creditor"):
            with self.subTest(role=role):
                baseline = self.case._ingest_and_read_target_entry(
                    role,
                    self._with_role_country(role, None),
                    f"{role}-party-country-none-{uuid.uuid4().hex}",
                )
                rich = self.case._ingest_and_read_target_entry(
                    role,
                    self._with_role_country(role, "DE"),
                    f"{role}-party-country-rich-{uuid.uuid4().hex}",
                )

                for projection in (baseline, rich):
                    self.case._assert_sha256(projection["counterparty_evidence_hash"])
                    self.case._assert_sha256(projection["source_entry_hash"])
                    self.assertEqual(
                        Decimal(str(projection["entry_amount"])),
                        self.case._expected_entry_amount(role),
                    )
                    self.assertEqual(projection["entry_currency_code"], "KRW")
                    first_detail = projection["entry_details"][0]
                    self.case._assert_sha256(first_detail["source_detail_hash"])
                    self.assertEqual(
                        Decimal(str(first_detail["detail_amount"])),
                        self.case._expected_detail_amount(role),
                    )
                    self.assertEqual(first_detail["detail_currency_code"], "KRW")

                self.assertNotEqual(
                    baseline["counterparty_evidence_hash"],
                    rich["counterparty_evidence_hash"],
                )
                self.assertNotEqual(
                    baseline["entry_details"][0]["source_detail_hash"],
                    rich["entry_details"][0]["source_detail_hash"],
                )
                self.assertNotEqual(baseline["source_entry_hash"], rich["source_entry_hash"])
                self.assertEqual(
                    self._public_projection(baseline),
                    self._public_projection(rich),
                )
                self.assertNotIn("DE", json.dumps(rich, sort_keys=True, default=str))

    def _with_role_country(self, role: str, country: str | None) -> bytes:
        """Return one direct debtor/creditor Pty branch with optional CtryOfRes."""
        payload = self.case._with_role_party(role, "Pty", self.party_name)
        if country is None:
            return payload

        name_marker = f"                  <Nm>{self.party_name}</Nm>\n".encode("utf-8")
        if payload.count(name_marker) != 1:
            raise AssertionError("target direct-party name must be unique")
        return payload.replace(
            name_marker,
            name_marker + f"                  <CtryOfRes>{country}</CtryOfRes>\n".encode("utf-8"),
            1,
        )

    def _assert_financial_truth(self, role: str, entry: object, detail: object) -> None:
        """Pin source party provenance away from authoritative amount and currency truth."""
        self.assertEqual(entry.entry_amount, self.case._expected_entry_amount(role))
        self.assertEqual(entry.entry_currency_code, "KRW")
        self.assertEqual(detail.detail_amount, self.case._expected_detail_amount(role))
        self.assertEqual(detail.detail_currency_code, "KRW")

    def _assert_semantic_hashes(
        self,
        statement: object,
        entry: object,
        detail: object,
        untouched_entry: object,
    ) -> None:
        """Require canonical evidence identities before any equality or inequality claim."""
        for value in (
            statement.source_artifact_hash,
            statement.account_identifier_hash,
            statement.normalized_payload_hash,
            entry.counterparty_evidence_hash,
            entry.source_entry_hash,
            detail.source_detail_hash,
            untouched_entry.source_entry_hash,
        ):
            self.case._assert_sha256(value)

    @staticmethod
    def _public_projection(entry: dict[str, object]) -> dict[str, object]:
        """Remove only server/internal evidence identifiers before privacy comparison."""
        projection = dict(entry)
        projection.pop("bank_statement_entry_id")
        projection.pop("counterparty_evidence_hash")
        projection.pop("source_entry_hash")
        projection["entry_details"] = [
            {
                key: value
                for key, value in dict(detail).items()
                if key != "source_detail_hash"
            }
            for detail in projection["entry_details"]
        ]
        return projection


if __name__ == "__main__":
    unittest.main()
