"""Focused REDs for counterparty account Id presence and representation semantics."""

from __future__ import annotations

import unittest

from accounting_information_platform import AccountingValidationError, MemoryArtifactStore
from tests import (
    test_postgres_bank_statement_detail_debtor_creditor_account_identification_evidence_red as account,
)


class BankStatementDetailDebtorCreditorAccountIdentificationReviewRedTests(
    unittest.TestCase
):
    """Close optional-Id and account-local whitespace false-GREEN paths."""

    @classmethod
    def setUpClass(cls) -> None:
        """Reuse the canonical real-PostgreSQL integration fixture."""
        account.BankStatementDetailDebtorCreditorAccountIdentificationEvidenceRedTests.setUpClass()

    def setUp(self) -> None:
        """Prepare one isolated account-evidence helper without inheriting its suite."""
        self.helper = (
            account.BankStatementDetailDebtorCreditorAccountIdentificationEvidenceRedTests(
                "setUp"
            )
        )
        self.addCleanup(self.helper.doCleanups)
        self.helper.setUp()
        self.addCleanup(self.helper.tearDown)

    def test_cash_account_present_with_id_absent_is_material_and_requires_correction(self) -> None:
        """CashAccount40.Id optionality remains evidence, not account-container optionality."""
        for role in ("debtor", "creditor"):
            baseline_payload = self.helper._with_account(role, self.helper.base_account)
            id_absent_payload = self._without_account_id(role, baseline_payload)
            baseline = self.helper._parse(baseline_payload)
            changed = self.helper._parse(id_absent_payload)
            target_index = self.helper.helper._target_entry_index(role)
            baseline_entry = baseline.entries[target_index]
            changed_entry = changed.entries[target_index]
            baseline_detail = baseline_entry.entry_details[0]
            changed_detail = changed_entry.entry_details[0]
            self.helper._assert_evidence_chain(
                baseline,
                baseline_entry,
                baseline_detail,
            )
            self.helper._assert_evidence_chain(
                changed,
                changed_entry,
                changed_detail,
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
            self.helper._assert_financial_truth(role, baseline_entry, baseline_detail)
            self.helper._assert_financial_truth(role, changed_entry, changed_detail)

            reference = self.helper.helper._register_statement_account(baseline_payload)
            store = MemoryArtifactStore()
            accepted = account.party.accept_bank_statement_evidence(
                self.helper.helper._command(
                    baseline_payload,
                    reference,
                    f"{role}-account-id-present-baseline",
                ),
                account.party.posting.DATABASE_URL,
                self.helper.helper.case.policy.tenant_reference,
                artifact_store=store,
            )
            self.assertFalse(accepted["replayed"])
            with self.assertRaisesRegex(
                AccountingValidationError,
                account._CORRECTION_ERROR,
            ):
                account.party.accept_bank_statement_evidence(
                    self.helper.helper._command(
                        id_absent_payload,
                        reference,
                        f"{role}-account-id-absent",
                    ),
                    account.party.posting.DATABASE_URL,
                    self.helper.helper.case.policy.tenant_reference,
                    artifact_store=store,
                )

    def test_whitespace_oracle_targets_the_counterparty_account_id(self) -> None:
        """Account-local Id whitespace changes bytes while all semantic hashes stay stable."""
        for role in ("debtor", "creditor"):
            baseline_payload = self.helper._with_account(role, self.helper.base_account)
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

            baseline = self.helper._parse(baseline_payload)
            formatted = self.helper._parse(formatted_payload)
            target_index = self.helper.helper._target_entry_index(role)
            baseline_entry = baseline.entries[target_index]
            formatted_entry = formatted.entries[target_index]
            baseline_detail = baseline_entry.entry_details[0]
            formatted_detail = formatted_entry.entry_details[0]
            for value in (
                baseline.source_artifact_hash,
                formatted.source_artifact_hash,
                baseline_entry.counterparty_evidence_hash,
                formatted_entry.counterparty_evidence_hash,
                baseline_detail.source_detail_hash,
                formatted_detail.source_detail_hash,
                baseline_entry.source_entry_hash,
                formatted_entry.source_entry_hash,
                baseline.normalized_payload_hash,
                formatted.normalized_payload_hash,
            ):
                self.helper._assert_sha256(value)

            self.assertNotEqual(
                baseline.source_artifact_hash,
                formatted.source_artifact_hash,
            )
            self.assertEqual(
                baseline_entry.counterparty_evidence_hash,
                formatted_entry.counterparty_evidence_hash,
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

    @staticmethod
    def _without_account_id(role: str, payload: bytes) -> bytes:
        """Remove only CashAccount40.Id while retaining DbtrAcct/CdtrAcct itself."""
        account_tag = "DbtrAcct" if role == "debtor" else "CdtrAcct"
        start = (
            f"              <{account_tag}>\n"
            "                <Id>\n"
        ).encode("utf-8")
        end = (
            "                </Id>\n"
            f"              </{account_tag}>\n"
        ).encode("utf-8")
        start_index = payload.find(start)
        if start_index < 0 or payload.find(start, start_index + 1) >= 0:
            raise AssertionError(f"focused {account_tag}/Id start must be unique")
        content_start = start_index + len(f"              <{account_tag}>\n".encode("utf-8"))
        end_index = payload.find(end, content_start)
        if end_index < 0:
            raise AssertionError(f"focused {account_tag}/Id end must exist")
        replacement = f"              <{account_tag}>\n              </{account_tag}>\n".encode(
            "utf-8"
        )
        return payload[:start_index] + replacement + payload[end_index + len(end) :]


if __name__ == "__main__":
    unittest.main()
