"""Review REDs for later repeated Contact13/Othr values on direct parties."""

from __future__ import annotations

import copy
import unittest

from accounting_information_platform import (
    AccountingValidationError,
    CAMT053_MESSAGE_DEFINITION,
    MemoryArtifactStore,
    accept_bank_statement_evidence,
    parse_bank_statement_payload,
)
from tests import (
    test_postgres_bank_statement_debtor_creditor_party_contact_details_evidence_red as contact_red,
)
from tests import test_postgres_posting as posting

_CORRECTION_ERROR = (
    r"^statement identity already exists with different entry evidence\. "
    r"Use an explicit correction contract, then retry ingest\.$"
)


class BankStatementDebtorCreditorPartyContactRepeatedOtherReviewRedTests(
    unittest.TestCase
):
    """Prove scalar semantics for later repeated direct-party Contact13/Othr items."""

    @classmethod
    def setUpClass(cls) -> None:
        """Reuse the canonical real-PostgreSQL integration fixture."""
        contact_red.BankStatementDebtorCreditorPartyContactDetailsEvidenceRedTests.setUpClass()

    def setUp(self) -> None:
        """Prepare the complete Contact13 helper with exception-safe nested cleanup."""
        self.case = contact_red.BankStatementDebtorCreditorPartyContactDetailsEvidenceRedTests(
            "setUp"
        )
        self.addCleanup(self.case.doCleanups)
        self.case.setUp()
        self.addCleanup(self.case.tearDown)
        self.variants = self._second_other_variants(self.case.base_contact)

    def test_second_other_scalar_values_and_optional_id_are_material(self) -> None:
        """Later Othr channel/id semantics affect both direct debtor and creditor identity."""
        for role in ("debtor", "creditor"):
            target_index = self.case.case._target_entry_index(role)
            untouched_index = 1 - target_index
            baseline = parse_bank_statement_payload(
                self.case._with_role_contact(role, self.case.base_contact),
                CAMT053_MESSAGE_DEFINITION,
            )
            baseline_entry = baseline.entries[target_index]
            baseline_detail = baseline_entry.entry_details[0]
            self.case._assert_financial_truth(role, baseline_entry, baseline_detail)
            self.case._assert_semantic_hashes(
                baseline,
                baseline_entry,
                baseline_detail,
                baseline.entries[untouched_index],
            )

            for semantic, contact in self.variants.items():
                with self.subTest(role=role, semantic=semantic):
                    changed = parse_bank_statement_payload(
                        self.case._with_role_contact(role, contact),
                        CAMT053_MESSAGE_DEFINITION,
                    )
                    changed_entry = changed.entries[target_index]
                    changed_detail = changed_entry.entry_details[0]
                    self.case._assert_financial_truth(role, changed_entry, changed_detail)
                    self.case._assert_semantic_hashes(
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

    def test_second_other_scalar_changes_reach_complete_correction_boundary(self) -> None:
        """Accepted later Othr scalar provenance cannot be silently replaced by replay."""
        for role in ("debtor", "creditor"):
            baseline_payload = self.case._with_role_contact(role, self.case.base_contact)
            reference = self.case.case._register_statement_account(baseline_payload)
            store = MemoryArtifactStore()
            accepted = accept_bank_statement_evidence(
                self.case.case._command(
                    baseline_payload,
                    reference,
                    f"{role}-party-contact-second-other-baseline",
                ),
                posting.DATABASE_URL,
                self.case.case.case.policy.tenant_reference,
                artifact_store=store,
            )
            self.assertFalse(accepted["replayed"])

            for semantic, contact in self.variants.items():
                with self.subTest(role=role, semantic=semantic):
                    with self.assertRaisesRegex(
                        AccountingValidationError,
                        _CORRECTION_ERROR,
                    ):
                        accept_bank_statement_evidence(
                            self.case.case._command(
                                self.case._with_role_contact(role, contact),
                                reference,
                                f"{role}-party-contact-second-other-{semantic}",
                            ),
                            posting.DATABASE_URL,
                            self.case.case.case.policy.tenant_reference,
                            artifact_store=store,
                        )

    @staticmethod
    def _second_other_variants(
        base: dict[str, object],
    ) -> dict[str, dict[str, object]]:
        """Change each scalar carried by the second repeated Othr independently."""
        channel_changed = copy.deepcopy(base)
        channel_contacts = channel_changed.get("other_contacts")
        if not isinstance(channel_contacts, list) or len(channel_contacts) != 2:
            raise AssertionError("review RED requires exactly two baseline Othr values")
        channel_contacts[1]["channel_type"] = "TELE"

        identification_changed = copy.deepcopy(base)
        identification_contacts = identification_changed.get("other_contacts")
        if not isinstance(identification_contacts, list) or len(identification_contacts) != 2:
            raise AssertionError("review RED requires exactly two baseline Othr values")
        identification_contacts[1]["identification"] = "swift-ops-2"

        identification_absent = copy.deepcopy(base)
        absent_contacts = identification_absent.get("other_contacts")
        if not isinstance(absent_contacts, list) or len(absent_contacts) != 2:
            raise AssertionError("review RED requires exactly two baseline Othr values")
        absent_contacts[1].pop("identification")

        return {
            "second-channel-type-value": channel_changed,
            "second-identification-value": identification_changed,
            "second-identification-absent": identification_absent,
        }


if __name__ == "__main__":
    unittest.main()
