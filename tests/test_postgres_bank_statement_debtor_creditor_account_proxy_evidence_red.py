"""PostgreSQL REDs for camt.053 debtor/creditor account proxy evidence."""

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


class BankStatementDebtorCreditorAccountProxyEvidenceRedTests(unittest.TestCase):
    """Preserve related-account proxy semantics as private, purpose-bound evidence."""

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
        """Prepare one nested PostgreSQL fixture and lawful CashAccount40 proxies."""
        self.case = posting.PostgresPostingTests("setUp")
        self.addCleanup(self.case.doCleanups)
        self.case.setUp()
        self.addCleanup(self.case.tearDown)
        self.fixture = load_canonical_statement_fixture().decode("utf-8")
        self.same_scalar_identifier = "DE89370400440532013000"
        self.primary_proxy = "treasury-proxy@example.com"
        self.changed_proxy = "reserve-proxy@example.com"

    def test_proxy_semantics_change_admitted_identity(self) -> None:
        """Proxy Id, type choice/value/absence, and presence are material evidence."""
        baseline = self._account_with_proxy(
            type_choice="Cd",
            type_value="EMAL",
            proxy_identifier=self.primary_proxy,
        )
        pairs = [
            (
                "proxy_identifier",
                baseline,
                self._account_with_proxy(
                    type_choice="Cd",
                    type_value="EMAL",
                    proxy_identifier=self.changed_proxy,
                ),
            ),
            (
                "proxy_type_value",
                baseline,
                self._account_with_proxy(
                    type_choice="Cd",
                    type_value="TELE",
                    proxy_identifier=self.primary_proxy,
                ),
            ),
            (
                "proxy_type_choice",
                baseline,
                self._account_with_proxy(
                    type_choice="Prtry",
                    type_value="EMAL",
                    proxy_identifier=self.primary_proxy,
                ),
            ),
            (
                "proxy_type_absence",
                baseline,
                self._account_with_proxy(
                    type_choice=None,
                    type_value=None,
                    proxy_identifier=self.primary_proxy,
                ),
            ),
            (
                "proxy_presence",
                baseline,
                self._account_without_proxy(),
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
                        self._with_role_account(role, left_account),
                        CAMT053_MESSAGE_DEFINITION,
                    )
                    right = parse_bank_statement_payload(
                        self._with_role_account(role, right_account),
                        CAMT053_MESSAGE_DEFINITION,
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

    def test_proxy_xml_formatting_is_representation_only(self) -> None:
        """Formatting around Prxy changes raw bytes, not admitted proxy semantics."""
        baseline = self._account_with_proxy(
            type_choice="Cd",
            type_value="EMAL",
            proxy_identifier=self.primary_proxy,
        )
        formatted = baseline.replace(
            "                <Prxy>\n                  <Tp>",
            "                <Prxy>\n\n                  <Tp>",
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

    def test_material_proxy_change_reaches_complete_correction_boundary(self) -> None:
        """Accepted evidence cannot silently replay changed related-account proxy data."""
        baseline_account = self._account_with_proxy(
            type_choice="Cd",
            type_value="EMAL",
            proxy_identifier=self.primary_proxy,
        )
        changed_account = self._account_with_proxy(
            type_choice="Cd",
            type_value="EMAL",
            proxy_identifier=self.changed_proxy,
        )
        for role in ("debtor", "creditor"):
            with self.subTest(role=role):
                baseline = self._with_role_account(role, baseline_account)
                changed = self._with_role_account(role, changed_account)
                reference = self._register_statement_account(baseline)
                store = MemoryArtifactStore()
                accept_bank_statement_evidence(
                    self._command(baseline, reference, f"{role}-account-proxy-baseline"),
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
                        self._command(changed, reference, f"{role}-account-proxy-changed"),
                        posting.DATABASE_URL,
                        self.case.policy.tenant_reference,
                        artifact_store=store,
                    )

    def test_buyer_projection_does_not_make_proxy_reversible(self) -> None:
        """Related-account proxy stays purpose-bound evidence rather than a buyer field."""
        rich_account = self._account_with_proxy(
            type_choice="Prtry",
            type_value="CWLPROXY",
            proxy_identifier=self.primary_proxy,
        )
        plain_account = self._account_without_proxy()
        for role in ("debtor", "creditor"):
            with self.subTest(role=role):
                with_proxy = self._ingest_and_read_target_detail(
                    role,
                    self._with_role_account(role, rich_account),
                    "account-proxy-present",
                )
                without_proxy = self._ingest_and_read_target_detail(
                    role,
                    self._with_role_account(role, plain_account),
                    "account-proxy-absent",
                )
                digest_key = f"{role}_account_evidence_hash"
                for projection in (with_proxy, without_proxy):
                    self._assert_sha256(projection[digest_key])
                    self._assert_sha256(projection["source_detail_hash"])
                    self.assertEqual(
                        Decimal(str(projection["detail_amount"])), self._expected_detail_amount(role)
                    )
                    self.assertEqual(projection["detail_currency_code"], "KRW")
                self.assertNotEqual(with_proxy[digest_key], without_proxy[digest_key])
                self.assertNotEqual(with_proxy["source_detail_hash"], without_proxy["source_detail_hash"])

                rich_public = dict(with_proxy)
                plain_public = dict(without_proxy)
                for projection in (rich_public, plain_public):
                    projection.pop(digest_key)
                    projection.pop("source_detail_hash")
                self.assertEqual(rich_public, plain_public)

                serialized = json.dumps(
                    {"with_proxy": with_proxy, "without_proxy": without_proxy},
                    sort_keys=True,
                    default=str,
                )
                self.assertNotIn(self.primary_proxy, serialized)
                self.assertNotIn("CWLPROXY", serialized)

    def _account_with_proxy(
        self,
        *,
        type_choice: str | None,
        type_value: str | None,
        proxy_identifier: str,
    ) -> str:
        """Return one IBAN CashAccount40 plus a lawful ProxyAccountIdentification1."""
        type_fragment = ""
        if type_choice is not None:
            if type_choice not in {"Cd", "Prtry"} or type_value is None:
                raise AssertionError("proxy type requires Cd|Prtry and a value")
            type_fragment = (
                "                  <Tp>\n"
                f"                    <{type_choice}>{type_value}</{type_choice}>\n"
                "                  </Tp>\n"
            )
        elif type_value is not None:
            raise AssertionError("proxy type value requires a type choice")
        return (
            self._iban_identification(self.same_scalar_identifier)
            + "\n                <Prxy>\n"
            + type_fragment
            + f"                  <Id>{proxy_identifier}</Id>\n"
            + "                </Prxy>"
        )

    def _account_without_proxy(self) -> str:
        """Return the same related-account IBAN without optional Prxy evidence."""
        return self._iban_identification(self.same_scalar_identifier)


if __name__ == "__main__":
    unittest.main()
