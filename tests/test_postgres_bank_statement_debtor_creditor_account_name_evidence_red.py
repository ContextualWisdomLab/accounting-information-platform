"""PostgreSQL REDs for camt.053 debtor/creditor account-name evidence."""

from __future__ import annotations

import json
import unittest
from decimal import Decimal

from accounting_information_platform import (
    AccountingValidationError,
    CAMT053_MESSAGE_DEFINITION,
    MemoryArtifactStore,
    accept_bank_statement_evidence,
    load_canonical_statement_fixture,
    parse_bank_statement_payload,
)
from tests import test_postgres_bank_statement_debtor_creditor_account_identification_choice_evidence_red as related_account_evidence
from tests import test_postgres_posting as posting


class BankStatementDebtorCreditorAccountNameEvidenceRedTests(unittest.TestCase):
    """Preserve related-account names as private, purpose-bound source evidence."""

    _iban_identification = staticmethod(
        related_account_evidence.BankStatementDebtorCreditorAccountIdentificationChoiceEvidenceRedTests._iban_identification
    )
    _with_role_account = (
        related_account_evidence.BankStatementDebtorCreditorAccountIdentificationChoiceEvidenceRedTests._with_role_account
    )
    _register_statement_account = (
        related_account_evidence.BankStatementDebtorCreditorAccountIdentificationChoiceEvidenceRedTests._register_statement_account
    )
    _ingest_and_read_target_detail = (
        related_account_evidence.BankStatementDebtorCreditorAccountIdentificationChoiceEvidenceRedTests._ingest_and_read_target_detail
    )
    _command = (
        related_account_evidence.BankStatementDebtorCreditorAccountIdentificationChoiceEvidenceRedTests._command
    )
    _target_entry_index = staticmethod(
        related_account_evidence.BankStatementDebtorCreditorAccountIdentificationChoiceEvidenceRedTests._target_entry_index
    )
    _expected_detail_amount = staticmethod(
        related_account_evidence.BankStatementDebtorCreditorAccountIdentificationChoiceEvidenceRedTests._expected_detail_amount
    )
    _assert_sha256 = (
        related_account_evidence.BankStatementDebtorCreditorAccountIdentificationChoiceEvidenceRedTests._assert_sha256
    )

    @classmethod
    def setUpClass(cls) -> None:
        """Reuse the canonical PostgreSQL integration fixture."""
        posting.PostgresPostingTests.setUpClass()

    def setUp(self) -> None:
        """Prepare one nested PostgreSQL fixture and the pinned CAMT.053 statement."""
        self.case = posting.PostgresPostingTests("setUp")
        self.addCleanup(self.case.doCleanups)
        self.case.setUp()
        self.addCleanup(self.case.tearDown)
        self.fixture = load_canonical_statement_fixture().decode("utf-8")
        self.same_scalar_identifier = "DE89370400440532013000"
        self.primary_name = "CWL Counterparty Treasury Alpha"
        self.changed_name = "CWL Counterparty Treasury Beta"

    def test_account_name_value_and_absence_change_admitted_identity(self) -> None:
        """CashAccount40/Nm value and optional presence are material source evidence."""
        pairs = [
            (
                "name_value",
                self._named_account(self.primary_name),
                self._named_account(self.changed_name),
            ),
            (
                "name_presence",
                self._named_account(self.primary_name),
                self._account_without_name(),
            ),
        ]
        for role in ("debtor", "creditor"):
            target_index = self._target_entry_index(role)
            untouched_index = 1 - target_index
            expected_amount = self._expected_detail_amount(role)
            digest_key = f"{role}_account_evidence_hash"
            for semantic, left_account, right_account in pairs:
                with self.subTest(role=role, semantic=semantic):
                    left = parse_bank_statement_payload(
                        self._with_role_account(role, left_account), CAMT053_MESSAGE_DEFINITION
                    )
                    right = parse_bank_statement_payload(
                        self._with_role_account(role, right_account), CAMT053_MESSAGE_DEFINITION
                    )
                    left_detail = left.entries[target_index].entry_details[0]
                    right_detail = right.entries[target_index].entry_details[0]

                    self.assertEqual(left_detail.detail_amount, expected_amount)
                    self.assertEqual(right_detail.detail_amount, expected_amount)
                    self.assertEqual(left_detail.detail_currency_code, "KRW")
                    self.assertEqual(right_detail.detail_currency_code, "KRW")
                    self._assert_sha256(left_detail.source_detail_hash)
                    self._assert_sha256(right_detail.source_detail_hash)
                    self._assert_sha256(getattr(left_detail, digest_key))
                    self._assert_sha256(getattr(right_detail, digest_key))
                    self._assert_sha256(left.entries[target_index].source_entry_hash)
                    self._assert_sha256(right.entries[target_index].source_entry_hash)
                    self._assert_sha256(left.normalized_payload_hash)
                    self._assert_sha256(right.normalized_payload_hash)
                    self.assertNotEqual(getattr(left_detail, digest_key), getattr(right_detail, digest_key))
                    self.assertNotEqual(left_detail.source_detail_hash, right_detail.source_detail_hash)
                    self.assertNotEqual(
                        left.entries[target_index].source_entry_hash,
                        right.entries[target_index].source_entry_hash,
                    )
                    self.assertNotEqual(left.normalized_payload_hash, right.normalized_payload_hash)
                    self.assertEqual(left.account_identifier_hash, right.account_identifier_hash)
                    self.assertEqual(
                        left.entries[untouched_index].source_entry_hash,
                        right.entries[untouched_index].source_entry_hash,
                    )

    def test_account_name_xml_formatting_is_representation_only(self) -> None:
        """Formatting around Nm changes raw bytes, not admitted account-name semantics."""
        baseline = self._named_account(self.primary_name)
        formatted = baseline.replace(
            f"\n                <Nm>{self.primary_name}</Nm>",
            f"\n\n                  <Nm>{self.primary_name}</Nm>",
        )
        self.assertNotEqual(baseline, formatted)
        for role in ("debtor", "creditor"):
            with self.subTest(role=role):
                left = parse_bank_statement_payload(
                    self._with_role_account(role, baseline), CAMT053_MESSAGE_DEFINITION
                )
                right = parse_bank_statement_payload(
                    self._with_role_account(role, formatted), CAMT053_MESSAGE_DEFINITION
                )
                target_index = self._target_entry_index(role)
                left_detail = left.entries[target_index].entry_details[0]
                right_detail = right.entries[target_index].entry_details[0]
                digest_key = f"{role}_account_evidence_hash"

                self._assert_sha256(left_detail.source_detail_hash)
                self._assert_sha256(right_detail.source_detail_hash)
                self._assert_sha256(getattr(left_detail, digest_key))
                self._assert_sha256(getattr(right_detail, digest_key))
                self._assert_sha256(left.entries[target_index].source_entry_hash)
                self._assert_sha256(right.entries[target_index].source_entry_hash)
                self._assert_sha256(left.normalized_payload_hash)
                self._assert_sha256(right.normalized_payload_hash)
                self.assertNotEqual(left.source_artifact_hash, right.source_artifact_hash)
                self.assertEqual(left_detail.source_detail_hash, right_detail.source_detail_hash)
                self.assertEqual(getattr(left_detail, digest_key), getattr(right_detail, digest_key))
                self.assertEqual(
                    left.entries[target_index].source_entry_hash,
                    right.entries[target_index].source_entry_hash,
                )
                self.assertEqual(left.normalized_payload_hash, right.normalized_payload_hash)

    def test_material_account_name_change_reaches_complete_correction_boundary(self) -> None:
        """Accepted evidence cannot silently replay a different related-account name."""
        for role in ("debtor", "creditor"):
            with self.subTest(role=role):
                baseline = self._with_role_account(role, self._named_account(self.primary_name))
                changed = self._with_role_account(role, self._named_account(self.changed_name))
                reference = self._register_statement_account(baseline)
                store = MemoryArtifactStore()
                accept_bank_statement_evidence(
                    self._command(baseline, reference, f"{role}-account-name-baseline"),
                    posting.DATABASE_URL,
                    self.case.policy.tenant_reference,
                    artifact_store=store,
                )
                with self.assertRaisesRegex(
                    AccountingValidationError,
                    (
                        r"^statement identity already exists with different entry evidence\. "
                        r"Use an explicit correction contract, then retry ingest\.$"
                    ),
                ):
                    accept_bank_statement_evidence(
                        self._command(changed, reference, f"{role}-account-name-changed"),
                        posting.DATABASE_URL,
                        self.case.policy.tenant_reference,
                        artifact_store=store,
                    )

    def test_buyer_projection_does_not_make_account_name_reversible(self) -> None:
        """Related-account name stays purpose-bound evidence rather than a buyer field."""
        for role in ("debtor", "creditor"):
            with self.subTest(role=role):
                with_name = self._ingest_and_read_target_detail(
                    role,
                    self._with_role_account(role, self._named_account(self.primary_name)),
                    "account-name-present",
                )
                without_name = self._ingest_and_read_target_detail(
                    role,
                    self._with_role_account(role, self._account_without_name()),
                    "account-name-absent",
                )
                digest_key = f"{role}_account_evidence_hash"
                for projection in (with_name, without_name):
                    self._assert_sha256(projection[digest_key])
                    self._assert_sha256(projection["source_detail_hash"])
                    self.assertEqual(
                        Decimal(str(projection["detail_amount"])), self._expected_detail_amount(role)
                    )
                    self.assertEqual(projection["detail_currency_code"], "KRW")
                self.assertNotEqual(with_name[digest_key], without_name[digest_key])
                self.assertNotEqual(with_name["source_detail_hash"], without_name["source_detail_hash"])

                named_public = dict(with_name)
                unnamed_public = dict(without_name)
                for projection in (named_public, unnamed_public):
                    projection.pop(digest_key)
                    projection.pop("source_detail_hash")
                self.assertEqual(named_public, unnamed_public)

                serialized = json.dumps(
                    {"named": with_name, "unnamed": without_name},
                    sort_keys=True,
                    default=str,
                )
                self.assertNotIn(self.primary_name, serialized)

    def _named_account(self, account_name: str) -> str:
        """Return one CashAccount40 with Id followed by its bank-reported name."""
        return (
            self._iban_identification(self.same_scalar_identifier)
            + f"\n                <Nm>{account_name}</Nm>"
        )

    def _account_without_name(self) -> str:
        """Return the same related-account identification without optional Nm."""
        return self._iban_identification(self.same_scalar_identifier)


if __name__ == "__main__":
    unittest.main()
