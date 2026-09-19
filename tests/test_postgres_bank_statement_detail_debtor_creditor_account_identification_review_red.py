"""Focused REDs for CashAccount40 Id presence and account-local whitespace."""

from __future__ import annotations

import unittest

from tests import (
    test_postgres_bank_statement_debtor_creditor_account_identification_choice_evidence_red as account,
)


class BankStatementDetailDebtorCreditorAccountIdentificationReviewRedTests(
    unittest.TestCase
):
    """Extend the established role-specific account-evidence contract only where uncovered."""

    @classmethod
    def setUpClass(cls) -> None:
        """Reuse the canonical real-PostgreSQL integration fixture."""
        account.BankStatementDebtorCreditorAccountIdentificationChoiceEvidenceRedTests.setUpClass()

    def setUp(self) -> None:
        """Prepare one isolated established account-evidence helper."""
        self.helper = (
            account.BankStatementDebtorCreditorAccountIdentificationChoiceEvidenceRedTests(
                "setUp"
            )
        )
        self.addCleanup(self.helper.doCleanups)
        self.helper.setUp()
        self.addCleanup(self.helper.tearDown)
        self.baseline_identification = self.helper._other_identification(
            "ACCT-001",
            scheme_kind="Prtry",
            scheme_value="LOCAL",
            issuer="BANK-A",
        )

    def test_cash_account_present_with_id_absent_is_role_specific_material_evidence(self) -> None:
        """Optional CashAccount40.Id changes the role account digest without changing owner truth."""
        for role in ("debtor", "creditor"):
            with self.subTest(role=role):
                baseline_payload = self.helper._with_role_account(
                    role,
                    self.baseline_identification,
                )
                id_absent_payload = self.helper._with_role_account(role, "")
                baseline = account.parse_bank_statement_payload(
                    baseline_payload,
                    account.CAMT053_MESSAGE_DEFINITION,
                )
                changed = account.parse_bank_statement_payload(
                    id_absent_payload,
                    account.CAMT053_MESSAGE_DEFINITION,
                )
                target_index = self.helper._target_entry_index(role)
                untouched_index = 1 - target_index
                baseline_entry = baseline.entries[target_index]
                changed_entry = changed.entries[target_index]
                baseline_detail = baseline_entry.entry_details[0]
                changed_detail = changed_entry.entry_details[0]
                digest_key = f"{role}_account_evidence_hash"

                for value in (
                    getattr(baseline_detail, digest_key),
                    getattr(changed_detail, digest_key),
                    baseline_detail.source_detail_hash,
                    changed_detail.source_detail_hash,
                    baseline_entry.source_entry_hash,
                    changed_entry.source_entry_hash,
                    baseline.normalized_payload_hash,
                    changed.normalized_payload_hash,
                    baseline.account_identifier_hash,
                    changed.account_identifier_hash,
                ):
                    self.helper._assert_sha256(value)

                self.assertNotEqual(
                    getattr(baseline_detail, digest_key),
                    getattr(changed_detail, digest_key),
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
                self.assertEqual(
                    baseline_detail.detail_amount,
                    self.helper._expected_detail_amount(role),
                )
                self.assertEqual(
                    changed_detail.detail_amount,
                    self.helper._expected_detail_amount(role),
                )
                self.assertEqual(baseline_detail.detail_currency_code, "KRW")
                self.assertEqual(changed_detail.detail_currency_code, "KRW")

                reference = self.helper._register_statement_account(baseline_payload)
                store = account.MemoryArtifactStore()
                accepted = account.accept_bank_statement_evidence(
                    self.helper._command(
                        baseline_payload,
                        reference,
                        f"{role}-account-id-present-baseline",
                    ),
                    account.posting.DATABASE_URL,
                    self.helper.case.policy.tenant_reference,
                    artifact_store=store,
                )
                self.assertFalse(accepted["replayed"])
                with self.assertRaisesRegex(
                    account.AccountingValidationError,
                    r"statement identity already exists with different entry evidence",
                ):
                    account.accept_bank_statement_evidence(
                        self.helper._command(
                            id_absent_payload,
                            reference,
                            f"{role}-account-id-absent",
                        ),
                        account.posting.DATABASE_URL,
                        self.helper.case.policy.tenant_reference,
                        artifact_store=store,
                    )

    def test_whitespace_oracle_targets_account_local_id_and_role_digest(self) -> None:
        """Whitespace under DbtrAcct/CdtrAcct Id changes bytes but not account semantics."""
        for role in ("debtor", "creditor"):
            with self.subTest(role=role):
                baseline_payload = self.helper._with_role_account(
                    role,
                    self.baseline_identification,
                )
                account_tag = "DbtrAcct" if role == "debtor" else "CdtrAcct"
                needle = (
                    f"              <{account_tag}>\n"
                    "                <Id>\n"
                ).encode("utf-8")
                self.assertEqual(baseline_payload.count(needle), 1)
                formatted_payload = baseline_payload.replace(
                    needle,
                    needle + b"                  \n",
                    1,
                )
                self.assertNotEqual(baseline_payload, formatted_payload)

                baseline = account.parse_bank_statement_payload(
                    baseline_payload,
                    account.CAMT053_MESSAGE_DEFINITION,
                )
                formatted = account.parse_bank_statement_payload(
                    formatted_payload,
                    account.CAMT053_MESSAGE_DEFINITION,
                )
                target_index = self.helper._target_entry_index(role)
                untouched_index = 1 - target_index
                baseline_entry = baseline.entries[target_index]
                formatted_entry = formatted.entries[target_index]
                baseline_detail = baseline_entry.entry_details[0]
                formatted_detail = formatted_entry.entry_details[0]
                digest_key = f"{role}_account_evidence_hash"

                for value in (
                    baseline.source_artifact_hash,
                    formatted.source_artifact_hash,
                    getattr(baseline_detail, digest_key),
                    getattr(formatted_detail, digest_key),
                    baseline_detail.source_detail_hash,
                    formatted_detail.source_detail_hash,
                    baseline_entry.source_entry_hash,
                    formatted_entry.source_entry_hash,
                    baseline.normalized_payload_hash,
                    formatted.normalized_payload_hash,
                    baseline.account_identifier_hash,
                    formatted.account_identifier_hash,
                ):
                    self.helper._assert_sha256(value)

                self.assertNotEqual(
                    baseline.source_artifact_hash,
                    formatted.source_artifact_hash,
                )
                self.assertEqual(
                    getattr(baseline_detail, digest_key),
                    getattr(formatted_detail, digest_key),
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
                    baseline.entries[untouched_index].source_entry_hash,
                    formatted.entries[untouched_index].source_entry_hash,
                )
                self.assertEqual(
                    formatted_detail.detail_amount,
                    self.helper._expected_detail_amount(role),
                )
                self.assertEqual(formatted_detail.detail_currency_code, "KRW")


if __name__ == "__main__":
    unittest.main()
