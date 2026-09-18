"""PostgreSQL REDs for camt.053 debtor/creditor account type evidence."""

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
from tests import test_postgres_posting as posting
from tests.test_postgres_bank_statement_debtor_creditor_account_identification_choice_evidence_red import (
    BankStatementDebtorCreditorAccountIdentificationChoiceEvidenceRedTests as RelatedAccountEvidenceHelpers,
)


class BankStatementDebtorCreditorAccountTypeChoiceEvidenceRedTests(unittest.TestCase):
    """Preserve related-account type choice semantics without reversible buyer disclosure."""

    _iban_identification = staticmethod(RelatedAccountEvidenceHelpers._iban_identification)
    _with_role_account = RelatedAccountEvidenceHelpers._with_role_account
    _register_statement_account = RelatedAccountEvidenceHelpers._register_statement_account
    _ingest_and_read_target_detail = RelatedAccountEvidenceHelpers._ingest_and_read_target_detail
    _command = RelatedAccountEvidenceHelpers._command
    _target_entry_index = staticmethod(RelatedAccountEvidenceHelpers._target_entry_index)
    _expected_detail_amount = staticmethod(RelatedAccountEvidenceHelpers._expected_detail_amount)
    _assert_sha256 = RelatedAccountEvidenceHelpers._assert_sha256

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

    def test_account_type_choice_value_and_absence_change_admitted_identity(self) -> None:
        """CashAccountType2Choice value, discriminator, and presence are material evidence."""
        pairs = [
            (
                "proprietary_value",
                self._typed_account("Prtry", "OPERATING-A"),
                self._typed_account("Prtry", "OPERATING-B"),
            ),
            (
                "same_scalar_choice",
                self._typed_account("Cd", "CACC"),
                self._typed_account("Prtry", "CACC"),
            ),
            (
                "type_presence",
                self._typed_account("Prtry", "OPERATING-A"),
                self._untyped_account(),
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

    def test_account_type_whitespace_is_representation_only(self) -> None:
        """Whitespace around a proprietary account type changes raw bytes, not admitted semantics."""
        baseline = self._typed_account("Prtry", "OPERATING-A")
        spaced = baseline.replace(
            "<Prtry>OPERATING-A</Prtry>", "<Prtry>  OPERATING-A  </Prtry>"
        )
        for role in ("debtor", "creditor"):
            with self.subTest(role=role):
                left = parse_bank_statement_payload(
                    self._with_role_account(role, baseline), CAMT053_MESSAGE_DEFINITION
                )
                right = parse_bank_statement_payload(
                    self._with_role_account(role, spaced), CAMT053_MESSAGE_DEFINITION
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

    def test_material_account_type_change_reaches_explicit_correction_boundary(self) -> None:
        """Accepted evidence cannot silently replay a different related-account type."""
        for role in ("debtor", "creditor"):
            with self.subTest(role=role):
                baseline = self._with_role_account(
                    role, self._typed_account("Prtry", "OPERATING-A")
                )
                changed = self._with_role_account(
                    role, self._typed_account("Prtry", "OPERATING-B")
                )
                reference = self._register_statement_account(baseline)
                store = MemoryArtifactStore()
                accept_bank_statement_evidence(
                    self._command(baseline, reference, f"{role}-account-type-baseline"),
                    posting.DATABASE_URL,
                    self.case.policy.tenant_reference,
                    artifact_store=store,
                )
                with self.assertRaisesRegex(
                    AccountingValidationError,
                    r"statement identity already exists with different entry evidence",
                ):
                    accept_bank_statement_evidence(
                        self._command(changed, reference, f"{role}-account-type-changed"),
                        posting.DATABASE_URL,
                        self.case.policy.tenant_reference,
                        artifact_store=store,
                    )

    def test_buyer_projection_does_not_make_account_type_reversible(self) -> None:
        """CashAccountType2Choice remains purpose-bound evidence, not a reversible buyer field."""
        same_scalar = "SVGS"
        for role in ("debtor", "creditor"):
            with self.subTest(role=role):
                coded = self._ingest_and_read_target_detail(
                    role,
                    self._with_role_account(role, self._typed_account("Cd", same_scalar)),
                    "account-type-coded",
                )
                proprietary = self._ingest_and_read_target_detail(
                    role,
                    self._with_role_account(role, self._typed_account("Prtry", same_scalar)),
                    "account-type-proprietary",
                )
                digest_key = f"{role}_account_evidence_hash"
                for projection in (coded, proprietary):
                    self._assert_sha256(projection[digest_key])
                    self._assert_sha256(projection["source_detail_hash"])
                    self.assertEqual(
                        Decimal(str(projection["detail_amount"])), self._expected_detail_amount(role)
                    )
                    self.assertEqual(projection["detail_currency_code"], "KRW")
                self.assertNotEqual(coded[digest_key], proprietary[digest_key])
                self.assertNotEqual(coded["source_detail_hash"], proprietary["source_detail_hash"])

                coded_public = dict(coded)
                proprietary_public = dict(proprietary)
                for projection in (coded_public, proprietary_public):
                    projection.pop(digest_key)
                    projection.pop("source_detail_hash")
                self.assertEqual(coded_public, proprietary_public)

                serialized = json.dumps(
                    {"coded": coded, "proprietary": proprietary},
                    sort_keys=True,
                    default=str,
                )
                self.assertNotIn(same_scalar, serialized)

    def _typed_account(self, kind: str, value: str) -> str:
        """Return Id followed by one CashAccountType2Choice fragment in schema sequence."""
        if kind not in {"Cd", "Prtry"}:
            raise AssertionError("account type kind must be Cd or Prtry")
        identification = self._iban_identification(self.same_scalar_identifier)
        return (
            identification
            + "\n                <Tp>\n"
            + f"                  <{kind}>{value}</{kind}>\n"
            + "                </Tp>"
        )

    def _untyped_account(self) -> str:
        """Return the same account identification without an optional account type."""
        return self._iban_identification(self.same_scalar_identifier)


if __name__ == "__main__":
    unittest.main()
